"""Bronze: OpenDota — fantasy-stat primary source.

Key endpoints: /proPlayers, /leagues, /proMatches (paged), /teams/{id}(+/matches),
/matches/{id} (parsed replays), /request/{id} (force parse), and /explorer?sql=
(public SQL over the parsed DB — bulk per-player-per-game rows in one query).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .. import config, http
from ..manifest import Snapshot

BASE = "https://api.opendota.com/api"


def _params(extra: dict | None = None) -> dict:
    p = dict(extra or {})
    if config.OPENDOTA_API_KEY:
        p["api_key"] = config.OPENDOTA_API_KEY
    return p


def fetch_json(snap: Snapshot, relpath: str, path: str, params: dict | None = None):
    """GET {BASE}{path} into the snapshot unless already present; return parsed JSON.

    Parses BEFORE writing so a non-JSON 200 (rare OpenDota hiccup) never poisons bronze.
    """
    if snap.has(relpath):
        return snap.read_json(relpath)
    resp = http.get(f"{BASE}{path}", bucket="opendota", params=_params(params))
    try:
        payload = resp.json()
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OpenDota returned non-JSON for {path} (status {resp.status_code}, "
            f"{len(resp.content)} bytes): {resp.content[:120]!r}"
        ) from e
    snap.write(relpath, resp.content, url=f"{BASE}{path}", params=params or {})
    return payload


def ingest_bootstrap(snap: Snapshot, pro_match_pages: int = 3) -> None:
    """Global endpoints needing no seeds: proPlayers, leagues, heroes, recent proMatches."""
    fetch_json(snap, "proPlayers.json", "/proPlayers")
    fetch_json(snap, "leagues.json", "/leagues")
    fetch_json(snap, "heroes.json", "/heroes")
    less_than = None
    for i in range(pro_match_pages):
        params = {"less_than_match_id": less_than} if less_than else {}
        rows = fetch_json(snap, f"proMatches/page_{i:02d}.json", "/proMatches", params)
        if not rows:
            break
        less_than = min(r["match_id"] for r in rows)


def ingest_team(snap: Snapshot, team_id: int) -> None:
    # Very new team ids (post-rename orgs) can 200 with an empty body on /teams/{id};
    # the matches log is the load-bearing part, so metadata failures are warnings.
    try:
        fetch_json(snap, f"teams/{team_id}.json", f"/teams/{team_id}")
    except RuntimeError as e:
        print(f"WARN opendota /teams/{team_id}: {e}")
    fetch_json(snap, f"team_matches/{team_id}.json", f"/teams/{team_id}/matches")


def ingest_match(snap: Snapshot, match_id: int) -> dict:
    return fetch_json(snap, f"matches/{match_id}.json", f"/matches/{match_id}")


def request_parse(match_id: int) -> dict:
    resp = http.post(f"{BASE}/request/{match_id}", bucket="opendota", json_body=None)
    return resp.json()


def _epoch(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def _month_edges(since: str, until: str | None) -> list[tuple[str, str]]:
    """[(month_start, next_month_start), ...] covering since..until (UTC dates)."""
    start = datetime.strptime(since, "%Y-%m-%d").replace(day=1)
    end = datetime.strptime(until, "%Y-%m-%d") if until else datetime.now(timezone.utc).replace(tzinfo=None)
    edges = []
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        edges.append((cur.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        cur = nxt
    return edges


EXPLORER_SQL = """
SELECT pm.match_id, pm.account_id, pm.hero_id, pm.player_slot,
  pm.kills, pm.deaths, pm.last_hits, pm.denies, pm.gold_per_min,
  pm.towers_killed, pm.obs_placed, pm.camps_stacked, pm.rune_pickups,
  pm.firstblood_claimed, pm.teamfight_participation, pm.stuns,
  pm.roshans_killed, pm.item_uses, pm.ability_uses, pm.killed,
  m.start_time, m.duration, m.radiant_win, m.radiant_team_id, m.dire_team_id,
  m.leagueid, l.name AS league_name, mp.patch
FROM player_matches pm
JOIN matches m ON m.match_id = pm.match_id
LEFT JOIN leagues l ON l.leagueid = m.leagueid
LEFT JOIN match_patch mp ON mp.match_id = pm.match_id
WHERE pm.account_id IN ({ids})
  AND m.start_time >= {t0} AND m.start_time < {t1}
ORDER BY pm.match_id, pm.player_slot
""".strip()


PICKS_BANS_SQL = """
SELECT pb.match_id, pb.ord, pb.is_pick, pb.hero_id, pb.team
FROM picks_bans pb
JOIN matches m ON m.match_id = pb.match_id
WHERE m.start_time >= {t0} AND m.start_time < {t1}
  AND EXISTS (SELECT 1 FROM player_matches pm
              WHERE pm.match_id = pb.match_id AND pm.account_id IN ({ids}))
ORDER BY pb.match_id, pb.ord
""".strip()


# Match-level coach-title condition inputs (rules §2.3): first_blood_time (pre-horn
# kills are clamped to 0 by the API) and deaths to Tormentor (killed_by is json, so
# ->> not the jsonb ? operator; correlated subquery keeps it on the match_id index).
MATCH_FLAGS_SQL = """
SELECT m.match_id, m.first_blood_time,
  (SELECT count(*) FROM player_matches pm2
    WHERE pm2.match_id = m.match_id
      AND (pm2.killed_by ->> 'npc_dota_miniboss') IS NOT NULL) AS tormentor_victims
FROM matches m
WHERE m.start_time >= {t0} AND m.start_time < {t1}
  AND EXISTS (SELECT 1 FROM player_matches pm
              WHERE pm.match_id = m.match_id AND pm.account_id IN ({ids}))
ORDER BY m.match_id
""".strip()


def _explorer_monthly(snap: Snapshot, sql_template: str, prefix: str,
                      account_ids: list[int], since: str, until: str | None) -> None:
    ids = ",".join(str(a) for a in sorted(set(account_ids)))
    for m0, m1 in _month_edges(since, until):
        rel = f"explorer/{prefix}_{m0[:7]}.json"
        if snap.has(rel):
            continue
        sql = sql_template.format(ids=ids, t0=_epoch(m0), t1=_epoch(m1))
        resp = http.get(f"{BASE}/explorer", bucket="opendota", params=_params({"sql": sql}))
        payload = resp.json()
        if payload.get("err"):
            raise RuntimeError(f"explorer error for {prefix} {m0}: {payload['err']}")
        snap.write(rel, resp.content, url=f"{BASE}/explorer", params={"month": m0, "kind": prefix})


def explorer_backfill(
    snap: Snapshot, account_ids: list[int], since: str, until: str | None = None
) -> None:
    """Monthly explorer pages: player stats (pm_*), drafts (pb_*), match flags (mf_*)."""
    _explorer_monthly(snap, EXPLORER_SQL, "pm", account_ids, since, until)
    _explorer_monthly(snap, PICKS_BANS_SQL, "pb", account_ids, since, until)
    _explorer_monthly(snap, MATCH_FLAGS_SQL, "mf", account_ids, since, until)


def explorer_rows(source: str = "opendota") -> list[dict]:
    """All player_matches explorer rows across snapshots, deduped by (match_id, account_id)."""
    from .. import manifest

    rows: dict[tuple[int, int], dict] = {}
    for path in manifest.all_files(source, "explorer/pm_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            rows[(row["match_id"], row["account_id"])] = row
    return list(rows.values())


def match_flag_rows(source: str = "opendota") -> list[dict]:
    """All match-flag explorer rows across snapshots, deduped by match_id."""
    from .. import manifest

    rows: dict[int, dict] = {}
    for path in manifest.all_files(source, "explorer/mf_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            rows[row["match_id"]] = row
    return list(rows.values())


def draft_rows(source: str = "opendota") -> list[dict]:
    """All picks_bans explorer rows across snapshots, deduped by (match_id, hero_id)."""
    from .. import manifest

    rows: dict[tuple[int, int], dict] = {}
    for path in manifest.all_files(source, "explorer/pb_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows", []):
            rows[(row["match_id"], row["hero_id"])] = row
    return list(rows.values())
