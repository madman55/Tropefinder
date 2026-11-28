"""Layer 2: Claude Haiku reads a book's stored text and judges each trope.

Rules given to the model, and enforced afterwards in code:
  * use ONLY the text provided, never outside knowledge of the book,
  * "present" and "absent" each need a quote copied exactly from that text,
  * "absent" only when the text explicitly rules the trope out,
  * otherwise "unknown".
Any quote not found word-for-word in the stored text is discarded, and a verdict
left without evidence becomes "unknown".
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import requests

from pipeline.classify.evidence import BookText, quote_found, strip_html

MODEL = "claude-haiku-5-5"
PROMPT_VERSION = "v4-2026-11-27"   # v4: taxonomy 0.4 (Special powers umbrella; narrower Superpowers)
API_URL = "https://api.anthropic.com/v1/messages"
BATCH_URL = "https://api.anthropic.com/v1/messages/batches"
BATCH_DISCOUNT = 0.5
# USD per million tokens (standard, non-batch), checked 2026-11-25.
PRICE_IN, PRICE_OUT = 0.10, 0.50

SYSTEM = """You classify books against a fixed list of story tropes for a book discovery site.

You will be given the ONLY evidence you may use: a book's description, its subject headings and reader tags. Judge each trope strictly from that evidence.

Rules:
- Do not use anything you may know about the book, its author or its series from elsewhere. If the evidence does not show it, it is "unknown".
- "present": the evidence clearly shows the trope is a significant part of the book. Give at least one quote.
- "absent": ONLY when the evidence states outright that the trope does not happen (for example "there is no romance"). A description covers only part of a book, so never infer absence from the setting, the genre or what the description happens to focus on: a story described as taking place in a dungeon may still begin on modern Earth. Silence is never "absent". Give the quote that states it.
- "unknown": everything else. Most tropes for most books will be unknown; that is expected and correct.
- Each quote must be copied EXACTLY, character for character, from one field: a phrase from the description, or one whole tag or subject exactly as listed. Name which field it came from.
- Tropes describe the book as a whole ("anywhere in the book"), except tropes in the protagonist category, which describe the main character only.
- confidence: 0.0 to 1.0, how strongly the quoted evidence supports your status.

Record your answer with the record_verdicts tool, with one entry for every trope in the list."""

TOOL = {
    "name": "record_verdicts",
    "description": "Record one verdict for every trope in the list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "trope": {"type": "string", "description": "the trope's slug"},
                        "status": {"type": "string", "enum": ["present", "absent", "unknown"]},
                        "confidence": {"type": "number"},
                        "evidence": {
                            "type": "array",
                            "maxItems": 2,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string", "enum": ["description", "tag", "subject"]},
                                    "quote": {"type": "string"},
                                },
                                "required": ["field", "quote"],
                            },
                        },
                    },
                    "required": ["trope", "status", "confidence"],
                },
            }
        },
        "required": ["verdicts"],
    },
}


@dataclass
class AIVerdict:
    trope_slug: str
    status: str
    confidence: float
    evidence: list[dict] = field(default_factory=list)   # verified {field, quote}
    rejected_quotes: int = 0


def trope_list_text(tropes: list[dict]) -> str:
    lines = []
    for t in tropes:
        hint = f" ({t['classifier_hint']})" if t.get("classifier_hint") else ""
        lines.append(f"- {t['slug']} [{t['category']}]: {t['definition']}{hint}")
    return "\n".join(lines)


def book_text(book: BookText) -> str:
    tags = "\n".join(f"- {tag_type}: {tag}" for tag_type, tag in book.tags) or "(none)"
    subjects = "\n".join(f"- {s}" for s in book.subjects) or "(none)"
    return (f"TITLE: {book.title}\n\nDESCRIPTION:\n{strip_html(book.description) or '(none)'}\n\n"
            f"SUBJECT HEADINGS:\n{subjects}\n\nREADER TAGS (type: tag):\n{tags}")


def verify(raw: list[dict], book: BookText, slugs: set[str]) -> list[AIVerdict]:
    """Keep only verdicts for known tropes, and only quotes found in the stored text."""
    out: dict[str, AIVerdict] = {}
    for v in raw:
        slug = v.get("trope")
        status = v.get("status")
        if slug not in slugs or status not in ("present", "absent", "unknown") or slug in out:
            continue
        try:
            confidence = min(1.0, max(0.0, float(v.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        good, rejected = [], 0
        for e in v.get("evidence") or []:
            # Tags are listed as "type: tag"; accept the model quoting either form.
            quote = e.get("quote", "")
            if e.get("field") == "tag" and ": " in quote and not quote_found(quote, "tag", book):
                quote = quote.split(": ", 1)[1]
            if quote_found(quote, e.get("field", ""), book):
                good.append({"field": e["field"], "quote": quote})
            else:
                rejected += 1
        if status in ("present", "absent") and not good:
            status, confidence = "unknown", 0.0
        out[slug] = AIVerdict(slug, status, confidence, good if status != "unknown" else [], rejected)
    return list(out.values())


class Claude:
    def __init__(self, api_key: str | None = None, model: str = MODEL, session=None):
        key = (api_key or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.model = model
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": key, "anthropic-version": "2023-06-01",
                                     "content-type": "application/json"})
        self.input_tokens = self.output_tokens = self.calls = 0
        self.billed_usd = 0.0

    @property
    def cost_usd(self) -> float:
        return round(self.billed_usd, 4)

    def body(self, book: BookText, tropes: list[dict]) -> dict:
        return {
            "model": self.model,
            "max_tokens": 16000,
            "system": SYSTEM,
            "tools": [TOOL],
            "tool_choice": {"type": "tool", "name": "record_verdicts"},
            "messages": [{"role": "user", "content":
                          f"TROPES:\n{trope_list_text(tropes)}\n\nBOOK EVIDENCE:\n{book_text(book)}"}],
        }

    def _verdicts(self, data: dict, price_factor: float = 1.0) -> list[dict]:
        verdicts = self._tool_input(data, price_factor).get("verdicts", [])
        if isinstance(verdicts, str):   # occasionally sent as a JSON string
            verdicts = json.loads(verdicts)
        return [v for v in verdicts if isinstance(v, dict)]

    def _tool_input(self, data: dict, price_factor: float = 1.0) -> dict:
        """Count the cost of one answer and return what it put into the forced tool."""
        self.calls += 1
        usage = data.get("usage", {})
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.billed_usd += (usage.get("input_tokens", 0) / 1e6 * PRICE_IN
                            + usage.get("output_tokens", 0) / 1e6 * PRICE_OUT) * price_factor
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input") or {}
        raise RuntimeError(f"No answer returned (stop_reason {data.get('stop_reason')})")

    def _send(self, method: str, url: str, **kw) -> dict:
        for attempt in range(6):
            r = self.session.request(method, url, timeout=180, **kw)
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(float(r.headers.get("retry-after", 10 * (attempt + 1))))
                continue
            if not r.ok:
                raise RuntimeError(f"Anthropic HTTP {r.status_code}: {r.text[:300]}")
            return r
        raise RuntimeError("Anthropic API kept failing")

    def classify(self, book: BookText, tropes: list[dict]) -> list[dict]:
        """One book, answered straight away (standard price)."""
        r = self._send("POST", API_URL, data=json.dumps(self.body(book, tropes)))
        return self._verdicts(r.json())

    def classify_batch(self, books: list[BookText], tropes: list[dict], poll_seconds: int = 60,
                       max_wait_seconds: int = 5 * 3600) -> dict[str, list[dict] | Exception]:
        """Many books through the Message Batches API (half price; usually done within an hour).
        Returns work_id -> verdicts, or the error for that book."""
        requests_ = [{"custom_id": b.work_id, "params": self.body(b, tropes)} for b in books]
        batch = self._send("POST", BATCH_URL, data=json.dumps({"requests": requests_})).json()
        print(f"Submitted batch {batch['id']} with {len(books)} books", flush=True)
        waited = 0
        while batch.get("processing_status") != "ended":
            if waited >= max_wait_seconds:
                raise RuntimeError(f"Batch {batch['id']} not finished after {waited // 60} minutes")
            time.sleep(poll_seconds)
            waited += poll_seconds
            batch = self._send("GET", f"{BATCH_URL}/{batch['id']}").json()
            print(f"  {waited // 60} min: {batch.get('request_counts')}", flush=True)
        out: dict[str, list[dict] | Exception] = {}
        lines = self._send("GET", batch["results_url"]).text.splitlines()
        for line in lines:
            if not line.strip():
                continue
            item = json.loads(line)
            result = item.get("result", {})
            try:
                if result.get("type") != "succeeded":
                    raise RuntimeError(f"batch item {result.get('type')}: {json.dumps(result.get('error'))[:200]}")
                out[item["custom_id"]] = self._verdicts(result["message"], price_factor=BATCH_DISCOUNT)
            except Exception as exc:
                out[item["custom_id"]] = exc
        return out
