"""Tests for candidate discovery: filters, de-duplication, stable ids."""

from pipeline.db import FakeDB
from pipeline.discover import acceptable, merge
from pipeline.import_openlibrary import run as ol_run
from pipeline.tests.test_import_openlibrary import FakeOL, fresh_db


def test_filters_out_sets_and_guides():
    assert acceptable("Storm Front", "Jim Butcher")
    assert acceptable("Mother of Learning: Arc 1", "Domagoj Kurmaic")
    for bad in ["The Dresden Files Box Set", "Cradle: Books 1-3", "Red Rising Omnibus",
                "Study Guide for Old Man's War", "Urban Fantasy Anthology", "The Art of Red Rising"]:
        assert not acceptable(bad, "Someone"), bad
    assert not acceptable("Untitled", None)


def f(title, author, via, pop, ol="", hc=""):
    return {"title": title, "author": author, "ol_work": ol, "hardcover_id": hc, "found_via": via, "popularity": pop}


def test_merge_dedupes_skips_seeds_and_round_robins():
    seeds = [{"seed_id": "1", "title": "Storm Front", "author": "Jim Butcher"}]
    found = [
        f("Storm Front", "Jim Butcher", "openlibrary:Wizards", 90, ol="OL1W"),          # already a seed
        f("Fool Moon", "Jim Butcher", "openlibrary:Wizards", 80, ol="OL2W"),
        f("Grave Peril", "Jim Butcher", "openlibrary:Wizards", 70, ol="OL3W"),
        f("Fool Moon", "Jim Butcher", "hardcover:urban fantasy", 500, hc="77"),        # same book, other source
        f("Defiance of the Fall", "JF Brink", "hardcover:LitRPG", 300, hc="88"),
    ]
    rows, added = merge([], found, seeds, target=3)
    assert added == 3 and [r["seed_id"] for r in rows] == ["C0001", "C0002", "C0003"]
    titles = [r["title"] for r in rows]
    assert "Storm Front" not in titles
    assert titles.count("Fool Moon") == 1
    fool = next(r for r in rows if r["title"] == "Fool Moon")
    assert fool["ol_work"] == "OL2W" and fool["hardcover_id"] == "77"     # both ids kept
    assert {r["found_via"].split(":")[0] for r in rows} == {"openlibrary", "hardcover"}  # round-robin


def test_merge_is_append_only_with_stable_ids():
    first, _ = merge([], [f("Fool Moon", "Jim Butcher", "openlibrary:Wizards", 80, ol="OL2W")], [], target=10)
    again, added = merge(first, [f("Fool Moon", "Jim Butcher", "openlibrary:Wizards", 80, ol="OL2W"),
                                 f("Grave Peril", "Jim Butcher", "openlibrary:Wizards", 70, ol="OL3W")], [], target=10)
    assert added == 1 and [r["seed_id"] for r in again] == ["C0001", "C0002"]


def test_importer_skips_known_work_without_requests():
    db, ol = fresh_db(), FakeOL()
    seeds = [{"seed_id": "C0001", "title": "Storm Front", "author": "Jim Butcher", "ol_work": "OL100W"}]
    ol_run(db, ol, seeds, dry_run=False)
    before = ol.requests_made
    res = ol_run(db, ol, seeds, dry_run=False)
    assert res[0].outcome == "already stored" and ol.requests_made == before
