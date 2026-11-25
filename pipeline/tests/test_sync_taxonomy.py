"""Tests for the taxonomy sync, using an in-memory database. Run with: python -m pytest"""

import copy

from pipeline.db import FakeDB
from pipeline.sync_taxonomy import sync
from pipeline.taxonomy import load

REAL = load()


def small():
    return [
        {"slug": "magic", "name": "Magic", "category": "abilities", "definition": "Uses magic.",
         "aliases": ["Sorcery", "spells"], "conflicts": ["no-magic"]},
        {"slug": "hard-magic", "name": "Hard magic", "category": "abilities", "parent": "magic",
         "definition": "Rules-based magic.", "related": ["magic"]},
        {"slug": "no-magic", "name": "No magic", "category": "abilities", "definition": "No magic at all."},
    ]


def test_first_run_loads_everything_from_real_taxonomy():
    db = FakeDB()
    r = sync(db, REAL)
    assert len(r.tropes_added) == len(REAL)
    assert len(db.tables["tropes"]) == len(REAL)
    children = [t for t in REAL if t.get("parent")]
    assert len(r.parents_set) == len(children)
    assert r.aliases_added == sum(len(t.get("aliases") or []) for t in REAL)


def test_second_run_changes_nothing():
    db = FakeDB()
    sync(db, REAL)
    calls_before = len(db.calls)
    again = sync(db, REAL)
    assert again.total_changes == 0
    # Only empty inserts may happen (no-ops); no updates or deletes.
    assert all(kind == "insert" for kind, _ in db.calls[calls_before:])
    assert len(db.tables["tropes"]) == len(REAL)


def test_dry_run_writes_nothing():
    db = FakeDB()
    r = sync(db, small(), dry_run=True)
    assert r.tropes_added == ["magic", "hard-magic", "no-magic"]
    assert db.calls == []


def test_parent_and_relations_are_linked_by_id():
    db = FakeDB()
    sync(db, small())
    by_slug = {t["slug"]: t for t in db.tables["tropes"]}
    assert by_slug["hard-magic"]["parent_id"] == by_slug["magic"]["id"]
    rels = {(r["trope_id"], r["related_trope_id"], r["relation"]) for r in db.tables["trope_relations"]}
    assert (by_slug["magic"]["id"], by_slug["no-magic"]["id"], "often_conflicts") in rels
    assert (by_slug["hard-magic"]["id"], by_slug["magic"]["id"], "related") in rels


def test_edit_updates_only_that_trope():
    db = FakeDB()
    sync(db, small())
    edited = small()
    edited[0]["definition"] = "Characters use magic or spells."
    r = sync(db, edited)
    assert r.tropes_updated == ["magic"]
    assert r.total_changes == 1


def test_removed_trope_is_deactivated_not_deleted():
    db = FakeDB()
    sync(db, small())
    fewer = [t for t in small() if t["slug"] != "no-magic"]
    fewer[0].pop("conflicts")
    r = sync(db, fewer)
    assert r.tropes_deactivated == ["no-magic"]
    assert r.relations_removed == 1
    row = next(t for t in db.tables["tropes"] if t["slug"] == "no-magic")
    assert row["is_active"] is False
    # Putting it back reactivates it.
    r2 = sync(db, small())
    assert "no-magic" in r2.tropes_updated
    assert next(t for t in db.tables["tropes"] if t["slug"] == "no-magic")["is_active"] is True


def test_alias_changes_and_case_insensitivity():
    db = FakeDB()
    sync(db, small())
    changed = small()
    changed[0]["aliases"] = ["SORCERY", "wizardry"]  # same as 'Sorcery' ignoring case; 'spells' removed
    r = sync(db, changed)
    assert r.aliases_added == 1 and r.aliases_removed == 1


def test_source_tag_aliases_are_left_alone():
    db = FakeDB()
    sync(db, small())
    magic_id = next(t["id"] for t in db.tables["tropes"] if t["slug"] == "magic")
    db.insert("trope_aliases", [{"trope_id": magic_id, "alias": "magic-system", "alias_type": "source_tag"}])
    r = sync(db, small())
    assert r.aliases_removed == 0
    assert any(a["alias_type"] == "source_tag" for a in db.tables["trope_aliases"])


def test_input_is_not_mutated():
    data = small()
    before = copy.deepcopy(data)
    sync(FakeDB(), data)
    assert data == before
