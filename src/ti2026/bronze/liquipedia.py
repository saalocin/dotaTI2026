"""Bronze: Liquipedia via api.php only (page HTML outside api.php is 403).

Terms: 1 req/2s general, action=parse 1 req/30s, custom UA with contact, gzip,
CC-BY-SA 3.0 attribution. We fetch BOTH wikitext and rendered HTML for every page
so the HTML fallback parser has data from day one.
"""

from __future__ import annotations

from .. import http
from ..manifest import Snapshot

API = "https://liquipedia.net/dota2/api.php"

PAGES: dict[str, str] = {
    "main": "The_International/2026",
    "group": "The_International/2026/Group_Stage",
    "bracket": "The_International/2026/Main_Event",
    "rankings": "Portal:Rankings",
}


def _slug(page: str) -> str:
    return page.replace("/", "_").replace(":", "_")


def fetch_page(snap: Snapshot, page: str) -> None:
    """Fetch wikitext + rendered HTML for one page into the snapshot (skip if present)."""
    for prop, subdir in (("wikitext", "parse_wikitext"), ("text", "parse_html")):
        rel = f"{subdir}/{_slug(page)}.json"
        if snap.has(rel):
            continue
        params = {
            "action": "parse",
            "page": page,
            "prop": prop,
            "format": "json",
            "formatversion": "2",
        }
        resp = http.get(API, bucket="liquipedia_parse", params=params)
        snap.write(rel, resp.content, url=str(resp.request.url), params=params)


def ingest(page_keys: list[str], snap: Snapshot | None = None, extra_pages: list[str] | None = None) -> Snapshot:
    snap = snap or Snapshot("liquipedia")
    for key in page_keys:
        fetch_page(snap, PAGES[key])
    for page in extra_pages or []:
        fetch_page(snap, page)
    return snap
