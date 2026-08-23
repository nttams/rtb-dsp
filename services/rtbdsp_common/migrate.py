"""Applies ``migrations/*.sql`` in order against DATABASE_URL. The 5th entry point selected
by ``python.Dockerfile``'s CMD — the one-shot ``migrate`` init service in the compose file.

Tracks applied filenames in a ``schema_migrations`` table so re-running is a no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from rtbdsp_common.settings import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def apply_migrations(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies every ``*.sql`` file in ``migrations_dir`` not yet recorded, in filename order.
    Returns the list of newly-applied filenames.
    """
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute("SELECT filename FROM schema_migrations")
            already = {row[0] for row in cur.fetchall()}
        conn.commit()

        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in already:
                continue
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,)
                )
            conn.commit()
            applied.append(path.name)
    return applied


def main() -> None:
    settings = get_settings()
    applied = apply_migrations(settings.database_url)
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No new migrations to apply.")


if __name__ == "__main__":
    main()
    sys.exit(0)
