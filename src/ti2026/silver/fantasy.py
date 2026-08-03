"""Build the fantasy (players) domain from bronze OpenDota data.

fan.player_game_stats: explorer bulk rows first, then /matches/{id} payloads
override (they are fresher — e.g. a match parsed after the explorer pull).
Restricted to the 80 seeded roster accounts.
"""

from __future__ import annotations

import json

from .. import manifest
from ..bronze import opendota as bronze_opendota
from ..parse import opendota_match

COLUMNS = [
    "match_id", "account_id", "hero_id", "is_radiant", "win", "team_id", "opp_team_id",
    "team_key", "opp_team_key", "duration_s", "start_time", "league_id", "league_name",
    "patch", "series_id", "kills", "deaths", "last_hits", "denies", "gpm",
    "madstone_proxy", "tower_kills", "obs_placed", "camps_stacked", "rune_pickups",
    "watchers_taken", "smokes_used", "lotuses_grabbed", "lotus_proxy_famango",
    "roshan_kills", "teamfight_participation", "stuns_s", "tormentor_kills",
    "first_blood", "courier_kills", "parsed", "source",
]


def build(con) -> None:
    from . import predictions

    roster = {a for (a,) in con.execute("SELECT account_id FROM fan.players").fetchall()}
    id2key = {tid: key for tid, (key, _src) in predictions.id2key_ext(con).items()}

    records: dict[tuple[int, int], dict] = {}
    for row in bronze_opendota.explorer_rows():
        if row["account_id"] not in roster:
            continue
        rec = opendota_match.from_explorer_row(row)
        records[(rec["match_id"], rec["account_id"])] = rec
    for path in manifest.all_files("opendota", "matches/*.json"):
        match = json.loads(path.read_text(encoding="utf-8"))
        for rec in opendota_match.from_match_json(match):
            if rec["account_id"] in roster:
                records[(rec["match_id"], rec["account_id"])] = rec

    rows = []
    for rec in records.values():
        rec["team_key"] = id2key.get(rec.get("team_id"))
        rec["opp_team_key"] = id2key.get(rec.get("opp_team_id"))
        rec["series_id"] = None  # filled from pred.games below
        rows.append(tuple(rec.get(c) for c in COLUMNS))

    placeholders = ",".join("?" for _ in COLUMNS)
    con.executemany(
        f"INSERT OR REPLACE INTO fan.player_game_stats ({', '.join(COLUMNS)}) VALUES ({placeholders})",
        rows,
    )
    con.execute(
        """
        UPDATE fan.player_game_stats s
        SET series_id = g.series_id
        FROM pred.games g
        WHERE g.match_id = s.match_id AND g.series_id IS NOT NULL
        """
    )
    # override team keys with pred.games' roster-majority attribution (a side is a
    # team iff >=3 of its seeded 5 are on it) — the id-based stamp above is only a
    # fallback for matches pred.games doesn't carry
    con.execute(
        """
        UPDATE fan.player_game_stats s
        SET team_key = CASE WHEN s.is_radiant THEN g.radiant_team_key ELSE g.dire_team_key END,
            opp_team_key = CASE WHEN s.is_radiant THEN g.dire_team_key ELSE g.radiant_team_key END
        FROM pred.games g
        WHERE g.match_id = s.match_id
        """
    )
    build_bios(con)
    build_profiles(con)


def build_profiles(con) -> None:
    """fan.player_profiles <- OpenDota /players payloads (bronze 'players')."""
    from ..bronze import players as bronze_players

    rows = []
    for acc, payload in bronze_players.profile_rows().items():
        prof = payload.get("profile") or {}
        rows.append((acc, payload.get("rank_tier"), payload.get("leaderboard_rank"),
                     bool(prof.get("plus")), prof.get("personaname"),
                     "opendota_players"))
    if rows:
        con.executemany("INSERT OR REPLACE INTO fan.player_profiles VALUES (?,?,?,?,?,?)",
                        rows)


def build_bios(con) -> None:
    """fan.player_bios <- Liquipedia infobox wikitext (bronze 'players') +
    OpenDota /proPlayers country codes. Quietly skips if never ingested."""
    from ..parse import liquipedia_player

    pages: dict[str, str] = {}
    rmap: dict[str, str] = {}
    for path in manifest.all_files("players", "bios/pages_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        pages.update(liquipedia_player.pages_from_query(payload))
        rmap.update(liquipedia_player.redirect_map(payload))
    pro_files = manifest.all_files("opendota", "proPlayers.json")
    pro = {p.get("account_id"): p
           for p in (json.loads(pro_files[-1].read_text(encoding="utf-8")) if pro_files else [])}
    if not pages and not pro:
        return

    roster = con.execute(
        "SELECT account_id, liquipedia_page, nickname FROM fan.players"
    ).fetchall()
    rows = []
    for acc, page, _nick in roster:
        spaced = (page or "").replace("_", " ")
        spaced = rmap.get(spaced, spaced)
        wikitext = pages.get(spaced) or pages.get(page or "")
        bio = liquipedia_player.bio_from_wikitext(wikitext or "")
        p = pro.get(acc) or {}
        rows.append((
            acc,
            bio["real_name"] or p.get("name"),
            bio["birth_date"],
            bio["country"],
            p.get("country_code") or None,
            json.dumps(bio["history"]),
            "liquipedia+opendota",
        ))
    con.executemany("INSERT OR REPLACE INTO fan.player_bios VALUES (?,?,?,?,?,?,?)", rows)
