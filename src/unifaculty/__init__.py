"""Uni Faculty Scraper — find professors with open, funded PhD/postdoc positions.

Phase 1: a polite, compliance-first crawler plus an LLM filter that writes one
CSV per university. See README.md and ACCEPTABLE_USE.md.
"""

__version__ = "0.1.0"

#: Product token used for robots.txt group matching (RFC 9309, section 2.2.1).
PRODUCT_TOKEN = "UniFacultyScraper"

REPO_URL = "https://github.com/ProHamedM/Uni-Faculty-Scrape"
