"""Catalogue health check: counts, likely duplicates, gaps, and a random sample to eyeball.

    python -m pipeline.audit

Read-only: it changes nothing.
"""

from __future__ import annotations

import os
import random
import sys
from collections import Counter, defaultdict

from pipeline.db import Database, SupabaseREST
from pipeline.normalise import main_title, surname

TABLES = ["works", "editions", "authors", "work_authors", "series", "work_series", "external_ids",
          "work_source_tags", "format_availability", "tropes", "trope_aliases", "book_tropes",
          "classification_evidence", "import_runs", "classification_runs"]


def likely_duplicates(works: list[dict], authors_of: dict[str, list[str]]) -> list[list[str]]:
    """Works sharing the same main title and an author surname."""
    groups: dict[str, list[str]] = defaultdict(list)
    for w in works:
        for name in authors_of.get(w["id"], []) or [""]:
            groups[f"{main_title(w['title'])}|{surname(name)}"].append(w["id"])
    titles = {w["id"]: w["title"] for w in works}
    out = []
    for key, ids in groups.items():
        ids = sorted(set(ids))
        if len(ids) > 1 and key.split("|")[0]:
            out.append([titles[i] for i in ids])
    return out


def audit(db: Database, sample_size: int = 50, seed: int = 7) -> str:
    counts = {t: len(db.select(t, "id" if t not in ("work_authors", "work_series") else "work_id")) for t in TABLES}
    works = db.select("works", "id,title,description,first_published_year")
    authors = {a["id"]: a["name"] for a in db.select("authors", "id,name")}
    authors_of: dict[str, list[str]] = defaultdict(list)
    for wa in db.select("work_authors", "work_id,author_id"):
        authors_of[wa["work_id"]].append(authors.get(wa["author_id"], ""))
    sources = {s["id"]: s["slug"] for s in db.select("data_sources", "id,slug")}
    links = defaultdict(set)
    for x in db.select("external_ids", "entity_type,entity_id,source_id"):
        if x["entity_type"] == "work":
            links[x["entity_id"]].add(sources.get(x["source_id"]))
    verdicts = Counter(b["work_id"] for b in db.select("book_tropes", "work_id,status") if b["status"] == "present")
    formats = defaultdict(set)
    for f in db.select("format_availability", "work_id,format"):
        if f.get("work_id"):
            formats[f["work_id"]].add(f["format"])

    dups = likely_duplicates(works, authors_of)
    no_desc = [w["title"] for w in works if not w.get("description")]
    no_tropes = [w["title"] for w in works if not verdicts.get(w["id"])]
    no_author = [w["title"] for w in works if not authors_of.get(w["id"])]
    both = sum(1 for w in works if {"openlibrary", "hardcover"} <= links[w["id"]])
    only_hc = sum(1 for w in works if links[w["id"]] == {"hardcover"})
    only_ol = sum(1 for w in works if links[w["id"]] == {"openlibrary"})

    rnd = random.Random(seed)
    sample = rnd.sample(works, min(sample_size, len(works)))
    lines = [
        "## Catalogue audit", "",
        "| Table | Rows |", "| --- | --- |", *[f"| {t} | {n} |" for t, n in counts.items()], "",
        f"- Works from both sources: {both}; Open Library only: {only_ol}; Hardcover only: {only_hc}.",
        f"- With an audiobook flag: {sum('audiobook' in formats[w['id']] for w in works)}; ebook: {sum('ebook' in formats[w['id']] for w in works)}.",
        f"- Average 'present' tropes per book: {sum(verdicts.values()) / max(1, len(works)):.1f}.",
        f"- **Likely duplicate works: {len(dups)}**" + ("" if not dups else ": " + "; ".join(" = ".join(g) for g in dups)),
        f"- Without a description: {len(no_desc)}" + (f" ({'; '.join(no_desc[:15])}{'…' if len(no_desc) > 15 else ''})" if no_desc else ""),
        f"- Without any 'present' trope: {len(no_tropes)}",
        f"- Without an author: {len(no_author)}" + (f" ({'; '.join(no_author[:10])})" if no_author else ""),
        "", f"### Random sample of {len(sample)} books (seed {seed}) for a hand check", "",
        "| Title | Author | Year | Sources | Present tropes | Formats |", "| --- | --- | --- | --- | --- | --- |",
    ]
    for w in sorted(sample, key=lambda x: x["title"].lower()):
        lines.append(f"| {w['title']} | {', '.join(authors_of.get(w['id'], []))} | {w.get('first_published_year') or ''} | "
                     f"{', '.join(sorted(s for s in links[w['id']] if s))} | {verdicts.get(w['id'], 0)} | "
                     f"{', '.join(sorted(formats[w['id']]))} |")
    return "\n".join(lines)


def main() -> int:
    text = audit(SupabaseREST.from_env())
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
