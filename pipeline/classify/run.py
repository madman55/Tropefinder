"""Classify every stored book against the trope list, then score against the gold labels.

    python -m pipeline.classify.run --dry-run     # classify and score, write nothing
    python -m pipeline.classify.run               # classify, write verdicts + evidence, score
    python -m pipeline.classify.run --force       # re-ask the AI even for books already done

How the layers combine for each book and trope:
  tag says present + AI present   -> present  (source_tag)
  tag says present + AI unknown   -> present  (source_tag)
  tag says present + AI absent    -> unknown, both pieces of evidence kept (conflict)
  no tag           + AI present   -> present  (ai_suggested)
  no tag           + AI absent    -> no row (unknown): see AI_ABSENT_TRUSTED
  anything else                   -> no row, which means unknown
Human-verified verdicts are never touched.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pipeline.classify.ai import MODEL, PROMPT_VERSION, AIVerdict, Claude, verify
from pipeline.classify.evidence import BookText
from pipeline.classify.tags import TagHit, build_lookup, tag_hits
from pipeline.db import Database, SupabaseREST
from pipeline.import_openlibrary import read_seed
from pipeline.normalise import author_matches, title_similarity

GOLD_PATH = Path(__file__).resolve().parents[2] / "data" / "gold_labels.csv"
PROTECTED = {"human_verified", "community_verified"}
# The AI's "absent" was wrong 3 of 3 times against Joseph's labels (prompt v2, 26 Nov):
# descriptions cover too little of a book to rule a trope out. Until that improves,
# an AI-only "absent" is not stored; "absent" comes from people.
AI_ABSENT_TRUSTED = False


@dataclass
class Verdict:
    trope_slug: str
    status: str
    confidence: float
    verification: str
    evidence: list[dict] = field(default_factory=list)  # {supports, evidence_type, field, quote, source_id}
    conflict: bool = False


def combine(hits: list[TagHit], ai: list[AIVerdict], ai_source_id: str | None) -> list[Verdict]:
    by_slug_hits: dict[str, list[TagHit]] = {}
    for h in hits:
        by_slug_hits.setdefault(h.trope_slug, []).append(h)
    by_slug_ai = {v.trope_slug: v for v in ai}
    out = []
    for slug in sorted(set(by_slug_hits) | set(by_slug_ai)):
        tag_ev = [{"supports": "present", "evidence_type": "source_tag", "field": h.field,
                   "quote": h.value, "source_id": h.source_id} for h in by_slug_hits.get(slug, [])]
        a = by_slug_ai.get(slug)
        ai_ev = [{"supports": a.status, "evidence_type": "quote", "field": e["field"], "quote": e["quote"],
                  "source_id": ai_source_id} for e in (a.evidence if a else [])]
        ai_status = a.status if a else "unknown"
        if tag_ev and ai_status == "absent":
            out.append(Verdict(slug, "unknown", 0.0, "ai_suggested", tag_ev + ai_ev, conflict=True))
        elif tag_ev:
            conf = max(0.8, a.confidence if a and ai_status == "present" else 0.0)
            out.append(Verdict(slug, "present", round(conf, 3), "source_tag", tag_ev + ai_ev))
        elif ai_status == "present" or (ai_status == "absent" and AI_ABSENT_TRUSTED):
            out.append(Verdict(slug, ai_status, round(a.confidence, 3), "ai_suggested", ai_ev))
    return out


# ------------------------------------------------------------------ loading
def load_books(db: Database) -> list[BookText]:
    sources = {s["id"]: s["slug"] for s in db.select("data_sources", "id,slug")}
    hardcover = next((i for i, s in sources.items() if s == "hardcover"), None)
    books = {w["id"]: BookText(w["id"], w["title"], w.get("description"), list(w.get("subjects") or []),
                               [], w.get("description_source_id"), w.get("subjects_source_id"), hardcover)
             for w in db.select("works", "id,title,description,description_source_id,subjects,subjects_source_id")}
    for t in db.select("work_source_tags", "work_id,tag_type,tag,position"):
        if t["work_id"] in books:
            books[t["work_id"]].tags.append((t["tag_type"], t["tag"]))
    return list(books.values())


def seed_work_ids(db: Database) -> dict[str, str]:
    """seed_id -> work_id, matched by title and author like the importers."""
    works = db.select("works", "id,title")
    authors = {a["id"]: a["name"] for a in db.select("authors", "id,name")}
    names: dict[str, list[str]] = {}
    for wa in db.select("work_authors", "work_id,author_id"):
        names.setdefault(wa["work_id"], []).append(authors.get(wa["author_id"], ""))
    out = {}
    for s in read_seed():
        for w in works:
            if title_similarity(s["title"], w["title"]) >= 0.88 and \
                    any(author_matches(s["author"], [n]) or author_matches(n, [s["author"]]) for n in names.get(w["id"], [])):
                out[s["seed_id"]] = w["id"]
                break
    return out


# ------------------------------------------------------------------ scoring
def score(verdicts: dict[tuple[str, str], str], gold: list[dict], seed_to_work: dict[str, str]) -> dict:
    s = {"labels": 0, "tp": 0, "fp": 0, "fn": 0, "abs_right": 0, "abs_wrong": 0, "unknown": 0,
         "gold_present": 0, "gold_unknown": 0, "disagreements": [], "missing_books": set()}
    for g in gold:
        wid = seed_to_work.get(g["seed_id"])
        if not wid:
            s["missing_books"].add(g["title"])
            continue
        if g["label"] == "unknown":
            s["gold_unknown"] += 1
            continue
        s["labels"] += 1
        mine = verdicts.get((wid, g["trope_slug"]), "unknown")
        s["gold_present"] += g["label"] == "present"
        if mine == "present":
            if g["label"] == "present":
                s["tp"] += 1
            else:
                s["fp"] += 1
                s["disagreements"].append((g["title"], g["trope_slug"], "present", g["label"]))
        elif mine == "absent":
            if g["label"] == "absent":
                s["abs_right"] += 1
            else:
                s["abs_wrong"] += 1
                s["disagreements"].append((g["title"], g["trope_slug"], "absent", g["label"]))
        else:
            s["unknown"] += 1
            s["fn"] += g["label"] == "present"
    said_present = s["tp"] + s["fp"]
    s["precision_present"] = s["tp"] / said_present if said_present else None
    s["recall_present"] = s["tp"] / s["gold_present"] if s["gold_present"] else None
    return s


def read_gold(path: Path = GOLD_PATH) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


# ------------------------------------------------------------------ run
def run(db: Database, claude: Claude | None, dry_run: bool, force: bool, limit: int | None = None,
        batch: bool = False) -> str:
    sources = {s["slug"]: s["id"] for s in db.select("data_sources", "id,slug")}
    ai_source = sources.get("ai_claude_haiku")
    tropes = [t for t in db.select("tropes", "id,slug,name,category,definition,classifier_hint,is_active,sort_order")
              if t.get("is_active", True)]
    tropes.sort(key=lambda t: t.get("sort_order") or 0)
    slugs = {t["slug"] for t in tropes}
    id_of = {t["slug"]: t["id"] for t in tropes}
    slug_of = {t["id"]: t["slug"] for t in tropes}
    lookup = build_lookup(tropes, db.select("trope_aliases", "trope_id,alias"))

    existing = db.select("book_tropes", "id,work_id,trope_id,status,verification,classification_run_id")
    current_runs = {r["id"] for r in db.select("classification_runs", "id,method,prompt_version")
                    if r["method"] == "ai" and r.get("prompt_version") == PROMPT_VERSION}
    done_works = {b["work_id"] for b in existing if b.get("classification_run_id") in current_runs}
    protected = {(b["work_id"], b["trope_id"]) for b in existing if b["verification"] in PROTECTED}

    books = load_books(db)
    if limit:  # the first N books not yet done
        pending = [b for b in books if force or b.work_id not in done_works]
        books = pending[:limit]
    run_id = None
    if not dry_run:
        run_id = db.insert("classification_runs", [{"method": "ai", "model_version": MODEL,
                                                    "prompt_version": PROMPT_VERSION}])[0]["id"]

    final: dict[tuple[str, str], str] = {}
    for b in existing:  # start from what is stored; re-classified books overwrite below
        if b["trope_id"] in slug_of:
            final[(b["work_id"], slug_of[b["trope_id"]])] = b["status"]

    lines, processed, rejected_total, conflicts = [], 0, 0, 0
    todo = [b for b in books if force or b.work_id not in done_works]
    skipped = len(books) - len(todo)
    batch_results: dict = {}
    if claude is not None and batch and todo:
        batch_results = claude.classify_batch(todo, tropes)
    for book in todo:
        hits = tag_hits(book, lookup)
        ai: list[AIVerdict] = []
        note = ""
        if claude is not None:
            try:
                raw = batch_results.get(book.work_id, RuntimeError("missing from batch")) if batch \
                    else claude.classify(book, tropes)
                if isinstance(raw, Exception):
                    raise raw
                ai = verify(raw, book, slugs)
            except Exception as exc:
                note = f"AI failed: {type(exc).__name__}: {str(exc)[:150]}"
        verdicts = combine(hits, ai, ai_source)
        rejected = sum(v.rejected_quotes for v in ai)
        rejected_total += rejected
        conflicts += sum(v.conflict for v in verdicts)
        processed += 1
        for k in [k for k in final if k[0] == book.work_id]:
            del final[k]
        for v in verdicts:
            if (book.work_id, id_of[v.trope_slug]) not in protected:
                final[(book.work_id, v.trope_slug)] = v.status
        n_present = sum(v.status == "present" for v in verdicts)
        n_absent = sum(v.status == "absent" for v in verdicts)
        lines.append(f"| {book.title} | {n_present} present, {n_absent} absent | {len(hits)} | "
                     f"{sum(v.status != 'unknown' for v in ai)} | {rejected} | {note} |")

        if dry_run:
            continue
        # Replace this book's machine verdicts (evidence goes with them); keep human ones.
        for b in existing:
            if b["work_id"] == book.work_id and b["verification"] not in PROTECTED:
                db.delete("book_tropes", {"id": b["id"]})
        for v in verdicts:
            if (book.work_id, id_of[v.trope_slug]) in protected:
                continue
            row = db.insert("book_tropes", [{"work_id": book.work_id, "trope_id": id_of[v.trope_slug],
                                             "status": v.status, "confidence": v.confidence,
                                             "verification": v.verification,
                                             "classification_run_id": run_id}])[0]
            db.insert("classification_evidence", [{
                "book_trope_id": row["id"], "supports": e["supports"], "evidence_type": e["evidence_type"],
                "field": e["field"], "quote": e["quote"], "source_id": e["source_id"] or ai_source,
                "classification_run_id": run_id, "model_version": MODEL if e["evidence_type"] == "quote" else None,
            } for e in v.evidence])

    if run_id:
        db.update("classification_runs", {"id": run_id}, {
            "finished_at": datetime.now(timezone.utc).isoformat(), "books_processed": processed,
            "cost_usd": claude.cost_usd if claude else 0,
            "notes": f"rejected quotes: {rejected_total}; conflicts: {conflicts}"})

    s = score(final, read_gold(), seed_work_ids(db))
    head = [
        f"## Trope classification{' (DRY RUN - nothing written)' if dry_run else ''}",
        "",
        f"Model {MODEL}, prompt {PROMPT_VERSION}{', Batch API' if batch else ''}. Books classified: {processed}"
        f" (skipped, already done: {skipped}). "
        f"AI calls: {claude.calls if claude else 0}, tokens in/out: {claude.input_tokens if claude else 0}/"
        f"{claude.output_tokens if claude else 0}, cost ${claude.cost_usd if claude else 0:.4f}. "
        f"Quotes rejected (not found in stored text): {rejected_total}. Tag/AI conflicts kept as unknown: {conflicts}.",
        "",
        "### Against Joseph's labels",
        "",
        f"Labels used: {s['labels']} (his {s['gold_unknown']} 'not sure' answers are left out).",
        "",
        f"- **Said present, and right: {s['tp']} of {s['tp'] + s['fp']} ({pct(s['precision_present'])})**. Target: at least 85%.",
        f"- Found {s['tp']} of the {s['gold_present']} tropes Joseph marked present ({pct(s['recall_present'])}). The rest stayed unknown: the stored text doesn't show them.",
        f"- Said absent: {s['abs_right'] + s['abs_wrong']} ({s['abs_right']} right, {s['abs_wrong']} wrong).",
        f"- Left unknown: {s['unknown']}.",
    ]
    if s["missing_books"]:
        head.append(f"- Labelled books not found in the database: {', '.join(sorted(s['missing_books']))}.")
    if s["disagreements"]:
        head += ["", "### Disagreements to look at", "", "| Book | Trope | Classifier | Joseph |", "| --- | --- | --- | --- |"]
        head += [f"| {t} | {slug} | {mine} | {gold} |" for t, slug, mine, gold in s["disagreements"]]
    head += ["", "### Per book", "", "| Book | Verdicts | Tag matches | AI verdicts | Quotes rejected | Note |",
             "| --- | --- | --- | --- | --- | --- |"]
    return "\n".join(head + lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classify books against the trope list.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="re-classify books already done with this prompt version")
    p.add_argument("--no-ai", action="store_true", help="tag matching only (free)")
    p.add_argument("--limit", type=int)
    p.add_argument("--batch", action="store_true", help="use the Message Batches API (half price, slower)")
    a = p.parse_args(argv)
    claude = None if a.no_ai else Claude()
    text = run(SupabaseREST.from_env(), claude, a.dry_run, a.force, a.limit, a.batch)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    Path("data/reports").mkdir(parents=True, exist_ok=True)
    Path("data/reports/classification.md").write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
