from pipeline.db import FakeDB
from pipeline.embed import book_text, plan, run, vector_literal


class FakeEmbedder:
    def __init__(self):
        self.calls, self.seen = 0, []

    def embed(self, texts):
        self.calls += 1
        self.seen += texts
        assert len(texts) <= 8
        return [[0.1] * 384 for _ in texts]


def make_db():
    db = FakeDB()
    db.tables["works"] = [{"id": f"w{i}", "title": f"Book {i}", "description": "<p>A hero <b>rises</b>.</p>"} for i in range(10)]
    db.tables["tropes"] = [{"id": "t1", "slug": "superpowers", "name": "Superpowers", "definition": "Powers.", "is_active": True},
                           {"id": "t2", "slug": "old", "name": "Old", "definition": "x", "is_active": False}]
    db.tables["trope_aliases"] = [{"trope_id": "t1", "alias": "metahumans"}]
    db.tables["book_tropes"] = [{"work_id": "w1", "trope_id": "t1", "status": "present"},
                                {"work_id": "w2", "trope_id": "t1", "status": "absent"}]
    db.tables["work_source_tags"] = [{"work_id": "w1", "tag_type": "genre", "tag": "Superhero", "position": 1}]
    return db


def test_book_text_has_title_clean_description_tropes_and_tags():
    db = make_db()
    from pipeline.classify.run import load_books
    b = next(x for x in load_books(db) if x.work_id == "w1")
    t = book_text(b, ["Superpowers"])
    assert t.startswith("Book 1. A hero rises .") and "Tropes: Superpowers." in t and "Tags: Superhero." in t
    assert "<" not in t


def test_embeds_everything_once_then_only_changes():
    db = make_db()
    e = FakeEmbedder()
    run(db, e)
    assert len(db.tables["work_embeddings"]) == 10 and len(db.tables["trope_embeddings"]) == 1   # inactive trope skipped
    assert e.calls == 3   # 1 trope call + 10 books in batches of 8
    assert any("Tropes: Superpowers" in s for s in e.seen) and not any("Book 2" in s and "Tropes" in s for s in e.seen)
    assert plan(db) == ([], [])                      # nothing changed: nothing to do
    db.tables["works"][3]["description"] = "Now about dragons."
    books, tropes = plan(db)
    assert [b[0] for b in books] == ["w3"] and tropes == []
    run(db, e)
    assert len(db.tables["work_embeddings"]) == 10   # updated, not duplicated


def test_dry_run_calls_nothing_and_vector_format():
    db = make_db()
    assert "Books to embed: 10" in run(db, None)
    assert not db.tables.get("work_embeddings")
    assert vector_literal([0.5, -1]) == "[0.500000,-1.000000]"
