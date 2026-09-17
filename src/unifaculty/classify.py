"""LLM classification + code-side verification + keep/drop rules.

The LLM proposes; the code verifies:
* every evidence quote must actually occur on the page,
* every person name must occur on the page,
* every email must be one the extractor found on the page,
* expired deadlines, off-field scores, missing funding evidence -> dropped.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from unifaculty.config import LLMSettings, ResearchProfile
from unifaculty.extract import ParsedPage
from unifaculty.llm.base import LLMError, LLMProvider, LLMResult
from unifaculty.logs import RunContext, get_logger, log_event
from unifaculty.models import LLMDecision
from unifaculty.ratelimit import RequestsPerMinute
from unifaculty.tracing import Stats, span

log = get_logger("classify")

PROMPT_VERSION = "classify_v1"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    return resources.files("unifaculty.prompts").joinpath(f"{version}.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- text helpers
def normalize_for_match(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_on_page(quote: str | None, page_text: str, threshold: float = 0.85) -> bool:
    """True if ``quote`` (normalized) appears on the page, allowing tiny LLM copy errors."""
    if not quote:
        return False
    q = normalize_for_match(quote).strip(" .\"'")
    t = normalize_for_match(page_text)
    if len(q) < 8:
        return False
    if q in t:
        return True
    matcher = difflib.SequenceMatcher(None, t, q, autojunk=False)
    match = matcher.find_longest_match(0, len(t), 0, len(q))
    if match.size / len(q) >= threshold:
        return True
    # Tolerate an ellipsis or small changes by checking long chunks.
    chunks = [c.strip() for c in re.split(r"\.\.\.|…", q) if len(c.strip()) >= 20]
    return bool(chunks) and all(c in t for c in chunks)


_TITLE_WORDS = re.compile(r"\b(prof|professor|professorin|dr|phd|ph\.d|habil|univ|mag|dipl|ing|rer|nat|mr|ms|mrs)\b\.?-?", re.I)


def name_on_page(name: str, page_norm: str) -> bool:
    """A person's name counts as on the page if, without academic titles, it appears there
    verbatim, or every part of it (2+ letters) appears as a word."""
    bare = normalize_for_match(_TITLE_WORDS.sub(" ", name or ""))
    bare = re.sub(r"\s+", " ", bare).strip(" .,")
    if len(bare) < 3:
        return False
    if bare in page_norm:
        return True
    parts = [p for p in re.split(r"[\s,.]+", bare) if len(p) >= 2]
    return len(parts) >= 2 and all(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", page_norm) for p in parts)


def extract_json_object(text: str) -> dict:
    """Parse the first JSON object in ``text`` (tolerates ```json fences and prose around it)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I | re.M)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(cleaned[start:i + 1])
                        if isinstance(value, dict):
                            return value
                    except json.JSONDecodeError:
                        break
        start = cleaned.find("{", start + 1)
    raise ValueError("no JSON object found in LLM output")


def truncate_page_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.8)
    return text[:head] + "\n[... truncated ...]\n" + text[-(limit - head):]


# --------------------------------------------------------------------------- outcome
@dataclass
class Verification:
    position_quote_ok: bool = False
    funding_quote_ok: bool = False
    dropped_people: list[str] = field(default_factory=list)
    dropped_emails: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    keep: bool
    reasons: list[str]
    decision: LLMDecision | None
    verification: Verification | None = None
    llm: LLMResult | None = None
    cached: bool = False
    error: str | None = None


def apply_rules(decision: LLMDecision, verification: Verification, profile: ResearchProfile,
                today: date) -> list[str]:
    """Return the list of reasons to reject (empty list = keep)."""
    reasons: list[str] = []
    pos, fund = decision.position, decision.funding
    if not pos.exists:
        reasons.append("no_open_position")
    else:
        if not verification.position_quote_ok:
            reasons.append("position_evidence_not_on_page")
        if not pos.degree_levels:
            reasons.append("degree_level_unknown")
        elif not set(pos.degree_levels) & set(profile.target_degree_levels):
            reasons.append("degree_level_not_targeted")
    if pos.is_expired:
        reasons.append("expired")
    if pos.deadline:
        try:
            if date.fromisoformat(pos.deadline[:10]) < today:
                reasons.append("expired")
        except ValueError:
            pass
    if fund.status == "none":
        reasons.append("no_funding")
    elif fund.status == "partial" and not profile.accept_partial_funding:
        reasons.append("partial_funding_not_accepted")
    elif fund.status == "unclear":
        if not profile.accept_unclear_funding:
            reasons.append("funding_unclear")
    if fund.status in ("full", "partial") and not verification.funding_quote_ok:
        reasons.append("funding_evidence_not_on_page")
    if fund.status in ("full", "partial") and decision.funding_confidence < profile.min_funding_confidence:
        reasons.append("low_funding_confidence")
    if decision.field_match.score < profile.min_field_match:
        reasons.append("off_field")
    excluded = [t.lower() for t in profile.exclude_topics]
    topics = " ".join(decision.field_match.matched_topics + [i for p in decision.people for i in p.research_interests]).lower()
    if excluded and any(t in topics for t in excluded):
        reasons.append("excluded_topic")
    if not decision.people:
        reasons.append("no_person_identified")
    seen: list[str] = []
    return [r for r in reasons if not (r in seen or seen.append(r))]


def verify(decision: LLMDecision, page: ParsedPage) -> Verification:
    v = Verification()
    v.position_quote_ok = quote_on_page(decision.position.evidence_quote, page.text)
    v.funding_quote_ok = quote_on_page(decision.funding.evidence_quote, page.text)
    page_norm = normalize_for_match(f"{page.title}\n{page.text}")
    allowed_emails = {e.lower() for e in page.emails}
    kept = []
    for person in decision.people:
        if not name_on_page(person.name, page_norm):
            v.dropped_people.append(person.name)
            continue
        if person.email and person.email.strip().lower() not in allowed_emails:
            v.dropped_emails.append(person.email)
            person.email = None
        kept.append(person)
    decision.people = kept
    return v


# --------------------------------------------------------------------------- classifier
class Classifier:
    def __init__(self, provider: LLMProvider, settings: LLMSettings, profile: ResearchProfile, ctx: RunContext,
                 stats: Stats, cache_dir: Path | None, today: date | None = None, use_cache: bool = True,
                 rpm: RequestsPerMinute | None = None):
        self.provider = provider
        self.settings = settings
        self.profile = profile
        self.ctx = ctx
        self.stats = stats
        self.cache_dir = Path(cache_dir) / "llm" if cache_dir else None
        self.use_cache = use_cache and self.cache_dir is not None
        self.today = today or date.today()
        self.rpm = rpm or RequestsPerMinute(settings.requests_per_minute)
        self.system_prompt = load_prompt()
        self.calls = 0
        self._profile_json = json.dumps(profile.model_dump(include={
            "current_degree", "background", "target_degree_levels", "target_fields", "keywords", "exclude_topics"}),
            ensure_ascii=False)

    @property
    def budget_left(self) -> int:
        return self.settings.max_calls_per_run - self.calls

    def build_user_message(self, page: ParsedPage, university: str) -> str:
        meta = {"url": page.url, "university": university, "title": page.title, "page_kind_hint": page.kind,
                "emails_on_page": page.emails[:25], "profile_links": page.profile_links,
                "keyword_signals": page.signals.as_dict()}
        return (f"TODAY: {self.today.isoformat()}\n\n"
                f"<<<PROFILE\n{self._profile_json}\nPROFILE>>>\n\n"
                f"<<<PAGE_META\n{json.dumps(meta, ensure_ascii=False)}\nPAGE_META>>>\n\n"
                f"<<<PAGE_TEXT\n{truncate_page_text(page.text, self.settings.max_page_chars)}\nPAGE_TEXT>>>\n")

    def _cache_key(self, user: str) -> str:
        h = hashlib.sha256()
        for part in (PROMPT_VERSION, self.provider.name, self.provider.model, self.system_prompt, user):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def classify(self, page: ParsedPage, university: str) -> Outcome:
        user = self.build_user_message(page, university)
        key = self._cache_key(user)
        cached = self._cache_get(key)
        llm_result: LLMResult | None = None
        with span("llm.classify", log, level=logging.INFO, url=page.url, kind=page.kind,
                  provider=self.provider.name, model=self.provider.model) as s:
            if cached is not None:
                raw_text = cached["text"]
                s.set(cache="hit")
                self.stats.inc("llm.cache_hits")
            else:
                if self.budget_left <= 0:
                    s.set(skipped="budget_exhausted")
                    self.stats.inc("llm.skipped_budget")
                    return Outcome(False, ["llm_budget_exhausted"], None)
                self.rpm.acquire()
                try:
                    llm_result = self.provider.complete_json(self.system_prompt, user,
                                                             temperature=self.settings.temperature,
                                                             max_output_tokens=self.settings.max_output_tokens)
                except LLMError as exc:
                    self.calls += 1
                    self.stats.inc("llm.errors")
                    s.set(error=str(exc)[:300], hint=exc.hint)
                    s.promote(logging.ERROR)
                    return Outcome(False, ["llm_error"], None, error=str(exc))
                self.calls += 1
                self.stats.inc("llm.calls")
                self.stats.timing("llm", llm_result.latency_ms)
                self.stats.tokens["input"] += llm_result.input_tokens
                self.stats.tokens["output"] += llm_result.output_tokens
                raw_text = llm_result.text
                s.set(cache="miss", latency_ms=llm_result.latency_ms, input_tokens=llm_result.input_tokens,
                      output_tokens=llm_result.output_tokens, finish=llm_result.finish_reason,
                      attempts=llm_result.attempts)

            try:
                decision = LLMDecision.model_validate(extract_json_object(raw_text))
            except (ValueError, ValidationError) as exc:
                self.stats.inc("llm.invalid_json")
                s.set(parse_error=str(exc)[:300])
                s.promote(logging.WARNING)
                self._write_call_log(page, user, raw_text, llm_result, None, None, ["invalid_llm_output"], key)
                return Outcome(False, ["invalid_llm_output"], None, llm=llm_result, error=str(exc))

            if cached is None:
                self._cache_put(key, raw_text)
            verification = verify(decision, page)
            reasons = apply_rules(decision, verification, self.profile, self.today)
            keep = not reasons
            s.set(keep=keep, reasons=reasons or None, funding=decision.funding.status,
                  field_score=round(decision.field_match.score, 2), funding_conf=round(decision.funding_confidence, 2),
                  position=decision.position.exists, people=[p.name for p in decision.people],
                  dropped_people=verification.dropped_people or None,
                  dropped_emails=verification.dropped_emails or None,
                  quotes_ok={"position": verification.position_quote_ok, "funding": verification.funding_quote_ok})
            if verification.dropped_people or verification.dropped_emails:
                log_event(log, logging.WARNING, "verify.hallucination_blocked", url=page.url,
                          dropped_people=verification.dropped_people, dropped_emails=verification.dropped_emails)
            self._write_call_log(page, user, raw_text, llm_result, decision, verification, reasons, key)
            return Outcome(keep, reasons, decision, verification, llm_result, cached=cached is not None)

    # ------------------------------------------------------------------ persistence
    def _cache_get(self, key: str) -> dict | None:
        if not self.use_cache:
            return None
        path = self.cache_dir / key[:2] / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def _cache_put(self, key: str, text: str) -> None:
        if not self.use_cache:
            return
        path = self.cache_dir / key[:2] / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"prompt_version": PROMPT_VERSION, "provider": self.provider.name,
                                    "model": self.provider.model, "text": text}, ensure_ascii=False), encoding="utf-8")

    def _write_call_log(self, page: ParsedPage, user: str, raw_text: str, result: LLMResult | None,
                        decision: LLMDecision | None, verification: Verification | None, reasons: list[str],
                        key: str) -> None:
        record = {
            "url": page.url, "page_kind": page.kind, "prompt_version": PROMPT_VERSION,
            "provider": self.provider.name, "model": self.provider.model, "cache_key": key,
            "cached": result is None, "latency_ms": result.latency_ms if result else None,
            "tokens": {"input": result.input_tokens, "output": result.output_tokens} if result else None,
            "decision": decision.model_dump() if decision else None,
            "verification": verification.__dict__ if verification else None,
            "keep": decision is not None and not reasons, "reasons": reasons,
            "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
        }
        if self.ctx.trace:
            record["system_prompt"] = self.system_prompt
            record["user_message"] = user
            record["raw_response"] = raw_text
        else:
            record["raw_response_preview"] = raw_text[:500]
        self.ctx.llm_dir.mkdir(parents=True, exist_ok=True)
        name = f"{self.calls:04d}-{hashlib.sha1(page.url.encode()).hexdigest()[:10]}.json"
        from unifaculty.logs import redact
        (self.ctx.llm_dir / name).write_text(redact(json.dumps(record, ensure_ascii=False, indent=2, default=str)),
                                             encoding="utf-8")
