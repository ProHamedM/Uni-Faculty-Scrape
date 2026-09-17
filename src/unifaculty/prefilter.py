"""Decide which crawled pages are worth an LLM call (cost control only)."""

from __future__ import annotations

from unifaculty.extract import ParsedPage


def should_classify(page: ParsedPage, mode: str, min_text_chars: int = 300) -> tuple[bool, str]:
    s = page.signals
    if len(page.text) < min_text_chars:
        return False, "too_little_text"
    if page.kind == "listing":
        return False, "listing_page_follow_links_instead"
    if mode == "off":
        return True, "prefilter_off"
    if s.negative_funding_terms and not s.funding_terms:
        return False, "page_states_no_funding"
    if mode == "strict":
        if (s.has_position or s.has_funding) and s.field_terms:
            return True, "position_or_funding_and_field_terms"
        return False, "strict_needs_position_and_field_terms"
    # loose (default)
    if page.kind == "position" and (s.has_position or s.has_funding):
        return True, "position_page"
    if page.kind == "profile" and s.field_terms:
        return True, "profile_with_field_terms"
    if s.has_position and (s.has_funding or s.field_terms):
        return True, "position_terms_with_funding_or_field"
    if page.kind == "profile" and s.has_position:
        return True, "profile_mentions_openings"
    return False, "no_position_or_field_signal"
