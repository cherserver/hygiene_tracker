from __future__ import annotations

from html import escape
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .storage import Store


def make_server(
    store: Store,
    host: str,
    port: int,
    on_change: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    class Handler(HygieneHandler):
        pass

    Handler.store = store
    Handler.on_change = on_change
    return ThreadingHTTPServer((host, port), Handler)


class HygieneHandler(BaseHTTPRequestHandler):
    store: Store
    on_change: Callable[[], None] | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(self._index())
        elif parsed.path == "/history":
            self._send_html(self._history())
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))

        if parsed.path == "/task":
            task_id = int(_one(data, "task_id"))
            name = _one(data, "name")
            interval_value = int(_one(data, "interval_value"))
            interval_unit = _one(data, "interval_unit")
            self.store.update_task(task_id, name, interval_value, interval_unit)
            self._notify_change()
            self._redirect("/")
        elif parsed.path == "/done":
            task_id = int(_one(data, "task_id"))
            calendar_event_id = _optional_int(data, "calendar_event_id")
            self.store.mark_done(task_id, calendar_event_id=calendar_event_id)
            self._notify_change()
            self._redirect("/")
        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _index(self) -> str:
        rows = []
        for status in self.store.statuses():
            task = status.task
            action_cell = _task_actions(status)
            rows.append(
                f"""
                <tr>
                  <td>
                    <div class="task-name">{escape(task.name)}</div>
                    <div class="task-source">{escape(task.source.replace("_", " ").title())}</div>
                  </td>
                  <td>{_status_badge(status.due_state.value)}</td>
                  <td><time>{status.next_due.isoformat()}</time></td>
                  <td>{status.last_completed.isoformat() if status.last_completed else '<span class="muted">Never</span>'}</td>
                  <td class="actions">{action_cell}</td>
                </tr>
                """
            )
        return _page(
            "Hygiene tracker",
            f"""
            <section class="panel">
              <div class="panel-head">
                <div>
                  <p class="eyebrow">Routine control</p>
                  <h1>Tasks</h1>
                </div>
                <nav><a class="active" href="/">Tasks</a><a href="/history">History</a></nav>
              </div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Task</th><th>Status</th><th>Next due</th><th>Last done</th><th>Edit</th></tr></thead>
                  <tbody>{''.join(rows) or '<tr><td colspan="5" class="empty">No active tasks</td></tr>'}</tbody>
                </table>
              </div>
            </section>
            """,
        )

    def _history(self) -> str:
        rows = [
            f"<tr><td>{item.completed_at.isoformat()}</td><td>{escape(item.task_name)}</td><td>{escape(item.note)}</td></tr>"
            for item in self.store.history(100)
        ]
        return _page(
            "Completion history",
            f"""
            <section class="panel">
              <div class="panel-head">
                <div>
                  <p class="eyebrow">Completed work</p>
                  <h1>History</h1>
                </div>
                <nav><a href="/">Tasks</a><a class="active" href="/history">History</a></nav>
              </div>
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Date</th><th>Task</th><th>Note</th></tr></thead>
                  <tbody>{''.join(rows) or '<tr><td colspan="3" class="empty">No completions yet</td></tr>'}</tbody>
                </table>
              </div>
            </section>
            """,
        )

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _notify_change(self) -> None:
        if self.on_change is not None:
            self.on_change()


def _one(data: dict[str, list[str]], key: str) -> str:
    value = data.get(key, [""])[0]
    if value == "":
        raise ValueError(f"missing {key}")
    return value


def _optional_int(data: dict[str, list[str]], key: str) -> int | None:
    value = data.get(key, [""])[0]
    return int(value) if value else None


def _task_actions(status) -> str:
    task = status.task
    confirm_text = escape(f'Mark "{task.name}" done today?', quote=True)
    done_form = f"""
    <form class="inline-form" method="post" action="/done" data-confirm="{confirm_text}" onsubmit="return confirm(this.dataset.confirm)">
      <input type="hidden" name="task_id" value="{task.id}">
      <input type="hidden" name="calendar_event_id" value="{status.calendar_event_id or ''}">
      <button class="primary">Done today</button>
    </form>
    """
    if task.source == "google_calendar":
        return f"<div class=\"calendar-label\">Google Calendar</div>{done_form}"

    return f"""
    <form class="edit-form" method="post" action="/task">
      <input type="hidden" name="task_id" value="{task.id}">
      <input aria-label="Task name" name="name" value="{escape(task.name)}">
      <input name="interval_value" type="number" min="1" value="{task.interval_value}">
      <select aria-label="Interval unit" name="interval_unit">
        <option value="days" {"selected" if task.interval_unit == "days" else ""}>days</option>
        <option value="months" {"selected" if task.interval_unit == "months" else ""}>months</option>
      </select>
      <button>Save</button>
    </form>
    {done_form}
    """


def _status_badge(label: str) -> str:
    return f'<span class="status {_status_class(label)}">{escape(label)}</span>'


def _status_class(label: str) -> str:
    return label.lower().replace(" ", "-")


def _page(title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{escape(title)}</title>
      <style>
        :root {{
          color-scheme: dark;
          --bg: #0c0f13;
          --surface: #14181e;
          --surface-2: #1e242c;
          --line: #3a4450;
          --text: #f5f8fb;
          --muted: #a6b1be;
          --soft: #ccd3dc;
          --accent: #6bb8ff;
          --ok: #40bd7c;
          --soon: #40bd7c;
          --today: #6bb8ff;
          --late: #e04c58;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          min-height: 100vh;
          margin: 0;
          background: var(--bg);
          color: var(--text);
          font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}
        main {{
          width: min(1180px, calc(100% - 32px));
          margin: 0 auto;
          padding: 32px 0;
        }}
        .panel {{
          background: var(--surface);
          border: 1px solid var(--line);
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 18px 60px rgba(0, 0, 0, .28);
        }}
        .panel-head {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          padding: 22px 24px;
          border-bottom: 1px solid var(--line);
        }}
        h1 {{
          margin: 0;
          font-size: clamp(1.65rem, 2.8vw, 2.3rem);
          line-height: 1;
          letter-spacing: 0;
        }}
        .eyebrow {{
          margin: 0 0 8px;
          color: var(--muted);
          font-size: .78rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: .08em;
        }}
        nav {{
          display: inline-flex;
          gap: 6px;
          padding: 4px;
          border: 1px solid var(--line);
          border-radius: 8px;
          background: #0f1318;
        }}
        a {{
          color: var(--soft);
          border-radius: 6px;
          padding: .55rem .8rem;
          text-decoration: none;
          font-weight: 700;
        }}
        a.active, a:hover {{
          background: var(--surface-2);
          color: var(--text);
        }}
        .table-wrap {{ overflow-x: auto; }}
        table {{
          border-collapse: collapse;
          width: 100%;
          min-width: 820px;
        }}
        th, td {{
          border-bottom: 1px solid rgba(58, 68, 80, .75);
          padding: .85rem 1rem;
          text-align: left;
          vertical-align: middle;
        }}
        th {{
          color: var(--muted);
          font-size: .76rem;
          font-weight: 800;
          text-transform: uppercase;
          letter-spacing: .08em;
          background: #101419;
        }}
        tr:last-child td {{ border-bottom: 0; }}
        tbody tr:hover {{ background: rgba(255, 255, 255, .025); }}
        .task-name {{ font-weight: 800; }}
        .task-source, .muted {{ color: var(--muted); font-size: .88rem; margin-top: .2rem; }}
        .status {{
          display: inline-flex;
          align-items: center;
          gap: .45rem;
          border: 1px solid currentColor;
          border-radius: 999px;
          padding: .32rem .62rem;
          font-weight: 800;
          white-space: nowrap;
        }}
        .status::before {{
          content: "";
          width: .48rem;
          height: .48rem;
          border-radius: 999px;
          background: currentColor;
        }}
        .ok {{ color: var(--ok); }}
        .due-soon {{ color: var(--soon); }}
        .due-today {{ color: var(--today); }}
        .overdue {{ color: var(--late); }}
        .actions {{ width: 44%; }}
        .edit-form, .inline-form {{
          display: flex;
          align-items: center;
          gap: .45rem;
          flex-wrap: wrap;
        }}
        .inline-form {{ margin-top: .45rem; }}
        input, select, button {{
          min-height: 2.25rem;
          border-radius: 7px;
          font: inherit;
        }}
        input, select {{
          border: 1px solid var(--line);
          background: #0f1318;
          color: var(--text);
          padding: .45rem .55rem;
        }}
        input[name="name"] {{ width: min(100%, 18rem); }}
        input[type="number"] {{ width: 5rem; }}
        button {{
          border: 1px solid var(--line);
          background: var(--surface-2);
          color: var(--text);
          padding: .45rem .75rem;
          font-weight: 800;
          cursor: pointer;
        }}
        button:hover {{ border-color: var(--accent); }}
        button.primary {{
          border-color: transparent;
          background: var(--accent);
          color: #07111b;
        }}
        .calendar-label {{ color: var(--muted); font-weight: 700; }}
        .empty {{ color: var(--muted); text-align: center; padding: 2rem; }}
        @media (max-width: 760px) {{
          main {{ width: min(100% - 18px, 1180px); padding: 9px 0; }}
          .panel-head {{ align-items: flex-start; flex-direction: column; padding: 18px; }}
          nav {{ width: 100%; }}
          a {{ flex: 1; text-align: center; }}
          th, td {{ padding: .75rem; }}
          .actions {{ width: auto; }}
        }}
      </style>
    </head>
    <body>
      <main>{body}</main>
    </body>
    </html>
    """


def main() -> None:
    import argparse
    from .config import load_config

    parser = argparse.ArgumentParser(description="Run the hygiene tracker web UI")
    parser.add_argument("--config", help="Path to config.toml")
    args = parser.parse_args()
    config = load_config(args.config)
    store = Store(config.database_path)
    store.seed_defaults()
    server = make_server(store, config.web_host, config.web_port)
    print(f"Serving on http://{config.web_host}:{config.web_port}")
    server.serve_forever()
