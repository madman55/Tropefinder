"""Tests for the Open Library importer, using made-up API responses shaped like
Open Library's (no network). Run with: python -m pytest"""

from pipeline.db import FakeDB
from pipeline.import_openlibrary import best_match, run
from pipeline.normalise import (author_matches, clean_isbn, isbn10_to_13, main_title, map_format,
                                normalise_text, title_similarity, year_from)


# ------------------------------------------------------------------ helpers

def test_normalise_and_titles():
    assert normalise_text("Kurmaić") == "kurmaic"
    assert normalise_text("Old Man's War") == "old man s war"
    assert main_title("Super Powereds: Year 1") == "super powereds"
    assert title_similarity("Super Powereds: Year 1", "Super Powereds") == 1.0
    assert title_similarity("Storm Front", "Storm Front (The Dresden Files, Book 1)") == 1.0
    assert title_similarity("Vicious", "Vengeful") < 0.88


def test_author_matching():
    assert author_matches("S. M. Stirling", ["S.M. Stirling"])
    assert author_matches("Domagoj Kurmaić", ["Domagoj Kurmaic"])
    assert author_matches("pirateaba", ["Pirateaba"])
    assert not author_matches("Jim Butcher", ["Jim Hines"])


def test_isbn_and_format_helpers():
    assert clean_isbn("978-0-451-52653-3") == ("9780451526533", None)
    assert clean_isbn("0-451-52653-8") == (None, "0451526538")
    assert clean_isbn("12345") == (None, None)
    assert isbn10_to_13("0451526538") == "9780451526533"
    assert map_format("Mass Market Paperback") == "paperback"
    assert map_format("Hardcover") == "hardcover"
    assert map_format("Audio CD") == "audiobook"
    assert map_format("ebook") == "ebook"
    assert map_format(None) == "unknown"
    assert year_from("May 15, 2000") == 2000


def test_best_match_needs_author_and_prefers_more_editions():
    docs = [
        {"key": "/works/OL1W", "title": "Storm Front", "author_name": ["Someone Else"], "edition_count": 99},
        {"key": "/works/OL2W", "title": "Storm Front", "author_name": ["Jim Butcher"], "edition_count": 3},
        {"key": "/works/OL3W", "title": "Storm Front", "author_name": ["Jim Butcher"], "edition_count": 40},
    ]
    doc, score = best_match(docs, "Storm Front", "Jim Butcher")
    assert doc["key"] == "/works/OL3W" and score == 1.0
    assert best_match(docs, "Storm Front", "Larry Correia") == (None, 0.0)


# ------------------------------------------------------------------ full runs

class FakeOL:
    """Answers like Open Library would, from fixed data."""

    def __init__(self):
        self.requests_made = 0
        self.fail_on = set()
        self.search_data = {
            "Storm Front": [{"key": "/works/OL100W", "title": "Storm Front", "author_name": ["Jim Butcher"],
                             "author_key": ["OL10A"], "first_publish_year": 2000, "edition_count": 3}],
            "Rivers of London": [{"key": "/works/OL200W", "title": "Rivers of London", "author_name": ["Ben Aaronovitch"],
                                  "author_key": ["OL20A"], "first_publish_year": 2011, "edition_count": 2}],
        }
        self.works = {
            "OL100W": {"title": "Storm Front", "description": {"type": "/type/text", "value": "Harry Dresden is a wizard."},
                       "subjects": ["Wizards", "Chicago (Ill.)", "Fiction"]},
            "OL200W": {"title": "Rivers of London", "description": "A police constable meets a ghost.", "subjects": []},
        }
        self.editions_data = {
            "OL100W": [
                {"key": "/books/OL1M", "isbn_13": ["9780451457813"], "physical_format": "Mass Market Paperback",
                 "publishers": ["Roc"], "publish_date": "April 2000", "number_of_pages": 322,
                 "languages": [{"key": "/languages/eng"}], "covers": [12345]},
                # Same book listed again with only its ISBN-10: must not create a second edition.
                {"key": "/books/OL2M", "isbn_10": ["0451457811"], "physical_format": "Paperback"},
                {"key": "/books/OL3M", "physical_format": "Audio CD", "publish_date": "2010"},
            ],
            "OL200W": [{"key": "/books/OL4M", "isbn_13": ["9780575097568"], "physical_format": "Hardcover"}],
        }

    def search(self, title, author=None, limit=10):
        self.requests_made += 1
        if title in self.fail_on:
            raise RuntimeError("simulated network failure")
        return self.search_data.get(title, [])

    def work(self, work_id):
        self.requests_made += 1
        return self.works[work_id]

    def editions(self, work_id, limit=50):
        self.requests_made += 1
        return self.editions_data[work_id]


SEEDS = [
    {"seed_id": "1", "title": "Storm Front", "author": "Jim Butcher"},
    {"seed_id": "2", "title": "Rivers of London", "author": "Ben Aaronovitch"},
    {"seed_id": "3", "title": "An Indie Book Not In The Catalogue", "author": "Nobody"},
]


def fresh_db():
    db = FakeDB()
    db.insert("data_sources", [{"slug": "openlibrary", "name": "Open Library"}])
    return db


def test_first_run_imports_and_reports_missing():
    db, ol = fresh_db(), FakeOL()
    results = run(db, ol, SEEDS, dry_run=False)
    assert [r.outcome for r in results] == ["imported", "imported", "not found"]
    assert len(db.tables["works"]) == 2
    storm = next(w for w in db.tables["works"] if w["title"] == "Storm Front")
    assert storm["description"] == "Harry Dresden is a wizard."
    assert storm["subjects"] == ["Wizards", "Chicago (Ill.)", "Fiction"]
    # 3 editions seen for Storm Front, but the ISBN-10 one is the same book as the ISBN-13 one.
    assert results[0].editions_seen == 3 and results[0].editions_added == 2
    assert len(db.tables["editions"]) == 3
    assert {e["format"] for e in db.tables["editions"]} == {"paperback", "audiobook", "hardcover"}
    run_row = db.tables["import_runs"][0]
    assert run_row["status"] == "succeeded" and run_row["counts"]["imported"] == 2


def test_second_run_adds_nothing():
    db, ol = fresh_db(), FakeOL()
    run(db, ol, SEEDS, dry_run=False)
    sizes = {t: len(rows) for t, rows in db.tables.items() if t != "import_runs"}
    results = run(db, ol, SEEDS, dry_run=False)
    assert [r.outcome for r in results] == ["unchanged", "unchanged", "not found"]
    assert sum(r.editions_added for r in results) == 0
    assert {t: len(rows) for t, rows in db.tables.items() if t != "import_runs"} == sizes


def test_changed_description_updates_work():
    db, ol = fresh_db(), FakeOL()
    run(db, ol, SEEDS, dry_run=False)
    ol.works["OL200W"]["description"] = "A new, longer description."
    results = run(db, ol, SEEDS, dry_run=False)
    assert results[1].outcome == "updated"
    assert len(db.tables["works"]) == 2


def test_dry_run_writes_nothing():
    db, ol = fresh_db(), FakeOL()
    writes_before = list(db.calls)
    results = run(db, ol, SEEDS, dry_run=True)
    assert [r.outcome for r in results] == ["would import", "would import", "not found"]
    assert db.calls == writes_before
    assert all(not rows for t, rows in db.tables.items() if t != "data_sources")


def test_same_book_under_new_id_is_flagged_not_duplicated():
    db, ol = fresh_db(), FakeOL()
    run(db, ol, SEEDS[:1], dry_run=False)
    ol.search_data["Storm Front"][0]["key"] = "/works/OL999W"   # Open Library re-keyed the work
    ol.works["OL999W"] = ol.works["OL100W"]
    ol.editions_data["OL999W"] = []
    results = run(db, ol, SEEDS[:1], dry_run=False)
    assert results[0].outcome == "needs review"
    assert len(db.tables["works"]) == 1


def test_one_failure_does_not_stop_the_run():
    db, ol = fresh_db(), FakeOL()
    ol.fail_on.add("Storm Front")
    results = run(db, ol, SEEDS, dry_run=False)
    assert [r.outcome for r in results] == ["error", "imported", "not found"]
    assert db.tables["import_runs"][0]["status"] == "partial"
