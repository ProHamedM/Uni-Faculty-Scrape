"""Find real-looking API keys before they reach git, configs or logs you share.

Used by ``unifaculty doctor``, by config loading (keys are refused in YAML), and by
``tests/test_no_secrets.py``, which fails CI if a key-shaped string is committed.

Tests and docs must never contain key-shaped literals, not even fake ones:
build them at runtime (see ``tests/helpers.py``) so secret scanners stay quiet.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Strict shapes of real credentials (low false-positive rate).
PATTERNS: dict[str, re.Pattern[str]] = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "OpenRouter API key": re.compile(r"sk-or-v1-[0-9a-f]{64}"),
    "Anthropic API key": re.compile(r"sk-ant-[a-z]+\d{2}-[0-9A-Za-z_\-]{80,}"),
    "OpenAI API key": re.compile(r"sk-(?:proj-|svcacct-)?[0-9A-Za-z_\-]{40,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36}\b|\bgithub_pat_[0-9A-Za-z_]{80,}"),
    "Private key": re.compile("-----BEGIN" + r"[A-Z ]*PRIVATE KEY-----"),
}

#: Files that may hold KEY=value pairs; only ``.env.example`` may be committed, and only with empty values.
ENV_FILE = re.compile(r"(^|/)\.env(\..+)?$")
ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD))\s*=\s*(\S.*)$")

SKIP_DIRS = {".git", ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".ruff_cache", ".cache",
             "runs", "output", "_temp", "build", "dist", "node_modules"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".whl", ".pyc"}


@dataclass
class Finding:
    path: str
    line: int
    kind: str

    def __str__(self) -> str:  # never includes the secret itself
        return f"{self.path}:{self.line}: {self.kind}"


def scan_text(text: str, path: str = "<text>") -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(Finding(path, lineno, kind))
                break
    return findings


def scan_env_file(text: str, path: str) -> list[Finding]:
    """In a committed .env-style file, any non-empty *_KEY / *_TOKEN / *_SECRET value is a finding."""
    findings = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = ENV_ASSIGNMENT.match(line)
        if m and not m.group(2).strip().startswith("#") and m.group(2).strip() not in ('""', "''"):
            findings.append(Finding(path, lineno, f"value assigned to {m.group(1)}"))
    return findings


def tracked_files(root: Path) -> list[Path]:
    """Files git tracks or would add (respects .gitignore); falls back to a directory walk."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             cwd=root, capture_output=True, check=True, timeout=30)
        return [root / p for p in out.stdout.decode("utf-8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        files = []
        for path in root.rglob("*"):
            if path.is_file() and not (set(path.relative_to(root).parts) & SKIP_DIRS):
                files.append(path)
        return files


def is_git_tracked(root: Path, relative: str) -> bool | None:
    """True/False if git answers, None if git isn't available or this isn't a repository."""
    try:
        result = subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=root,
                                capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        return True
    if b"not a git repository" in result.stderr.lower():
        return None
    return False


def scan_repo(root: Path) -> list[Finding]:
    root = Path(root)
    findings: list[Finding] = []
    for path in tracked_files(root):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(text, rel))
        if ENV_FILE.search(rel):
            if rel.endswith(".env.example"):
                findings.extend(scan_env_file(text, rel))
            else:
                findings.append(Finding(rel, 1, "environment file must not be committed"))
    return findings


def looks_like_placeholder(value: str) -> bool:
    low = value.lower()
    return any(marker in low for marker in ("fake", "test", "xxxx", "your", "example", "changeme", "<", "placeholder"))
