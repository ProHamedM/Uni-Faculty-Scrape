"""Typed configuration: settings, research profile and university files (YAML)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from unifaculty.compliance import MAX_PAGES_CEILING, MIN_DELAY_FLOOR
from unifaculty.urls import host_in_scope, host_of

DegreeLevel = Literal["phd", "master", "postdoc"]


class ConfigError(ValueError):
    pass


# --------------------------------------------------------------------------- universities
class Section(BaseModel):
    """A department, institute, doctoral school or job portal to crawl from."""

    name: str
    tags: list[str] = Field(default_factory=list)
    seeds: list[str] = Field(min_length=1)
    kind: Literal["department", "jobs", "doctoral_school", "group"] = "department"

    @field_validator("seeds")
    @classmethod
    def _http_only(cls, seeds: list[str]) -> list[str]:
        for seed in seeds:
            if not seed.startswith(("http://", "https://")):
                raise ValueError(f"seed must be an http(s) URL: {seed}")
        return seeds


class UniversityConfig(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,39}$")
    name: str
    country: str
    languages: list[str] = Field(default_factory=lambda: ["en"])
    allowed_domains: list[str] = Field(min_length=1)
    sections: list[Section] = Field(min_length=1)
    max_pages: int = Field(300, ge=1, le=MAX_PAGES_CEILING)
    max_depth: int = Field(3, ge=0, le=6)
    min_delay_seconds: float = Field(MIN_DELAY_FLOOR)
    seeds_verified: str | None = None
    notes: str = ""

    @field_validator("min_delay_seconds")
    @classmethod
    def _respect_floor(cls, value: float) -> float:
        # Config may slow the crawler down, never speed it up past the floor.
        return max(float(value), MIN_DELAY_FLOOR)

    @model_validator(mode="after")
    def _seeds_in_scope(self) -> "UniversityConfig":
        for section in self.sections:
            for seed in section.seeds:
                if not host_in_scope(host_of(seed), self.allowed_domains):
                    raise ValueError(f"seed {seed} is outside allowed_domains {self.allowed_domains}")
        return self

    def select_sections(self, tags: list[str]) -> list[Section]:
        if not tags:
            return list(self.sections)
        wanted = {t.lower() for t in tags}
        chosen = [s for s in self.sections if wanted & {t.lower() for t in s.tags} or s.kind in ("jobs", "doctoral_school")]
        return chosen or list(self.sections)


# --------------------------------------------------------------------------- research profile
class ResearchProfile(BaseModel):
    current_degree: str
    background: str = ""
    target_degree_levels: list[DegreeLevel] = Field(default_factory=lambda: ["phd"], min_length=1)
    target_fields: list[str] = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    exclude_topics: list[str] = Field(default_factory=list)
    department_tags: list[str] = Field(default_factory=list)
    min_field_match: float = Field(0.6, ge=0, le=1)
    min_funding_confidence: float = Field(0.6, ge=0, le=1)
    accept_partial_funding: bool = True
    accept_unclear_funding: bool = False

    def field_terms(self) -> list[str]:
        terms = [*self.target_fields, *self.keywords]
        seen: set[str] = set()
        out = []
        for term in terms:
            t = term.strip().lower()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
        return out


# --------------------------------------------------------------------------- settings
class LLMSettings(BaseModel):
    provider: Literal["gemini", "openrouter", "anthropic", "ollama", "openai_compat", "mock"] = "gemini"
    model: str | None = None
    base_url: str | None = None
    temperature: float = Field(0.0, ge=0, le=1)
    max_output_tokens: int = Field(2048, ge=256, le=16000)
    max_page_chars: int = Field(12000, ge=2000, le=60000)
    requests_per_minute: float = Field(10, gt=0, le=1000)
    max_calls_per_run: int = Field(150, ge=1, le=10000)
    timeout_seconds: float = Field(90, ge=5, le=600)
    max_retries: int = Field(4, ge=0, le=10)


class CrawlSettings(BaseModel):
    fetcher: Literal["curl_cffi", "httpx"] = "curl_cffi"
    impersonate: str = "chrome"
    timeout_seconds: float = Field(25, ge=3, le=120)
    max_bytes: int = Field(3_000_000, ge=50_000, le=20_000_000)
    prefilter: Literal["strict", "loose", "off"] = "loose"
    min_text_chars: int = Field(200, ge=0)


class Settings(BaseModel):
    llm: LLMSettings = Field(default_factory=LLMSettings)
    crawl: CrawlSettings = Field(default_factory=CrawlSettings)
    output_dir: str = "output"
    runs_dir: str = "runs"
    cache_dir: str = ".cache"
    universities_dir: str = "config/universities"


# --------------------------------------------------------------------------- loading
_SECRET_KEY_NAMES = re.compile(r"(?:^|[_-])(?:api[_-]?key|apikey|secret|password|passwd|token|access[_-]?token)$", re.I)


def _refuse_secrets(path: Path, text: str, data: dict) -> None:
    """API keys belong in environment variables / .env (git-ignored), never in YAML that may be committed."""
    from unifaculty.secretscan import scan_text

    found = scan_text(text, str(path))
    if found:
        raise ConfigError(f"{found[0]} — an API key must not be stored in a config file. Remove it, revoke it at "
                          "the provider if this file was ever committed or shared, and put the new key in .env.")

    def walk(node, trail=""):
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{trail}.{key}" if trail else str(key)
                if _SECRET_KEY_NAMES.search(str(key)) and value not in (None, ""):
                    raise ConfigError(f"{path}: '{here}' looks like a credential. Config files only take settings; "
                                      "put API keys in .env (see .env.example).")
                walk(value, here)
        elif isinstance(node, list):
            for item in node:
                walk(item, trail)

    walk(data)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    # utf-8-sig: Windows editors (e.g. Notepad) may save a byte-order mark.
    text = path.read_text(encoding="utf-8-sig")
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    _refuse_secrets(path, text, data)
    return data


def load_settings(path: Path | None) -> Settings:
    if path is None or not Path(path).exists():
        return Settings()
    data = _read_yaml(Path(path))
    try:
        return Settings.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_profile(path: Path) -> ResearchProfile:
    data = _read_yaml(Path(path))
    try:
        return ResearchProfile.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_university(path: Path) -> UniversityConfig:
    data = _read_yaml(Path(path))
    try:
        return UniversityConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


def load_universities(directory: Path) -> dict[str, UniversityConfig]:
    directory = Path(directory)
    if not directory.is_dir():
        raise ConfigError(f"universities directory not found: {directory}")
    out: dict[str, UniversityConfig] = {}
    for path in sorted(directory.glob("*.y*ml")):
        uni = load_university(path)
        if uni.slug in out:
            raise ConfigError(f"duplicate university slug '{uni.slug}' in {path}")
        out[uni.slug] = uni
    return out


def load_dotenv(path: Path = Path(".env")) -> list[str]:
    """Tiny .env loader (KEY=VALUE lines). Existing environment variables win.

    Returns the names (never the values) of variables it set.
    """
    loaded: list[str] = []
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
