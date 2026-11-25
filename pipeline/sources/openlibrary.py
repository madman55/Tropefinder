"""Open Library API client.

Follows Open Library's published guidelines (checked 2026-11-25):
  * identify the app with a User-Agent (name + contact),
  * stay at or under 1 request per second (3/s is allowed when identified;
    we stay at 1/s to be polite),
  * cache responses,
  * use the monthly data dumps, not the API, for bulk work.

Every response is saved under data/cache/openlibrary/ so runs can be inspected
and re-run without hitting the API again.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE = "https://openlibrary.org"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache" / "openlibrary"
SEARCH_FIELDS = "key,title,subtitle,author_name,author_key,first_publish_year,edition_count"


class OpenLibrary:
    def __init__(self, user_agent: str | None = None, cache_dir: Path = DEFAULT_CACHE,
                 min_interval: float = 1.0, use_cache: bool = True, session=None):
        self.user_agent = user_agent or os.environ.get("OPENLIBRARY_USER_AGENT") or "TropeFinder/0.1 (personal book discovery project)"
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.use_cache = use_cache
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = self.user_agent
        self._last = 0.0
        self.requests_made = 0

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{BASE}{path}" + (f"?{urlencode(params)}" if params else "")
        cache_file = self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")
        if self.use_cache and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))["body"]

        for attempt in range(4):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.requests_made += 1
            r = self.session.get(url, timeout=30)
            if r.status_code == 404:
                body: dict = {}
                break
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            body = r.json()
            break
        else:
            raise RuntimeError(f"Open Library kept failing for {url}")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({"url": url, "body": body}, ensure_ascii=False), encoding="utf-8")
        return body

    def search(self, title: str, author: str | None = None, limit: int = 10) -> list[dict]:
        params = {"title": title, "fields": SEARCH_FIELDS, "limit": limit}
        if author:
            params["author"] = author
        return self._get("/search.json", params).get("docs", [])

    def work(self, work_id: str) -> dict:
        return self._get(f"/works/{work_id}.json")

    def editions(self, work_id: str, limit: int = 50) -> list[dict]:
        return self._get(f"/works/{work_id}/editions.json", {"limit": limit}).get("entries", [])


def strip_key(key: str | None) -> str | None:
    """'/works/OL123W' -> 'OL123W'."""
    return key.rsplit("/", 1)[-1] if key else None


def description_text(work: dict) -> str | None:
    d = work.get("description")
    if isinstance(d, dict):
        d = d.get("value")
    return d.strip() if isinstance(d, str) and d.strip() else None
