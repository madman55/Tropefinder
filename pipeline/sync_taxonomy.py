"""Copy taxonomy/tropes.yaml into the database.

The YAML file is the source of truth. Running the sync:
  * adds tropes that are new in the file,
  * updates tropes whose name, definition, category, hint, order or parent changed,
  * marks tropes removed from the file as inactive (never deletes them, because
    books may already be classified with them),
  * adds and removes aliases (synonyms) and related / conflicts links to match the file.

Running it twice in a row changes nothing the second time.

Usage, from the repository root:
    python -m pipeline.sync_taxonomy --dry-run   # show what would change, change nothing
    python -m pipeline.sync_taxonomy             # apply the changes
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

from pipeline.db import Database, SupabaseREST
from pipeline.taxonomy import load, validate

TROPE_FIELDS = ("name", "category", "definition", "classifier_hint", "sort_order", "is_active")
MANAGED_ALIAS_TYPE = "synonym"  # aliases of other types (e.g. source_tag) are managed elsewhere
RELATION_KEYS = {"related": "related", "conflicts": "often_conflicts", "implies": "implies"}


@dataclass
class Report:
    tropes_added: list[str] = field(default_factory=list)
    tropes_updated: list[str] = field(default_factory=list)
    tropes_deactivated: list[str] = field(default_factory=list)
    parents_set: list[str] = field(default_factory=list)
    aliases_added: int = 0
    aliases_removed: int = 0
    relations_added: int = 0
    relations_removed: int = 0

    @property
    def total_changes(self) -> int:
        return (len(self.tropes_added) + len(self.tropes_updated) + len(self.tropes_deactivated)
                + len(self.parents_set) + self.aliases_added + self.aliases_removed
                + self.relations_added + self.relations_removed)

    def lines(self) -> list[str]:
        out = [
            f"Tropes added:        {len(self.tropes_added)}",
            f"Tropes updated:      {len(self.tropes_updated)}",
            f"Tropes deactivated:  {len(self.tropes_deactivated)}",
            f"Parent links set:    {len(self.parents_set)}",
            f"Aliases added:       {self.aliases_added}",
            f"Aliases removed:     {self.aliases_removed}",
            f"Relations added:     {self.relations_added}",
            f"Relations removed:   {self.relations_removed}",
            f"Total changes:       {self.total_changes}",
        ]
        for label, items in (("updated", self.tropes_updated), ("deactivated", self.tropes_deactivated)):
            if items:
                out.append(f"  {label}: {', '.join(items)}")
        return out


def desired_row(trope: dict, order: int) -> dict:
    return {
        "slug": trope["slug"],
        "name": trope["name"],
        "category": trope["category"],
        "definition": trope["definition"],
        "classifier_hint": trope.get("classifier_hint"),
        "sort_order": order,
        "is_active": True,
    }


def sync(db: Database, tropes: list[dict], dry_run: bool = False) -> Report:
    report = Report()
    existing = {r["slug"]: r for r in db.select("tropes", "id,slug,name,category,definition,classifier_hint,sort_order,is_active,parent_id")}
    wanted_slugs = [t["slug"] for t in tropes]

    # 1. Tropes themselves (without parents, so order of insertion doesn't matter).
    to_insert = []
    for order, t in enumerate(tropes):
        want = desired_row(t, order)
        have = existing.get(t["slug"])
        if have is None:
            to_insert.append(want)
            report.tropes_added.append(t["slug"])
        else:
            changes = {k: want[k] for k in TROPE_FIELDS if have.get(k) != want[k]}
            if changes:
                report.tropes_updated.append(t["slug"])
                if not dry_run:
                    db.update("tropes", {"id": have["id"]}, changes)

    for slug, have in existing.items():
        if slug not in wanted_slugs and have.get("is_active"):
            report.tropes_deactivated.append(slug)
            if not dry_run:
                db.update("tropes", {"id": have["id"]}, {"is_active": False})

    if to_insert and not dry_run:
        for row in db.insert("tropes", to_insert):
            existing[row["slug"]] = row

    if dry_run:
        # New tropes have no ids yet; give them placeholders so the rest can be planned.
        for row in to_insert:
            existing[row["slug"]] = {**row, "id": f"new:{row['slug']}", "parent_id": None}

    id_of = {slug: row["id"] for slug, row in existing.items()}

    # 2. Parent links.
    for t in tropes:
        want_parent = id_of[t["parent"]] if t.get("parent") else None
        if existing[t["slug"]].get("parent_id") != want_parent:
            report.parents_set.append(t["slug"])
            if not dry_run:
                db.update("tropes", {"id": id_of[t["slug"]]}, {"parent_id": want_parent})

    # 3. Aliases (only the synonym type; matched case-insensitively).
    have_aliases = {
        (a["trope_id"], a["alias"].strip().lower()): a
        for a in db.select("trope_aliases", "id,trope_id,alias,alias_type")
        if a["alias_type"] == MANAGED_ALIAS_TYPE
    }
    want_aliases = {}
    for t in tropes:
        for alias in t.get("aliases") or []:
            want_aliases[(id_of[t["slug"]], str(alias).strip().lower())] = str(alias).strip()

    new_aliases = [
        {"trope_id": tid, "alias": text, "alias_type": MANAGED_ALIAS_TYPE}
        for key, text in want_aliases.items() if key not in have_aliases
        for tid in [key[0]]
    ]
    report.aliases_added = len(new_aliases)
    stale_aliases = [a for key, a in have_aliases.items() if key not in want_aliases]
    report.aliases_removed = len(stale_aliases)
    if not dry_run:
        for a in stale_aliases:
            db.delete("trope_aliases", {"id": a["id"]})
        db.insert("trope_aliases", new_aliases)

    # 4. Related / conflicts links.
    have_rel = {
        (r["trope_id"], r["related_trope_id"], r["relation"])
        for r in db.select("trope_relations", "trope_id,related_trope_id,relation")
    }
    want_rel = set()
    for t in tropes:
        for key, relation in RELATION_KEYS.items():
            for other in t.get(key) or []:
                want_rel.add((id_of[t["slug"]], id_of[other], relation))

    new_rel = sorted(want_rel - have_rel)
    stale_rel = sorted(have_rel - want_rel)
    report.relations_added = len(new_rel)
    report.relations_removed = len(stale_rel)
    if not dry_run:
        for tid, rid, rel in stale_rel:
            db.delete("trope_relations", {"trope_id": tid, "related_trope_id": rid, "relation": rel})
        db.insert("trope_relations", [
            {"trope_id": tid, "related_trope_id": rid, "relation": rel} for tid, rid, rel in new_rel
        ])

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync taxonomy/tropes.yaml into the database.")
    parser.add_argument("--dry-run", action="store_true", help="show what would change without changing anything")
    args = parser.parse_args(argv)

    tropes = load()
    problems = validate(tropes)
    if problems:
        print("The taxonomy file has problems; nothing was synced:")
        for p in problems:
            print(f"  - {p}")
        return 1

    db = SupabaseREST.from_env()
    report = sync(db, tropes, dry_run=args.dry_run)
    print(("DRY RUN - nothing changed\n" if args.dry_run else "") + "\n".join(report.lines()))

    if not args.dry_run:
        # Prove the second-run rule: a fresh plan against the updated database must be empty.
        check = sync(db, tropes, dry_run=True)
        if check.total_changes:
            print("\nWARNING: a second pass still finds changes:\n" + "\n".join(check.lines()))
            return 1
        print("\nVerified: running again would change nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
