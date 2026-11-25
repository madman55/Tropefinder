"""Tests for the taxonomy loader and checker. Run with: python -m pytest"""

from pipeline.taxonomy import load, validate


def test_real_taxonomy_is_valid():
    problems = validate(load())
    assert problems == [], "\n".join(problems)


def test_duplicate_slug_is_caught():
    tropes = [
        {"slug": "magic", "name": "Magic", "category": "abilities", "definition": "x"},
        {"slug": "magic", "name": "Magic again", "category": "abilities", "definition": "x"},
    ]
    assert any("used 2 times" in p for p in validate(tropes))


def test_missing_parent_is_caught():
    tropes = [{"slug": "a", "name": "A", "category": "abilities", "definition": "x", "parent": "nope"}]
    assert any("parent 'nope' does not exist" in p for p in validate(tropes))


def test_parent_loop_is_caught():
    tropes = [
        {"slug": "a", "name": "A", "category": "abilities", "definition": "x", "parent": "b"},
        {"slug": "b", "name": "B", "category": "abilities", "definition": "x", "parent": "a"},
    ]
    assert any("loops back" in p for p in validate(tropes))


def test_bad_category_and_slug_are_caught():
    tropes = [{"slug": "Bad Slug", "name": "B", "category": "romance", "definition": "x"}]
    problems = validate(tropes)
    assert any("unknown category" in p for p in problems)
    assert any("lowercase words" in p for p in problems)


def test_shared_alias_is_caught():
    tropes = [
        {"slug": "a", "name": "A", "category": "abilities", "definition": "x", "aliases": ["Guns"]},
        {"slug": "b", "name": "B", "category": "abilities", "definition": "x", "aliases": ["guns"]},
    ]
    assert any("used by both" in p for p in validate(tropes))


def test_unknown_conflict_is_caught():
    tropes = [{"slug": "a", "name": "A", "category": "abilities", "definition": "x", "conflicts": ["zzz"]}]
    assert any("conflicts 'zzz' does not exist" in p for p in validate(tropes))
