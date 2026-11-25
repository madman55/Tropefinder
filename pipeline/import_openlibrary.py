"""Import the seed book list from Open Library.

For each book in data/seed_books.csv:
  1. search Open Library by title + author (then title only if nothing found),
  2. accept a result only if the title is a close match AND the author matches;
     anything uncertain is reported as "not found", never guessed,
  3. fetch the work (description, subjects) and up to 50 editions,
  4. write work, authors, editions and source IDs, skipping anything already stored.

Running it again adds nothing new: works, authors and editions are recognised
by their Open Library IDs and by ISBN.

Usage, from the repository root:
    python -m pipeline.import_openlibrary --dry-run   # fetch and match, write nothing
    python -m pipeline.import_openlibrary             # fetch, match and write
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pipeline.db import Database, SupabaseREST
from pipeline.normalise import (author_matches, first_isbn, isbn10_to_13, map_format,
                                normalise_text, title_similarity, year_from)
from pipeline.sources.openlibrary import OpenLibrary, description_text, strip_key

SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_books.csv"
MATCH_THRESHOLD = 0.88
MAX_EDITIONS = 50


@dataclass
class BookResult:
    seed_id: str
    seed_title: str
    seed_author: str
    outcome: str = "not found"  # imported | unchanged | updated | not found | needs review | error
    ol_work: str | None = None
    matched_title: str | None = None
    matched_authors: str | None = None
    score: float = 0.0
    has_description: bool = False
    subjects: int = 0
    editions_seen: int = 0
    editions_added: int = 0
    isbns: int = 0
    formats: dict = field(default_factory=dict)
    note: str = ""


def read_seed(path: Path = SEED_PATH) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def best_match(docs: list[dict], title: str, author: str) -> tuple[dict | None, float]:
    best, best_key = None, (0.0, 0)
    for d in docs:
        if not author_matches(author, d.get("author_name")):
            continue
        full_title = d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else "")
        score = max(title_similarity(title, d.get("title", "")), title_similarity(title, full_title))
        key = (round(score, 3), d.get("edition_count") or 0)
        if score >= MATCH_THRESHOLD and key > best_key:
            best, best_key = d, key
    return best, best_key[0]


class Importer:
    def __init__(self, db: Database | None, ol: OpenLibrary, dry_run: bool):
        self.db, self.ol, self.dry_run = db, ol, dry_run
        self.source_id = None
        self.ext: dict[tuple[str, str], str] = {}  # (entity_type, external_id) -> our id
        self.isbn13_owner: dict[str, str] = {}      # isbn13 -> work_id
        self.isbn10_owner: dict[str, str] = {}
        self.title_index: dict[str, set[str]] = {}   # normalised title -> work ids
        self.work_authors: set[tuple[str, str]] = set()
        self.author_names: dict[str, str] = {}       # author id -> name
        self.works_cache: dict[str, dict] = {}
        if db is not None:
            self._load_state()

    def _load_state(self) -> None:
        sources = {s["slug"]: s["id"] for s in self.db.select("data_sources", "id,slug")}
        self.source_id = sources["openlibrary"]
        for e in self.db.select("external_ids", "entity_type,entity_id,source_id,external_id"):
            if e["source_id"] == self.source_id:
                self.ext[(e["entity_type"], e["external_id"])] = e["entity_id"]
        for ed in self.db.select("editions", "work_id,isbn_13,isbn_10"):
            if ed.get("isbn_13"):
                self.isbn13_owner[ed["isbn_13"]] = ed["work_id"]
            if ed.get("isbn_10"):
                self.isbn10_owner[ed["isbn_10"]] = ed["work_id"]
        for w in self.db.select("works", "id,title,subtitle,normalised_title,first_published_year,description,description_source_id,subjects,subjects_source_id"):
            self.works_cache[w["id"]] = w
            self.title_index.setdefault(w["normalised_title"], set()).add(w["id"])
        for wa in self.db.select("work_authors", "work_id,author_id"):
            self.work_authors.add((wa["work_id"], wa["author_id"]))
        for a in self.db.select("authors", "id,name"):
            self.author_names[a["id"]] = a["name"]

    def _same_book_stored(self, normalised_title: str, doc_authors: list[str]) -> bool:
        """A different record with the same title and an overlapping author is already stored."""
        for work_id in self.title_index.get(normalised_title, set()):
            names = [self.author_names.get(a, "") for (w, a) in self.work_authors if w == work_id]
            if any(author_matches(n, doc_authors) for n in names):
                return True
        return False

    # ------------------------------------------------------------------ writes
    def _link(self, entity_type: str, entity_id: str, external_id: str, url: str) -> None:
        self.db.insert("external_ids", [{
            "entity_type": entity_type, "entity_id": entity_id, "source_id": self.source_id,
            "external_id": external_id, "raw_url": url,
            "last_fetched_at": datetime.now(timezone.utc).isoformat(),
        }])
        self.ext[(entity_type, external_id)] = entity_id

    def _upsert_work(self, res: BookResult, doc: dict, work: dict) -> str | None:
        wid_ol = res.ol_work
        row = {
            "title": work.get("title") or doc.get("title"),
            "subtitle": work.get("subtitle") or doc.get("subtitle"),
            "normalised_title": normalise_text(work.get("title") or doc.get("title")),
            "first_published_year": doc.get("first_publish_year") if 1000 <= (doc.get("first_publish_year") or 0) <= 2100 else None,
            "description": description_text(work),
            "description_source_id": self.source_id if description_text(work) else None,
            "subjects": [str(s) for s in (work.get("subjects") or [])][:100],
            "subjects_source_id": self.source_id if work.get("subjects") else None,
        }
        existing_id = self.ext.get(("work", wid_ol))
        if existing_id:
            current = self.works_cache.get(existing_id, {})
            changes = {k: v for k, v in row.items() if current.get(k) != v}
            if changes:
                self.db.update("works", {"id": existing_id}, changes)
                self.works_cache[existing_id] = {**current, **changes}
                res.outcome = "updated"
            else:
                res.outcome = "unchanged"
            return existing_id

        # Not known by its Open Library ID: is the same title already stored from elsewhere?
        if self._same_book_stored(row["normalised_title"], doc.get("author_name") or []):
            res.outcome = "needs review"
            res.note = "same title and author already stored under another record; not merged automatically"
            return None

        new = self.db.insert("works", [row])[0]
        self._link("work", new["id"], wid_ol, f"https://openlibrary.org/works/{wid_ol}")
        self.title_index.setdefault(row["normalised_title"], set()).add(new["id"])
        self.works_cache[new["id"]] = {"id": new["id"], **row}
        res.outcome = "imported"
        return new["id"]

    def _upsert_authors(self, work_id: str, doc: dict) -> None:
        names = doc.get("author_name") or []
        keys = doc.get("author_key") or []
        for position, (key, name) in enumerate(zip(keys, names), start=1):
            author_id = self.ext.get(("author", key))
            if not author_id:
                author_id = self.db.insert("authors", [{
                    "name": name, "normalised_name": normalise_text(name),
                }])[0]["id"]
                self._link("author", author_id, key, f"https://openlibrary.org/authors/{key}")
                self.author_names[author_id] = name
            if (work_id, author_id) not in self.work_authors:
                self.db.insert("work_authors", [{"work_id": work_id, "author_id": author_id,
                                                 "role": "author", "position": position}])
                self.work_authors.add((work_id, author_id))

    def _add_editions(self, res: BookResult, work_id: str | None, editions: list[dict]) -> None:
        for ed in editions:
            ed_id = strip_key(ed.get("key"))
            isbn10 = first_isbn(ed.get("isbn_10"), "10")
            isbn13 = first_isbn(ed.get("isbn_13"), "13") or (isbn10_to_13(isbn10) if isbn10 else None)
            fmt = map_format(ed.get("physical_format"))
            res.formats[fmt] = res.formats.get(fmt, 0) + 1
            res.isbns += 1 if (isbn13 or isbn10) else 0
            if self.dry_run or work_id is None or not ed_id:
                continue
            if ("edition", ed_id) in self.ext:
                continue
            # The same ISBN already stored: don't create a second edition row for it.
            owner = (self.isbn13_owner.get(isbn13) if isbn13 else None) or (self.isbn10_owner.get(isbn10) if isbn10 else None)
            if owner:
                if owner != work_id:
                    res.note = (res.note + "; " if res.note else "") + f"ISBN {isbn13 or isbn10} already belongs to another work"
                continue
            langs = [strip_key(l.get("key")) for l in ed.get("languages") or [] if isinstance(l, dict)]
            covers = [c for c in ed.get("covers") or [] if isinstance(c, int) and c > 0]
            pages = ed.get("number_of_pages")
            row = {
                "work_id": work_id,
                "title": ed.get("title"),
                "isbn_13": isbn13,
                "isbn_10": isbn10,
                "format": fmt,
                "publisher": (ed.get("publishers") or [None])[0],
                "published_text": ed.get("publish_date"),
                "published_year": year_from(ed.get("publish_date")),
                "language": langs[0] if langs else None,
                "page_count": pages if isinstance(pages, int) and pages > 0 else None,
                "cover_url": f"https://covers.openlibrary.org/b/id/{covers[0]}-M.jpg" if covers else None,
                "source_id": self.source_id,
            }
            new = self.db.insert("editions", [row])[0]
            self._link("edition", new["id"], ed_id, f"https://openlibrary.org/books/{ed_id}")
            if isbn13:
                self.isbn13_owner[isbn13] = work_id
            if isbn10:
                self.isbn10_owner[isbn10] = work_id
            res.editions_added += 1

    # ------------------------------------------------------------------ per book
    def import_book(self, seed: dict) -> BookResult:
        res = BookResult(seed["seed_id"], seed["title"], seed["author"])
        try:
            docs = self.ol.search(seed["title"], seed["author"])
            doc, score = best_match(docs, seed["title"], seed["author"])
            if doc is None:
                docs = self.ol.search(seed["title"])
                doc, score = best_match(docs, seed["title"], seed["author"])
            if doc is None:
                res.note = f"{len(docs)} search results, none matched title + author closely enough"
                return res

            res.ol_work = strip_key(doc["key"])
            res.matched_title = doc.get("title")
            res.matched_authors = ", ".join(doc.get("author_name") or [])
            res.score = score
            work = self.ol.work(res.ol_work)
            editions = self.ol.editions(res.ol_work, MAX_EDITIONS)
            res.has_description = bool(description_text(work))
            res.subjects = len(work.get("subjects") or [])
            res.editions_seen = len(editions)

            if self.dry_run:
                res.outcome = "would import" if ("work", res.ol_work) not in self.ext else "already stored"
                self._add_editions(res, None, editions)
                return res

            work_id = self._upsert_work(res, doc, work)
            if work_id:
                self._upsert_authors(work_id, doc)
            self._add_editions(res, work_id, editions)
            return res
        except Exception as exc:  # one bad book must not stop the run
            res.outcome = "error"
            res.note = f"{type(exc).__name__}: {exc}"[:300]
            return res


def report_markdown(results: list[BookResult], dry_run: bool, requests_made: int) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    lines = [
        f"## Open Library import {'(DRY RUN - nothing written)' if dry_run else ''}".strip(),
        "",
        " · ".join(f"**{k}**: {v}" for k, v in sorted(counts.items())) + f" · API requests: {requests_made}",
        "",
        "| # | Seed title | Result | Matched as | Score | Desc | Subjects | Editions (added) | Formats | Note |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        matched = f"{r.matched_title} — {r.matched_authors} ({r.ol_work})" if r.ol_work else ""
        fmts = ", ".join(f"{k} {v}" for k, v in sorted(r.formats.items()))
        lines.append(
            f"| {r.seed_id} | {r.seed_title} | {r.outcome} | {matched} | {r.score:.2f} | "
            f"{'yes' if r.has_description else 'no'} | {r.subjects} | {r.editions_seen} ({r.editions_added}) | {fmts} | {r.note} |"
        )
    return "\n".join(lines)


def run(db: Database | None, ol: OpenLibrary, seeds: list[dict], dry_run: bool) -> list[BookResult]:
    importer = Importer(db, ol, dry_run)
    run_id = None
    if db is not None and not dry_run:
        run_id = db.insert("import_runs", [{"source_id": importer.source_id, "job_name": "import_openlibrary_seed"}])[0]["id"]

    results = [importer.import_book(s) for s in seeds]

    if run_id:
        counts: dict[str, int] = {}
        for r in results:
            counts[r.outcome] = counts.get(r.outcome, 0) + 1
        counts["editions_added"] = sum(r.editions_added for r in results)
        counts["api_requests"] = ol.requests_made
        errors = [f"{r.seed_id} {r.seed_title}: {r.note}" for r in results if r.outcome == "error"]
        db.update("import_runs", {"id": run_id}, {
            "status": "partial" if errors else "succeeded",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "counts": counts,
            "error_log": "\n".join(errors) or None,
        })
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import seed books from Open Library.")
    parser.add_argument("--dry-run", action="store_true", help="fetch and match only; write nothing")
    parser.add_argument("--limit", type=int, help="only the first N seed books")
    args = parser.parse_args(argv)

    seeds = read_seed()[: args.limit] if args.limit else read_seed()
    ol = OpenLibrary()
    # A dry run still reads the database so it can say what's already stored.
    db = SupabaseREST.from_env()
    results = run(db, ol, seeds, args.dry_run)
    report = report_markdown(results, args.dry_run, ol.requests_made)
    print(report)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/openlibrary_import.md").write_text(report + "\n", encoding="utf-8")
    return 1 if any(r.outcome == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
