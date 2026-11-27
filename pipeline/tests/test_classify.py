"""Tests for the trope engine: quote checking, tag mapping, combining layers, scoring."""

from pipeline.classify.ai import verify
from pipeline.classify.evidence import BookText, quote_found
from pipeline.classify.run import combine, score
from pipeline.classify.tags import build_lookup, tag_hits

BOOK = BookText("w1", "Storm Front",
                "Harry Dresden is the only wizard in the Chicago phone book. He works with the police “special investigations” unit.",
                ["Wizards", "Chicago (Ill.)"], [("genre", "Urban Fantasy"), ("tag", "Magic"), ("content_warning", "Violence")],
                "src-ol", "src-ol", "src-hc")


def test_quote_must_really_be_there():
    assert quote_found("the only wizard in the Chicago phone book", "description", BOOK)
    assert quote_found('police "special investigations" unit', "description", BOOK)   # curly quotes ignored
    assert quote_found("only  wizard\nin the", "description", BOOK)                     # spacing ignored
    assert not quote_found("Harry is a soldier", "description", BOOK)
    assert not quote_found("ab", "description", BOOK)
    assert quote_found("magic", "tag", BOOK) and not quote_found("Romance", "tag", BOOK)
    assert quote_found("Wizards", "subject", BOOK)
    assert not quote_found("Wizards", "tag", BOOK)                                      # wrong field


def test_verify_drops_invented_quotes_and_unknown_tropes():
    raw = [
        {"trope": "magic", "status": "present", "confidence": 0.9,
         "evidence": [{"field": "description", "quote": "the only wizard"}]},
        {"trope": "soldier", "status": "present", "confidence": 0.9,
         "evidence": [{"field": "description", "quote": "a decorated marine"}]},           # not in text
        {"trope": "romance", "status": "absent", "confidence": 0.7, "evidence": []},     # silence isn't absent
        {"trope": "made-up-trope", "status": "present", "confidence": 1, "evidence": []},
        {"trope": "detective", "status": "present", "confidence": 2,
         "evidence": [{"field": "tag", "quote": "genre: Urban Fantasy"}]},              # "type: tag" form accepted
    ]
    out = {v.trope_slug: v for v in verify(raw, BOOK, {"magic", "soldier", "romance", "detective"})}
    assert out["magic"].status == "present"
    assert out["soldier"].status == "unknown" and out["soldier"].rejected_quotes == 1
    assert out["romance"].status == "unknown"
    assert "made-up-trope" not in out
    assert out["detective"].confidence == 1.0 and out["detective"].evidence[0]["quote"] == "Urban Fantasy"


def test_tag_mapping_uses_names_and_aliases_not_content_warnings():
    tropes = [{"id": "t1", "slug": "magic", "name": "Magic", "is_active": True},
              {"id": "t2", "slug": "urban-setting", "name": "Modern city setting", "is_active": True},
              {"id": "t3", "slug": "dark-tone", "name": "Dark tone", "is_active": True}]
    aliases = [{"trope_id": "t2", "alias": "urban fantasy"}, {"trope_id": "t3", "alias": "violence"}]
    hits = {(h.trope_slug, h.field) for h in tag_hits(BOOK, build_lookup(tropes, aliases))}
    assert ("magic", "tag") in hits and ("urban-setting", "tag") in hits
    assert not any(slug == "dark-tone" for slug, _ in hits)


def test_combine_rules():
    from pipeline.classify.ai import AIVerdict
    from pipeline.classify.tags import TagHit
    hits = [TagHit("magic", "tag", "Magic", "hc"), TagHit("romance", "tag", "Romance", "hc")]
    ai = [AIVerdict("magic", "present", 0.95, [{"field": "description", "quote": "wizard"}]),
          AIVerdict("romance", "absent", 0.6, [{"field": "description", "quote": "no romance"}]),
          AIVerdict("detective", "present", 0.7, [{"field": "description", "quote": "police"}]),
          AIVerdict("harem", "unknown", 0.0)]
    v = {x.trope_slug: x for x in combine(hits, ai, "ai")}
    assert (v["magic"].status, v["magic"].verification, v["magic"].confidence) == ("present", "source_tag", 0.95)
    assert v["romance"].status == "unknown" and v["romance"].conflict and len(v["romance"].evidence) == 2
    assert (v["detective"].status, v["detective"].verification) == ("present", "ai_suggested")
    assert "harem" not in v


def test_ai_only_absent_is_not_stored():
    from pipeline.classify.ai import AIVerdict
    v = combine([], [AIVerdict("academy-setting", "absent", 0.9, [{"field": "description", "quote": "no school"}])], "ai")
    assert v == []


def test_score():
    gold = [{"seed_id": "1", "title": "A", "trope_slug": "magic", "label": "present"},
            {"seed_id": "1", "title": "A", "trope_slug": "romance", "label": "absent"},
            {"seed_id": "1", "title": "A", "trope_slug": "soldier", "label": "present"},
            {"seed_id": "1", "title": "A", "trope_slug": "harem", "label": "unknown"},
            {"seed_id": "2", "title": "B", "trope_slug": "magic", "label": "present"}]
    s = score({("w1", "magic"): "present", ("w1", "romance"): "present"}, gold, {"1": "w1"})
    assert (s["tp"], s["fp"], s["fn"]) == (1, 1, 1)
    assert s["precision_present"] == 0.5 and s["recall_present"] == 0.5
    assert s["gold_unknown"] == 1 and s["missing_books"] == {"B"}
    assert ("A", "romance", "present", "absent") in s["disagreements"]


def test_verdicts_sent_as_a_json_string_are_read():
    import json
    from pipeline.classify.ai import Claude

    class Resp:
        ok, status_code, headers = True, 200, {}
        def json(self):
            return {"usage": {}, "content": [{"type": "tool_use", "input": {"verdicts": json.dumps(
                [{"trope": "magic", "status": "unknown", "confidence": 0}, "junk"])}}]}

    class Session:
        headers = {}
        def request(self, *a, **k):
            return Resp()

    c = Claude(api_key="x", session=Session())
    assert c.classify(BOOK, [{"slug": "magic", "category": "abilities", "definition": "d"}]) == \
        [{"trope": "magic", "status": "unknown", "confidence": 0}]


def test_batch_results_are_matched_to_books_and_half_price():
    import json
    from pipeline.classify.ai import Claude

    msg = {"usage": {"input_tokens": 1000000, "output_tokens": 0},
           "content": [{"type": "tool_use", "input": {"verdicts": [{"trope": "magic", "status": "unknown", "confidence": 0}]}}]}

    class Resp:
        ok, status_code, headers = True, 200, {}
        def __init__(self, body=None, text=""):
            self._b, self.text = body, text
        def json(self):
            return self._b

    class Session:
        headers = {}
        def request(self, method, url, **k):
            if method == "POST":
                return Resp({"id": "b1", "processing_status": "ended", "results_url": "https://r"})
            return Resp(text="\n".join([
                json.dumps({"custom_id": "w1", "result": {"type": "succeeded", "message": msg}}),
                json.dumps({"custom_id": "w2", "result": {"type": "errored", "error": {"type": "overloaded"}}})]))

    c = Claude(api_key="x", session=Session())
    other = BookText("w2", "Other")
    out = c.classify_batch([BOOK, other], [{"slug": "magic", "category": "abilities", "definition": "d"}], poll_seconds=0)
    assert out["w1"] == [{"trope": "magic", "status": "unknown", "confidence": 0}]
    assert isinstance(out["w2"], Exception)
    assert c.cost_usd == 0.05   # 1M input tokens at $0.10, halved


def test_tags_match_plurals_and_aliases():
    from pipeline.classify.tags import match_key
    assert match_key("Superheroes") == match_key("superhero")
    assert match_key("Aliens") == match_key("alien")
    assert match_key("Mysteries") == match_key("mystery")
    assert match_key("Dragons & Mythical Creatures") == match_key("dragon and mythical creature")
    assert match_key("Progress") == "progress" and match_key("Fantasy") == "fantasy"
    tropes = [{"id": "t1", "slug": "superhero", "name": "Superhero protagonist", "is_active": True},
              {"id": "t2", "slug": "urban-setting", "name": "Modern city setting", "is_active": True}]
    aliases = [{"trope_id": "t1", "alias": "superheroes"}, {"trope_id": "t2", "alias": "urban fantasy"}]
    book = BookText("w", "X", None, [], [("genre", "Superhero"), ("genre", "Urban Fantasy")], None, None, "hc")
    assert {h.trope_slug for h in tag_hits(book, build_lookup(tropes, aliases))} == {"superhero", "urban-setting"}
