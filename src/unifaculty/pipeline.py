"""Orchestration: crawl -> prefilter -> LLM classify -> verify -> merge -> CSV, per university."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from unifaculty import __version__
from unifaculty.classify import Classifier, Outcome
from unifaculty.config import ResearchProfile, Settings, UniversityConfig
from unifaculty.crawler import CrawledPage, Crawler
from unifaculty.fetcher import Fetcher
from unifaculty.llm.base import LLMProvider
from unifaculty.logs import RunContext, current_university, get_logger, log_event
from unifaculty.models import PROFILE_LINK_COLUMNS, FacultyRecord, Rejection
from unifaculty.output import merge_records, publish_latest, write_records_csv, write_rejections_csv
from unifaculty.prefilter import should_classify
from unifaculty.ratelimit import HostRateLimiter, RequestsPerMinute
from unifaculty.tracing import Stats, span
from unifaculty.urls import slugify

log = get_logger("pipeline")


@dataclass
class UniversityResult:
    slug: str
    name: str
    csv_path: str | None
    kept: int
    rejected: int
    pages_fetched: int
    llm_calls: int
    blocked_hosts: dict[str, str]
    stats: dict
    interrupted: bool = False
    duration_s: float = 0.0
    sections: list[str] = field(default_factory=list)


def records_from_outcome(outcome: Outcome, page: CrawledPage, uni: UniversityConfig, provider: LLMProvider,
                         run_id: str) -> list[FacultyRecord]:
    d = outcome.decision
    assert d is not None
    records = []
    people = [p for p in d.people if (p.role or "supervisor") in ("supervisor", "contact")] or d.people
    for person in people:
        rec = FacultyRecord(
            university=uni.name, name=person.name, title_role=person.title or "",
            department=person.department or page.section, research_interests=person.research_interests,
            degree_levels=d.position.degree_levels, position_title=d.position.title or "",
            funding_status=d.funding.status, funding_source=d.funding.source or "",
            deadline=d.position.deadline or "", start_date=d.position.start_date or "",
            email=person.email or "", field_match_score=d.field_match.score,
            funding_confidence=d.funding_confidence, matched_topics=d.field_match.matched_topics,
            rationale=d.field_match.rationale,
            funding_evidence=[d.funding.evidence_quote] if d.funding.evidence_quote else [],
            position_evidence=[d.position.evidence_quote] if d.position.evidence_quote else [],
            source_urls=[page.final_url], llm_provider=provider.name, llm_model=provider.model,
            last_checked_utc=page.fetched_at, run_id=run_id,
        )
        # Profile links are only attached on single-person pages, where they clearly belong to that person.
        if len(people) == 1:
            for kind in PROFILE_LINK_COLUMNS:
                setattr(rec, kind, list(page.parsed.profile_links.get(kind, [])))
        records.append(rec)
    return records


def run_university(uni: UniversityConfig, profile: ResearchProfile, settings: Settings, ctx: RunContext,
                   fetcher: Fetcher, provider: LLMProvider, *, use_cache: bool = True,
                   limiter: HostRateLimiter | None = None, rpm: RequestsPerMinute | None = None,
                   today: date | None = None, output_dir: Path | None = None,
                   on_progress: Callable[[str], None] | None = None) -> UniversityResult:
    token = current_university.set(uni.slug)
    started = time.time()
    stats = Stats()
    sections = uni.select_sections(profile.department_tags)
    field_terms = profile.field_terms()
    crawler = Crawler(uni, fetcher, settings.crawl, stats, field_terms, limiter=limiter,
                      pages_dir=ctx.pages_dir / uni.slug if ctx.debug_dump else None)
    classifier = Classifier(provider, settings.llm, profile, ctx, stats, Path(settings.cache_dir),
                            today=today, use_cache=use_cache, rpm=rpm)
    records: list[FacultyRecord] = []
    rejections: list[Rejection] = []
    interrupted = False
    budget_warned = False
    csv_path: Path | None = None
    out_root = Path(output_dir or settings.output_dir)

    try:
        with span("university", log, level=logging.INFO, university_name=uni.name, sections=[s.name for s in sections],
                  max_pages=uni.max_pages, fetcher=fetcher.name, provider=provider.name, model=provider.model) as us:
            crawler.seed(sections)
            try:
                for page in crawler.crawl():
                    stats.inc("pages.parsed")
                    ok, why = should_classify(page.parsed, settings.crawl.prefilter, settings.crawl.min_text_chars)
                    log_event(log, logging.DEBUG, "prefilter.decision", url=page.final_url, kind=page.parsed.kind,
                              classify=ok, reason=why)
                    if not ok:
                        stats.inc("prefilter.skipped")
                        stats.reasons[f"prefilter:{why}"] += 1
                        sig = page.parsed.signals
                        if sig.has_position or sig.has_funding:
                            # A page that *looked* like an opportunity: keep an audit trail of why it was skipped.
                            rejections.append(Rejection(university=uni.name, url=page.final_url,
                                                        page_kind=page.parsed.kind, stage="prefilter", reasons=[why],
                                                        summary=page.parsed.title[:200]))
                        continue
                    stats.inc("prefilter.passed")
                    outcome = classifier.classify(page.parsed, uni.name)
                    if outcome.keep:
                        new = records_from_outcome(outcome, page, uni, provider, ctx.run_id)
                        records.extend(new)
                        stats.inc("candidates.kept", len(new))
                        for rec in new:
                            log_event(log, logging.INFO, "candidate.kept", name=rec.name, email=rec.email or None,
                                      funding=rec.funding_status, field_score=round(rec.field_match_score, 2),
                                      levels=rec.degree_levels, url=page.final_url)
                        if on_progress:
                            on_progress(f"kept {', '.join(r.name for r in new)}")
                    else:
                        stats.inc("candidates.rejected")
                        for reason in outcome.reasons:
                            stats.reasons[reason] += 1
                        d = outcome.decision
                        rejections.append(Rejection(
                            university=uni.name, url=page.final_url, page_kind=page.parsed.kind,
                            stage="llm" if d is None else "rules", reasons=outcome.reasons,
                            summary=(d.summary if d else (outcome.error or ""))[:300],
                            field_match_score=d.field_match.score if d else None,
                            funding_status=d.funding.status if d else None))
                        log_event(log, logging.DEBUG, "candidate.rejected", url=page.final_url, reasons=outcome.reasons)
                    if classifier.budget_left <= 0 and not budget_warned:
                        budget_warned = True
                        log_event(log, logging.WARNING, "llm.budget_exhausted", calls=classifier.calls,
                                  hint="raise llm.max_calls_per_run or narrow department_tags; crawl continues without LLM")
            except KeyboardInterrupt:
                interrupted = True
                log_event(log, logging.WARNING, "run.interrupted", hint="writing partial results")

            merged = merge_records(records)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            csv_path = write_records_csv(out_root / uni.slug / f"{uni.slug}__{slugify(profile.target_fields[0], 30)}__{stamp}.csv", merged)
            publish_latest(csv_path)
            if rejections:
                write_rejections_csv(ctx.run_dir / "rejected.csv", rejections)
            us.set(kept=len(merged), rejected=len(rejections), pages=crawler.pages_fetched,
                   llm_calls=classifier.calls, csv=str(csv_path), interrupted=interrupted or None,
                   blocked_hosts=crawler.guard_stopped() or None)
    finally:
        current_university.reset(token)

    return UniversityResult(slug=uni.slug, name=uni.name, csv_path=str(csv_path) if csv_path else None,
                            kept=len(merge_records(records)), rejected=len(rejections),
                            pages_fetched=crawler.pages_fetched, llm_calls=classifier.calls,
                            blocked_hosts=crawler.guard_stopped(), stats=stats.to_dict(), interrupted=interrupted,
                            duration_s=round(time.time() - started, 1), sections=[s.name for s in sections])


def write_summary(ctx: RunContext, results: list[UniversityResult], profile: ResearchProfile, settings: Settings,
                  provider: LLMProvider, fetcher_name: str) -> Path:
    summary = {
        "run_id": ctx.run_id, "tool_version": __version__,
        "started_utc": datetime.fromtimestamp(ctx.started_at, tz=timezone.utc).isoformat(timespec="seconds"),
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.time() - ctx.started_at, 1),
        "fetcher": fetcher_name, "llm": {"provider": provider.name, "model": provider.model},
        "prefilter": settings.crawl.prefilter,
        "profile": {"target_fields": profile.target_fields, "target_degree_levels": profile.target_degree_levels,
                    "min_field_match": profile.min_field_match, "min_funding_confidence": profile.min_funding_confidence},
        "universities": [r.__dict__ for r in results],
        "totals": {"kept": sum(r.kept for r in results), "rejected": sum(r.rejected for r in results),
                   "pages_fetched": sum(r.pages_fetched for r in results),
                   "llm_calls": sum(r.llm_calls for r in results)},
    }
    path = ctx.run_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
