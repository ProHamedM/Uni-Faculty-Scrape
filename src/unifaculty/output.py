"""Merge records and write CSV files (Excel-friendly, injection-safe)."""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path

from unifaculty.models import CSV_COLUMNS, PROFILE_LINK_COLUMNS, FacultyRecord, Rejection

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: str) -> str:
    """Neutralize spreadsheet formula injection (OWASP CSV injection)."""
    if value and value.startswith(_FORMULA_START) and not re.fullmatch(r"-?\d+(\.\d+)?", value):
        return "'" + value
    return value


def _merge_list(a: list, b: list) -> list:
    out = list(a)
    for item in b:
        if item and item not in out:
            out.append(item)
    return out


def _name_key(name: str) -> str:
    name = re.sub(r"\b(prof|dr|professor|univ|habil|phd|ph\.d|mag|dipl|ing|rer|nat)\b\.?", " ", name.lower())
    return re.sub(r"[^a-zäöüß]+", " ", name).strip()


def merge_records(records: list[FacultyRecord]) -> list[FacultyRecord]:
    merged: dict[str, FacultyRecord] = {}
    for rec in records:
        key = rec.email.lower() if rec.email else f"name:{_name_key(rec.name)}"
        # Also join a name-only record with an existing email record for the same person.
        if key.startswith("name:"):
            for k, existing in merged.items():
                if _name_key(existing.name) == key[5:]:
                    key = k
                    break
        if key not in merged:
            merged[key] = rec
            continue
        cur = merged[key]
        for attr in ("title_role", "department", "position_title", "funding_source", "deadline", "start_date", "email"):
            if not getattr(cur, attr) and getattr(rec, attr):
                setattr(cur, attr, getattr(rec, attr))
        rank = {"full": 3, "partial": 2, "unclear": 1, "none": 0, "": -1}
        if rank.get(rec.funding_status, -1) > rank.get(cur.funding_status, -1):
            cur.funding_status = rec.funding_status
        cur.field_match_score = max(cur.field_match_score, rec.field_match_score)
        cur.funding_confidence = max(cur.funding_confidence, rec.funding_confidence)
        for attr in ["research_interests", "degree_levels", "matched_topics", "funding_evidence",
                     "position_evidence", "source_urls", *PROFILE_LINK_COLUMNS]:
            setattr(cur, attr, _merge_list(getattr(cur, attr), getattr(rec, attr)))
        if rec.rationale and rec.rationale not in cur.rationale:
            cur.rationale = f"{cur.rationale} / {rec.rationale}" if cur.rationale else rec.rationale
    return sorted(merged.values(), key=lambda r: (-r.field_match_score, -r.funding_confidence, r.name))


def write_records_csv(path: Path, records: list[FacultyRecord]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow({k: safe_cell(v) for k, v in rec.to_row().items()})
    return path


def write_rejections_csv(path: Path, rejections: list[Rejection]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["university", "url", "page_kind", "stage", "reasons", "summary", "field_match_score", "funding_status"]
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8-sig" if not exists else "utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        for rej in rejections:
            writer.writerow({k: safe_cell(v) for k, v in rej.to_row().items()})
    return path


def publish_latest(csv_path: Path) -> Path:
    latest = csv_path.parent / "latest.csv"
    shutil.copyfile(csv_path, latest)
    return latest
