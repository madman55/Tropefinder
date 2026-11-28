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
# Books removed on review (wrong language, children's, comics, anthologies...): never re-added.
EXCLUSIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "discovery_exclusions.csv"
FIELDS = ["seed_id", "title", "author", "ol_work", "hardcover_id", "found_via", "popularity"]

# Open Library searches for the initial target genres. Each is restricted to books first
# published from 1980, in English, and excludes comics and children's books: sorting by
# number of editions otherwise favours old classics and children's series.
OL_EXCLUDE = ('-subject:"Comic books, strips, etc." -subject:"Comic books" -subject:"Comics & graphic novels" '
              '-subject:"Cartoons and comics" -subject:"Graphic novels" -subject:"Manga" '
              '-subject:"Juvenile fiction" -subject:"Children\'s fiction" -subject:"Juvenile literature" '
              '-subject:"Picture books" -subject:"Children\'s stories" '
              '-subject:"History and criticism" -subject:"Criticism, interpretation, etc." '
              '-subject:"Motion pictures"')
OL_QUERIES = {
    "LitRPG": 'subject:"LitRPG"',
    "Urban fantasy": 'subject:"Urban fantasy"',
    "Military science fiction": 'subject:"Military science fiction"',
    "Superheroes": 'subject:"Superheroes" subject:"Fiction"',
    "Time travel": 'subject:"Time travel" subject:"Fiction"',
    "Space opera": 'subject:"Space opera"',
    "Alternative histories": 'subject:"Alternative histories (Fiction)"',
    "Science fantasy": 'subject:"Science fantasy"',
    "Magic (fantasy)": 'subject:"Magic" subject:"Fantasy fiction"',
    # added 28 Nov 2026 to grow towards ~2,000 books
    "Science fiction": 'subject:"Science fiction"',
    "Fantasy fiction": 'subject:"Fantasy fiction"',
    "Dystopias": 'subject:"Dystopias"',
    "Cyberpunk": 'subject:"Cyberpunk fiction"',
    "Dragons": 'subject:"Dragons" subject:"Fiction"',
    "Vampires": 'subject:"Vampires" subject:"Fiction"',
    "Wizards": 'subject:"Wizards" subject:"Fiction"',
    "Robots": 'subject:"Robots" subject:"Fiction"',
    "Interplanetary voyages": 'subject:"Interplanetary voyages"',
    "Life on other planets": 'subject:"Life on other planets"',
    "End of the world": 'subject:"End of the world" subject:"Fiction"',
    "Imaginary wars": 'subject:"Imaginary wars and battles"',
    "Steampunk": 'subject:"Steampunk fiction"',
}
OL_FROM_YEAR = 1980
# Hardcover keyword searches (matched against titles, series and descriptions).
HC_TERMS = ["LitRPG", "GameLit", "progression fantasy", "cultivation", "isekai", "dungeon core",
            "system apocalypse", "superhero novel", "military science fiction", "urban fantasy",
            "portal fantasy", "time travel novel", "space marines", "reincarnated",
            # added 28 Nov 2026
            "epic fantasy", "space opera", "cyberpunk", "dystopian", "post-apocalyptic", "grimdark",
            "cozy fantasy", "monster hunter", "dragon rider", "alien invasion", "first contact",
            "zombie apocalypse", "magic academy", "dungeon crawler", "kingdom building", "base building",
            "vampire", "werewolf", "necromancer", "mecha", "artificial intelligence", "military fantasy",
            "sword and sorcery", "steampunk", "time loop", "generation ship", "colony ship"]
HC_PAGES = 5
OL_PAGES = 3          # 100 results per page
PER_OL_SUBJECT = 150
PER_HC_TERM = 25
MIN_OL_EDITIONS = 2
MIN_HC_READERS = 10
MAX_PER_AUTHOR = 5

NOT_A_SINGLE_BOOK = re.compile(
    r"\b(omnibus|box(ed)?\s*set|boxset|bundle|collection|anthology|complete series|"
    r"books?\s*\d+\s*(-|–|&|to|and|through)\s*\d+|volumes?\s*\d+\s*(-|–|&|to)\s*\d+|"
    r"study guide|summary|sparknotes|cliffsnotes|analysis of|coloring|colouring|calendar|"
    r"cookbook|art of|companion|encyclopedia|guide to|"
    r"manga|manhua|manhwa|light novel|comic|graphic novel|"
    r"tales of|stories|novels featuring|a history|plays|masterworks)\b", re.IGNORECASE)
# Publisher names that appear as authors of comic collections.
NOT_AN_AUTHOR = re.compile(r"\b(marvel comics|dc comics|various|corporation|broadcasting|publishing|press)\b", re.IGNORECASE)
COLLECTED_WORKS = re.compile(r"^\s*(novels|works|selected works|collected works|stories|plays|poems)\s*$", re.IGNORECASE)
SEQUEL_MARKER = re.compile(r"(\bbook\s*#?\s*|\bno\.?\s*|\bpart\s*|\bvol\.?\s*|\bvolume\s*|#\s*)([2-9]|[1-9]\d)\b",
                           re.IGNORECASE)
NUMBERED_MAIN_TITLE = re.compile(r"[A-Za-z].*\s([2-9]|[1-9]\d|II|III|IV|V|VI|VII|VIII|IX|X)$")  # "Player Manager 2", not "1632"


def is_sequel(title: str) -> bool:
    main = re.split(r":| - | \(", title, maxsplit=1)[0].strip()
    return bool(SEQUEL_MARKER.search(title) or NUMBERED_MAIN_TITLE.search(main))


def dedupe_key(title: str, author: str) -> str:
    return f"{main_title(title)}|{surname(author)}"


def latin_script(text: str) -> bool:
    """English-language catalogue for now: reject titles in non-Latin scripts."""
    return all(ord(c) < 0x300 or c in "‘’“”–—…" for c in text)


def acceptable(title: str | None, author: str | None) -> bool:
    """A single, English-language book that isn't a later volume of a series."""
    if not (title and author) or len(normalise_text(title)) < 2:
        return False
    if not latin_script(title) or not latin_script(author) or NOT_AN_AUTHOR.search(author):
        return False
    if " / " in title or COLLECTED_WORKS.match(title):   # "A / B / C" omnibus listings
        return False
    return not NOT_A_SINGLE_BOOK.search(title) and not is_sequel(title)


def from_openlibrary(ol: OpenLibrary) -> list[dict]:
    out = []
    for label, query in OL_QUERIES.items():
        docs = [d for page in range(1, OL_PAGES + 1) for d in ol._get("/search.json", {
            "q": f"{query} language:eng first_publish_year:[{OL_FROM_YEAR} TO *] {OL_EXCLUDE}",
            "sort": "editions", "limit": 100, "page": page, "lang": "en",
            "fields": "key,title,author_name,first_publish_year,edition_count"}).get("docs", [])]
        kept = 0
        for d in docs:
            authors = d.get("author_name") or []
            if kept >= PER_OL_SUBJECT or (d.get("edition_count") or 0) < MIN_OL_EDITIONS:
                continue
            if (d.get("first_publish_year") or 0) < OL_FROM_YEAR:
                continue
            if not authors or not acceptable(d.get("title"), authors[0]):
                continue
            out.append({"title": d["title"], "author": authors[0], "ol_work": strip_key(d.get("key")),
                        "hardcover_id": "", "found_via": f"openlibrary:{label}",
                        "popularity": d.get("edition_count") or 0})
            kept += 1
    return out


def from_hardcover(hc: Hardcover) -> list[dict]:
    out = []
    for term in HC_TERMS:
        docs = [d for page in range(1, HC_PAGES + 1) for d in hc.search_books(term, per_page=PER_HC_TERM, page=page)]
        for d in docs:
            authors = as_list(d.get("author_names"))
            if (d.get("users_count") or 0) < MIN_HC_READERS or not authors:
                continue
            if not acceptable(d.get("title"), authors[0]):
                continue
            out.append({"title": d["title"], "author": authors[0], "ol_work": "",
                        "hardcover_id": str(d.get("id")), "found_via": f"hardcover:{term}",
                        "popularity": d.get("users_count") or 0})
    return out


def merge(existing: list[dict], found: list[dict], seeds: list[dict], target: int,
          excluded: list[dict] | None = None) -> tuple[list[dict], int]:
    """Append new, de-duplicated candidates (round-robin across searches) until `target` exist."""
    taken = {dedupe_key(s["title"], s["author"]) for s in seeds + (excluded or [])}
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

    per_author: dict[str, int] = {}
    for r in rows:
        per_author[surname(r["author"])] = per_author.get(surname(r["author"]), 0) + 1
    added = 0
    next_id = max([int(r["seed_id"][1:]) for r in rows] or [0]) + 1
    queues = list(by_search.values())
    while len(rows) < target and any(queues):
        for q in queues:
            while q:
                f = q.pop(0)
                key = dedupe_key(f["title"], f["author"])
                if key in taken or per_author.get(surname(f["author"]), 0) >= MAX_PER_AUTHOR:
                    continue
                twin = next((g for g in found if dedupe_key(g["title"], g["author"]) == key and g is not f), None)
                row = {**f, "seed_id": f"C{next_id:04d}"}
                if twin:
                    for k in ("ol_work", "hardcover_id"):
                        row[k] = row[k] or twin[k]
                rows.append(row)
                taken.add(key)
                per_author[surname(f["author"])] = per_author.get(surname(f["author"]), 0) + 1
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
    p.add_argument("--target", type=int, default=2000, help="total candidates wanted in the list")
    a = p.parse_args(argv)
    ol, hc = OpenLibrary(), Hardcover()
    found = from_openlibrary(ol) + from_hardcover(hc)
    excluded = read_candidates(EXCLUSIONS_PATH)
    rows, added = merge(read_candidates(), found, read_seed(SEED_PATH), a.target, excluded)
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
