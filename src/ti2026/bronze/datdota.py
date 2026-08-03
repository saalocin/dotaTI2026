"""Bronze: datdota events API (keyless JSON at api.datdota.com).

Cloudflare-fronted: needs full browser-like headers, and may still block
non-browser TLS fingerprints. datdota is a droppable cross-check source, so
failures are warnings, not errors.
"""

from __future__ import annotations

import httpx

from .. import http
from ..manifest import Snapshot

BASE = "https://api.datdota.com/api"

ENDPOINTS: dict[str, str] = {
    "tormentors": "/events/tormentor-kills",
    "roshans": "/events/roshan-killed",
    "first_bloods": "/events/first-bloods",
    "couriers": "/events/courier-deaths",
}

BROWSER_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://datdota.com",
    "Referer": "https://datdota.com/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}

# datdota date params use DD/MM/YYYY; keep payloads patch-era sized.
DEFAULT_PARAMS = {"after": "01/01/2026"}


def ingest(snap: Snapshot, keys: list[str], params: dict | None = None) -> list[str]:
    """Fetch each endpoint; returns keys that FAILED (empty list = all good)."""
    failed = []
    for key in keys:
        rel = f"events_{key}.json"
        if snap.has(rel):
            continue
        url = f"{BASE}{ENDPOINTS[key]}"
        q = dict(DEFAULT_PARAMS if params is None else params)
        try:
            try:
                resp = http.get(url, bucket="datdota", params=q, headers=BROWSER_HEADERS, ua="browser")
            except httpx.HTTPStatusError:
                q = {}
                resp = http.get(url, bucket="datdota", headers=BROWSER_HEADERS, ua="browser")
            snap.write(rel, resp.content, url=url, params=q)
        except httpx.HTTPStatusError as e:
            print(f"WARN datdota {key}: {e.response.status_code} — cross-check source skipped")
            failed.append(key)
    return failed
