"""HTML -> text, links, emails, profile links and a page-kind hint."""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment

from unifaculty.signals import JOBS_PATH_TERMS, PEOPLE_PATH_TERMS, TextSignals, text_signals
from unifaculty.urls import normalize_url

EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*\.[A-Za-z]{2,24})(?![\w-])")
# "jane.doe [at] uni-example [dot] de", "jane (at) example.edu", "jane{at}example{dot}edu", "jane ät example.de"
_DOMAIN_SEP = r"(?:\s*[\[\(\{<]\s*(?:dot|punkt|\.)\s*[\]\)\}>]\s*|\.)"
OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._%+-]{0,63})\s*(?:[\[\(\{<]\s*(?:at|ät|@)\s*[\]\)\}>]|\s(?:ät|＠)\s)\s*"
    r"([A-Za-z0-9-]+(?:" + _DOMAIN_SEP + r"[A-Za-z0-9-]+)+)",
    re.IGNORECASE,
)
DOT_RE = re.compile(_DOMAIN_SEP, re.IGNORECASE)
IMAGE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js"}

PROFILE_HOSTS = {
    "linkedin": re.compile(r"^(?:[a-z]{2,3}\.)?linkedin\.com$"),
    "github": re.compile(r"^github\.com$"),
    "google_scholar": re.compile(r"^scholar\.google\.[a-z.]+$"),
    "orcid": re.compile(r"^orcid\.org$"),
    "researchgate": re.compile(r"^(?:www\.)?researchgate\.net$"),
    "dblp": re.compile(r"^dblp\.(?:org|uni-trier\.de)$"),
    "semantic_scholar": re.compile(r"^(?:www\.)?semanticscholar\.org$"),
    "x": re.compile(r"^(?:www\.)?(?:x|twitter)\.com$"),
    "bluesky": re.compile(r"^bsky\.app$"),
}
PROFILE_PATH_OK = {
    "linkedin": lambda p, q: p.startswith(("/in/", "/pub/")),
    "github": lambda p, q: p.count("/") in (1, 2) and p.strip("/") != "" and not p.startswith(("/orgs/", "/topics/", "/features", "/about")),
    "google_scholar": lambda p, q: "user=" in q,
    "orcid": lambda p, q: bool(re.match(r"^/\d{4}-\d{4}-\d{4}-\d{3}[\dX]", p)),
    "researchgate": lambda p, q: p.startswith("/profile/"),
    "dblp": lambda p, q: p.startswith(("/pid/", "/pers/")),
    "semantic_scholar": lambda p, q: p.startswith("/author/"),
    "x": lambda p, q: p.count("/") == 1 and len(p) > 1 and p[1:] not in {"share", "intent", "home", "search"},
    "bluesky": lambda p, q: p.startswith("/profile/"),
}

_STRIP_TAGS = ["script", "style", "noscript", "svg", "canvas", "iframe", "template", "form", "button", "select"]
_BOILERPLATE_TAGS = ["nav", "footer", "header", "aside"]
_BLOCK_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "td", "th", "dd", "dt", "div", "section",
               "article", "blockquote", "pre", "table", "ul", "ol", "dl", "address", "figcaption"]


@dataclass
class Link:
    url: str
    anchor: str


@dataclass
class ParsedPage:
    url: str
    title: str
    text: str
    lang: str | None
    links: list[Link]
    emails: list[str]
    profile_links: dict[str, list[str]]
    meta_robots: set[str]
    headings: list[str]
    signals: TextSignals = field(default_factory=TextSignals)
    kind: str = "other"

    @property
    def noindex(self) -> bool:
        return bool({"noindex", "none"} & self.meta_robots)

    @property
    def nofollow(self) -> bool:
        return bool({"nofollow", "none"} & self.meta_robots)


def decode_cfemail(encoded: str) -> str | None:
    """Decode Cloudflare's email obfuscation (``data-cfemail`` hex)."""
    try:
        key = int(encoded[:2], 16)
        return "".join(chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2))
    except (ValueError, IndexError):
        return None


def extract_emails(soup: BeautifulSoup, text: str) -> list[str]:
    found: list[str] = []

    def add(candidate: str | None) -> None:
        if not candidate:
            return
        candidate = candidate.strip().strip(".,;:()[]<>\"'").lower()
        if EMAIL_RE.fullmatch(candidate) and candidate.rsplit(".", 1)[-1] not in IMAGE_TLDS and candidate not in found:
            found.append(candidate)

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            address = unquote(href[7:].split("?", 1)[0])
            for part in address.split(","):
                add(part)
    for el in soup.select("[data-cfemail]"):
        add(decode_cfemail(el.get("data-cfemail", "")))
    for a in soup.find_all("a", href=True):
        if "/cdn-cgi/l/email-protection#" in a["href"]:
            add(decode_cfemail(a["href"].split("#", 1)[1]))
    for match in EMAIL_RE.findall(text):
        add(match)
    for local, domain in OBFUSCATED_RE.findall(text):
        add(f"{local}@{DOT_RE.sub('.', domain)}")
    return found


def classify_profile_link(url: str) -> str | None:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    for kind, host_re in PROFILE_HOSTS.items():
        if host_re.match(host) and PROFILE_PATH_OK[kind](parts.path or "/", parts.query or ""):
            return kind
    return None


def _visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.find("article")
    root = main if main and len(main.get_text(" ", strip=True)) > 200 else soup.body or soup
    if root is not main:
        for tag in root.find_all(_BOILERPLATE_TAGS):
            tag.decompose()
    for br in root.find_all("br"):
        br.replace_with("\n")
    # Put line breaks around block elements, then flatten: keeps paragraphs apart
    # without duplicating text from nested inline elements.
    for el in root.find_all(_BLOCK_TAGS):
        el.insert_before("\n")
        el.insert_after("\n")
    text = root.get_text(" ")
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t ​]+", " ", text)
    lines, seen = [], set()
    for line in (raw.strip() for raw in text.splitlines()):
        if line and line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


def guess_kind(url: str, title: str, headings: list[str], signals: TextSignals, emails: list[str],
               profile_links: dict[str, list[str]], person_link_count: int) -> str:
    """Rough page type: position | listing | profile | other (a hint for logs, prefilter and the LLM)."""
    path = urlsplit(url).path.lower()
    head = " ".join([title, *headings[:3]]).lower()
    path_tokens = set(re.split(r"[^a-z0-9~]+", path))
    if path_tokens & set(JOBS_PATH_TERMS) and signals.has_position:
        return "position"
    if len(emails) > 6 or person_link_count > 12:
        return "listing"
    academic_title = bool(re.search(r"\b(prof\.?|professor|professorin|dr\.|lecturer)(?!\w)", title.lower()))
    if (path_tokens & set(PEOPLE_PATH_TERMS) or "/~" in path or academic_title) and len(emails) <= 3:
        return "profile"
    if signals.has_position and any(t in head for t in ("position", "phd", "doctoral", "stelle", "mitarbeiter",
                                                       "assistant", "vacanc", "job")):
        return "position"
    if profile_links and len(emails) <= 2:
        return "profile"
    return "other"


def parse_html(html: str, base_url: str, field_terms: list[str] | None = None) -> ParsedPage:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
    lang = (soup.html.get("lang") if soup.html else None) or None
    meta_robots: set[str] = set()
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^(robots|unifacultyscraper)$", re.I)}):
        meta_robots |= {v.strip().lower() for v in (meta.get("content") or "").split(",") if v.strip()}
    base_tag = soup.find("base", href=True)
    base = normalize_url(base_tag["href"], base_url) if base_tag else base_url

    links: list[Link] = []
    profile_links: dict[str, list[str]] = {}
    seen_links: set[str] = set()
    person_link_count = 0
    for a in soup.find_all("a", href=True):
        rel = {r.lower() for r in (a.get("rel") or [])}
        url = normalize_url(a["href"], base)
        if not url:
            continue
        anchor = a.get_text(" ", strip=True)[:200]
        kind = classify_profile_link(url)
        if kind:
            bucket = profile_links.setdefault(kind, [])
            if url not in bucket:
                bucket.append(url)
            continue
        if "nofollow" in rel or url in seen_links:
            continue
        seen_links.add(url)
        links.append(Link(url=url, anchor=anchor))
        if any(f"/{t}/" in urlsplit(url).path.lower() for t in ("people", "person", "staff", "team", "personen", "mitarbeiter")):
            person_link_count += 1

    headings = [h.get_text(" ", strip=True)[:200] for h in soup.find_all(["h1", "h2"])][:10]
    raw_text_for_emails = soup.get_text(" ", strip=True)
    emails = extract_emails(soup, raw_text_for_emails)
    text = _visible_text(soup)
    signals = text_signals(f"{title}\n{text}", field_terms)
    kind = guess_kind(base_url, title, headings, signals, emails, profile_links, person_link_count)
    return ParsedPage(url=base_url, title=title, text=text, lang=lang, links=links, emails=emails,
                      profile_links=profile_links, meta_robots=meta_robots, headings=headings,
                      signals=signals, kind=kind)
