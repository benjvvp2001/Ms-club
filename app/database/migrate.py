"""Apply clubs migrations explicitly, independently from API startup."""

from pathlib import Path

import psycopg

from app.core.config import get_database_url


def main() -> None:
    directory = Path(__file__).resolve().parents[2] / "db" / "migrations"
    migrations = sorted(directory.glob("*.sql"))
    if not migrations:
        # The service currently reads the existing sportmatch_clubs tables as they are.
        print("No migrations to apply")
        return
    # Every migration is idempotent, so they are all applied in order each time.
    with psycopg.connect(get_database_url(), autocommit=True) as conn:
        for path in migrations:
            conn.execute(path.read_text(encoding="utf-8"))
            print(f"Applied {path.name}")


if __name__ == "__main__":
    main()
