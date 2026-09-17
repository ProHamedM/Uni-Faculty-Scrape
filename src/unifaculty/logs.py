"""Logging, tracing context and secret redaction.

Every run gets its own folder::

    runs/<run_id>/
        run.log        human-readable, DEBUG and above
        events.jsonl   one JSON object per event (TRACE and above with --trace)
        llm/           one JSON file per LLM call
        pages/         raw HTML snapshots (only with --debug-dump)
        rejected.csv   every candidate the filter dropped, with reasons
        summary.json   counts, status codes, timings, token usage

Events are logged with :func:`log_event` using a dotted event name
(``fetch.done``, ``robots.disallowed``, ``llm.call``) plus structured fields,
so the same call feeds the console, ``run.log`` and ``events.jsonl``.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

LOGGER_NAME = "unifaculty"
TRACE = 5
logging.addLevelName(TRACE, "TRACE")
# Library default: stay silent until setup_logging() configures handlers.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())

# Context shared by every log record emitted inside a span / university run.
current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span", default=None)
parent_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("parent_span", default=None)
current_university: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_university", default=None)
current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_run_id", default=None)

SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)

_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),            # Google API keys
    re.compile(r"sk-or-v1-[0-9A-Za-z]{20,}"),           # OpenRouter
    re.compile(r"sk-ant-[0-9A-Za-z_\-]{20,}"),          # Anthropic
    re.compile(r"\bsk-[0-9A-Za-z_\-]{20,}"),             # OpenAI-style
    re.compile(r"(?i)(bearer\s+)[0-9A-Za-z._\-]{16,}"),  # Authorization headers
    re.compile(r"(?i)(x-(?:goog-)?api-key[\"']?\s*[:=]\s*[\"']?)[0-9A-Za-z._\-]{16,}"),
]


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def redact(text: str) -> str:
    """Remove API keys and bearer tokens from any string."""
    if not text:
        return text
    for env in SECRET_ENV_VARS:
        value = os.environ.get(env)
        if value and len(value) >= 8:
            text = text.replace(value, f"<redacted:{env}>")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: f"{m.group(1)}<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: _redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_redact_obj(v) for v in obj]
    return obj


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Log a structured event. ``event`` is a dotted name, fields are key/values."""
    if logger.isEnabledFor(level):
        exc_info = fields.pop("exc_info", None)
        logger.log(level, event, extra={"uf_fields": fields}, exc_info=exc_info)


class ContextFilter(logging.Filter):
    """Attach run/university/span ids to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.uf_run_id = current_run_id.get()
        record.uf_university = current_university.get()
        record.uf_span = current_span.get()
        record.uf_parent = parent_span.get()
        if not hasattr(record, "uf_fields"):
            record.uf_fields = {}
        return True


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _utc_iso(record.created),
            "level": record.levelname,
            "event": record.getMessage(),
            "logger": record.name,
            "run_id": getattr(record, "uf_run_id", None),
            "university": getattr(record, "uf_university", None),
            "span_id": getattr(record, "uf_span", None),
            "parent_span_id": getattr(record, "uf_parent", None),
        }
        fields = getattr(record, "uf_fields", {}) or {}
        payload.update({k: v for k, v in fields.items() if k not in payload})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(_redact_obj(payload), ensure_ascii=False, default=str))


class TextFormatter(logging.Formatter):
    """``14:05:12.345 INFO  [stanford] fetch.done url=... status=200 elapsed_ms=812``"""

    COLORS: ClassVar[dict[str, str]] = {"TRACE": "\033[90m", "DEBUG": "\033[36m", "INFO": "\033[32m",
                                        "WARNING": "\033[33m", "ERROR": "\033[31m", "CRITICAL": "\033[1;31m"}
    RESET = "\033[0m"

    def __init__(self, color: bool = False, field_limit: int = 160, show_span: bool = False):
        super().__init__()
        self.color = color
        self.field_limit = field_limit
        self.show_span = show_span

    def format(self, record: logging.LogRecord) -> str:
        t = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.") + f"{int(record.msecs):03d}"
        level = record.levelname
        lvl = f"{level:<7}"
        if self.color:
            lvl = f"{self.COLORS.get(level, '')}{lvl}{self.RESET}"
        uni = getattr(record, "uf_university", None)
        parts = [t, lvl]
        if uni:
            parts.append(f"[{uni}]")
        if self.show_span and getattr(record, "uf_span", None):
            parts.append(f"<{record.uf_span}>")
        parts.append(record.getMessage())
        fields = getattr(record, "uf_fields", {}) or {}
        for key, value in fields.items():
            if value is None or value == "" or value == [] or value == {}:
                continue
            parts.append(f"{key}={_short(value, self.field_limit)}")
        line = " ".join(parts)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return redact(line)


@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    trace: bool = False
    debug_dump: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def llm_dir(self) -> Path:
        return self.run_dir / "llm"

    @property
    def pages_dir(self) -> Path:
        return self.run_dir / "pages"


def new_run_id(label: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40] or "run"
    return f"{stamp}-{safe}-{secrets.token_hex(2)}"


def setup_logging(
    runs_dir: Path,
    label: str,
    verbosity: int = 0,
    quiet: bool = False,
    trace: bool = False,
    debug_dump: bool = False,
    console_stream=None,
) -> RunContext:
    """Configure console + run.log + events.jsonl for a new run and return its context."""
    run_id = new_run_id(label)
    run_dir = Path(runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "llm").mkdir(exist_ok=True)
    if debug_dump:
        (run_dir / "pages").mkdir(exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(TRACE)
    logger.propagate = False
    ctx_filter = ContextFilter()

    stream = console_stream or sys.stderr
    if hasattr(stream, "reconfigure"):
        # Windows consoles/redirects may use a legacy code page: never crash on a character, escape it.
        try:
            stream.reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover
            pass
    console = logging.StreamHandler(stream)
    if quiet:
        console.setLevel(logging.WARNING)
    elif verbosity >= 2:
        console.setLevel(TRACE)
    elif verbosity == 1:
        console.setLevel(logging.DEBUG)
    else:
        console.setLevel(logging.INFO)
    use_color = hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")
    console.setFormatter(TextFormatter(color=use_color, field_limit=400 if verbosity >= 2 else 160,
                                       show_span=verbosity >= 2))
    console.addFilter(ctx_filter)
    logger.addHandler(console)

    text_file = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    text_file.setLevel(TRACE if trace else logging.DEBUG)
    text_file.setFormatter(TextFormatter(color=False, field_limit=2000, show_span=True))
    text_file.addFilter(ctx_filter)
    logger.addHandler(text_file)

    jsonl = logging.FileHandler(run_dir / "events.jsonl", encoding="utf-8")
    jsonl.setLevel(TRACE if trace else logging.DEBUG)
    jsonl.setFormatter(JsonlFormatter())
    jsonl.addFilter(ctx_filter)
    logger.addHandler(jsonl)

    current_run_id.set(run_id)
    ctx = RunContext(run_id=run_id, run_dir=run_dir, trace=trace, debug_dump=debug_dump)
    log_event(logger, logging.INFO, "run.logging_ready", run_dir=str(run_dir),
              console_level=logging.getLevelName(console.level), trace=trace, debug_dump=debug_dump)
    return ctx


def close_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)
    logger.addHandler(logging.NullHandler())
