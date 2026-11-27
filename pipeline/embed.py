"""Give every book and trope a meaning vector (embedding) for meaning search.

Text is sent to our own "embed" Edge Function (Supabase's built-in gte-small model), and
the 384 numbers that come back are stored in work_embeddings / trope_embeddings. A hash of
the text is stored too, so books whose text hasn't changed are skipped on later runs.

    python -m pipeline.embed --dry-run     # show what would be embedded, call nothing
    python -m pipeline.embed               # embed new and changed books and tropes
    python -m pipeline.embed --force       # re-embed everything

What goes into a book's text: title, description, reader tags and Open Library subjects,
and the tropes we have evidence for. All embeddings must come from the same model.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

import requests

from pipeline.classify.evidence import strip_html
from pipeline.classify.run import load_books
from pipeline.db import Database, SupabaseREST, _load_dotenv

MODEL = "gte-small"
BATCH = 8           # the function accepts at most 8 texts per call
MAX_CHARS = 1800    # gte-small reads roughly the first 512 word-pieces; keep the important parts first


def book_text(book, present_tropes: list[str]) -> str:
    parts = [book.title + "."]
    if book.description:
        parts.append(" ".join(strip_html(book.description).split())[:1100])
    tags = [t for kind, t in book.tags if kind in ("genre", "tag", "mood")][:25]
    if present_tropes:
        parts.append("Tropes: " + ", ".join(present_tropes) + ".")
    if tags:
        parts.append("Tags: " + ", ".join(tags) + ".")
    if book.subjects:
        parts.append("Subjects: " + ", ".join(book.subjects[:15]) + ".")
    return " ".join(parts)[:MAX_CHARS]


def trope_text(trope: dict, aliases: list[str]) -> str:
    text = f"{trope['name']}. {trope.get('definition') or ''}"
    if aliases:
        text += " Also called: " + ", ".join(aliases) + "."
    return text[:MAX_CHARS]


def text_hash(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{text}".encode()).hexdigest()[:16]


class Embedder:
    def __init__(self, url: str, key: str, session=None):
        self.url = url.rstrip("/") + "/functions/v1/embed"
        self.session = session or requests.Session()
        self.session.headers.update({"apikey": key, "Content-Type": "application/json"})
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        for attempt in range(4):
            r = self.session.post(self.url, json={"input": texts}, timeout=120)
            self.calls += 1
            if r.status_code < 500 and r.status_code != 429:
                break
            time.sleep(5 * (attempt + 1))
        if r.status_code >= 400:
            raise RuntimeError(f"embed function answered HTTP {r.status_code}: {r.text[:300]}")
        body = r.json()
        vecs = body.get("embeddings") or []
        if body.get("model") != MODEL or len(vecs) != len(texts) or any(len(v) != 384 for v in vecs):
            raise RuntimeError(f"unexpected answer from embed function: {str(body)[:300]}")
        return vecs


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def plan(db: Database, force: bool = False) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """(id, text, hash) for books and tropes that need embedding."""
    tropes = [t for t in db.select("tropes", "id,slug,name,definition,is_active") if t.get("is_active")]
    name_of = {t["id"]: t["name"] for t in tropes}
    present: dict[str, list[str]] = {}
    for b in db.select("book_tropes", "work_id,trope_id,status"):
        if b["status"] == "present" and b["trope_id"] in name_of:
            present.setdefault(b["work_id"], []).append(name_of[b["trope_id"]])
    aliases: dict[str, list[str]] = {}
    for a in db.select("trope_aliases", "trope_id,alias"):
        aliases.setdefault(a["trope_id"], []).append(a["alias"])

    have_w = {e["work_id"]: e["text_hash"] for e in db.select("work_embeddings", "work_id,text_hash")}
    have_t = {e["trope_id"]: e["text_hash"] for e in db.select("trope_embeddings", "trope_id,text_hash")}

    books = []
    for b in load_books(db):
        text = book_text(b, sorted(present.get(b.work_id, [])))
        h = text_hash(text)
        if force or have_w.get(b.work_id) != h:
            books.append((b.work_id, text, h))
    trope_jobs = []
    for t in tropes:
        text = trope_text(t, sorted(aliases.get(t["id"], [])))
        h = text_hash(text)
        if force or have_t.get(t["id"]) != h:
            trope_jobs.append((t["id"], text, h))
    return books, trope_jobs


def store(db: Database, table: str, key: str, jobs, vectors, existing: set[str]) -> None:
    for (id_, _text, h), v in zip(jobs, vectors):
        row = {"embedding": vector_literal(v), "text_hash": h, "model": MODEL}
        if id_ in existing:
            db.update(table, {key: id_}, row)
        else:
            db.insert(table, [{key: id_, **row}])
            existing.add(id_)


def run(db: Database, embedder: Embedder | None, force: bool = False, limit: int | None = None) -> str:
    books, tropes = plan(db, force)
    if limit:
        books = books[:limit]
    lines = [f"Books to embed: {len(books)}. Tropes to embed: {len(tropes)}."]
    if embedder is None:
        lines += [f"  e.g. {t[:160]}…" for _, t, _ in books[:3]]
        return "\n".join(lines)
    for table, key, jobs in (("trope_embeddings", "trope_id", tropes), ("work_embeddings", "work_id", books)):
        existing = {e[key] for e in db.select(table, key)}
        for i in range(0, len(jobs), BATCH):
            chunk = jobs[i:i + BATCH]
            store(db, table, key, chunk, embedder.embed([t for _, t, _ in chunk]), existing)
            if (i // BATCH) % 10 == 0:
                print(f"  {table}: {min(i + BATCH, len(jobs))}/{len(jobs)}", flush=True)
    lines.append(f"Done. Calls to the embed function: {embedder.calls}.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Embed books and tropes for meaning search.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int)
    a = p.parse_args(argv)
    _load_dotenv()
    db = SupabaseREST.from_env()
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ["SUPABASE_SECRET_KEY"]
    embedder = None if a.dry_run else Embedder(os.environ["SUPABASE_URL"], key)
    text = run(db, embedder, a.force, a.limit)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("## Embeddings\n\n" + text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
