"""What a book's stored evidence looks like, and the word-for-word quote check."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.normalise import normalise_text

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


@dataclass
class BookText:
    """Everything we hold about a book that a verdict may cite."""
    work_id: str
    title: str
    description: str | None = None
    subjects: list[str] = field(default_factory=list)                 # Open Library subjects
    tags: list[tuple[str, str]] = field(default_factory=list)          # (tag_type, tag) from Hardcover
    description_source_id: str | None = None
    subjects_source_id: str | None = None
    tags_source_id: str | None = None


def _flat(text: str) -> str:
    return " ".join(text.translate(_QUOTES).split())


def quote_found(quote: str | None, field_name: str, book: BookText) -> bool:
    """True only if the quote really is in the stored text of the named field.

    Description: the quote must appear word-for-word (spacing and curly quotes ignored)
    and be at least 3 characters. Tag / subject: it must equal one of the stored values.
    """
    if not quote or len(quote.strip()) < 3:
        return False
    if field_name == "description":
        return bool(book.description) and _flat(quote).lower() in _flat(book.description).lower()
    if field_name == "tag":
        return normalise_text(quote) in {normalise_text(t) for _, t in book.tags}
    if field_name == "subject":
        return normalise_text(quote) in {normalise_text(s) for s in book.subjects}
    return False


def strip_html(text: str | None) -> str | None:
    if not text:
        return text
    return re.sub(r"<[^>]+>", " ", text)
