"""OpenDota payloads -> fan.player_game_stats records.

ALL 18 fantasy-stat field mappings live here and nowhere else.
Two entry points: explorer rows (bulk backfill) and /matches/{id} player objects
(live/gap-sweep). Both funnel through _stat_record so mappings can't drift.

Parse-only stats are NULL when the replay is unparsed. lotuses_grabbed is ALWAYS
NULL (no source anywhere); lotus_proxy_famango tracks lotus *consumption* only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _jload(v) -> dict:
    if v is None:
        return {}
    if isinstance(v, str):
        return json.loads(v) if v.strip() else {}
    return v


def _utc(epoch) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None) if epoch else None


def _stat_record(
    *,
    match_id: int,
    account_id: int,
    hero_id,
    player_slot,
    scalars: dict,
    item_uses: dict,
    ability_uses: dict,
    killed: dict,
    parsed: bool,
    start_time,
    duration,
    radiant_win,
    radiant_team_id,
    dire_team_id,
    league_id,
    league_name,
    patch,
    source: str,
) -> dict:
    is_radiant = player_slot is not None and player_slot < 128
    win = None if radiant_win is None else (radiant_win == is_radiant)

    def p(v):  # parse-only scalar
        return v if parsed else None

    return {
        "match_id": match_id,
        "account_id": account_id,
        "hero_id": hero_id,
        "is_radiant": is_radiant,
        "win": win,
        "team_id": radiant_team_id if is_radiant else dire_team_id,
        "opp_team_id": dire_team_id if is_radiant else radiant_team_id,
        "duration_s": duration,
        "start_time": _utc(start_time),
        "league_id": league_id,
        "league_name": league_name,
        "patch": str(patch) if patch is not None else None,
        "kills": scalars.get("kills"),
        "deaths": scalars.get("deaths"),
        "last_hits": scalars.get("last_hits"),
        "denies": scalars.get("denies"),
        "gpm": scalars.get("gold_per_min"),
        "madstone_proxy": p(item_uses.get("madstone_bundle", 0)),
        "tower_kills": p(scalars.get("towers_killed")),
        "obs_placed": p(scalars.get("obs_placed")),
        "camps_stacked": p(scalars.get("camps_stacked")),
        "rune_pickups": p(scalars.get("rune_pickups")),
        "watchers_taken": p(ability_uses.get("ability_lamp_use", 0)),
        "smokes_used": p(item_uses.get("smoke_of_deceit", 0)),
        "lotuses_grabbed": None,  # no source anywhere — see TI2026_Rules.md
        "lotus_proxy_famango": p(item_uses.get("famango", 0) + item_uses.get("great_famango", 0)),
        "roshan_kills": p(scalars.get("roshans_killed", killed.get("npc_dota_roshan", 0) if parsed else None)),
        "teamfight_participation": p(scalars.get("teamfight_participation")),
        "stuns_s": p(scalars.get("stuns")),
        "tormentor_kills": p(killed.get("npc_dota_miniboss", 0)),
        "first_blood": bool(scalars.get("firstblood_claimed")) if parsed else None,
        "courier_kills": p(scalars.get("courier_kills", killed.get("npc_dota_courier", 0) if parsed else None)),
        "parsed": parsed,
        "source": source,
    }


def from_explorer_row(row: dict) -> dict:
    parsed = row.get("teamfight_participation") is not None or row.get("stuns") is not None
    return _stat_record(
        match_id=row["match_id"],
        account_id=row["account_id"],
        hero_id=row.get("hero_id"),
        player_slot=row.get("player_slot"),
        scalars=row,
        item_uses=_jload(row.get("item_uses")),
        ability_uses=_jload(row.get("ability_uses")),
        killed=_jload(row.get("killed")),
        parsed=parsed,
        start_time=row.get("start_time"),
        duration=row.get("duration"),
        radiant_win=row.get("radiant_win"),
        radiant_team_id=row.get("radiant_team_id"),
        dire_team_id=row.get("dire_team_id"),
        league_id=row.get("leagueid"),
        league_name=row.get("league_name"),
        patch=row.get("patch"),
        source="opendota_explorer",
    )


def drafts_from_match_json(match: dict) -> list[dict]:
    """/matches/{id} picks_bans -> draft action rows (same shape as the explorer's)."""
    out = []
    for pb in match.get("picks_bans") or []:
        out.append(
            {
                "match_id": match["match_id"],
                "ord": pb.get("order"),
                "is_pick": bool(pb.get("is_pick")),
                "hero_id": pb.get("hero_id"),
                "team": pb.get("team"),
            }
        )
    return out


def from_match_json(match: dict) -> list[dict]:
    """/matches/{id} -> one record per roster-relevant player (caller filters accounts)."""
    parsed = match.get("version") is not None
    league = match.get("league") or {}
    records = []
    for pl in match.get("players", []):
        if not pl.get("account_id"):
            continue
        records.append(
            _stat_record(
                match_id=match["match_id"],
                account_id=pl["account_id"],
                hero_id=pl.get("hero_id"),
                player_slot=pl.get("player_slot"),
                scalars=pl,
                item_uses=_jload(pl.get("item_uses")),
                ability_uses=_jload(pl.get("ability_uses")),
                killed=_jload(pl.get("killed")),
                parsed=parsed,
                start_time=match.get("start_time"),
                duration=match.get("duration"),
                radiant_win=match.get("radiant_win"),
                radiant_team_id=(match.get("radiant_team") or {}).get("team_id", match.get("radiant_team_id")),
                dire_team_id=(match.get("dire_team") or {}).get("team_id", match.get("dire_team_id")),
                league_id=league.get("leagueid", match.get("leagueid")),
                league_name=league.get("name"),
                patch=match.get("patch"),
                source="opendota_match",
            )
        )
    return records
