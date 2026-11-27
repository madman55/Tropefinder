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

    tropes = {t["id"]: t for t in db.select("tropes", "id,slug,name,is_active")}
    per_trope: dict[str, Counter] = defaultdict(Counter)
    for b in db.select("book_tropes", "trope_id,status,verification"):
        if b["status"] == "present":
            per_trope[b["trope_id"]][b["verification"]] += 1
    tags = Counter(t["tag"] for t in db.select("work_source_tags", "tag,tag_type") if t["tag_type"] in ("genre", "tag"))

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
        "", "### Books per trope ('present')", "",
        "| Trope | Books | From tags | From AI | From people |", "| --- | --- | --- | --- | --- |",
        *[f"| {t['slug']} | {sum(per_trope[i].values())} | {per_trope[i]['source_tag']} | {per_trope[i]['ai_suggested']} | "
          f"{per_trope[i]['human_verified'] + per_trope[i]['community_verified']} |"
          for i, t in sorted(tropes.items(), key=lambda x: -sum(per_trope[x[0]].values())) if t.get("is_active")],
        "", "### Most common reader tags (genre and tag)", "",
        ", ".join(f"{t} ({n})" for t, n in tags.most_common(120)),
        "", f"### Random sample of {len(sample)} books (seed {seed}) for a hand check", "",
        "| Title | Author | Year | Sources | Present tropes | Formats |", "| --- | --- | --- | --- | --- | --- |",
    ]
    for w in sorted(sample, key=lambda x: x["title"].lower()):
        lines.append(f"| {w['title']} | {', '.join(authors_of.get(w['id'], []))} | {w.get('first_published_year') or ''} | "
                     f"{', '.join(sorted(s for s in links[w['id']] if s))} | {verdicts.get(w['id'], 0)} | "
                     f"{', '.join(sorted(formats[w['id']]))} |")
    return "\n".join(lines)


def gold_check(db: Database) -> str:
    """For each trope Joseph labelled, what the catalogue says now (to see which finds were missed)."""
    from pipeline.classify.run import counts_for_map, read_gold, seed_work_ids
    seed_to_work = seed_work_ids(db)
    trope_rows = db.select("tropes", "id,slug,parent_id")
    slug_of = {t["id"]: t["slug"] for t in trope_rows}
    counts_for = counts_for_map(trope_rows, db.select("trope_relations", "trope_id,related_trope_id,relation"))
    now = {(b["work_id"], slug_of.get(b["trope_id"])): (b["status"], b["verification"])
           for b in db.select("book_tropes", "work_id,trope_id,status,verification")}
    rows, found, total = [], 0, 0
    for g in read_gold():
        if g["label"] != "present":
            continue
        total += 1
        wid = seed_to_work.get(g["seed_id"])
        status, how = now.get((wid, g["trope_slug"]), ("unknown", ""))
        via = [x for x in counts_for.get(g["trope_slug"], ()) if x != g["trope_slug"]
               and now.get((wid, x), ("",))[0] == "present"]
        if status != "present" and via:
            status = "present (via " + ", ".join(sorted(via)) + ")"
        found += status.startswith("present")
        if not status.startswith("present"):
            rows.append(f"| {g['title']} | {g['trope_slug']} | {status} |")
    return "\n".join([
        "", f"### Joseph's 'present' labels: {found} of {total} found", "",
        "Missed ones (still unknown or wrong):", "", "| Book | Trope | Now |", "| --- | --- | --- |", *rows])


def main() -> int:
    db = SupabaseREST.from_env()
    text = audit(db) + "\n" + gold_check(db)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
