"""Bronze: Polymarket prediction markets (public gamma API, keyless).

A live "The International 2026: Winner" market gives real-money champion
probabilities — the benchmark our model card has been missing — and Polymarket
also lists per-series Dota markets during events. Snapshots are intraday-capable
so market drift during TI is preserved.
"""

from __future__ import annotations

import json

from .. import http
from ..manifest import Snapshot

SEARCH = "https://gamma-api.polymarket.com/public-search"
EVENTS = "https://gamma-api.polymarket.com/events"

QUERIES = ["The International 2026", "Dota 2"]


def ingest(snap: Snapshot) -> int:
    """Search for open TI/Dota events, then store each event's full market list."""
    slugs: dict[str, dict] = {}
    for i, q in enumerate(QUERIES):
        rel = f"search_{i}.json"
        if not snap.has(rel):
            resp = http.get(SEARCH, bucket="polymarket", params={"q": q})
            snap.write(rel, resp.content, url=str(resp.request.url), params={"q": q})
        for ev in (snap.read_json(rel).get("events") or []):
            title = (ev.get("title") or "").lower()
            if ev.get("closed"):
                continue
            if "international" in title or "dota" in title:
                slugs[ev["slug"]] = ev
    n = 0
    for slug in sorted(slugs):
        rel = f"events/{slug[:80]}.json"
        if snap.has(rel):
            n += 1
            continue
        resp = http.get(EVENTS, bucket="polymarket", params={"slug": slug})
        payload = resp.json()
        if not payload:
            continue
        snap.write(rel, resp.content, url=str(resp.request.url), params={"slug": slug})
        markets = (payload[0] or {}).get("markets") or []
        print(f"  market event: {payload[0].get('title')} ({len(markets)} markets)")
        n += 1
    return n
