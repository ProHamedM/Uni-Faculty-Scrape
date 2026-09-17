"""Deterministic offline stand-in for an LLM.

Used by the test-suite and by ``unifaculty run --llm mock`` to dry-run the whole
pipeline (crawl, logging, CSV) without an API key. It is intentionally simple
and NOT a substitute for real filtering quality.
"""

from __future__ import annotations

import json
import re

from unifaculty.llm.base import LLMProvider, LLMResult
from unifaculty.signals import FUNDING_TERMS, NEGATIVE_FUNDING_TERMS, POSITION_TERMS


def _section(user: str, start: str, end: str) -> str:
    m = re.search(re.escape(start) + r"\n(.*?)\n" + re.escape(end), user, re.S)
    return m.group(1) if m else ""


def _sentence_with(text: str, terms: list[str]) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        low = sentence.lower()
        if any(t in low for t in terms) and len(sentence) > 15:
            return sentence.strip()[:300]
    return None


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, model: str = "mock-1", **kw):
        super().__init__(model, **kw)

    def _complete(self, system: str, user: str, temperature: float, max_output_tokens: int) -> LLMResult:
        page = _section(user, "<<<PAGE_TEXT", "PAGE_TEXT>>>")
        meta = json.loads(_section(user, "<<<PAGE_META", "PAGE_META>>>") or "{}")
        profile = json.loads(_section(user, "<<<PROFILE", "PROFILE>>>") or "{}")
        low = page.lower()
        negative = _sentence_with(page, NEGATIVE_FUNDING_TERMS)
        funding_quote = None if negative else _sentence_with(page, FUNDING_TERMS)
        position_quote = _sentence_with(page, POSITION_TERMS)
        fields = [f.lower() for f in profile.get("target_fields", []) + profile.get("keywords", [])]
        matched = [f for f in fields if f in low]
        score = min(1.0, 0.35 * len(matched)) if matched else 0.1
        people = []
        name_match = re.search(r"(Prof\.?(?: Dr\.)?|Dr\.) +([A-Z][a-z]+(?: [A-Z][a-z]+){1,2})", page)
        if name_match:
            emails = meta.get("emails_on_page", [])
            people.append({"name": name_match.group(2), "title": name_match.group(1), "department": None,
                           "email": emails[0] if emails else None, "research_interests": matched,
                           "role": "supervisor"})
        levels = [lvl for lvl, words in {"phd": ["phd", "doctoral"], "postdoc": ["postdoc"], "master": ["master"]}.items()
                  if any(w in low for w in words)]
        decision = {
            "page_type": "position_ad" if position_quote else ("person_profile" if people else "other"),
            "people": people,
            "position": {"exists": bool(position_quote), "degree_levels": levels, "title": None,
                         "deadline": None, "start_date": None, "is_expired": False,
                         "evidence_quote": position_quote},
            "funding": {"status": "none" if negative else ("full" if funding_quote and "fully funded" in low
                                                           else ("partial" if funding_quote else "unclear")),
                        "source": None, "evidence_quote": negative or funding_quote},
            "field_match": {"score": score, "matched_topics": matched, "rationale": "keyword overlap (mock)"},
            "funding_confidence": 0.9 if funding_quote else 0.2,
            "summary": "mock decision",
        }
        return LLMResult(text=json.dumps(decision), provider=self.name, model=self.model,
                         input_tokens=len(user) // 4, output_tokens=200, finish_reason="stop")
