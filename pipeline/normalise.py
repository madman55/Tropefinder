"""Text clean-up and matching helpers used to compare titles, authors and ISBNs."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

ISBN13 = re.compile(r"^97[89][0-9]{10}$")
ISBN10 = re.compile(r"^[0-9]{9}[0-9X]$")
YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2}|2100)\b")


def normalise_text(text: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse spaces. 'Kurmaić' -> 'kurmaic'."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def main_title(title: str | None) -> str:
    """The part before a subtitle: 'Super Powereds: Year 1' -> 'super powereds'."""
    if not title:
        return ""
    return normalise_text(re.split(r":| - | \(", title, maxsplit=1)[0])


def title_similarity(a: str, b: str) -> float:
    """0..1. Full titles compared, and main titles compared; the better score wins."""
    full = SequenceMatcher(None, normalise_text(a), normalise_text(b)).ratio()
    ma, mb = main_title(a), main_title(b)
    main = SequenceMatcher(None, ma, mb).ratio() if ma and mb else 0.0
    # A seed title equal to the other's full or main title is a strong match.
    if normalise_text(a) in (normalise_text(b), mb) or normalise_text(b) in (normalise_text(a), ma):
        return 1.0
    return max(full, main * 0.95)


def surname(name: str) -> str:
    tokens = normalise_text(name).split()
    return tokens[-1] if tokens else ""


def author_matches(seed_author: str, candidate_names: list[str] | None) -> bool:
    """True if the seed author's surname (or whole name) appears in any candidate name."""
    want_last = surname(seed_author)
    want_full = normalise_text(seed_author).replace(" ", "")
    for name in candidate_names or []:
        tokens = normalise_text(name).split()
        if want_last and want_last in tokens:
            return True
        if want_full and want_full == "".join(tokens):
            return True
    return False


def clean_isbn(value: str | None) -> tuple[str | None, str | None]:
    """Return (isbn13, isbn10) for a raw ISBN string; whichever is valid, else None."""
    if not value:
        return None, None
    raw = re.sub(r"[^0-9Xx]", "", value).upper()
    if ISBN13.match(raw):
        return raw, None
    if ISBN10.match(raw):
        return None, raw
    return None, None


def isbn10_to_13(isbn10: str) -> str:
    """Convert a valid ISBN-10 to its ISBN-13 form (978 prefix, new check digit)."""
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def first_isbn(values: list[str] | None, kind: str) -> str | None:
    for v in values or []:
        isbn13, isbn10 = clean_isbn(v)
        if kind == "13" and isbn13:
            return isbn13
        if kind == "10" and isbn10:
            return isbn10
    return None


def map_format(physical_format: str | None) -> str:
    """Map Open Library's free-text physical_format onto our format list."""
    f = normalise_text(physical_format)
    if not f:
        return "unknown"
    if "audio" in f or f in ("mp3 cd", "cd"):
        return "audiobook"
    if "ebook" in f or "electronic" in f or "kindle" in f or "epub" in f:
        return "ebook"
    if "hard" in f or "library binding" in f:
        return "hardcover"
    if "paper" in f or "soft" in f or "mass market" in f or "trade" in f:
        return "paperback"
    return "other"


def year_from(text: str | None) -> int | None:
    m = YEAR.search(text or "")
    return int(m.group(1)) if m else None
