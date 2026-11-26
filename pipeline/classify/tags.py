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
        lookup.setdefault(normalise_text(t["name"]), t["slug"])
        lookup.setdefault(normalise_text(t["slug"].replace("-", " ")), t["slug"])
    for a in aliases:
        t = by_id.get(a["trope_id"])
        if t:
            lookup.setdefault(normalise_text(a["alias"]), t["slug"])
    lookup.pop("", None)
    return lookup


def tag_hits(book: BookText, lookup: dict[str, str]) -> list[TagHit]:
    hits: dict[tuple[str, str], TagHit] = {}
    for tag_type, tag in book.tags:
        slug = lookup.get(normalise_text(tag))
        if slug and tag_type in USED_TAG_TYPES:
            hits.setdefault((slug, tag), TagHit(slug, "tag", tag, book.tags_source_id))
    for subject in book.subjects:
        slug = lookup.get(normalise_text(subject))
        if slug:
            hits.setdefault((slug, subject), TagHit(slug, "subject", subject, book.subjects_source_id))
    return list(hits.values())
