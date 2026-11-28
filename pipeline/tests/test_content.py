from pipeline.classify.content import ContentChecker, decide
from pipeline.classify.evidence import BookText
from pipeline.check_content import run
from pipeline.db import FakeDB


def book(tags=(), title="X"):
    return BookText("w", title, "desc", [], list(tags), None, None, "hc")


def test_picture_books_nonfiction_and_guides_are_hidden():
    assert decide(book(), {"audience": "picture_book", "kind": "story", "explicit": "no", "reason": "r"}).hidden_reason == "children"
    assert decide(book(), {"audience": "adult", "kind": "nonfiction", "explicit": "no", "reason": "r"}).hidden_reason == "nonfiction"
    assert decide(book(), {"audience": "young_adult", "kind": "companion", "explicit": "no", "reason": "r"}).hidden_reason == "companion"
    for aud in ("middle_grade", "young_adult", "adult", "unclear"):
        assert decide(book(), {"audience": aud, "kind": "story", "explicit": "no", "reason": "r"}).hidden_reason is None


def test_adult_from_ai_or_tags_and_protected_books_never_hidden():
    assert decide(book(), {"audience": "adult", "kind": "story", "explicit": "yes", "reason": "steamy"}).adult_content
    r = decide(book([("tag", "Harem"), ("genre", "LitRPG")]), {"audience": "adult", "kind": "story", "explicit": "unclear", "reason": ""})
    assert r.adult_content and "harem" in r.note
    assert not decide(book([("genre", "Romance")]), {"audience": "adult", "kind": "story", "explicit": "no", "reason": ""}).adult_content
    assert decide(book([("tag", "Picture Books")]), {"audience": "unclear", "kind": "story", "explicit": "no", "reason": ""}).hidden_reason == "children"
    assert decide(book(), {"audience": "picture_book", "kind": "nonfiction", "explicit": "no", "reason": ""}, protected=True).hidden_reason is None
    assert decide(book(), {"audience": "nonsense", "kind": "??", "explicit": "no", "reason": ""}).audience == "unclear"


class FakeChecker(ContentChecker):
    def __init__(self, answers):
        self.answers, self.calls, self.billed_usd = answers, 0, 0.0
        self.model = "m"

    def classify(self, b, _):
        self.calls += 1
        if self.answers.get(b.title) == "boom":
            raise RuntimeError("bad answer")
        return self.answers.get(b.title, {"audience": "adult", "kind": "story", "explicit": "no", "reason": "ok"})


def test_run_stores_flags_skips_checked_and_survives_failures():
    db = FakeDB()
    db.tables["works"] = [{"id": "a", "title": "Little Robots"}, {"id": "b", "title": "Storm Front"},
                          {"id": "c", "title": "Broken"}]
    db.tables["external_ids"] = []
    c = FakeChecker({"Little Robots": {"audience": "picture_book", "kind": "story", "explicit": "no", "reason": "toddlers"},
                     "Broken": "boom"})
    text = run(db, c)
    w = {x["title"]: x for x in db.tables["works"]}
    assert w["Little Robots"]["hidden_reason"] == "children" and w["Storm Front"]["hidden_reason"] is None
    assert "content_checked" not in w["Broken"] and "Failed: 1" in text
    c2 = FakeChecker({})
    run(db, c2)
    assert c2.calls == 1          # only the failed one is retried
    assert "nothing stored" in run(FakeDB(), None)
