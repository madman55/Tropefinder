"""Hardcover GraphQL API client.

Rules (Hardcover API docs, checked 2026-11-26):
  * token in an `authorization` header; never in browser code,
  * free tier: 60 requests/minute, 5,000/day; each top-level field counts as one,
  * search queries time out after 2 seconds,
  * aggregate book data is allowed if identified as Hardcover data; user-owned
    data (libraries, reviews, lists) is not used; images are not used.

We stay at one request every 1.1 seconds and cache every response.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

ENDPOINT = "https://api.hardcover.app/v1/graphql"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "hardcover"

SEARCH_QUERY = """
query Search($q: String!, $per: Int!) {
  search(query: $q, query_type: "Book", per_page: $per, page: 1) { results }
}
"""


class Hardcover:
    def __init__(self, token: str | None = None, cache_dir: Path = DEFAULT_CACHE,
                 min_interval: float = 1.1, use_cache: bool = True, session=None):
        token = (token or os.environ.get("HARDCOVER_TOKEN") or "").strip()
        if not token:
            raise ValueError("HARDCOVER_TOKEN is not set")
        self.token = token
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.use_cache = use_cache
        self.session = session or requests.Session()
        self.session.headers.update({"Content-Type": "application/json",
                                     "User-Agent": "TropeFinder/0.1 (personal book discovery project)"})
        self._last = 0.0
        self._auth_flipped = False
        self.requests_made = 0

    def _auth_header(self) -> str:
        # Hardcover's settings page may show the token with or without "Bearer ".
        # Use it as given; if that is rejected, try the other form once.
        has_bearer = self.token.lower().startswith("bearer ")
        if self._auth_flipped:
            return self.token[7:] if has_bearer else f"Bearer {self.token}"
        return self.token

    def query(self, query: str, variables: dict) -> dict:
        key = hashlib.sha1(json.dumps([query, variables], sort_keys=True).encode()).hexdigest()
        cache_file = self.cache_dir / f"{key}.json"
        if self.use_cache and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))["body"]

        for attempt in range(5):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests_made += 1
            r = self.session.post(ENDPOINT, json={"query": query, "variables": variables},
                                  headers={"authorization": self._auth_header()}, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 10)))
                continue
            if r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            text = r.text.lower()
            auth_problem = (r.status_code in (400, 401, 403) and "authoriz" in text) or (
                r.ok and any("jwt" in str(e).lower() or "unauthor" in str(e).lower()
                             for e in (r.json().get("errors") or [])))
            if auth_problem and not self._auth_flipped:
                self._auth_flipped = True
                continue
            if not r.ok:
                raise RuntimeError(f"Hardcover HTTP {r.status_code}: {r.text[:300]}")
            body = r.json()
            if body.get("errors"):
                raise RuntimeError(f"Hardcover error: {json.dumps(body['errors'])[:300]}")
            break
        else:
            raise RuntimeError("Hardcover kept failing")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"query": query, "variables": variables, "body": body},
                                         ensure_ascii=False), encoding="utf-8")
        return body

    def search_books(self, text: str, per_page: int = 5) -> list[dict]:
        """Return the search hits' documents (each a dict of book fields)."""
        body = self.query(SEARCH_QUERY, {"q": text, "per": per_page})
        results = (body.get("data") or {}).get("search", {}).get("results")
        if isinstance(results, str):
            results = json.loads(results)
        hits = (results or {}).get("hits") or []
        return [h.get("document", h) for h in hits if isinstance(h, dict)]


def as_list(value) -> list[str]:
    """Hardcover fields can be lists of strings or of {tag: ...} objects; flatten to strings."""
    out = []
    for v in value or []:
        if isinstance(v, str):
            out.append(v.strip())
        elif isinstance(v, dict):
            name = v.get("tag") or v.get("name") or v.get("value")
            if name:
                out.append(str(name).strip())
    return [v for v in out if v]
