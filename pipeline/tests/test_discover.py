"""Tests for candidate discovery: filters, de-duplication, stable ids."""

from pipeline.db import FakeDB
from pipeline.discover import acceptable, merge
from pipeline.import_openlibrary import run as ol_run
from pipeline.tests.test_import_openlibrary import FakeOL, fresh_db


def test_filters_out_sets_and_guides():
    assert acceptable("Storm Front", "Jim Butcher")
    assert acceptable("Mother of Learning: Arc 1", "Domagoj Kurmaic")
    assert acceptable("1632", "Eric Flint") and acceptable("Fahrenheit 451", "Ray Bradbury")
    assert acceptable("Ascendant (Book 1): A Progression Fantasy", "EmergencyComplaints")
    for bad in ["The Dresden Files Box Set", "Cradle: Books 1-3", "Red Rising Omnibus",
                "Study Guide for Old Man's War", "Urban Fantasy Anthology", "The Art of Red Rising",
                "ONE PIECE 12", "Player Manager 2: A Sports Progression Fantasy", "Street Cultivation 3",
                "Campaign (Last Life Book #6): A Progression Fantasy Series", "Naked City: Tales of Urban Fantasy",
                "Time Travel: A History", "86―エイティシックス― (light novel)", "Grandmaster, Vol. 2",
                "I Got a Cheat Skill (manga)", "Grantville Gazette III", "Marvel Masterworks"]:
        assert not acceptable(bad, "Someone"), bad
    assert not acceptable("Untitled", None)
    assert not acceptable("Fine Title", "尾田栄一郎")
    assert not acceptable("X-Men", "Marvel Comics")
    assert not acceptable("Two Ghosts for Sister Rachel / Run, Run, Rudolph / Six", "Lynsay Sands")
    assert not acceptable("Doctor Who", "British Broadcasting Corporation")
    assert not acceptable("Novels", "H. G. Wells")


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


def test_merge_caps_books_per_author():
    found = [f(f"Discworld Title {c}", "Terry Pratchett", "openlibrary:Wizards", 10, ol=f"OL{c}W") for c in "ABCDE"]
    rows, added = merge([], found, [], target=10)
    assert added == 3


def test_excluded_books_are_never_added():
    found = [f("Czas pogardy", "Andrzej Sapkowski", "openlibrary:Magic", 50, ol="OL9W"),
             f("The Last Wish", "Andrzej Sapkowski", "openlibrary:Magic", 40, ol="OL8W")]
    rows, _ = merge([], found, [], target=10, excluded=[{"title": "Czas pogardy", "author": "Andrzej Sapkowski"}])
    assert [r["title"] for r in rows] == ["The Last Wish"]


def test_audit_finds_likely_duplicates():
    from pipeline.audit import likely_duplicates
    works = [{"id": "1", "title": "Storm Front"}, {"id": "2", "title": "Storm Front (Dresden Files #1)"},
             {"id": "3", "title": "Fool Moon"}, {"id": "4", "title": "Storm Front"}]
    authors = {"1": ["Jim Butcher"], "2": ["Jim Butcher"], "3": ["Jim Butcher"], "4": ["Someone Else"]}
    assert likely_duplicates(works, authors) == [["Storm Front", "Storm Front (Dresden Files #1)"]]
