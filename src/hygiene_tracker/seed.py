from __future__ import annotations

import argparse
from datetime import date

from .config import load_config
from .storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize and seed the hygiene tracker database")
    parser.add_argument("--config", help="Path to config.toml")
    parser.add_argument("--database", help="Override database path")
    parser.add_argument("--today", help="Seed start date as YYYY-MM-DD")
    args = parser.parse_args()

    config = load_config(args.config)
    database_path = args.database or config.database_path
    today = date.fromisoformat(args.today) if args.today else date.today()

    store = Store(database_path)
    store.seed_defaults(today=today)
    print(f"Database ready: {database_path}")


if __name__ == "__main__":
    main()

