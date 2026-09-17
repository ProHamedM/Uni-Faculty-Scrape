"""Cheap keyword signals (English + German) used to rank links and pre-filter pages.

These only decide *what is worth an LLM call*. The keep/drop decision about
funding and field fit is made by the LLM and verified in code (classify.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

POSITION_TERMS = [
    # English
    "phd position", "ph.d. position", "doctoral position", "doctoral candidate", "doctoral researcher",
    "phd student position", "phd candidate", "open position", "open positions", "vacancy", "vacancies",
    "job opening", "we are hiring", "we are looking for", "looking for phd", "looking for motivated",
    "postdoc position", "postdoctoral position", "postdoctoral researcher", "research assistant",
    "research associate", "studentship", "predoc", "pre-doc", "prae doc", "praedoc", "university assistant",
    "join our group", "join our lab", "prospective students", "call for applications", "apply now",
    # German
    "stellenangebot", "stellenangebote", "stellenausschreibung", "ausschreibung", "offene stellen",
    "doktorand", "doktorandin", "doktorand*in", "promotionsstelle", "promotionsstellen",
    "wissenschaftliche mitarbeiterin", "wissenschaftlicher mitarbeiter", "wissenschaftliche*r mitarbeiter*in",
    "universitätsassistent", "universitätsassistentin", "prae-doc", "praedoc-stelle",
]

FUNDING_TERMS = [
    "fully funded", "full funding", "funded position", "funded phd", "funding is available",
    "funding available", "scholarship", "stipend", "fellowship", "salary", "tuition waiver",
    "tuition and stipend", "grant-funded", "funded by the", "erc grant", "erc starting grant",
    "dfg", "fwf", "horizon europe", "marie curie", "msca", "nsf grant", "research assistantship",
    "teaching assistantship", "paid position", "employment contract",
    "vergütung", "entgeltgruppe", "e 13", "e13", "tv-l", "tv-g-u", "tvöd", "stipendium",
    "finanziert", "drittmittel", "kollektivvertrag", "gehalt", "vollzeit", "teilzeit", "75%", "65%",
]

NEGATIVE_FUNDING_TERMS = [
    "no funding", "self-funded", "self funded", "bring your own funding", "own funding",
    "not funded", "unfunded", "without funding", "keine finanzierung", "externally funded only",
]

PEOPLE_PATH_TERMS = [
    "people", "person", "faculty", "staff", "team", "members", "profile", "profiles", "directory",
    "professors", "professor", "researchers", "our-group", "~", "personen", "mitarbeiter",
    "mitarbeitende", "professuren", "professur", "lehrstuhl", "supervisors", "who-we-are",
]

RESEARCH_PATH_TERMS = [
    "research", "group", "groups", "lab", "labs", "institute", "chair", "forschung", "arbeitsgruppe",
    "arbeitsgruppen", "forschungsgruppe", "forschungsgruppen", "subeinheiten", "projects", "projekte",
]

JOBS_PATH_TERMS = [
    "jobs", "job", "careers", "career", "positions", "position", "vacancies", "openings", "join",
    "stellen", "stellenangebote", "stellenportal", "ausschreibungen", "karriere", "promotion", "phd",
    "doctoral", "admissions", "apply", "pooled-calls", "calls",
]

LOW_VALUE_PATH_TERMS = [
    "login", "logout", "signin", "sign-in", "cart", "calendar", "events", "event", "news", "press",
    "archive", "print", "sitemap", "search", "suche", "tag", "tags", "feed", "rss", "impressum",
    "datenschutz", "privacy", "cookie", "accessibility", "barrierefreiheit", "alumni", "giving",
    "donate", "shop", "wp-admin", "wp-login", "share", "download", "gallery",
]

DEGREE_TERMS = {
    "phd": ["phd", "ph.d", "doctoral", "doctorate", "doktorand", "promotion", "predoc", "pre-doc", "prae doc", "praedoc"],
    "master": ["master's", "masters", "msc", "m.sc", "master student", "masterarbeit", "master thesis"],
    "postdoc": ["postdoc", "post-doc", "postdoctoral", "post-doctoral"],
}


def _compile_terms(terms: list[str]) -> re.Pattern[str]:
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![\w])(" + "|".join(escaped) + r")(?![\w])", re.IGNORECASE)


_POSITION_RE = _compile_terms(POSITION_TERMS)
_FUNDING_RE = _compile_terms(FUNDING_TERMS)
_NEGATIVE_RE = _compile_terms(NEGATIVE_FUNDING_TERMS)


@dataclass
class TextSignals:
    position_terms: list[str] = field(default_factory=list)
    funding_terms: list[str] = field(default_factory=list)
    negative_funding_terms: list[str] = field(default_factory=list)
    degree_levels: list[str] = field(default_factory=list)
    field_terms: list[str] = field(default_factory=list)

    @property
    def has_position(self) -> bool:
        return bool(self.position_terms)

    @property
    def has_funding(self) -> bool:
        return bool(self.funding_terms)

    def as_dict(self) -> dict:
        return {"position": self.position_terms[:8], "funding": self.funding_terms[:8],
                "negative_funding": self.negative_funding_terms[:5], "degree": self.degree_levels,
                "field": self.field_terms[:8]}


def _unique_lower(matches: list[str]) -> list[str]:
    seen: list[str] = []
    for m in matches:
        m = m.lower()
        if m not in seen:
            seen.append(m)
    return seen


def find_field_terms(text: str, terms: list[str]) -> list[str]:
    if not terms or not text:
        return []
    return _unique_lower(_compile_terms(terms).findall(text))


def text_signals(text: str, field_terms: list[str] | None = None) -> TextSignals:
    lowered = text.lower()
    degrees = [level for level, words in DEGREE_TERMS.items() if any(w in lowered for w in words)]
    return TextSignals(
        position_terms=_unique_lower(_POSITION_RE.findall(text)),
        funding_terms=_unique_lower(_FUNDING_RE.findall(text)),
        negative_funding_terms=_unique_lower(_NEGATIVE_RE.findall(text)),
        degree_levels=degrees,
        field_terms=find_field_terms(text, field_terms or []),
    )


def _tokens(s: str) -> set[str]:
    return set(t for t in re.split(r"[^a-z0-9~äöüß]+", s.lower()) if t)


def score_link(url: str, anchor: str, field_terms: list[str]) -> int:
    """Crawl priority for a discovered link (higher = sooner)."""
    tokens = _tokens(url) | _tokens(anchor)
    text = f"{url} {anchor}".lower()
    score = 0
    if tokens & set(JOBS_PATH_TERMS) or _POSITION_RE.search(anchor or ""):
        score += 6
    if tokens & set(PEOPLE_PATH_TERMS) or "/~" in url:
        score += 4
    if tokens & set(RESEARCH_PATH_TERMS):
        score += 3
    if field_terms and any(term in text for term in field_terms):
        score += 3
    if tokens & set(LOW_VALUE_PATH_TERMS):
        score -= 5
    return score
