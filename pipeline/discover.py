"""Find candidate books in the target genres and add them to data/candidates.csv.

Sources (both used within their published limits):
  * Open Library subject searches, most-published first (well-established books),
  * Hardcover keyword searches for genre terms, most-read first (better for indie
    LitRPG / progression titles, which Open Library covers poorly).

Filters out box sets, omnibuses, anthologies, study guides and books we already have,
and merges the same book found by both sources. The list is append-only: existing
candidates keep their ids, so batches can be imported in order.

    python -m pipeline.discover --target 470
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

from pipeline.import_openlibrary import SEED_PATH, read_seed
from pipeline.normalise import main_title, normalise_text, surname
from pipeline.sources.hardcover import Hardcover, as_list
from pipeline.sources.openlibrary import OpenLibrary, strip_key

CANDIDATES_PATH = Path(__file__).resolve().parent.parent / "data" / "candidates.csv"
FIELDS = ["seed_id", "title", "author", "ol_work", "hardcover_id", "found_via", "popularity"]

# Open Library subject headings for the initial target genres.
OL_SUBJECTS = ["LitRPG", "Urban fantasy", "Military science fiction", "Superheroes", "Time travel",
               "Space opera", "Alternative histories", "Science fantasy", "Wizards", "Magic"]
# Hardcover keyword searches (matched against titles, series and descriptions).
HC_TERMS = ["LitRPG", "GameLit", "progression fantasy", "cultivation", "isekai", "dungeon core",
            "superhero", "military science fiction", "urban fantasy", "portal fantasy",
            "time travel", "space marines"]
PER_OL_SUBJECT = 40
PER_HC_TERM = 25
MIN_OL_EDITIONS = 3
MIN_HC_READERS = 20

NOT_A_SINGLE_BOOK = re.compile(
    r"\b(omnibus|box(ed)?\s*set|boxset|bundle|collection|anthology|complete series|"
    r"books?\s*\d+\s*(-|–|&|to|and|through)\s*\d+|volumes?\s*\d+\s*(-|–|&|to)\s*\d+|"
    r"study guide|summary|sparknotes|cliffsnotes|analysis of|coloring|colouring|calendar|"
    r"cookbook|art of|companion|encyclopedia|guide to)\b", re.IGNORECASE)


def dedupe_key(title: str, author: str) -> str:
    return f"{main_title(title)}|{surname(author)}"


def acceptable(title: str | None, author: str | None) -> bool:
    return bool(title and author and not NOT_A_SINGLE_BOOK.search(title) and len(normalise_text(title)) >= 2)


def from_openlibrary(ol: OpenLibrary) -> list[dict]:
    out = []
    for subject in OL_SUBJECTS:
        docs = ol._get("/search.json", {
            "q": f'subject:"{subject}" language:eng', "sort": "editions", "limit": PER_OL_SUBJECT * 2,
            "fields": "key,title,author_name,first_publish_year,edition_count"}).get("docs", [])
        kept = 0
        for d in docs:
            authors = d.get("author_name") or []
            if kept >= PER_OL_SUBJECT or (d.get("edition_count") or 0) < MIN_OL_EDITIONS:
                continue
            if not authors or not acceptable(d.get("title"), authors[0]):
                continue
            out.append({"title": d["title"], "author": authors[0], "ol_work": strip_key(d.get("key")),
                        "hardcover_id": "", "found_via": f"openlibrary:{subject}",
                        "popularity": d.get("edition_count") or 0})
            kept += 1
    return out


def from_hardcover(hc: Hardcover) -> list[dict]:
    out = []
    for term in HC_TERMS:
        for d in hc.search_books(term, per_page=PER_HC_TERM):
            authors = as_list(d.get("author_names"))
            if (d.get("users_count") or 0) < MIN_HC_READERS or not authors:
                continue
            if not acceptable(d.get("title"), authors[0]):
                continue
            out.append({"title": d["title"], "author": authors[0], "ol_work": "",
                        "hardcover_id": str(d.get("id")), "found_via": f"hardcover:{term}",
                        "popularity": d.get("users_count") or 0})
    return out


def merge(existing: list[dict], found: list[dict], seeds: list[dict], target: int) -> tuple[list[dict], int]:
    """Append new, de-duplicated candidates (round-robin across searches) until `target` exist."""
    taken = {dedupe_key(s["title"], s["author"]) for s in seeds}
    rows = list(existing)
    index = {dedupe_key(r["title"], r["author"]): r for r in rows}
    taken |= set(index)

    # Fill in the other source's id when both found the same book.
    for f in found:
        r = index.get(dedupe_key(f["title"], f["author"]))
        if r is not None:
            for k in ("ol_work", "hardcover_id"):
                if f[k] and not r.get(k):
                    r[k] = f[k]

    by_search: dict[str, list[dict]] = {}
    for f in found:
        by_search.setdefault(f["found_via"], []).append(f)
    for lst in by_search.values():
        lst.sort(key=lambda x: -int(x["popularity"] or 0))

    added = 0
    next_id = max([int(r["seed_id"][1:]) for r in rows] or [0]) + 1
    queues = list(by_search.values())
    while len(rows) < target and any(queues):
        for q in queues:
            while q:
                f = q.pop(0)
                key = dedupe_key(f["title"], f["author"])
                if key in taken:
                    continue
                twin = next((g for g in found if dedupe_key(g["title"], g["author"]) == key and g is not f), None)
                row = {**f, "seed_id": f"C{next_id:04d}"}
                if twin:
                    for k in ("ol_work", "hardcover_id"):
                        row[k] = row[k] or twin[k]
                rows.append(row)
                taken.add(key)
                next_id += 1
                added += 1
                break
            if len(rows) >= target:
                break
    return rows, added


def read_candidates(path: Path = CANDIDATES_PATH) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_candidates(rows: list[dict], path: Path = CANDIDATES_PATH) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Discover candidate books in the target genres.")
    p.add_argument("--target", type=int, default=470, help="total candidates wanted in the list")
    a = p.parse_args(argv)
    ol, hc = OpenLibrary(), Hardcover()
    found = from_openlibrary(ol) + from_hardcover(hc)
    rows, added = merge(read_candidates(), found, read_seed(SEED_PATH), a.target)
    write_candidates(rows)
    by_source: dict[str, int] = {}
    for r in rows[-added:] if added else []:
        by_source[r["found_via"]] = by_source.get(r["found_via"], 0) + 1
    text = "\n".join([
        "## Book discovery", "",
        f"Found {len(found)} results ({ol.requests_made} Open Library + {hc.requests_made} Hardcover requests). "
        f"Added {added} new candidates; the list now holds {len(rows)}.", "",
        "| Found via | New candidates |", "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in sorted(by_source.items(), key=lambda x: -x[1])],
    ])
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
