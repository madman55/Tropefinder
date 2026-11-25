"""Small database client for the Python jobs.

Talks to Supabase over its REST API (PostgREST) using the secret key, which
bypasses row-level security. Only ever run server-side: on your machine with a
.env file, or in GitHub Actions with repository secrets.

Every job talks to the database through the four methods below, so a different
backend (or the in-memory FakeDB used in tests) can be swapped in.
"""

from __future__ import annotations

import os
from typing import Protocol

import requests

PAGE_SIZE = 1000  # Supabase returns at most 1000 rows per request by default


class Database(Protocol):
    def select(self, table: str, columns: str = "*") -> list[dict]: ...
    def insert(self, table: str, rows: list[dict]) -> list[dict]: ...
    def update(self, table: str, match: dict, values: dict) -> None: ...
    def delete(self, table: str, match: dict) -> None: ...


class SupabaseREST:
    def __init__(self, url: str, secret_key: str, timeout: float = 30):
        if not url or not secret_key:
            raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY must both be set")
        self.base = url.rstrip("/") + "/rest/v1"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"apikey": secret_key, "Content-Type": "application/json"})
        # Legacy service_role keys are JWTs ("eyJ...") and also need a Bearer header.
        # New-style sb_secret_ keys go in the apikey header only.
        if secret_key.startswith("eyJ"):
            self.session.headers["Authorization"] = f"Bearer {secret_key}"

    @classmethod
    def from_env(cls) -> "SupabaseREST":
        _load_dotenv()
        return cls(os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SECRET_KEY", ""))

    def _check(self, response: requests.Response, action: str) -> None:
        if response.status_code >= 400:
            # Never print headers: they contain the key.
            raise RuntimeError(f"{action} failed: HTTP {response.status_code}: {response.text[:500]}")

    @staticmethod
    def _filters(match: dict) -> dict:
        if not match:
            raise ValueError("Refusing to update/delete without a filter")
        return {k: f"eq.{v}" for k, v in match.items()}

    def select(self, table: str, columns: str = "*") -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            r = self.session.get(
                f"{self.base}/{table}",
                params={"select": columns, "offset": offset, "limit": PAGE_SIZE, "order": "created_at"},
                timeout=self.timeout,
            )
            self._check(r, f"select {table}")
            page = r.json()
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        r = self.session.post(
            f"{self.base}/{table}", json=rows,
            headers={"Prefer": "return=representation"}, timeout=self.timeout,
        )
        self._check(r, f"insert into {table}")
        return r.json()

    def update(self, table: str, match: dict, values: dict) -> None:
        r = self.session.patch(
            f"{self.base}/{table}", params=self._filters(match), json=values,
            headers={"Prefer": "return=minimal"}, timeout=self.timeout,
        )
        self._check(r, f"update {table}")

    def delete(self, table: str, match: dict) -> None:
        r = self.session.delete(
            f"{self.base}/{table}", params=self._filters(match),
            headers={"Prefer": "return=minimal"}, timeout=self.timeout,
        )
        self._check(r, f"delete from {table}")


def _load_dotenv(path: str = ".env") -> None:
    """Read KEY=value lines from .env into the environment (without overriding)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class FakeDB:
    """In-memory stand-in for tests. Mimics generated ids and simple equality filters."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, str]] = []
        self._next = 0

    def _rows(self, table: str) -> list[dict]:
        return self.tables.setdefault(table, [])

    def select(self, table: str, columns: str = "*") -> list[dict]:
        return [dict(r) for r in self._rows(table)]

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        out = []
        for row in rows:
            self._next += 1
            new = {"id": f"{table}-{self._next}", **row}
            self._rows(table).append(new)
            out.append(dict(new))
        self.calls.append(("insert", table))
        return out

    def update(self, table: str, match: dict, values: dict) -> None:
        for r in self._rows(table):
            if all(r.get(k) == v for k, v in match.items()):
                r.update(values)
        self.calls.append(("update", table))

    def delete(self, table: str, match: dict) -> None:
        self.tables[table] = [r for r in self._rows(table) if not all(r.get(k) == v for k, v in match.items())]
        self.calls.append(("delete", table))

