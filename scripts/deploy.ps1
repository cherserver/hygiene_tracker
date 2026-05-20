[CmdletBinding()]
param(
    [string]$SshTarget = "cher@hygiene",
    [string]$AppDir = "/opt/hygiene-tracker",
    [string]$ConfigDir = "/etc/hygiene-tracker",
    [string]$DataDir = "/var/lib/hygiene-tracker",
    [string]$ServiceName = "hygiene-tracker.service",
    [string]$PythonBin = "",
    [switch]$SkipApt,
    [switch]$SkipSeed,
    [switch]$SkipService,
    [switch]$NoRestart,
    [switch]$KeepTempFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found. Install or enable it, then try again."
    }
}

function Invoke-Native {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "'$FilePath' failed with exit code $LASTEXITCODE."
    }
}

function ConvertTo-ShellSingleQuoted {
    param([string]$Value)

    return "'" + $Value.Replace("'", "'\''") + "'"
}

function ConvertTo-Flag {
    param([switch]$Value)

    if ($Value.IsPresent) {
        return "1"
    }

    return "0"
}

$ScriptDir = Split-Path -Parent $PSCommandPath
$RepoRoot = Split-Path -Parent $ScriptDir
$DeployId = Get-Date -Format "yyyyMMddHHmmss"
$TempDir = [System.IO.Path]::GetTempPath()
$ArchivePath = Join-Path $TempDir "hygiene-tracker-$DeployId.tar.gz"
$LocalRemoteScriptPath = Join-Path $TempDir "hygiene-tracker-deploy-$DeployId.sh"
$RemoteArchivePath = "/tmp/hygiene-tracker-$DeployId.tar.gz"
$RemoteScriptPath = "/tmp/hygiene-tracker-deploy-$DeployId.sh"

Require-Command ssh
Require-Command scp
Require-Command tar

$tarArgs = @(
    "-czf", $ArchivePath,
    "--exclude=.git",
    "--exclude=.venv",
    "--exclude=.test-data",
    "--exclude=__pycache__",
    "--exclude=*.pyc",
    "--exclude=.pytest_cache",
    "--exclude=.mypy_cache",
    "--exclude=.ruff_cache",
    "--exclude=config.toml",
    "--exclude=*.sqlite3",
    "--exclude=*.db",
    "--exclude=tmp",
    "-C", $RepoRoot,
    "."
)

Write-Host "Creating deploy archive from $RepoRoot"
Invoke-Native tar $tarArgs

$remoteTemplate = @'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=__APP_DIR__
CONFIG_DIR=__CONFIG_DIR__
DATA_DIR=__DATA_DIR__
SERVICE_NAME=__SERVICE_NAME__
REMOTE_ARCHIVE=__REMOTE_ARCHIVE__
REMOTE_SCRIPT=__REMOTE_SCRIPT__
PYTHON_BIN=__PYTHON_BIN__
SKIP_APT=__SKIP_APT__
SKIP_SEED=__SKIP_SEED__
SKIP_SERVICE=__SKIP_SERVICE__
NO_RESTART=__NO_RESTART__

log() {
    printf '\n==> %s\n' "$*"
}

REMOTE_USER="$(id -un)"
REMOTE_GROUP="$(id -gn)"
STAGE_DIR=""

cleanup() {
    if [ -n "$STAGE_DIR" ] && [ -d "$STAGE_DIR" ]; then
        rm -rf "$STAGE_DIR"
    fi

    rm -f "$REMOTE_ARCHIVE" "$REMOTE_SCRIPT"
}

trap cleanup EXIT

if [ "$SKIP_APT" = "0" ]; then
    log "Installing Raspberry Pi OS packages"
    sudo apt-get update
    sudo apt-get install -y python3-venv python3-pip python3-pygame python3-pil python3-gpiozero sqlite3 avahi-daemon rsync
else
    log "Skipping apt package install"
fi

if [ -n "$PYTHON_BIN" ]; then
    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        printf 'Requested Python command not found on Pi: %s\n' "$PYTHON_BIN" >&2
        exit 1
    fi
elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
else
    PYTHON_BIN="python3"
fi

"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else "Python 3.12+ is required by pyproject.toml; found " + sys.version.split()[0])'

log "Preparing directories"
sudo mkdir -p "$APP_DIR" "$CONFIG_DIR" "$DATA_DIR"
sudo chown -R "$REMOTE_USER:$REMOTE_GROUP" "$APP_DIR" "$DATA_DIR"

for group_name in video input gpio spi; do
    if getent group "$group_name" >/dev/null 2>&1; then
        sudo usermod -aG "$group_name" "$REMOTE_USER"
    fi
done

log "Syncing application files to $APP_DIR"
STAGE_DIR="$(mktemp -d)"
tar -xzf "$REMOTE_ARCHIVE" -C "$STAGE_DIR"

if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude='.venv/' "$STAGE_DIR"/ "$APP_DIR"/
else
    tar -xzf "$REMOTE_ARCHIVE" -C "$APP_DIR"
fi

log "Installing Python package"
cd "$APP_DIR"
"$PYTHON_BIN" -m venv .venv --system-site-packages
. "$APP_DIR/.venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .

if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    log "Creating first config at $CONFIG_DIR/config.toml"
    sudo cp "$APP_DIR/config.example.toml" "$CONFIG_DIR/config.toml"
    sudo chown root:root "$CONFIG_DIR/config.toml"
    sudo chmod 0644 "$CONFIG_DIR/config.toml"
else
    log "Keeping existing config at $CONFIG_DIR/config.toml"
fi

if [ "$SKIP_SEED" = "0" ]; then
    log "Seeding database if needed"
    "$APP_DIR/.venv/bin/hygiene-tracker-seed" --config "$CONFIG_DIR/config.toml"
else
    log "Skipping database seed"
fi

if [ "$SKIP_SERVICE" = "0" ]; then
    log "Installing systemd service"
    sudo cp "$APP_DIR/systemd/hygiene-tracker.service" "/etc/systemd/system/$SERVICE_NAME"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"

    if [ "$NO_RESTART" = "0" ]; then
        log "Restarting $SERVICE_NAME"
        sudo systemctl restart "$SERVICE_NAME"
    else
        log "Leaving $SERVICE_NAME stopped/running as-is"
    fi

    sudo systemctl --no-pager --full status "$SERVICE_NAME" || true
else
    log "Skipping systemd service install"
fi

log "Deploy complete"
'@

$remoteScript = $remoteTemplate
$remoteScript = $remoteScript.Replace("__APP_DIR__", (ConvertTo-ShellSingleQuoted $AppDir))
$remoteScript = $remoteScript.Replace("__CONFIG_DIR__", (ConvertTo-ShellSingleQuoted $ConfigDir))
$remoteScript = $remoteScript.Replace("__DATA_DIR__", (ConvertTo-ShellSingleQuoted $DataDir))
$remoteScript = $remoteScript.Replace("__SERVICE_NAME__", (ConvertTo-ShellSingleQuoted $ServiceName))
$remoteScript = $remoteScript.Replace("__REMOTE_ARCHIVE__", (ConvertTo-ShellSingleQuoted $RemoteArchivePath))
$remoteScript = $remoteScript.Replace("__REMOTE_SCRIPT__", (ConvertTo-ShellSingleQuoted $RemoteScriptPath))
$remoteScript = $remoteScript.Replace("__PYTHON_BIN__", (ConvertTo-ShellSingleQuoted $PythonBin))
$remoteScript = $remoteScript.Replace("__SKIP_APT__", (ConvertTo-Flag $SkipApt))
$remoteScript = $remoteScript.Replace("__SKIP_SEED__", (ConvertTo-Flag $SkipSeed))
$remoteScript = $remoteScript.Replace("__SKIP_SERVICE__", (ConvertTo-Flag $SkipService))
$remoteScript = $remoteScript.Replace("__NO_RESTART__", (ConvertTo-Flag $NoRestart))

[System.IO.File]::WriteAllText($LocalRemoteScriptPath, $remoteScript, [System.Text.UTF8Encoding]::new($false))

try {
    Write-Host "Uploading archive and remote installer to $SshTarget"
    Invoke-Native scp @($ArchivePath, "${SshTarget}:$RemoteArchivePath")
    Invoke-Native scp @($LocalRemoteScriptPath, "${SshTarget}:$RemoteScriptPath")

    Write-Host "Running deploy on $SshTarget"
    Invoke-Native ssh @($SshTarget, "chmod +x '$RemoteScriptPath' && '$RemoteScriptPath'")
}
finally {
    if (-not $KeepTempFiles.IsPresent) {
        Remove-Item -LiteralPath $ArchivePath, $LocalRemoteScriptPath -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-Host "Kept local temp files:"
        Write-Host "  $ArchivePath"
        Write-Host "  $LocalRemoteScriptPath"
    }
}
