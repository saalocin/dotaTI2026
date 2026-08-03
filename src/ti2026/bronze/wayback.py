"""Wayback Machine snapshots of Liquipedia's Portal:Rankings.

The live leaderboard is current-state only, so time-correct rating priors for
walk-forward backtesting exist ONLY in web archives. Bonus: pre-rename rows
(Tundra Esports -> Iron Wing, L1GA TEAM -> HULIGANI) carry the real ratings the
live board reset away. Liquipedia content is CC-BY-SA 3.0 — attribution applies
to anything published from this data.
"""

from __future__ import annotations

import re

from .. import http, manifest

AVAILABLE_API = "https://archive.org/wayback/available"
TARGET = "https://liquipedia.net/dota2/Portal:Rankings"

# just before each backtest month's cutoff, plus a near-now snapshot
DEFAULT_STAMPS = [
    "20260125", "20260225", "20260325", "20260425",
    "20260525", "20260625", "20260725",
]


def ingest(snap: manifest.Snapshot, stamps: list[str] | None = None) -> list[str]:
    """Fetch the archived Portal:Rankings closest to each YYYYMMDD stamp.

    Files are named by the ACTUAL archive timestamp (rankings_YYYYMMDD.html), so
    two stamps resolving to the same capture dedupe naturally."""
    fetched: list[str] = []
    for stamp in stamps or DEFAULT_STAMPS:
        try:
            avail = http.get(
                AVAILABLE_API, bucket="wayback",
                params={"url": TARGET, "timestamp": stamp},
            ).json()
        except Exception as exc:  # noqa: BLE001 — availability probe is best-effort
            print(f"  [warn] wayback availability {stamp}: {exc}")
            continue
        closest = (avail.get("archived_snapshots") or {}).get("closest") or {}
        if closest.get("available"):
            ts_full = closest["timestamp"]
            if snap.has(f"rankings_{ts_full[:8]}.html"):
                fetched.append(ts_full[:8])
                continue
            # id_ flag returns the original page bytes without the wayback toolbar
            url = closest["url"].replace(f"/{ts_full}/", f"/{ts_full}id_/")
        else:
            # the availability API misses captures the CDX index has — fetch direct;
            # wayback resolves a partial timestamp to the nearest capture (404 if none)
            url = f"https://web.archive.org/web/{stamp}id_/{TARGET}"
        try:
            page = http.get(url, bucket="wayback")
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] wayback fetch {stamp}: {exc}")
            continue
        m = re.search(r"/web/(\d{14})", str(page.url))
        ts = m.group(1)[:8] if m else stamp
        relpath = f"rankings_{ts}.html"
        if not snap.has(relpath):
            snap.write(relpath, page.content, url=str(page.url), params={"requested": stamp})
            print(f"  archived rankings {ts} ({len(page.content):,} bytes)")
        fetched.append(ts)
    return fetched
