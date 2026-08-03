"""Build the predictions (teams) domain from bronze.

- pred.team_ratings   <- every liquipedia snapshot's Portal:Rankings
- pred.games          <- team match logs + explorer roster games (12-month window);
                         team_key via roster-majority attribution (>=3 seeded players/side),
                         crosswalk id (incl. alt ids) as fallback — see pred.games DDL comment
- pred.series pre_ti  <- synthesized from games (same entity pair+league, <12h gaps)
- pred.series TI + swiss_standings + bracket fill <- Liquipedia TI2026 pages (empty until Aug 13)
- pred.odds           <- seeds/manual_odds.csv
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone

from .. import config, manifest
from ..parse import liquipedia_html, liquipedia_ratings

GAMES_WINDOW_DAYS = 365
SERIES_GAP_H = 12

GROUP_PAGE = "The_International_2026_Group_Stage.json"
MAIN_PAGE = "The_International_2026_Main_Event.json"
RANKINGS_PAGE = "Portal_Rankings.json"


def _utc(epoch) -> datetime | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None) if epoch else None


def _snapshot_ts(snapshot_id: str) -> datetime:
    fmt = "%Y-%m-%dT%H%M" if "T" in snapshot_id else "%Y-%m-%d"
    return datetime.strptime(snapshot_id, fmt)


def alias_map(con) -> dict[str, str]:
    m: dict[str, str] = {}
    for team_key, display, aliases in con.execute(
        "SELECT team_key, display_name, aliases FROM pred.teams"
    ).fetchall():
        for name in {display, *(aliases or [])}:
            m[name.strip().casefold()] = team_key
    return m


def _resolve(am: dict[str, str], raw: str | None) -> str | None:
    return am.get((raw or "").strip().casefold())


def _read_parse_html(path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["parse"]["text"]


def build_ratings(con, am: dict[str, str]) -> None:
    reset_keys = {
        k for (k,) in con.execute(
            "SELECT team_key FROM pred.teams WHERE seed_note ILIKE '%reset%'"
        ).fetchall()
    }
    for path in manifest.all_files("liquipedia", f"parse_html/{RANKINGS_PAGE}"):
        snap_date = _snapshot_ts(path.parent.parent.name).date()
        rows = liquipedia_ratings.ratings(_read_parse_html(path))
        for r in rows:
            key = _resolve(am, r["raw_name"])
            con.execute(
                "INSERT OR REPLACE INTO pred.team_ratings VALUES (?,?,?,?,?,?,?,?)",
                [snap_date, "liquipedia", r["raw_name"], key, r["rank"], r["rating"],
                 None, key in reset_keys],
            )
    # archived Portal:Rankings (Wayback) — same source, historical snapshot_dates.
    # Pre-rename raw names (Tundra Esports, L1GA TEAM) resolve via crosswalk aliases,
    # recovering the very ratings the live board reset away.
    for path in manifest.all_files("wayback", "rankings_*.html"):
        snap_date = datetime.strptime(path.stem.split("_")[1], "%Y%m%d").date()
        for r in liquipedia_ratings.ratings(path.read_text(encoding="utf-8", errors="replace")):
            key = _resolve(am, r["raw_name"])
            con.execute(
                "INSERT OR REPLACE INTO pred.team_ratings VALUES (?,?,?,?,?,?,?,?)",
                [snap_date, "liquipedia", r["raw_name"], key, r["rank"], r["rating"],
                 None, key in reset_keys],
            )


def _game_shell(mid: int, row: dict, source: str) -> dict:
    return {
        "match_id": mid,
        "radiant_team_id": None,
        "dire_team_id": None,
        "radiant_team_key": None,
        "dire_team_key": None,
        "radiant_key_source": None,
        "dire_key_source": None,
        "radiant_win": row.get("radiant_win"),
        "duration": row.get("duration"),
        "start_time": row.get("start_time"),
        "leagueid": row.get("leagueid"),
        "league_name": row.get("league_name"),
        "source": source,
    }


def _load_team_matches(games: dict[int, dict], cutoff: float) -> None:
    """Merge OpenDota /teams/{id}/matches logs (id-stamped, no player detail)."""
    for path in manifest.all_files("opendota", "team_matches/*.json"):
        team_id = int(path.stem)
        for row in json.loads(path.read_text(encoding="utf-8")):
            if (row.get("start_time") or 0) < cutoff:
                continue
            mid = row["match_id"]
            g = games.setdefault(mid, _game_shell(mid, row, "opendota_team_matches"))
            opp = row.get("opposing_team_id")
            if row.get("radiant"):
                g["radiant_team_id"] = team_id
                g["dire_team_id"] = g["dire_team_id"] or opp
            elif row.get("radiant") is False:
                g["dire_team_id"] = team_id
                g["radiant_team_id"] = g["radiant_team_id"] or opp


def _merge_player_games(games, membership, roster_team, cutoff) -> None:
    """Explorer rows + /matches payloads: ensure a pred.games row exists for every
    roster game (team logs miss teams whose ids drifted) and count seeded-roster
    players per side for majority attribution."""
    from ..bronze import opendota as bronze_opendota

    seen: set[tuple[int, int]] = set()

    def visit(mid: int, is_radiant: bool, account_id: int, meta: dict, source: str) -> None:
        if (meta.get("start_time") or 0) < cutoff:
            return
        g = games.setdefault(mid, _game_shell(mid, meta, source))
        for f in ("radiant_team_id", "dire_team_id", "radiant_win", "duration",
                  "start_time", "leagueid", "league_name"):
            if g.get(f) is None and meta.get(f) is not None:
                g[f] = meta[f]
        key = roster_team.get(account_id)
        if key and (mid, account_id) not in seen:
            seen.add((mid, account_id))
            side = membership.setdefault(mid, {True: {}, False: {}})[is_radiant]
            side[key] = side.get(key, 0) + 1

    for row in bronze_opendota.explorer_rows():
        slot = row.get("player_slot")
        if row.get("account_id") is None or slot is None:
            continue
        visit(row["match_id"], slot < 128, row["account_id"], row, "opendota_explorer")
    for path in manifest.all_files("opendota", "matches/*.json"):
        match = json.loads(path.read_text(encoding="utf-8"))
        league = match.get("league") or {}
        meta = {
            "radiant_team_id": (match.get("radiant_team") or {}).get("team_id", match.get("radiant_team_id")),
            "dire_team_id": (match.get("dire_team") or {}).get("team_id", match.get("dire_team_id")),
            "radiant_win": match.get("radiant_win"),
            "duration": match.get("duration"),
            "start_time": match.get("start_time"),
            "leagueid": league.get("leagueid", match.get("leagueid")),
            "league_name": league.get("name"),
        }
        for pl in match.get("players", []):
            if not pl.get("account_id") or pl.get("player_slot") is None:
                continue
            visit(match["match_id"], pl["player_slot"] < 128, pl["account_id"], meta, "opendota_match")


def id2key_ext(con) -> dict[int, tuple[str, str]]:
    """opendota team id -> (team_key, key_source); primary ids beat alt ids."""
    rows = con.execute(
        "SELECT team_key, opendota_team_id, opendota_alt_ids FROM pred.teams"
    ).fetchall()
    m: dict[int, tuple[str, str]] = {}
    for key, _tid, alts in rows:
        for a in alts or []:
            m.setdefault(int(a), (key, "team_id_alt"))
    for key, tid, _alts in rows:
        if tid is not None:
            m[int(tid)] = (key, "team_id")
    return m


def attribute_side(member_counts: dict[str, int] | None, team_id, id2k) -> tuple[str | None, str | None]:
    """One side's team: roster-majority (>=3 of the seeded 5 together) beats ids —
    ids drift on re-registration (HULIGANI) and persist across roster swaps (OG).
    3+3 > 5, so a majority is always unique."""
    for key, n in (member_counts or {}).items():
        if n >= 3:
            return key, "roster_majority"
    return id2k.get(team_id) or (None, None)


def _ent(key: str | None, tid) -> str | None:
    """Series-grouping entity: attributed team_key when known, else the raw id —
    a renamed team stays one entity even as its id drifts between events."""
    return key or (f"id:{tid}" if tid else None)


def _ent_key(e: str | None) -> str | None:
    return None if e is None or e.startswith("id:") else e


def _ent_raw(e: str | None) -> str | None:
    return e.removeprefix("id:") if e else None


def build_games_and_preti_series(con, am: dict[str, str]) -> None:
    id2k = id2key_ext(con)
    roster_team: dict[int, str] = dict(
        con.execute("SELECT account_id, team_key FROM fan.players").fetchall()
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=GAMES_WINDOW_DAYS)).timestamp()

    games: dict[int, dict] = {}
    membership: dict[int, dict[bool, dict[str, int]]] = {}
    _load_team_matches(games, cutoff)
    _merge_player_games(games, membership, roster_team, cutoff)

    for g in games.values():
        counts = membership.get(g["match_id"], {})
        g["radiant_team_key"], g["radiant_key_source"] = attribute_side(
            counts.get(True), g["radiant_team_id"], id2k
        )
        g["dire_team_key"], g["dire_key_source"] = attribute_side(
            counts.get(False), g["dire_team_id"], id2k
        )

    con.executemany(
        "INSERT OR REPLACE INTO pred.games VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                g["match_id"], None, None,
                g["radiant_team_id"], g["dire_team_id"],
                g["radiant_team_key"], g["dire_team_key"],
                g["radiant_key_source"], g["dire_key_source"],
                g["radiant_win"], g["duration"], g["leagueid"], g["league_name"],
                _utc(g["start_time"]), g["source"],
            )
            for g in games.values()
        ],
    )

    # real series where STRATZ ground truth exists; heuristic synthesis for the rest
    stratz_map = {
        mid: (sid, bo) for mid, sid, bo in con.execute(
            "SELECT match_id, series_id, best_of FROM pred.match_series"
        ).fetchall()
    }

    ordered = []
    for g in games.values():
        e_r = _ent(g["radiant_team_key"], g["radiant_team_id"])
        e_d = _ent(g["dire_team_key"], g["dire_team_id"])
        if e_r and e_d and e_r != e_d and g["start_time"]:
            g["_er"], g["_ed"] = e_r, e_d
            ordered.append(g)
    ordered.sort(key=lambda g: g["start_time"])

    series_rows, game_updates = [], []

    by_sid: dict[int, list[dict]] = {}
    leftovers = []
    for g in ordered:
        hit = stratz_map.get(g["match_id"])
        if hit and hit[0]:
            by_sid.setdefault(hit[0], []).append(g)
        else:
            leftovers.append(g)
    for sid_raw, gs in by_sid.items():
        gs.sort(key=lambda x: x["match_id"])
        sid = f"sz:{sid_raw}"
        bo = next((stratz_map[g["match_id"]][1] for g in gs
                   if stratz_map.get(g["match_id"], (None, None))[1]), None)
        e1, e2 = sorted((gs[0]["_er"], gs[0]["_ed"]))
        decided = [g for g in gs if g["radiant_win"] is not None and {g["_er"], g["_ed"]} == {e1, e2}]
        w1 = sum(1 for g in decided if (g["_er"] == e1) == g["radiant_win"])
        w2 = len(decided) - w1
        winner = e1 if w1 > w2 else e2 if w2 > w1 else None
        series_rows.append(
            (
                sid, "pre_ti", None, bo,
                _ent_key(e1), _ent_key(e2), _ent_raw(e1), _ent_raw(e2), w1, w2,
                _ent_key(winner), _utc(gs[0]["start_time"]), True, "stratz_series",
            )
        )
        for i, g in enumerate(gs, 1):
            game_updates.append((sid, i, g["match_id"]))

    # heuristic for the remainder: same unordered entity pair + league, gaps < SERIES_GAP_H
    buckets: dict[tuple, list[dict]] = {}
    for g in leftovers:
        pair = tuple(sorted((g["_er"], g["_ed"])))
        buckets.setdefault((pair, g["leagueid"]), []).append(g)

    for (pair, leagueid), gs in buckets.items():
        chunk: list[dict] = []
        chunks = [chunk]
        for g in gs:
            if chunk and g["start_time"] - chunk[-1]["start_time"] > SERIES_GAP_H * 3600:
                chunk = [g]
                chunks.append(chunk)
            else:
                chunk.append(g)
        for ch in chunks:
            if not ch:
                continue
            sid = f"od:{min(g['match_id'] for g in ch)}"
            e1, e2 = pair
            decided = [g for g in ch if g["radiant_win"] is not None]
            w1 = sum(1 for g in decided if (g["_er"] == e1) == g["radiant_win"])
            w2 = len(decided) - w1
            winner = e1 if w1 > w2 else e2 if w2 > w1 else None
            series_rows.append(
                (
                    sid, "pre_ti", None, None,
                    _ent_key(e1), _ent_key(e2), _ent_raw(e1), _ent_raw(e2), w1, w2,
                    _ent_key(winner), _utc(ch[0]["start_time"]), True, "opendota_synth",
                )
            )
            for i, g in enumerate(sorted(ch, key=lambda x: x["match_id"]), 1):
                game_updates.append((sid, i, g["match_id"]))
    con.executemany(
        "INSERT OR REPLACE INTO pred.series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", series_rows
    )
    con.executemany("UPDATE pred.games SET series_id = ?, game_no = ? WHERE match_id = ?", game_updates)


def _series_id(stage: str, rec: liquipedia_html.SeriesRec) -> str:
    pair = "-".join(sorted(re.sub(r"\W+", "", n).lower() for n in (rec.team1_raw, rec.team2_raw)))
    return f"lp:{stage}:{rec.timestamp or 0}:{pair}"


def _upsert_lp_series(con, am, stage: str, recs: list[liquipedia_html.SeriesRec], round_labels=True):
    for rec in recs:
        if not rec.has_teams:
            continue  # TBD bracket node — kept by the parser only for slot alignment
        sid = _series_id(stage, rec)
        completed = rec.winner_raw is not None
        con.execute(
            "INSERT OR REPLACE INTO pred.series VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sid, stage, rec.round_label if round_labels else None,
                max(len(rec.game_match_ids), (rec.score1 or 0) + (rec.score2 or 0)) or None,
                _resolve(am, rec.team1_raw), _resolve(am, rec.team2_raw),
                rec.team1_raw, rec.team2_raw, rec.score1, rec.score2,
                _resolve(am, rec.winner_raw), _utc(rec.timestamp), completed, "liquipedia_html",
            ],
        )
        for i, mid in enumerate(rec.game_match_ids, 1):
            exists = con.execute("SELECT 1 FROM pred.games WHERE match_id = ?", [mid]).fetchone()
            if exists:
                con.execute(
                    "UPDATE pred.games SET series_id = ?, game_no = ? WHERE match_id = ?", [sid, i, mid]
                )
            else:
                con.execute(
                    "INSERT INTO pred.games (match_id, series_id, game_no, source) VALUES (?,?,?,?)",
                    [mid, sid, i, "liquipedia_html"],
                )


def build_ti_pages(con, am: dict[str, str]) -> None:
    # swiss standings from EVERY snapshot (evolution is the point)
    for path in manifest.all_files("liquipedia", f"parse_html/{GROUP_PAGE}"):
        ts = _snapshot_ts(path.parent.parent.name)
        html = _read_parse_html(path)
        for row in liquipedia_html.swiss_standings(html):
            con.execute(
                "INSERT OR REPLACE INTO pred.swiss_standings VALUES (?,?,?,?,?,?,?,?)",
                [ts, _resolve(am, row["raw_name"]), row["raw_name"],
                 sum(1 for r in row["rounds"] if r), row["wins"], row["losses"], None, None],
            )

    # series from the LATEST snapshot of each TI page
    latest_group = manifest.all_files("liquipedia", f"parse_html/{GROUP_PAGE}")
    latest_main = manifest.all_files("liquipedia", f"parse_html/{MAIN_PAGE}")
    if latest_group:
        html = _read_parse_html(latest_group[-1])
        _upsert_lp_series(con, am, "group_swiss", liquipedia_html.matchlist_series(html))
        _upsert_lp_series(con, am, "group_elim", liquipedia_html.bracket_series(html))
    if latest_main:
        html = _read_parse_html(latest_main[-1])
        bracket = liquipedia_html.bracket_series(html)
        slot_map = liquipedia_html.assign_bracket_slots(bracket)  # stamps round_label first
        _upsert_lp_series(con, am, "main_event", bracket)
        for slot_id, rec in slot_map.items():
            if not rec.has_teams:
                continue
            con.execute(
                """UPDATE pred.bracket_slots
                   SET team1_key = ?, team2_key = ?, winner_key = ?, series_id = ?
                   WHERE slot_id = ?""",
                [_resolve(am, rec.team1_raw), _resolve(am, rec.team2_raw),
                 _resolve(am, rec.winner_raw), _series_id("main_event", rec), slot_id],
            )


def build_heroes(con) -> None:
    files = manifest.all_files("opendota", "heroes.json")
    if not files:
        return
    heroes = json.loads(files[-1].read_text(encoding="utf-8"))
    con.executemany(
        "INSERT OR REPLACE INTO meta.heroes VALUES (?,?,?,?,?,?)",
        [
            (h["id"], h.get("name"), h.get("localized_name"), h.get("primary_attr"),
             h.get("attack_type"), h.get("roles") or [])
            for h in heroes
        ],
    )


def build_draft_actions(con) -> None:
    from ..bronze import opendota as bronze_opendota
    from ..parse import opendota_match

    rows: dict[tuple[int, int], tuple] = {}
    for r in bronze_opendota.draft_rows():
        rows[(r["match_id"], r["hero_id"])] = (
            r["match_id"], r.get("ord"), bool(r.get("is_pick")), r["hero_id"],
            r.get("team"), None, "opendota_explorer",
        )
    for path in manifest.all_files("opendota", "matches/*.json"):
        match = json.loads(path.read_text(encoding="utf-8"))
        for r in opendota_match.drafts_from_match_json(match):
            if r["hero_id"] is None:
                continue
            rows[(r["match_id"], r["hero_id"])] = (
                r["match_id"], r.get("ord"), r["is_pick"], r["hero_id"],
                r.get("team"), None, "opendota_match",
            )
    con.executemany(
        "INSERT OR REPLACE INTO pred.draft_actions VALUES (?,?,?,?,?,?,?)", list(rows.values())
    )
    con.execute(
        """
        UPDATE pred.draft_actions d
        SET team_key = CASE WHEN d.team = 0 THEN g.radiant_team_key ELSE g.dire_team_key END
        FROM pred.games g
        WHERE g.match_id = d.match_id AND d.team IN (0, 1)
        """
    )


def build_match_series(con) -> None:
    """pred.match_series <- STRATZ aliased-batch payloads (bronze 'stratz')."""
    bo_map = {"BEST_OF_ONE": 1, "BEST_OF_TWO": 2, "BEST_OF_THREE": 3, "BEST_OF_FIVE": 5}
    rows: dict[int, tuple] = {}
    for path in manifest.all_files("stratz", "series/batch_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for m in (payload.get("data") or {}).values():
            if not m or not m.get("seriesId"):
                continue
            stype = ((m.get("series") or {}).get("type")) or ""
            rows[int(m["id"])] = (int(m["id"]), int(m["seriesId"]),
                                  bo_map.get(stype), "stratz")
    if rows:
        con.executemany("INSERT OR REPLACE INTO pred.match_series VALUES (?,?,?,?)",
                        list(rows.values()))


def build_match_flags(con) -> None:
    """pred.match_flags <- explorer mf_* pages (coach-title condition inputs)."""
    from ..bronze import opendota as bronze_opendota

    rows = bronze_opendota.match_flag_rows()
    if not rows:
        return
    con.executemany(
        "INSERT OR REPLACE INTO pred.match_flags VALUES (?,?,?,?)",
        [(r["match_id"], r.get("first_blood_time"), r.get("tormentor_victims"),
          "opendota_explorer") for r in rows],
    )


def build_market_odds(con, am: dict[str, str]) -> None:
    """pred.market_odds <- every Polymarket snapshot (drift history is the point)."""
    from ..parse import polymarket as pm_parse

    def resolve(question: str) -> str | None:
        q = question.casefold()
        best = None
        for n, k in am.items():   # casefolded alias -> team_key
            n = n.strip()
            if len(n) < 2:
                continue
            if len(n) <= 3:       # short tags (OG, VG, 1w) need word boundaries
                if not re.search(rf"\b{re.escape(n)}\b", q):
                    continue
            elif n not in q:
                continue
            if best is None or len(n) > best[0]:
                best = (len(n), k)
        return best[1] if best else None

    for path in manifest.all_files("polymarket", "events/*.json"):
        ts = _snapshot_ts(path.parent.parent.name)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload:
            continue
        event = payload[0]
        for r in pm_parse.market_rows(event):
            if r["prob"] is None:
                continue
            con.execute(
                "INSERT OR REPLACE INTO pred.market_odds VALUES (?,?,?,?,?,?,?)",
                [ts, "polymarket", event.get("slug"), r["question"] or r["group_title"],
                 resolve(f"{r['question']} {r['group_title']}"), r["prob"], r["volume"]],
            )


def build_news(con) -> None:
    files = manifest.all_files("steamnews", "news.json")
    if not files:
        return
    seen: dict[str, tuple] = {}
    for path in files:
        for it in json.loads(path.read_text(encoding="utf-8")).get(
                "appnews", {}).get("newsitems", []):
            seen[str(it["gid"])] = (str(it["gid"]), _utc(it.get("date")),
                                    it.get("title"), it.get("url"), it.get("feedlabel"))
    con.executemany("INSERT OR REPLACE INTO meta.news VALUES (?,?,?,?,?)",
                    list(seen.values()))


def build_manual_odds(con, am: dict[str, str]) -> None:
    path = config.SEEDS_DIR / "manual_odds.csv"
    if not path.exists():
        return
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not (r.get("odds1") and r.get("odds2")):
                continue
            o1, o2 = float(r["odds1"]), float(r["odds2"])
            raw1, raw2 = 1.0 / o1, 1.0 / o2
            s = raw1 + raw2
            con.execute(
                "INSERT OR REPLACE INTO pred.odds VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    datetime.strptime(r["fetched_date"], "%Y-%m-%d"), "manual",
                    f"{r['team1']} vs {r['team2']}", None,
                    _resolve(am, r["team1"]), _resolve(am, r["team2"]),
                    o1, o2, raw1 / s, raw2 / s,
                    int(r["best_of"]) if r.get("best_of") else None,
                    r.get("stage") or None, False,
                ],
            )


def build(con) -> None:
    am = alias_map(con)
    build_heroes(con)
    build_ratings(con, am)
    build_match_series(con)          # must precede games (series grouping reads it)
    build_games_and_preti_series(con, am)
    build_ti_pages(con, am)
    build_draft_actions(con)
    build_match_flags(con)
    build_market_odds(con, am)
    build_news(con)
    build_manual_odds(con, am)
