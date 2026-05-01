from __future__ import annotations

import argparse
import threading
import time

from .buttons import ButtonActions, ButtonController
from .config import load_config
from .display import create_dashboard
from .google_calendar import sync_calendar
from .storage import Store
from .web import make_server


DONE_CONFIRM_SECONDS = 4.0


class App:
    def __init__(self, config_path: str | None = None, enable_web: bool = False) -> None:
        self.config = load_config(config_path)
        self.store = Store(self.config.database_path)
        self.store.seed_defaults()
        self.dashboard = create_dashboard(
            backend=self.config.display_backend,
            width=self.config.screen_width,
            height=self.config.screen_height,
            fullscreen=self.config.fullscreen,
            video_driver=self.config.video_driver,
            framebuffer=self.config.framebuffer,
            display=self.config.display,
            button_label_centers=self.config.button_label_centers,
        )
        self.selected_index: int | None = None
        self.last_selection_at = 0.0
        self.running = True
        self._dirty = True
        self._statuses = []
        self._lock = threading.Lock()
        self._pending_done_task_id: int | None = None
        self._pending_done_calendar_event_id: int | None = None
        self._pending_done_until = 0.0
        self.buttons = ButtonController(
            self.config.buttons,
            ButtonActions(
                up=self.select_previous,
                down=self.select_next,
                done=self.mark_selected_done,
                menu=self.toggle_history,
            ),
        )
        self.buttons.start()
        self.web_server = (
            make_server(self.store, self.config.web_host, self.config.web_port, on_change=self.request_refresh)
            if enable_web
            else None
        )
        self.web_thread: threading.Thread | None = None
        self.sync_thread: threading.Thread | None = None

    def run(self) -> None:
        if self.web_server is not None:
            self.web_thread = threading.Thread(target=self.web_server.serve_forever, daemon=True)
            self.web_thread.start()
            self.dashboard.set_flash(f"Web UI on :{self.config.web_port}")
        if self.config.calendar.enabled:
            self.sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
            self.sync_thread.start()

        try:
            while self.running:
                for action in self.dashboard.poll_events():
                    self.handle_action(action)

                if self._dirty:
                    self.refresh()
                self.expire_selection()

                history_lines = [
                    f"{item.completed_at.isoformat()}  {item.task_name}"
                    for item in self.store.history(20)
                ]
                self.dashboard.draw(
                    self._statuses,
                    self.selected_index,
                    history_lines=history_lines,
                    gpio_available=self.buttons.available,
                )
                self.dashboard.tick()
        finally:
            self.close()

    def handle_action(self, action: str) -> None:
        if action == "quit":
            self.running = False
        elif action == "up":
            self.select_previous()
        elif action == "down":
            self.select_next()
        elif action == "done":
            self.mark_selected_done()
        elif action == "menu":
            self.toggle_history()

    def refresh(self) -> None:
        with self._lock:
            self._statuses = self.store.statuses(
                due_soon_fraction=self.config.due_soon_fraction,
                calendar_due_soon_days=self.config.calendar.due_soon_days,
            )
            if self.selected_index is not None and self._statuses:
                self.selected_index %= len(self._statuses)
            elif not self._statuses:
                self.selected_index = None
            self._dirty = False

    def request_refresh(self) -> None:
        with self._lock:
            self._dirty = True
            self._clear_done_confirmation_locked()

    def select_previous(self) -> None:
        with self._lock:
            if self._statuses:
                self._clear_done_confirmation_locked()
                if self.selected_index is None:
                    self.selected_index = 0
                else:
                    self.selected_index = (self.selected_index - 1) % len(self._statuses)
                self.last_selection_at = time.monotonic()

    def select_next(self) -> None:
        with self._lock:
            if self._statuses:
                self._clear_done_confirmation_locked()
                if self.selected_index is None:
                    self.selected_index = 0
                else:
                    self.selected_index = (self.selected_index + 1) % len(self._statuses)
                self.last_selection_at = time.monotonic()

    def mark_selected_done(self) -> None:
        now = time.monotonic()
        with self._lock:
            if not self._statuses or self.selected_index is None:
                self._clear_done_confirmation_locked()
                return
            status = self._statuses[self.selected_index]
            task = status.task
            is_confirmed = (
                self._pending_done_task_id == task.id
                and self._pending_done_calendar_event_id == status.calendar_event_id
                and now <= self._pending_done_until
            )
            if not is_confirmed:
                self._pending_done_task_id = task.id
                self._pending_done_calendar_event_id = status.calendar_event_id
                self._pending_done_until = now + DONE_CONFIRM_SECONDS
                self.last_selection_at = now
                self.dashboard.set_flash(f"Press Done again: {task.name}", seconds=DONE_CONFIRM_SECONDS)
                return
        self.store.mark_done(task.id, calendar_event_id=status.calendar_event_id)
        self.dashboard.set_flash(_completion_message(task.name))
        with self._lock:
            self.selected_index = None
            self._clear_done_confirmation_locked()
        self._dirty = True

    def toggle_history(self) -> None:
        with self._lock:
            self.selected_index = None
            self._clear_done_confirmation_locked()
        self.dashboard.toggle_history()

    def expire_selection(self) -> None:
        now = time.monotonic()
        timeout = self.config.selection_timeout_seconds
        with self._lock:
            if self._pending_done_task_id is not None and now > self._pending_done_until:
                self._clear_done_confirmation_locked()
            if timeout <= 0:
                return
            if self.selected_index is None:
                return
            if now - self.last_selection_at >= timeout:
                self.selected_index = None
                self._clear_done_confirmation_locked()

    def close(self) -> None:
        if self.web_server is not None:
            self.web_server.shutdown()
            self.web_server.server_close()
        self.buttons.close()
        self.dashboard.close()
        time.sleep(0.05)

    def _sync_loop(self) -> None:
        while self.running:
            try:
                result = sync_calendar(self.store, self.config.calendar)
                print(f"Calendar synced: {result.imported} events", flush=True)
                self.request_refresh()
            except Exception as exc:
                print(f"Calendar sync failed: {exc}", flush=True)
            sleep_seconds = max(60, self.config.calendar.sync_interval_minutes * 60)
            for _ in range(sleep_seconds):
                if not self.running:
                    break
                time.sleep(1)

    def _clear_done_confirmation_locked(self) -> None:
        self._pending_done_task_id = None
        self._pending_done_calendar_event_id = None
        self._pending_done_until = 0.0


def _completion_message(task_name: str) -> str:
    name = task_name.lower()
    if "fountain" in name or "water" in name:
        return f"{task_name} refreshed"
    if "air" in name or "filter" in name or "conditioner" in name:
        return f"{task_name} reset"
    if "floor" in name or "steam" in name or "clean" in name:
        return "Floor is fresh"
    return f"{task_name} done"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the hygiene tracker device display")
    parser.add_argument("--config", help="Path to config.toml")
    parser.add_argument("--web", action="store_true", help="Enable the optional local web UI")
    args = parser.parse_args()
    App(config_path=args.config, enable_web=args.web).run()


if __name__ == "__main__":
    main()
