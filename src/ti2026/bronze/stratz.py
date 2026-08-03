"""Bronze: STRATZ GraphQL — optional gap-filler (needs STRATZ_TOKEN in .env).

Behind Cloudflare: browser-like headers + Bearer token required. Kept thin by design;
OpenDota is strictly better for the 18 fantasy stats.
"""

from __future__ import annotations

from .. import config, http
from ..manifest import Snapshot

URL = "https://api.stratz.com/graphql"

MATCH_QUERY = """
query ($id: Long!) {
  match(id: $id) {
    id
    durationSeconds
    players {
      steamAccountId
      isRadiant
      kills deaths numLastHits numDenies goldPerMinute
      stats {
        campStack
        courierKills { time }
        runes { rune action time }
        wards { type time }
        itemUsed { itemId count }
      }
    }
  }
}
""".strip()


def _headers() -> dict:
    if not config.STRATZ_TOKEN:
        raise RuntimeError("STRATZ_TOKEN not set in .env — skipping STRATZ ingest")
    return {
        "Authorization": f"Bearer {config.STRATZ_TOKEN}",
        # STRATZ's gateway requires this exact UA; anything else gets a Cloudflare 403.
        "User-Agent": "STRATZ_API",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def ingest_matches(snap: Snapshot, match_ids: list[int]) -> None:
    for mid in match_ids:
        rel = f"matches/{mid}.json"
        if snap.has(rel):
            continue
        body = {"query": MATCH_QUERY, "variables": {"id": mid}}
        resp = http.post(URL, bucket="stratz", json_body=body, headers=_headers())
        snap.write(rel, resp.content, url=URL, params={"match_id": mid})


SERIES_BATCH = 25   # GraphQL aliases per request; free tier allows 250 req/min


def ingest_series(snap: Snapshot, match_ids: list[int]) -> int:
    """Series ground truth per match (seriesId + BEST_OF_* type) via aliased
    match queries — the one fact OpenDota lacks (our series are synthesized)."""
    todo = sorted(set(match_ids))
    n_req = 0
    for i in range(0, len(todo), SERIES_BATCH):
        chunk = todo[i:i + SERIES_BATCH]
        rel = f"series/batch_{chunk[0]}.json"
        if snap.has(rel):
            continue
        aliases = " ".join(
            f"m{j}: match(id: {mid}) {{ id seriesId series {{ id type }} }}"
            for j, mid in enumerate(chunk)
        )
        body = {"query": f"query {{ {aliases} }}"}
        resp = http.post(URL, bucket="stratz", json_body=body, headers=_headers())
        snap.write(rel, resp.content, url=URL, params={"first_match": chunk[0], "n": len(chunk)})
        n_req += 1
    return n_req
