"""Run the content check over the catalogue and store the flags on each book.

    python -m pipeline.check_content --dry-run          # tag rules only, no AI, nothing stored
    python -m pipeline.check_content --batch             # AI via the half-price Batch API
    python -m pipeline.check_content --batch --force     # re-check books already checked

Books already checked with the current CHECK_VERSION are skipped. Joseph's labelled books are
never hidden. Prints and saves a report listing every hidden and adult book for review.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from pipeline.classify.content import CHECK_VERSION, ContentChecker, decide
from pipeline.classify.run import load_books, seed_work_ids
from pipeline.db import Database, SupabaseREST, _load_dotenv


def run(db: Database, checker: ContentChecker | None, force: bool = False, limit: int | None = None,
        batch: bool = False) -> str:
    done = {w["id"] for w in db.select("works", "id,content_checked") if w.get("content_checked") == CHECK_VERSION}
    books = [b for b in load_books(db) if force or b.work_id not in done]
    if limit:
        books = books[:limit]
    protected = set(seed_work_ids(db).values())

    answers: dict = {}
    if checker is not None and books:
        if batch:
            answers = checker.classify_batch(books, None)
        else:
            for b in books:
                try:
                    answers[b.work_id] = checker.classify(b, None)
                except Exception as exc:  # one bad answer shouldn't stop the run
                    answers[b.work_id] = exc

    counts: Counter = Counter()
    hidden_rows, adult_rows, failed = [], [], []
    for b in books:
        ans = answers.get(b.work_id)
        if isinstance(ans, Exception):
            failed.append(f"{b.title}: {str(ans)[:100]}")
            continue
        r = decide(b, ans, protected=b.work_id in protected)
        counts[r.hidden_reason or "shown"] += 1
        counts["adult"] += r.adult_content
        if r.hidden_reason:
            hidden_rows.append(f"| {b.title} | {r.hidden_reason} | {r.note} |")
        if r.adult_content:
            adult_rows.append(f"| {b.title} | {r.note} |")
        if checker is not None:   # tag rules alone (dry run) never write
            db.update("works", {"id": b.work_id}, {
                "audience": r.audience, "content_kind": r.content_kind, "adult_content": r.adult_content,
                "hidden_reason": r.hidden_reason, "content_note": r.note, "content_checked": CHECK_VERSION})

    cost = f" AI cost ${checker.cost_usd}." if checker else " (dry run: tag rules only, nothing stored)"
    lines = [
        "## Content check", "",
        f"Books checked: {len(books)} (skipped, already checked: {len(done) if not force else 0}).{cost}",
        f"Shown: {counts['shown']}. Hidden as children's: {counts['children']}, non-fiction: {counts['nonfiction']}, "
        f"companion guides: {counts['companion']}. Marked adult: {counts['adult']}. Failed: {len(failed)}.",
        "", "### Hidden", "", "| Book | Why | Note |", "| --- | --- | --- |", *sorted(hidden_rows),
        "", "### Adult", "", "| Book | Note |", "| --- | --- |", *sorted(adult_rows),
    ]
    if failed:
        lines += ["", "### Failed (will be retried next run)", "", *[f"- {f}" for f in failed]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Check who each book is for and whether it's explicit.")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--limit", type=int)
    a = p.parse_args(argv)
    _load_dotenv()
    db = SupabaseREST.from_env()
    checker = None if a.dry_run else ContentChecker()
    text = run(db, checker, a.force, a.limit, a.batch)
    print(text)
    with open("content-report.md", "w", encoding="utf-8") as f:
        f.write(text + "\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
