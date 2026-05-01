from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CalendarConfig
from .storage import Store


SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


@dataclass(frozen=True)
class SyncResult:
    imported: int
    calendar_id: str


def authorize(config: CalendarConfig) -> None:
    credentials = _credentials(config)
    _save_credentials(credentials, config.token_path)


def sync_calendar(store: Store, config: CalendarConfig) -> SyncResult:
    credentials = _credentials(config)
    service = _calendar_service(credentials)
    today = date.today()
    time_min = datetime.combine(today, time.min, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    time_max = datetime.combine(today + timedelta(days=config.days_ahead), time.max, tzinfo=timezone.utc).isoformat().replace(
        "+00:00",
        "Z",
    )

    imported = 0
    seen_event_ids: set[str] = set()
    page_token = None
    while True:
        request = service.events().list(
            calendarId=config.calendar_id,
            singleEvents=True,
            showDeleted=True,
            orderBy="startTime",
            timeMin=time_min,
            timeMax=time_max,
            pageToken=page_token,
        )
        response = request.execute()
        for event in response.get("items", []):
            parsed = _parse_event(config.calendar_id, event)
            if parsed is None:
                continue
            seen_event_ids.add(parsed["google_event_id"])
            store.upsert_calendar_event(**parsed)
            imported += 1

        page_token = response.get("nextPageToken")
        if not page_token:
            store.set_sync_token(config.calendar_id, response.get("nextSyncToken"))
            store.mark_missing_calendar_events_cancelled(
                calendar_id=config.calendar_id,
                start_date=today,
                end_date=today + timedelta(days=config.days_ahead),
                seen_google_event_ids=seen_event_ids,
            )
            store.deactivate_stale_calendar_tasks(calendar_id=config.calendar_id)
            break

    _save_credentials(credentials, config.token_path)
    return SyncResult(imported=imported, calendar_id=config.calendar_id)


def _credentials(config: CalendarConfig):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar sync requires: "
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc

    token_path = Path(config.token_path)
    credentials = None
    if token_path.exists():
        credentials = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        credentials_path = Path(config.credentials_path)
        if not credentials_path.exists():
            raise FileNotFoundError(f"Google OAuth credentials not found: {credentials_path}")
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        credentials = flow.run_local_server(host="127.0.0.1", port=config.auth_port, open_browser=False)

    return credentials


def _save_credentials(credentials, token_path: str) -> None:
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(credentials.to_json(), encoding="utf-8")


def _calendar_service(credentials):
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar sync requires: "
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _parse_event(calendar_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
    summary = event.get("summary", "").strip()
    if not summary:
        return None

    start = event.get("start", {})
    end = event.get("end", {})
    start_value = start.get("date") or start.get("dateTime")
    end_value = end.get("date") or end.get("dateTime") or start_value
    if not start_value:
        return None

    due_date = _date_from_google_value(start_value)
    recurring_id = event.get("recurringEventId") or event.get("id")
    return {
        "calendar_id": calendar_id,
        "google_event_id": event["id"],
        "google_recurring_event_id": recurring_id,
        "summary": summary,
        "due_date": due_date,
        "start_at": start_value,
        "end_at": end_value,
        "status": event.get("status", "confirmed"),
        "updated_at": event.get("updated", ""),
        "html_link": event.get("htmlLink", ""),
    }


def _date_from_google_value(value: str) -> date:
    if "T" not in value:
        return date.fromisoformat(value)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).date()


def main_auth() -> None:
    import argparse
    from .config import load_config

    parser = argparse.ArgumentParser(description="Authorize Google Calendar access")
    parser.add_argument("--config", help="Path to config.toml")
    args = parser.parse_args()
    config = load_config(args.config)
    authorize(config.calendar)
    print(f"Google Calendar token saved: {config.calendar.token_path}")


def main_sync() -> None:
    import argparse
    from .config import load_config

    parser = argparse.ArgumentParser(description="Sync Google Calendar events into SQLite")
    parser.add_argument("--config", help="Path to config.toml")
    args = parser.parse_args()
    app_config = load_config(args.config)
    store = Store(app_config.database_path)
    result = sync_calendar(store, app_config.calendar)
    print(f"Synced {result.imported} events from {result.calendar_id}")
