"""Tests for Hardcover enrichment, using made-up search results shaped like Hardcover's."""

from pipeline.db import FakeDB
from pipeline.enrich_hardcover import pick_hit, run
from pipeline.sources.hardcover import as_list


def test_as_list_flattens_strings_and_objects():
    assert as_list(["Fantasy", {"tag": "LitRPG"}, {"name": "Dark"}, "", None]) == ["Fantasy", "LitRPG", "Dark"]


def test_isbn_match_beats_popularity():
    docs = [{"id": 1, "title": "Storm Front", "author_names": ["Jim Butcher"], "isbns": [], "users_count": 900},
            {"id": 2, "title": "Storm Front", "author_names": ["Jim Butcher"], "isbns": ["0451457811"], "users_count": 5}]
    doc, how = pick_hit(docs, "Storm Front", "Jim Butcher", {"9780451457813"})  # same book as ISBN-10 0451457811
    assert doc["id"] == 2 and how == "isbn"


def test_wrong_author_is_rejected():
    docs = [{"id": 1, "title": "Vicious", "author_names": ["Someone Else"], "isbns": []}]
    assert pick_hit(docs, "Vicious", "V. E. Schwab", set()) == (None, "")


class FakeHC:
    def __init__(self):
        self.requests_made = 0
        self.data = {
            "Storm Front Jim Butcher": [{
                "id": 101, "slug": "storm-front", "title": "Storm Front", "author_names": ["Jim Butcher"],
                "isbns": ["9780451457813"], "users_count": 5000, "description": "HC description.",
                "genres": ["Fantasy", "Urban Fantasy"], "moods": ["adventurous"], "tags": ["Wizards", "Detective"],
                "content_warnings": ["Violence"], "series_names": ["The Dresden Files"],
                "featured_series": {"position": 1, "series": {"name": "The Dresden Files"}},
                "has_audiobook": True, "has_ebook": True}],
            "Super Powereds: Year 1 Drew Hayes": [{
                "id": 202, "slug": "super-powereds-year-1", "title": "Super Powereds: Year 1",
                "author_names": ["Drew Hayes"], "isbns": ["9781234567897"], "release_year": 2014,
                "description": "Students with powers.", "genres": ["Superhero"], "tags": ["Academy"],
                "has_audiobook": True, "has_ebook": False}],
        }

    def search_books(self, text):
        self.requests_made += 1
        return [dict(d) for d in self.data.get(text, [])]


SEEDS = [{"seed_id": "9", "title": "Storm Front", "author": "Jim Butcher"},
         {"seed_id": "15", "title": "Super Powereds: Year 1", "author": "Drew Hayes"},
         {"seed_id": "99", "title": "Unknown Book", "author": "Nobody"}]


def db_with_storm_front():
    db = FakeDB()
    db.insert("data_sources", [{"slug": "openlibrary"}, {"slug": "hardcover"}])
    w = db.insert("works", [{"title": "Storm Front", "normalised_title": "storm front", "description": None}])[0]
    a = db.insert("authors", [{"name": "Jim Butcher", "normalised_name": "jim butcher"}])[0]
    db.insert("work_authors", [{"work_id": w["id"], "author_id": a["id"]}])
    db.insert("editions", [{"work_id": w["id"], "isbn_13": "9780451457813"}])
    for t in ("external_ids", "work_source_tags", "format_availability", "series", "work_series"):
        db.tables.setdefault(t, [])
    return db, w["id"]


def test_enrich_existing_and_create_missing():
    db, wid = db_with_storm_front()
    results = run(db, FakeHC(), SEEDS, dry_run=False)
    assert [r.outcome for r in results] == ["enriched", "created", "not found"]
    assert results[0].match == "isbn" and results[0].description_added
    storm = next(w for w in db.tables["works"] if w["id"] == wid)
    assert storm["description"] == "HC description."
    tags = {(t["tag_type"], t["tag"]) for t in db.tables["work_source_tags"] if t["work_id"] == wid}
    assert ("genre", "Urban Fantasy") in tags and ("content_warning", "Violence") in tags
    assert {f["format"] for f in db.tables["format_availability"] if f["work_id"] == wid} == {"audiobook", "ebook"}
    assert db.tables["series"][0]["name"] == "The Dresden Files"
    assert db.tables["work_series"][0]["position"] == 1
    sp = next(w for w in db.tables["works"] if w["title"] == "Super Powereds: Year 1")
    hardcover_id = next(d["id"] for d in db.tables["data_sources"] if d["slug"] == "hardcover")
    assert sp["description_source_id"] == hardcover_id
    assert {f["format"] for f in db.tables["format_availability"] if f["work_id"] == sp["id"]} == {"audiobook"}


def test_second_run_changes_nothing():
    db, _ = db_with_storm_front()
    run(db, FakeHC(), SEEDS, dry_run=False)
    sizes = {t: len(r) for t, r in db.tables.items() if t != "import_runs"}
    results = run(db, FakeHC(), SEEDS, dry_run=False)
    assert [r.outcome for r in results] == ["unchanged", "unchanged", "not found"]
    assert {t: len(r) for t, r in db.tables.items() if t != "import_runs"} == sizes


def test_existing_description_is_not_overwritten():
    db, wid = db_with_storm_front()
    db.update("works", {"id": wid}, {"description": "Open Library description."})
    run(db, FakeHC(), SEEDS[:1], dry_run=False)
    assert next(w for w in db.tables["works"] if w["id"] == wid)["description"] == "Open Library description."


def test_removed_tag_is_removed():
    db, wid = db_with_storm_front()
    hc = FakeHC()
    run(db, hc, SEEDS[:1], dry_run=False)
    hc.data["Storm Front Jim Butcher"][0]["tags"] = ["Wizards"]
    r = run(db, hc, SEEDS[:1], dry_run=False)[0]
    assert r.outcome == "enriched" and r.tags_removed == 1


def test_dry_run_writes_nothing():
    db, _ = db_with_storm_front()
    before = list(db.calls)
    results = run(db, FakeHC(), SEEDS, dry_run=True)
    assert [r.outcome for r in results] == ["would enrich", "would create", "not found"]
    assert db.calls == before


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.ok = status < 400
        self.text = __import__("json").dumps(body)
        self.headers = {}

    def json(self):
        return self._body


class _Session:
    """Rejects any authorization header that doesn't start with 'Bearer '."""
    def __init__(self):
        self.headers, self.sent = {}, []

    def post(self, url, json=None, headers=None, timeout=None):
        auth = headers["authorization"]
        self.sent.append(auth)
        if not auth.startswith("Bearer "):
            return _Resp(400, {"error": "invalid_request", "error_description": "Invalid Authorization format."})
        return _Resp(200, {"data": {"search": {"results": {"hits": [{"document": {"id": 1, "title": "X"}}]}}}})


def test_token_without_bearer_is_retried_with_it(tmp_path):
    from pipeline.sources.hardcover import Hardcover
    session = _Session()
    hc = Hardcover(token="abc123", cache_dir=tmp_path, min_interval=0, session=session)
    assert hc.search_books("x") == [{"id": 1, "title": "X"}]
    assert session.sent == ["abc123", "Bearer abc123"]
    hc.search_books("y")
    assert session.sent[-1] == "Bearer abc123"   # remembers the working form


def test_exact_title_beats_sequel_sharing_an_isbn():
    # A source mixed one of Arc 2's ISBNs into Arc 1's record.
    docs = [{"id": 2, "title": "Mother of Learning: ARC 2", "author_names": ["Domagoj Kurmaic"],
             "isbns": ["9780000000002"], "users_count": 900},
            {"id": 1, "title": "Mother of Learning", "author_names": ["Domagoj Kurmaic"],
             "isbns": ["9780000000001"], "users_count": 800}]
    doc, _ = pick_hit(docs, "Mother of Learning", "Domagoj Kurmaić", {"9780000000002"})
    assert doc["id"] == 1
