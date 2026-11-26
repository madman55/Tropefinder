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
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pipeline.import_openlibrary import SEED_PATH

from pipeline.db import Database, SupabaseREST
from pipeline.import_openlibrary import read_seed
from pipeline.normalise import (author_matches, clean_isbn, isbn10_to_13, main_title, normalise_text,
                                title_similarity, titles_compatible)
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


SLUG_YEAR = re.compile(r"\s(1[5-9]\d\d|20\d\d)$")
SLUG_NOT_SINGLE_BOOK = re.compile(r"\b(complete series|books? \d+ \d+|box set|boxset|omnibus|stories|anthology)\b")


def slug_matches(title: str, slug_title: str) -> bool:
    """Compare our title with a Hardcover web-address slug, which drops punctuation
    and apostrophes and may end in a year ("old-mans-war-2005")."""
    def plain(t: str) -> str:
        t = normalise_text(t.replace("'", "").replace("\u2019", ""))
        return " ".join(w for w in t.split() if w != "and")
    slug = SLUG_YEAR.sub("", " " + plain(slug_title)).strip()
    if SLUG_NOT_SINGLE_BOOK.search(slug):
        return False
    ours = plain(title)
    return titles_compatible(ours, slug) or titles_compatible(plain(main_title(title)), slug) \
        or (len(slug) >= 4 and f" {slug} " in f" {ours} ")


def pick_hit(docs: list[dict], title: str, author: str, known_isbns: set[str]) -> tuple[dict | None, str]:
    """Pick the best Hardcover result for a seed book.

    A result qualifies if it shares an ISBN with our editions, or has a close title
    AND matching author. Among qualifiers, an exact title match wins (so book 1 beats
    "Book: Arc 2" even if a source mixed their ISBNs), then ISBN overlap, then popularity.
    """
    from pipeline.discover import acceptable   # box sets, non-English editions, etc.
    want = normalise_text(title)
    ranked = []
    for d in docs:
        names = as_list(d.get("author_names"))
        # A shared ISBN is not enough on its own: some Open Library records mix ISBNs from
        # several books of a series, so the titles must be compatible too.
        if not titles_compatible(title, d.get("title")) or not acceptable(d.get("title"), (names or ["?"])[0]):
            continue
        overlap = len(known_isbns & isbn_set(d.get("isbns")))
        close = author_matches(author, names) and title_similarity(title, d.get("title") or "") >= MATCH_THRESHOLD
        if not (overlap or close):
            continue
        exact = normalise_text(d.get("title")) == want
        ranked.append(((exact, overlap > 0, overlap, d.get("users_count") or 0), d, overlap))
    if not ranked:
        return None, ""
    ranked.sort(key=lambda x: x[0], reverse=True)
    _, best, overlap = ranked[0]
    return best, ("isbn" if overlap else "title+author")


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
        self._ol_linked = set()
        ol_src = sources.get("openlibrary")
        for x in db.select("external_ids", "entity_type,entity_id,source_id,external_id"):
            if x["source_id"] == self.src:
                self.ext[(x["entity_type"], x["external_id"])] = x["entity_id"]
            elif x["source_id"] == ol_src and x["entity_type"] == "work":
                self._ol_linked.add(x["entity_id"])
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

    def remove_hardcover_data(self, work_id: str) -> None:
        """Undo a wrong Hardcover link: its id, tags, format flags, series links and description."""
        self.db.delete("external_ids", {"entity_type": "work", "entity_id": work_id, "source_id": self.src})
        self.db.delete("work_source_tags", {"work_id": work_id, "source_id": self.src})
        self.db.delete("format_availability", {"work_id": work_id, "source_id": self.src})
        self.db.delete("work_series", {"work_id": work_id})   # series come only from Hardcover so far
        if self.works.get(work_id, {}).get("description_source_id") == self.src:
            self.db.update("works", {"id": work_id}, {"description": None, "description_source_id": None})

    def recheck_links(self) -> list[str]:
        """Find stored Hardcover links whose book title is incompatible with ours, and remove them.
        Works created from Hardcover are left alone (their title came from Hardcover)."""
        removed = []
        for x in self.db.select("external_ids", "entity_type,entity_id,source_id,external_id,raw_url"):
            if x["source_id"] != self.src or x["entity_type"] != "work":
                continue
            w = self.works.get(x["entity_id"], {})
            if x["entity_id"] not in self._ol_linked:   # created from Hardcover: title is Hardcover's own
                continue
            slug_title = (x.get("raw_url") or "").rsplit("/", 1)[-1].replace("-", " ")
            if slug_title and w.get("title") and not slug_matches(w["title"], slug_title):
                removed.append(f"{w['title']} (was linked to '{slug_title}')")
                if not self.dry_run:
                    self.remove_hardcover_data(x["entity_id"])
        return removed

    # --------------------------------------------------------------- per book
    def enrich(self, seed: dict) -> Result:
        res = Result(seed["seed_id"], seed["title"])
        known = (seed.get("hardcover_id") or "").strip()
        if known and ("work", known) in self.ext and not self.dry_run:
            res.outcome, res.hc_id = "already enriched", known
            return res
        try:
            work_id = self.find_work(seed)
            known_isbns = self.work_isbns.get(work_id, set()) if work_id else set()
            docs = self.hc.search_books(f"{seed['title']} {seed['author']}")
            doc, how = pick_hit(docs, seed["title"], seed["author"], known_isbns)
            if doc is None:
                docs = self.hc.search_books(seed["title"])
                doc, how = pick_hit(docs, seed["title"], seed["author"], known_isbns)
            if doc is None:
                res.note = f"{len(docs)} results; none matched by ISBN or title + author"
                return res
            res.hc_id, res.hc_title, res.match = str(doc.get("id")), doc.get("title"), how
            if normalise_text(doc.get("title")) != normalise_text(seed["title"]):
                seen = "; ".join(f"{d.get('title')} [{d.get('id')}, {d.get('users_count') or 0} readers]" for d in docs)
                res.note = f"title differs from seed; search results were: {seen}"[:600]

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


def recheck(db: Database, dry_run: bool) -> list[str]:
    return Enricher(db, None, dry_run).recheck_links()


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
    parser.add_argument("--list", default=str(SEED_PATH), help="CSV of books (default: the seed list)")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--recheck", action="store_true",
                        help="remove stored Hardcover links whose titles are incompatible (no API calls)")
    args = parser.parse_args(argv)
    rows = read_seed(Path(args.list))
    seeds = rows[args.offset: args.offset + args.limit] if args.limit else rows[args.offset:]
    if args.recheck:
        removed = recheck(SupabaseREST.from_env(), args.dry_run)
        text = (f"## Hardcover link recheck{' (DRY RUN)' if args.dry_run else ''}\n\n"
                f"{len(removed)} links with incompatible titles {'would be ' if args.dry_run else ''}removed:\n\n"
                + "\n".join(f"- {r}" for r in removed))
        print(text)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text + "\n")
        return 0
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
