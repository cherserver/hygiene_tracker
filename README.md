# Hygiene Tracker

Offline-first hygiene routine display for a Raspberry Pi Zero 2 W with a small Argon40 SPI/TFT screen. It is designed as a single-purpose household appliance face: always-on, readable on a low-brightness/low-color display, and focused on recurring hygiene tasks rather than general dashboard widgets.

The device shows each task with equal visual weight most of the time. Selection is temporary and only exists so a physical button can mark a task complete. The display uses plain timing labels such as `7 days`, `1 day`, and `Today`; overdue tasks keep the same day wording and rely on red color to communicate urgency.

The visual language is intentionally simple for the TFT: seafoam green for clean/normal, dry-sand amber for used progress and today, and coral red for overdue status. Task rows use stronger borders and solid surfaces so the interface stays legible on the physical screen.

## Features

- SQLite task definitions and completion history
- Next due date calculated from the last completed date
- Device display optimized for a 320x240 low-color TFT
- Plain task timing labels: days remaining, Today, or overdue days in red
- Two-color cleanness gauge: green remaining time covered by dry-sand used time
- Equal-weight task rows with temporary selection highlight
- Task-aware completion flash messages
- Pygame display UI for desktop testing
- Direct framebuffer renderer for small SPI/TFT screens without X11
- gpiozero button support with keyboard fallback
- Optional local web UI for editing task names and intervals
- Optional Google Calendar-driven task source with offline SQLite cache
- systemd unit for autostart

## Default Tasks

| Task | Interval |
| --- | --- |
| Cat fountain | every 7 days |
| Air conditioner | every 1 month |
| Full floor cleaning with steam | every 2 months |

When the database is first seeded, tasks start as due today. After a task is marked done, its next due date is calculated from that completion date.

## Controls

| Action | Button | Keyboard fallback |
| --- | --- | --- |
| Previous task | Up | Up or W |
| Next task | Down | Down or S |
| Mark done today | Done | Enter or Space |
| Log screen | Menu/Back | Tab or H |
| Quit desktop test run | - | Esc or Q |

## Install on Raspberry Pi

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip python3-pygame python3-pil python3-gpiozero sqlite3 avahi-daemon
sudo mkdir -p /opt/hygiene-tracker /etc/hygiene-tracker /var/lib/hygiene-tracker
sudo chown -R "$USER:$USER" /opt/hygiene-tracker /var/lib/hygiene-tracker
sudo usermod -aG video,input,gpio,spi "$USER"
```

Copy this project into `/opt/hygiene-tracker`, then:

```bash
cd /opt/hygiene-tracker
python3 -m venv .venv --system-site-packages
. .venv/bin/activate
pip install -e .
cp config.example.toml /etc/hygiene-tracker/config.toml
hygiene-tracker-seed --config /etc/hygiene-tracker/config.toml
```

Edit `/etc/hygiene-tracker/config.toml`, set `display_backend = "fbdev"` and `framebuffer = "/dev/fb1"` for the confirmed `fb_ili9340` display, and set the BCM GPIO pins for the Argon40 display buttons on your hardware.

## Deploy From Windows

From this repository on the Windows development machine:

```powershell
.\scripts\deploy.ps1
```

The deploy script defaults to `cher@hygiene` over SSH pubkey auth. It creates a tar archive of the current working tree, copies it to the Pi, installs the Raspberry Pi OS packages, syncs the app into `/opt/hygiene-tracker`, creates `/etc/hygiene-tracker/config.toml` if missing, seeds the database, installs the systemd unit, and restarts `hygiene-tracker.service`.

Useful options:

```powershell
.\scripts\deploy.ps1 -SkipApt
.\scripts\deploy.ps1 -NoRestart
.\scripts\deploy.ps1 -PythonBin python3.12
.\scripts\deploy.ps1 -SshTarget cher@hygiene -AppDir /opt/hygiene-tracker
```

The script preserves an existing `/etc/hygiene-tracker/config.toml` and excludes local databases, virtual environments, `.git`, and temporary test data from the upload.

## Google Calendar Mode

Calendar mode lets a dedicated Google Calendar drive exact task dates. The app expands recurring Google events into local SQLite rows, so the device still boots and displays the last synced schedule when offline. Marking an item done records local completion history for that event occurrence; it does not edit Google Calendar.

Create a dedicated calendar, such as `Hygiene`, and add recurring all-day events for the tasks you want displayed.

Install Google client libraries on the Pi:

```bash
cd /opt/hygiene-tracker
. .venv/bin/activate
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
pip install -e . --no-deps
```

In Google Cloud Console:

1. Enable the Google Calendar API.
2. Create an OAuth client with application type `Desktop app`.
3. Download the JSON file to `/etc/hygiene-tracker/google-credentials.json`.
4. If the app is in testing mode, add your Google account as a test user.

Set `/etc/hygiene-tracker/config.toml`:

```toml
[calendar]
enabled = true
calendar_id = "primary"
credentials_path = "/etc/hygiene-tracker/google-credentials.json"
token_path = "/var/lib/hygiene-tracker/google-token.json"
auth_port = 8765
days_ahead = 120
sync_interval_minutes = 60
due_soon_days = 3
```

For a non-primary calendar, use its calendar ID from Google Calendar settings.

Authorize once over SSH with local port forwarding:

```bash
ssh -L 8765:127.0.0.1:8765 cher@hygiene.local
cd /opt/hygiene-tracker
. .venv/bin/activate
hygiene-tracker-google-auth --config /etc/hygiene-tracker/config.toml
```

Open the printed URL in your desktop browser. The OAuth callback returns through the SSH tunnel and saves `/var/lib/hygiene-tracker/google-token.json`.

Run an initial sync:

```bash
hygiene-tracker-calendar-sync --config /etc/hygiene-tracker/config.toml
```

After that, the display service syncs in the background at `sync_interval_minutes`.

## Run Manually

Desktop/windowed test:

```bash
hygiene-tracker --config config.example.toml --web
```

On the Pi framebuffer:

```bash
hygiene-tracker --config /etc/hygiene-tracker/config.toml --web
```

The optional web UI listens on the configured port. It is an admin surface for editing tasks, not the primary device experience. With mDNS/Avahi configured, use `http://hygiene-tracker.local:8080`; otherwise use the Pi IP address.

## Autostart With systemd

```bash
sudo cp systemd/hygiene-tracker.service /etc/systemd/system/hygiene-tracker.service
sudo systemctl daemon-reload
sudo systemctl enable hygiene-tracker.service
sudo systemctl start hygiene-tracker.service
sudo systemctl status hygiene-tracker.service
```

View logs:

```bash
journalctl -u hygiene-tracker.service -f
```

## Development

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m unittest discover -s tests
```

Project layout:

- `src/hygiene_tracker/schedule.py` contains date and due-state logic
- `src/hygiene_tracker/storage.py` owns SQLite schema, seed data, and history
- `src/hygiene_tracker/display.py` renders the desktop/Pygame device display
- `src/hygiene_tracker/framebuffer_display.py` renders the TFT device display directly to `/dev/fb*` without X11
- `src/hygiene_tracker/buttons.py` maps gpiozero buttons to actions
- `src/hygiene_tracker/web.py` serves the optional local web UI
- `src/hygiene_tracker/google_calendar.py` syncs Google Calendar event instances into SQLite

## Repository Notes

- Use `config.example.toml` as the public template; keep local `config.toml`, SQLite databases, and Google OAuth files out of Git.
- Pull requests should run `python -m unittest discover -s tests` before submission.
- See `CONTRIBUTING.md` for contribution guidelines and `CHANGELOG.md` for release notes.

## License

This project is available under the MIT License. See `LICENSE` for details.
