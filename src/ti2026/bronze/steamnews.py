"""Bronze: official Dota 2 news via Steam's keyless ISteamNews API.

Patch announcements (7.41e ...) and TI event posts (Predictions/Fantasy rules)
land here first — the calendar/meta context the model's recency weighting and
the in-event runbook care about.
"""

from __future__ import annotations

from .. import http
from ..manifest import Snapshot

URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
APPID_DOTA2 = 570


def ingest(snap: Snapshot, count: int = 30) -> int:
    rel = "news.json"
    if snap.has(rel):
        return 0
    resp = http.get(URL, bucket="steam",
                    params={"appid": APPID_DOTA2, "count": count, "maxlength": 300})
    snap.write(rel, resp.content, url=str(resp.request.url), params={"count": count})
    return len(resp.json().get("appnews", {}).get("newsitems", []))
