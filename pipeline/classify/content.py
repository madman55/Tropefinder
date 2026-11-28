"""Content check: who a book is for, whether it's a story, and whether it's explicit.

One short AI question per book (title, author-free description, reader tags), plus plain
tag rules. The answer decides two things on the works table:
  hidden_reason  'children' (picture books, early readers), 'nonfiction', 'companion'
                 (guides, art books, encyclopedias): kept in the catalogue, left out of search.
  adult_content  explicit sexual content: left out of search unless the reader asks.
Middle-grade and young-adult books stay visible.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.classify.ai import Claude
from pipeline.classify.evidence import BookText, strip_html

CHECK_VERSION = "content-v1-2026-11-28"

SYSTEM = """You sort books for a science-fiction and fantasy discovery site. From the book's title,
description and reader tags only, answer four questions with the record_content_check tool.
- audience: who the book is written for. picture_book = illustrated books for young children;
  early_reader = chapter books for roughly ages 5-8; middle_grade = roughly ages 8-12;
  young_adult = teens; adult = adults; unclear = the text doesn't say.
- kind: story = a novel, novella or story collection (including graphic novels);
  nonfiction = about real things (science, history, essays, how-to, biography);
  companion = a guide, encyclopedia, art book, quiz or other companion to a fictional series;
  other = anything else (poetry, games, scripts).
- explicit: yes only if the description or tags make clear the book contains explicit sexual
  content (e.g. tags like Erotica, Smut, Explicit, Harem with sexual content, or a description
  promising steamy scenes); no if it clearly doesn't; unclear otherwise. Violence alone is not
  explicit. Romance alone is not explicit.
- reason: one short sentence pointing at the words that decided it.
Be conservative: when the text doesn't make it clear, answer unclear or the most ordinary option
(adult, story, no)."""

TOOL = {
    "name": "record_content_check",
    "description": "Record the content check for this book.",
    "input_schema": {
        "type": "object",
        "properties": {
            "audience": {"type": "string",
                         "enum": ["picture_book", "early_reader", "middle_grade", "young_adult", "adult", "unclear"]},
            "kind": {"type": "string", "enum": ["story", "nonfiction", "companion", "other"]},
            "explicit": {"type": "string", "enum": ["yes", "no", "unclear"]},
            "reason": {"type": "string"},
        },
        "required": ["audience", "kind", "explicit", "reason"],
    },
}

# Reader tags that on their own mark a book as adult (explicit sexual content).
ADULT_TAGS = {"erotica", "erotic", "erotic romance", "smut", "explicit", "explicit sex", "sexual content",
              "harem", "reverse harem", "steamy", "spicy", "adult content", "porn", "bdsm"}
# Reader tags that on their own mark a book as for young children.
CHILD_TAGS = {"picture book", "picture books", "board book", "early reader", "early readers",
              "beginning reader", "easy reader"}


@dataclass
class ContentResult:
    audience: str
    content_kind: str
    adult_content: bool
    hidden_reason: str | None
    note: str


def book_prompt(book: BookText) -> str:
    tags = ", ".join(tag for _t, tag in book.tags[:40]) or "(none)"
    desc = " ".join(strip_html(book.description or "").split())[:2500] or "(none)"
    subjects = ", ".join(book.subjects[:20]) or "(none)"
    return f"TITLE: {book.title}\n\nDESCRIPTION: {desc}\n\nREADER TAGS: {tags}\n\nSUBJECT HEADINGS: {subjects}"


class ContentChecker(Claude):
    """Same connection, cost counting and batch handling as the trope classifier."""

    def body(self, book: BookText, _unused=None) -> dict:
        return {"model": self.model, "max_tokens": 400, "system": SYSTEM, "tools": [TOOL],
                "tool_choice": {"type": "tool", "name": "record_content_check"},
                "messages": [{"role": "user", "content": book_prompt(book)}]}

    def _verdicts(self, data: dict, price_factor: float = 1.0) -> dict:   # type: ignore[override]
        return self._tool_input(data, price_factor)


def decide(book: BookText, answer: dict | None, protected: bool = False) -> ContentResult:
    """Combine the AI answer with tag rules. protected: one of Joseph's labelled books (never hidden)."""
    tags = {tag.strip().lower() for _t, tag in book.tags}
    answer = answer or {}
    audience = answer.get("audience") if answer.get("audience") in TOOL["input_schema"]["properties"]["audience"]["enum"] else "unclear"
    kind = answer.get("kind") if answer.get("kind") in ("story", "nonfiction", "companion", "other") else "story"
    adult_tags = sorted(tags & ADULT_TAGS)
    adult = answer.get("explicit") == "yes" or bool(adult_tags)
    hidden = None
    if audience in ("picture_book", "early_reader") or (tags & CHILD_TAGS and audience != "adult"):
        hidden = "children"
    elif kind == "nonfiction":
        hidden = "nonfiction"
    elif kind == "companion":
        hidden = "companion"
    if protected:
        hidden = None
    note = (answer.get("reason") or "")[:300]
    if adult_tags:
        note = (note + f" [adult tags: {', '.join(adult_tags)}]").strip()
    return ContentResult(audience, kind, adult, hidden, note)
