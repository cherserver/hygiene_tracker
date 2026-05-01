# Contributing

Thanks for taking a look at Hygiene Tracker.

## Local Setup

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m unittest discover -s tests
```

On Linux/macOS, activate the environment with:

```bash
. .venv/bin/activate
```

## Before Opening a Pull Request

- Keep changes focused on one behavior or maintenance task.
- Update `README.md` when setup, commands, or user-facing behavior changes.
- Add or update tests for scheduling, storage, and app behavior changes.
- Run `python -m unittest discover -s tests`.

## Configuration and Secrets

Do not commit local `config.toml`, SQLite databases, Google OAuth credentials,
or generated preview files. Use `config.example.toml` as the public template.
