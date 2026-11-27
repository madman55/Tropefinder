"""Load and check the trope taxonomy in taxonomy/tropes.yaml.

Run from the repository root:

    python -m pipeline.taxonomy

It prints a summary and exits with an error if anything in the file is wrong
(duplicate slugs, a parent that doesn't exist, a loop in the hierarchy, etc.).
Syncing the taxonomy into the database comes in Stage 2.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import yaml

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "tropes.yaml"

# Must match the category check constraint in the database migration.
CATEGORIES = {
    "protagonist",
    "abilities",
    "setting",
    "story-mechanics",
    "technology",
    "relationships",
    "structure-and-tone",
    "format",
}
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED_KEYS = {
    "slug", "name", "category", "parent", "definition",
    "classifier_hint", "aliases", "related", "conflicts", "implies",
}
REQUIRED_KEYS = {"slug", "name", "category", "definition"}


def load(path: Path = TAXONOMY_PATH) -> list[dict]:
    """Read the YAML file and return the list of trope entries."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get("tropes"), list):
        raise ValueError("tropes.yaml must contain a top-level 'tropes:' list")
    return data["tropes"]


def validate(tropes: list[dict]) -> list[str]:
    """Return a list of problems. An empty list means the taxonomy is valid."""
    problems: list[str] = []

    slugs = [t.get("slug") for t in tropes]
    for slug, n in Counter(slugs).items():
        if n > 1:
            problems.append(f"slug '{slug}' is used {n} times")
    known = set(slugs)

    alias_owner: dict[str, str] = {}
    for t in tropes:
        slug = t.get("slug", "<missing slug>")

        missing = REQUIRED_KEYS - t.keys()
        if missing:
            problems.append(f"{slug}: missing {sorted(missing)}")
        unknown = t.keys() - ALLOWED_KEYS
        if unknown:
            problems.append(f"{slug}: unknown keys {sorted(unknown)}")
        if not SLUG_PATTERN.match(str(slug)):
            problems.append(f"{slug}: slug must be lowercase words joined by hyphens")
        if t.get("category") not in CATEGORIES:
            problems.append(f"{slug}: unknown category '{t.get('category')}'")
        if not str(t.get("definition", "")).strip():
            problems.append(f"{slug}: definition is empty")

        parent = t.get("parent")
        if parent is not None:
            if parent not in known:
                problems.append(f"{slug}: parent '{parent}' does not exist")
            elif parent == slug:
                problems.append(f"{slug}: is its own parent")

        for field in ("related", "conflicts", "implies"):
            for other in t.get(field) or []:
                if other not in known:
                    problems.append(f"{slug}: {field} '{other}' does not exist")
                elif other == slug:
                    problems.append(f"{slug}: lists itself in {field}")

        for alias in t.get("aliases") or []:
            key = str(alias).strip().lower()
            if not key:
                problems.append(f"{slug}: empty alias")
                continue
            if key in known and key != slug:
                problems.append(f"{slug}: alias '{alias}' is another trope's slug")
            owner = alias_owner.get(key)
            if owner and owner != slug:
                problems.append(f"alias '{alias}' is used by both '{owner}' and '{slug}'")
            alias_owner[key] = slug

    # Loops in the hierarchy (a -> b -> a).
    parent_of = {t.get("slug"): t.get("parent") for t in tropes}
    for start in parent_of:
        seen = {start}
        node = parent_of.get(start)
        while node is not None:
            if node in seen:
                problems.append(f"{start}: parent chain loops back on itself")
                break
            seen.add(node)
            node = parent_of.get(node)

    return problems


def main() -> int:
    tropes = load()
    problems = validate(tropes)
    by_category = Counter(t.get("category") for t in tropes)
    top_level = sum(1 for t in tropes if not t.get("parent"))

    print(f"Taxonomy: {len(tropes)} tropes ({top_level} top-level, {len(tropes) - top_level} children)")
    for category in sorted(by_category):
        print(f"  {category:<20} {by_category[category]}")

    if problems:
        print(f"\n{len(problems)} problem(s) found:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nNo problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
