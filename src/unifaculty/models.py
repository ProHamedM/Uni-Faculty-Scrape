"""Data shapes: the LLM's decision (validated) and the CSV record."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, field_validator


def _clamp(value: float | None) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


class Person(BaseModel):
    name: str
    title: str | None = None
    department: str | None = None
    email: str | None = None
    research_interests: list[str] = Field(default_factory=list)
    role: str | None = None


class Position(BaseModel):
    exists: bool = False
    degree_levels: list[str] = Field(default_factory=list)
    title: str | None = None
    deadline: str | None = None
    start_date: str | None = None
    is_expired: bool | None = None
    evidence_quote: str | None = None

    @field_validator("degree_levels", mode="before")
    @classmethod
    def _normalize_levels(cls, value):
        mapping = {"doctoral": "phd", "doctorate": "phd", "ph.d.": "phd", "ph.d": "phd", "predoc": "phd",
                   "masters": "master", "master's": "master", "msc": "master", "post-doc": "postdoc",
                   "postdoctoral": "postdoc"}
        out = []
        for item in value or []:
            key = str(item).strip().lower()
            key = mapping.get(key, key)
            if key in ("phd", "master", "postdoc") and key not in out:
                out.append(key)
        return out


class Funding(BaseModel):
    status: Literal["full", "partial", "none", "unclear"] = "unclear"
    source: str | None = None
    evidence_quote: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value):
        v = str(value or "unclear").strip().lower()
        return v if v in ("full", "partial", "none", "unclear") else "unclear"


class FieldMatch(BaseModel):
    score: float = 0.0
    matched_topics: list[str] = Field(default_factory=list)
    rationale: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, value):
        return _clamp(value)


class LLMDecision(BaseModel):
    page_type: str = "other"
    people: list[Person] = Field(default_factory=list)
    position: Position = Field(default_factory=Position)
    funding: Funding = Field(default_factory=Funding)
    field_match: FieldMatch = Field(default_factory=FieldMatch)
    funding_confidence: float = 0.0
    summary: str = ""

    @field_validator("funding_confidence", mode="before")
    @classmethod
    def _clamp_conf(cls, value):
        return _clamp(value)


CSV_COLUMNS = [
    "university", "name", "title_role", "department", "research_interests", "degree_levels",
    "position_title", "funding_status", "funding_source", "deadline", "start_date",
    "email", "linkedin", "github", "google_scholar", "orcid", "researchgate", "dblp",
    "semantic_scholar", "x", "bluesky", "field_match_score", "funding_confidence",
    "matched_topics", "rationale", "funding_evidence", "position_evidence", "source_urls",
    "llm_provider", "llm_model", "last_checked_utc", "run_id",
]

PROFILE_LINK_COLUMNS = ["linkedin", "github", "google_scholar", "orcid", "researchgate", "dblp", "semantic_scholar", "x", "bluesky"]


@dataclass
class FacultyRecord:
    university: str
    name: str
    title_role: str = ""
    department: str = ""
    research_interests: list[str] = field(default_factory=list)
    degree_levels: list[str] = field(default_factory=list)
    position_title: str = ""
    funding_status: str = ""
    funding_source: str = ""
    deadline: str = ""
    start_date: str = ""
    email: str = ""
    linkedin: list[str] = field(default_factory=list)
    github: list[str] = field(default_factory=list)
    google_scholar: list[str] = field(default_factory=list)
    orcid: list[str] = field(default_factory=list)
    researchgate: list[str] = field(default_factory=list)
    dblp: list[str] = field(default_factory=list)
    semantic_scholar: list[str] = field(default_factory=list)
    x: list[str] = field(default_factory=list)
    bluesky: list[str] = field(default_factory=list)
    field_match_score: float = 0.0
    funding_confidence: float = 0.0
    matched_topics: list[str] = field(default_factory=list)
    rationale: str = ""
    funding_evidence: list[str] = field(default_factory=list)
    position_evidence: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    llm_provider: str = ""
    llm_model: str = ""
    last_checked_utc: str = ""
    run_id: str = ""

    def to_row(self) -> dict[str, str]:
        row = {}
        for key, value in asdict(self).items():
            if isinstance(value, list):
                row[key] = " | ".join(str(v) for v in value if v)
            elif isinstance(value, float):
                row[key] = f"{value:.2f}"
            else:
                row[key] = "" if value is None else str(value)
        return row


@dataclass
class Rejection:
    university: str
    url: str
    page_kind: str
    stage: str          # prefilter | llm | verify | rules
    reasons: list[str]
    summary: str = ""
    field_match_score: float | None = None
    funding_status: str | None = None

    def to_row(self) -> dict[str, str]:
        return {"university": self.university, "url": self.url, "page_kind": self.page_kind, "stage": self.stage,
                "reasons": " | ".join(self.reasons), "summary": self.summary,
                "field_match_score": "" if self.field_match_score is None else f"{self.field_match_score:.2f}",
                "funding_status": self.funding_status or ""}
