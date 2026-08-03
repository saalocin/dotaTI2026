"""Bronze: odds — weakest source, droppable by design.

Practical order: seeds/manual_odds.csv (hand-entered, read directly by silver) >
egamersworld page snapshots (server-rendered HTML, parse later) > OddsPortal AJAX
(deferred: endpoint archaeology not worth it before the event).
"""

from __future__ import annotations

from .. import http
from ..manifest import Snapshot

EGW_URLS = {
    "egw_matches": "https://egamersworld.com/dota2/matches",
}


def ingest_egamersworld(snap: Snapshot) -> None:
    for key, url in EGW_URLS.items():
        rel = f"{key}.html"
        if snap.has(rel):
            continue
        resp = http.get(url, bucket="odds", ua="browser")
        snap.write(rel, resp.content, url=url)
