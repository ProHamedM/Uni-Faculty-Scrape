"""URL normalization and scope checks."""

from __future__ import annotations

import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

TRACKING_PARAMS = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|_hsenc|_hsmi|ref_src)$", re.I)

NON_HTML_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tif", ".tiff",
    ".zip", ".gz", ".tar", ".rar", ".7z", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".odt", ".ods", ".odp", ".mp3", ".mp4", ".m4a", ".mov", ".avi", ".webm", ".wav", ".ics",
    ".css", ".js", ".json", ".xml", ".rss", ".atom", ".bib", ".txt", ".csv", ".exe", ".dmg",
    ".apk", ".woff", ".woff2", ".ttf", ".eot",
}

SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "data:", "ftp:", "file:", "#")


def normalize_url(href: str, base: str | None = None) -> str | None:
    """Resolve ``href`` against ``base`` and return a canonical http(s) URL, or None."""
    if not href:
        return None
    href = href.strip()
    if href.lower().startswith(SKIP_SCHEMES):
        return None
    absolute = urljoin(base, href) if base else href
    try:
        parts = urlsplit(absolute)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    # Collapse "/a/./b/../c" while keeping a trailing slash.
    trailing = path.endswith("/")
    path = posixpath.normpath(path)
    if path == ".":
        path = "/"
    if trailing and not path.endswith("/"):
        path += "/"
    if not path.startswith("/"):
        path = "/" + path
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING_PARAMS.match(k)]
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    netloc = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        netloc = f"{netloc}:{parts.port}"
    return f"{parts.scheme.lower()}://{netloc}"


def host_in_scope(host: str, allowed_domains: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for domain in allowed_domains:
        domain = domain.lower().strip().lstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def looks_like_html(url: str) -> bool:
    path = urlsplit(url).path.lower()
    _, ext = posixpath.splitext(path)
    return ext not in NON_HTML_EXTENSIONS


def path_and_query(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "item"
