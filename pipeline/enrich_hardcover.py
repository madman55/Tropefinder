"""Enrich the catalogue with Hardcover data, and fill seed-list gaps from it.

For every seed book:
  * find the book on Hardcover (ISBN overlap first; otherwise close title AND
    author match; anything uncertain is skipped, never guessed),
  * if the work is already stored (from Open Library): add Hardcover's genres,
    moods, reader tags, content warnings, series, audiobook/ebook flags, and its
    description only where we have none,
  * if the seed book is missing from the database: create it from Hardcover,
    clearly sourced to Hardcover.

Running it again changes nothing unless Hardcover's data changed.

Usage, from the repository root:
    python -m pipeline.enrich_hardcover --dry-run
    python -m pipeline.enrich_hardcover
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline.db import Database, SupabaseREST
from pipeline.import_openlibrary import read_seed
from pipeline.normalise import (author_matches, clean_isbn, isbn10_to_13, normalise_text,
                                title_similarity)
from pipeline.sources.hardcover import Hardcover, as_list

MATCH_THRESHOLD = 0.88
TAG_FIELDS = (("genres", "genre"), ("moods", "mood"), ("tags", "tag"), ("content_warnings", "content_warning"))
MAX_TAGS_PER_TYPE = 25
MAX_NEW_EDITIONS = 20


@dataclass
class Result:
    seed_id: str
    title: str
    outcome: str = "not found"  # enriched | unchanged | created | would enrich | would create | not found | error
    hc_id: str | None = None
    hc_title: str | None = None
    match: str = ""             # isbn | title+author
    tags: int = 0
    tags_added: int = 0
    tags_removed: int = 0
    description_added: bool = False
    formats: str = ""
    series: str = ""
    note: str = ""


def isbn_set(values) -> set[str]:
    out = set()
    for v in values or []:
        i13, i10 = clean_isbn(str(v))
        if i13:
            out.add(i13)
        if i10:
            out.add(isbn10_to_13(i10))
    return out


def pick_hit(docs: list[dict], title: str, author: str, known_isbns: set[str]) -> tuple[dict | None, str]:
    by_isbn = [d for d in docs if known_isbns & isbn_set(d.get("isbns"))]
    if by_isbn:
        return max(by_isbn, key=lambda d: d.get("users_count") or 0), "isbn"
    good = [d for d in docs
            if author_matches(author, as_list(d.get("author_names")))
            and title_similarity(title, d.get("title") or "") >= MATCH_THRESHOLD]
    if good:
        return max(good, key=lambda d: d.get("users_count") or 0), "title+author"
    return None, ""


class Enricher:
    def __init__(self, db: Database, hc: Hardcover, dry_run: bool):
        self.db, self.hc, self.dry_run = db, hc, dry_run
        sources = {s["slug"]: s["id"] for s in db.select("data_sources", "id,slug")}
        self.src = sources["hardcover"]
        self.works = {w["id"]: w for w in db.select("works", "id,title,normalised_title,description,description_source_id")}
        self.authors = {a["id"]: a["name"] for a in db.select("authors", "id,name")}
        self.work_author_ids: dict[str, list[str]] = {}
        for wa in db.select("work_authors", "work_id,author_id"):
            self.work_author_ids.setdefault(wa["work_id"], []).append(wa["author_id"])
        self.work_isbns: dict[str, set[str]] = {}
        self.isbn_owner: dict[str, str] = {}
        for e in db.select("editions", "work_id,isbn_13,isbn_10"):
            s = self.work_isbns.setdefault(e["work_id"], set())
            if e.get("isbn_13"):
                s.add(e["isbn_13"]); self.isbn_owner[e["isbn_13"]] = e["work_id"]
            if e.get("isbn_10"):
                s.add(isbn10_to_13(e["isbn_10"]))
        self.ext = {}
        for x in db.select("external_ids", "entity_type,entity_id,source_id,external_id"):
            if x["source_id"] == self.src:
                self.ext[(x["entity_type"], x["external_id"])] = x["entity_id"]
        self.tags: dict[str, dict[tuple[str, str], dict]] = {}
        for t in db.select("work_source_tags", "id,work_id,source_id,tag_type,tag,position"):
            if t["source_id"] == self.src:
                self.tags.setdefault(t["work_id"], {})[(t["tag_type"], t["tag"])] = t
        self.formats = {(f["work_id"], f["format"]) for f in db.select("format_availability", "work_id,format,source_id")
                        if f["source_id"] == self.src and f.get("work_id")}
        self.series = {s["normalised_name"]: s["id"] for s in db.select("series", "id,normalised_name")}
        self.work_series = {(ws["work_id"], ws["series_id"]): ws for ws in db.select("work_series", "work_id,series_id,position")}

    def find_work(self, seed: dict) -> str | None:
        want = normalise_text(seed["title"])
        for wid, w in self.works.items():
            if title_similarity(seed["title"], w["title"]) >= MATCH_THRESHOLD or w["normalised_title"] == want:
                names = [self.authors.get(a, "") for a in self.work_author_ids.get(wid, [])]
                if any(author_matches(n, [seed["author"]]) or author_matches(seed["author"], [n]) for n in names):
                    return wid
        return None

    # --------------------------------------------------------------- writes
    def _sync_tags(self, res: Result, work_id: str, doc: dict) -> None:
        want: dict[tuple[str, str], int] = {}
        for field, tag_type in TAG_FIELDS:
            for pos, tag in enumerate(as_list(doc.get(field))[:MAX_TAGS_PER_TYPE], start=1):
                want.setdefault((tag_type, tag), pos)
        res.tags = len(want)
        have = self.tags.get(work_id, {})
        add = [k for k in want if k not in have]
        remove = [k for k in have if k not in want]
        res.tags_added, res.tags_removed = len(add), len(remove)
        if self.dry_run:
            return
        for k in remove:
            self.db.delete("work_source_tags", {"id": have[k]["id"]})
        self.db.insert("work_source_tags", [
            {"work_id": work_id, "source_id": self.src, "tag_type": t, "tag": tag, "position": want[(t, tag)]}
            for (t, tag) in add])

    def _sync_formats(self, res: Result, work_id: str, doc: dict) -> bool:
        changed = False
        flags = [("audiobook", doc.get("has_audiobook")), ("ebook", doc.get("has_ebook"))]
        res.formats = ", ".join(f for f, v in flags if v) or "none stated"
        for fmt, value in flags:
            # Only a "yes" is recorded. "No" from one source is not proof a format doesn't exist.
            if value and (work_id, fmt) not in self.formats:
                changed = True
                if not self.dry_run:
                    self.db.insert("format_availability", [{"work_id": work_id, "format": fmt,
                                                            "source_id": self.src, "confirmed": True}])
                    self.formats.add((work_id, fmt))
        return changed

    def _sync_series(self, res: Result, work_id: str, doc: dict) -> bool:
        changed = False
        featured = doc.get("featured_series") if isinstance(doc.get("featured_series"), dict) else {}
        featured_name = ((featured.get("series") or {}).get("name") if isinstance(featured.get("series"), dict) else None)
        position = featured.get("position") if featured else doc.get("featured_series_position")
        names = as_list(doc.get("series_names"))
        if featured_name and featured_name not in names:
            names.insert(0, featured_name)
        res.series = ", ".join(names)
        for i, name in enumerate(names):
            key = normalise_text(name)
            if not key:
                continue
            pos = position if (i == 0 and isinstance(position, (int, float))) else None
            sid = self.series.get(key)
            if sid and (work_id, sid) in self.work_series:
                continue
            changed = True
            if self.dry_run:
                continue
            if not sid:
                sid = self.db.insert("series", [{"name": name, "normalised_name": key}])[0]["id"]
                self.series[key] = sid
            self.db.insert("work_series", [{"work_id": work_id, "series_id": sid, "position": pos}])
            self.work_series[(work_id, sid)] = {"position": pos}
        return changed

    def _link(self, work_id: str, doc: dict) -> bool:
        hc_id = str(doc.get("id"))
        if ("work", hc_id) in self.ext:
            return False
        if not self.dry_run:
            slug = doc.get("slug")
            self.db.insert("external_ids", [{
                "entity_type": "work", "entity_id": work_id, "source_id": self.src, "external_id": hc_id,
                "raw_url": f"https://hardcover.app/books/{slug}" if slug else None,
                "last_fetched_at": datetime.now(timezone.utc).isoformat()}])
            self.ext[("work", hc_id)] = work_id
        return True

    def _create_work(self, res: Result, doc: dict) -> str | None:
        title = doc.get("title")
        year = doc.get("release_year")
        description = (doc.get("description") or "").strip() or None
        row = {"title": title, "subtitle": doc.get("subtitle"), "normalised_title": normalise_text(title),
               "first_published_year": year if isinstance(year, int) and 1000 <= year <= 2100 else None,
               "description": description, "description_source_id": self.src if description else None}
        if self.dry_run:
            return None
        wid = self.db.insert("works", [row])[0]["id"]
        self.works[wid] = {"id": wid, **row}
        for pos, name in enumerate(as_list(doc.get("author_names")), start=1):
            aid = next((a for a, n in self.authors.items() if normalise_text(n) == normalise_text(name)), None)
            if not aid:
                aid = self.db.insert("authors", [{"name": name, "normalised_name": normalise_text(name)}])[0]["id"]
                self.authors[aid] = name
            self.db.insert("work_authors", [{"work_id": wid, "author_id": aid, "role": "author", "position": pos}])
        added = 0
        for isbn in sorted(isbn_set(doc.get("isbns"))):
            if added >= MAX_NEW_EDITIONS or isbn in self.isbn_owner:
                continue
            self.db.insert("editions", [{"work_id": wid, "isbn_13": isbn, "format": "unknown", "source_id": self.src}])
            self.isbn_owner[isbn] = wid
            added += 1
        res.note = f"created from Hardcover with {added} ISBN editions"
        return wid

    # --------------------------------------------------------------- per book
    def enrich(self, seed: dict) -> Result:
        res = Result(seed["seed_id"], seed["title"])
        try:
            work_id = self.find_work(seed)
            known = self.work_isbns.get(work_id, set()) if work_id else set()
            docs = self.hc.search_books(f"{seed['title']} {seed['author']}")
            doc, how = pick_hit(docs, seed["title"], seed["author"], known)
            if doc is None:
                docs = self.hc.search_books(seed["title"])
                doc, how = pick_hit(docs, seed["title"], seed["author"], known)
            if doc is None:
                res.note = f"{len(docs)} results; none matched by ISBN or title + author"
                return res
            res.hc_id, res.hc_title, res.match = str(doc.get("id")), doc.get("title"), how

            created = False
            if work_id is None:
                existing = self.ext.get(("work", res.hc_id))
                if existing:
                    work_id = existing
                else:
                    work_id = self._create_work(res, doc)
                    created = True
                    if self.dry_run:
                        res.outcome = "would create"
                        self._sync_tags(res, "new", doc)
                        self._sync_formats(res, "new", doc)
                        self._sync_series(res, "new", doc)
                        return res

            changed = self._link(work_id, doc)
            w = self.works.get(work_id, {})
            hc_desc = (doc.get("description") or "").strip()
            if not created and hc_desc and not w.get("description"):
                res.description_added = changed = True
                if not self.dry_run:
                    self.db.update("works", {"id": work_id}, {"description": hc_desc, "description_source_id": self.src})
                    w["description"] = hc_desc
            self._sync_tags(res, work_id, doc)
            changed |= bool(res.tags_added or res.tags_removed)
            changed |= self._sync_formats(res, work_id, doc)
            changed |= self._sync_series(res, work_id, doc)
            if created:
                res.outcome = "created"
            elif self.dry_run:
                res.outcome = "would enrich" if changed else "unchanged"
            else:
                res.outcome = "enriched" if changed else "unchanged"
            return res
        except Exception as exc:
            res.outcome = "error"
            res.note = f"{type(exc).__name__}: {exc}"[:300]
            return res


def report(results: list[Result], dry_run: bool, requests_made: int) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.outcome] = counts.get(r.outcome, 0) + 1
    lines = [f"## Hardcover enrichment{' (DRY RUN - nothing written)' if dry_run else ''}", "",
             " · ".join(f"**{k}**: {v}" for k, v in sorted(counts.items())) + f" · API requests: {requests_made}", "",
             "| # | Seed title | Result | Hardcover match | Via | Tags (+/-) | Desc added | Formats | Series | Note |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r.seed_id} | {r.title} | {r.outcome} | {r.hc_title or ''} {f'({r.hc_id})' if r.hc_id else ''} | "
                     f"{r.match} | {r.tags} (+{r.tags_added}/-{r.tags_removed}) | {'yes' if r.description_added else ''} | "
                     f"{r.formats} | {r.series} | {r.note} |")
    return "\n".join(lines)


def run(db: Database, hc: Hardcover, seeds: list[dict], dry_run: bool) -> list[Result]:
    enricher = Enricher(db, hc, dry_run)
    run_id = None
    if not dry_run:
        run_id = db.insert("import_runs", [{"source_id": enricher.src, "job_name": "enrich_hardcover_seed"}])[0]["id"]
    results = [enricher.enrich(s) for s in seeds]
    if run_id:
        counts: dict = {}
        for r in results:
            counts[r.outcome] = counts.get(r.outcome, 0) + 1
        counts["api_requests"] = hc.requests_made
        errors = [f"{r.seed_id} {r.title}: {r.note}" for r in results if r.outcome == "error"]
        db.update("import_runs", {"id": run_id}, {"status": "partial" if errors else "succeeded",
                                                   "finished_at": datetime.now(timezone.utc).isoformat(),
                                                   "counts": counts, "error_log": "\n".join(errors) or None})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrich seed books with Hardcover data.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    seeds = read_seed()[: args.limit] if args.limit else read_seed()
    hc = Hardcover()
    results = run(SupabaseREST.from_env(), hc, seeds, args.dry_run)
    text = report(results, args.dry_run, hc.requests_made)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/hardcover_enrichment.md").write_text(text + "\n", encoding="utf-8")
    return 1 if any(r.outcome == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
