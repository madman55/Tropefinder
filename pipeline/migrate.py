"""Apply new database migrations to Supabase through its Management API.

Replaces copying SQL files into the SQL Editor by hand. Applied migrations are recorded
in private.applied_migrations (a schema the public API doesn't expose), so each file runs
once. On first use, the migrations Joseph already ran by hand are recorded as applied.

    python -m pipeline.migrate --dry-run    # list what would run
    python -m pipeline.migrate              # apply pending migrations, in order

Needs SUPABASE_ACCESS_TOKEN (a Supabase access token with database write access) and
SUPABASE_URL (to find the project ref). Uses an experimental Supabase endpoint
(POST /v1/projects/{ref}/database/query), so check its docs if it ever stops working.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests

from pipeline.db import _load_dotenv

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
API = "https://api.supabase.com/v1/projects/{ref}/database/query"

# Run by hand in the SQL Editor before this tool existed (25-27 Nov 2026).
APPLIED_BY_HAND = [
    "20261125000000_initial_schema.sql",
    "20261125010000_grant_public_read.sql",
    "20261125020000_work_subjects.sql",
    "20261126000000_work_source_tags.sql",
    "20261127000000_search_books.sql",
]

SETUP = """
create schema if not exists private;
revoke all on schema private from public, anon, authenticated;
create table if not exists private.applied_migrations (
  name       text primary key,
  applied_at timestamptz not null default now(),
  how        text not null default 'migrate.py'
);
"""


class ManagementAPI:
    def __init__(self, token: str, ref: str, session=None):
        if not token or not ref:
            raise ValueError("SUPABASE_ACCESS_TOKEN and SUPABASE_URL (or SUPABASE_PROJECT_REF) are needed")
        self.url = API.format(ref=ref)
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def sql(self, query: str) -> list:
        r = self.session.post(self.url, json={"query": query}, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase answered HTTP {r.status_code}: {r.text[:500]}")
        try:
            body = r.json()
        except ValueError:
            return []
        return body if isinstance(body, list) else []


def project_ref() -> str:
    if os.environ.get("SUPABASE_PROJECT_REF"):
        return os.environ["SUPABASE_PROJECT_REF"]
    m = re.match(r"https://([a-z0-9]{20})\.supabase\.co", os.environ.get("SUPABASE_URL", ""))
    return m.group(1) if m else ""


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def pending(api, files: list[Path]) -> list[Path]:
    api.sql(SETUP)
    done = {row["name"] for row in api.sql("select name from private.applied_migrations")}
    if not done:   # first run: record the ones applied by hand
        values = ", ".join(f"('{n}', 'by hand in the SQL Editor')" for n in APPLIED_BY_HAND)
        api.sql(f"insert into private.applied_migrations (name, how) values {values} on conflict do nothing")
        done = set(APPLIED_BY_HAND)
    return [f for f in files if f.name not in done]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply pending Supabase migrations.")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    _load_dotenv()
    api = ManagementAPI(os.environ.get("SUPABASE_ACCESS_TOKEN", ""), project_ref())
    todo = pending(api, migration_files())
    if not todo:
        print("Database is up to date: no pending migrations.")
        return 0
    print(("Would apply" if a.dry_run else "Applying") + f" {len(todo)} migration(s):")
    for f in todo:
        print(f"  - {f.name}")
        if a.dry_run:
            continue
        api.sql(f.read_text(encoding="utf-8"))
        api.sql(f"insert into private.applied_migrations (name) values ('{f.name}')")
        print("    applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
