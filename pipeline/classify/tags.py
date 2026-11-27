"""Layer 1: map reader tags and subject headings onto tropes.

A Hardcover tag or Open Library subject that equals a trope's name or one of its
aliases (ignoring case and punctuation) is evidence the trope is present.
Content warnings are not used: they describe the book's content, not its tropes.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.classify.evidence import BookText
from pipeline.normalise import normalise_text

USED_TAG_TYPES = {"genre", "tag", "mood"}


def singular(word: str) -> str:
    """Rough English singular, applied the same way to tags and trope names."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and (word.endswith("oes") or word.endswith("ses") or word.endswith("xes")
                          or word.endswith("ches") or word.endswith("shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and not word.endswith("us"):
        return word[:-1]
    return word


def match_key(text: str) -> str:
    """Normalised, singular form: 'Superheroes' and 'superhero' give the same key."""
    return " ".join(singular(w) for w in normalise_text(text).split())


@dataclass
class TagHit:
    trope_slug: str
    field: str        # 'tag' or 'subject'
    value: str        # the tag or subject as stored
    source_id: str | None


def build_lookup(tropes: list[dict], aliases: list[dict]) -> dict[str, str]:
    """normalised phrase -> trope slug, from active trope names and their aliases."""
    by_id = {t["id"]: t for t in tropes if t.get("is_active", True)}
    lookup: dict[str, str] = {}
    for t in by_id.values():
        lookup.setdefault(match_key(t["name"]), t["slug"])
        lookup.setdefault(match_key(t["slug"].replace("-", " ")), t["slug"])
    for a in aliases:
        t = by_id.get(a["trope_id"])
        if t:
            lookup.setdefault(match_key(a["alias"]), t["slug"])
    lookup.pop("", None)
    return lookup


def tag_hits(book: BookText, lookup: dict[str, str]) -> list[TagHit]:
    hits: dict[tuple[str, str], TagHit] = {}
    for tag_type, tag in book.tags:
        slug = lookup.get(match_key(tag))
        if slug and tag_type in USED_TAG_TYPES:
            hits.setdefault((slug, tag), TagHit(slug, "tag", tag, book.tags_source_id))
    for subject in book.subjects:
        slug = lookup.get(match_key(subject))
        if slug:
            hits.setdefault((slug, subject), TagHit(slug, "subject", subject, book.subjects_source_id))
    return list(hits.values())
