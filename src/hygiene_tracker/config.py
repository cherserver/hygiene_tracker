from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast


DEFAULT_CONFIG_PATHS = [
    Path("/etc/hygiene-tracker/config.toml"),
    Path("config.toml"),
]


@dataclass(frozen=True)
class ButtonConfig:
    up: int = 5
    down: int = 6
    done: int = 13
    menu: int = 19
    bounce_time: float = 0.08


@dataclass(frozen=True)
class CalendarConfig:
    enabled: bool = False
    calendar_id: str = "primary"
    credentials_path: str = "/etc/hygiene-tracker/google-credentials.json"
    token_path: str = "/var/lib/hygiene-tracker/google-token.json"
    auth_port: int = 8765
    days_ahead: int = 120
    sync_interval_minutes: int = 60
    due_soon_days: int = 3


@dataclass(frozen=True)
class AppConfig:
    database_path: str = "data/hygiene.sqlite3"
    display_backend: str = "pygame"
    fullscreen: bool = False
    screen_width: int = 320
    screen_height: int = 240
    button_label_centers: tuple[int, int, int, int] = (56, 123, 190, 257)
    selection_timeout_seconds: int = 12
    video_driver: str = ""
    framebuffer: str = ""
    display: str = ""
    due_soon_fraction: float = 0.2
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    buttons: ButtonConfig = ButtonConfig()
    calendar: CalendarConfig = CalendarConfig()


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = _resolve_config_path(path)
    if config_path is None:
        return AppConfig()

    raw = _read_simple_toml(config_path)
    button_raw = raw.get("buttons", {})
    calendar_raw = raw.get("calendar", {})
    return AppConfig(
        database_path=str(raw.get("database_path", AppConfig.database_path)),
        display_backend=str(raw.get("display_backend", AppConfig.display_backend)),
        fullscreen=bool(raw.get("fullscreen", AppConfig.fullscreen)),
        screen_width=int(raw.get("screen_width", AppConfig.screen_width)),
        screen_height=int(raw.get("screen_height", AppConfig.screen_height)),
        button_label_centers=_parse_centers(raw.get("button_label_centers", AppConfig.button_label_centers)),
        selection_timeout_seconds=int(raw.get("selection_timeout_seconds", AppConfig.selection_timeout_seconds)),
        video_driver=str(raw.get("video_driver", AppConfig.video_driver)),
        framebuffer=str(raw.get("framebuffer", AppConfig.framebuffer)),
        display=str(raw.get("display", AppConfig.display)),
        due_soon_fraction=float(raw.get("due_soon_fraction", AppConfig.due_soon_fraction)),
        web_host=str(raw.get("web_host", AppConfig.web_host)),
        web_port=int(raw.get("web_port", AppConfig.web_port)),
        buttons=ButtonConfig(
            up=int(button_raw.get("up", ButtonConfig.up)),
            down=int(button_raw.get("down", ButtonConfig.down)),
            done=int(button_raw.get("done", ButtonConfig.done)),
            menu=int(button_raw.get("menu", ButtonConfig.menu)),
            bounce_time=float(button_raw.get("bounce_time", ButtonConfig.bounce_time)),
        ),
        calendar=CalendarConfig(
            enabled=bool(calendar_raw.get("enabled", CalendarConfig.enabled)),
            calendar_id=str(calendar_raw.get("calendar_id", CalendarConfig.calendar_id)),
            credentials_path=str(calendar_raw.get("credentials_path", CalendarConfig.credentials_path)),
            token_path=str(calendar_raw.get("token_path", CalendarConfig.token_path)),
            auth_port=int(calendar_raw.get("auth_port", CalendarConfig.auth_port)),
            days_ahead=int(calendar_raw.get("days_ahead", CalendarConfig.days_ahead)),
            sync_interval_minutes=int(calendar_raw.get("sync_interval_minutes", CalendarConfig.sync_interval_minutes)),
            due_soon_days=int(calendar_raw.get("due_soon_days", CalendarConfig.due_soon_days)),
        ),
    )


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        candidate = Path(path)
        if not candidate.exists():
            raise FileNotFoundError(candidate)
        return candidate
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def _read_simple_toml(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    section: dict[str, object] | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            nested: dict[str, object] = {}
            data[name] = nested
            section = nested
            continue
        if "=" not in stripped:
            continue
        key, value = [part.strip() for part in stripped.split("=", 1)]
        target = section if section is not None else data
        target[key] = _parse_value(value)
    return data


def _parse_value(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value.strip("\"'")


def _parse_centers(value: object) -> tuple[int, int, int, int]:
    if isinstance(value, tuple) and len(value) == 4:
        return tuple(int(item) for item in value)  # type: ignore[return-value]
    if isinstance(value, list) and len(value) == 4:
        return tuple(int(item) for item in value)  # type: ignore[return-value]
    return (56, 123, 190, 257)
