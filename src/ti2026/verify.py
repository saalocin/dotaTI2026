"""`ti verify` — end-to-end sanity checks over silver. Pre-event-tolerant:
TI-stage checks (swiss, bracket fill) only run once those tables have rows.
"""

from __future__ import annotations

import duckdb

from . import config

REQUIRED_SLOT_MIX = {"safelane": 1, "mid": 1, "offlane": 1, "support": 2}


class Report:
    def __init__(self):
        self.failures = 0

    def check(self, ok: bool, label: str, detail: str = ""):
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))

    def info(self, label: str):
        print(f"[info] {label}")


def run() -> bool:
    if not config.SILVER_DB.exists():
        print("silver DB missing — run `ti build-silver` first")
        return False
    con = duckdb.connect(str(config.SILVER_DB), read_only=True)
    r = Report()

    # crosswalk
    n_teams, n_ids, n_distinct = con.execute(
        """SELECT count(*), count(opendota_team_id), count(DISTINCT opendota_team_id)
           FROM pred.teams"""
    ).fetchone()
    r.check(n_teams == 16, "crosswalk has 16 teams", f"got {n_teams}")
    r.check(n_ids == 16 and n_distinct == 16, "opendota_team_id set + unique for all 16")

    # roster
    n_players = con.execute("SELECT count(*) FROM fan.players").fetchone()[0]
    r.check(n_players == 80, "roster has 80 players", f"got {n_players}")
    bad_mix = con.execute(
        """
        WITH mix AS (
            SELECT team_key, fantasy_slot, count(*) AS n
            FROM fan.players GROUP BY 1, 2
        )
        SELECT team_key FROM mix
        GROUP BY team_key
        HAVING count(*) FILTER (WHERE fantasy_slot = 'support' AND n = 2) <> 1
            OR count(*) FILTER (WHERE fantasy_slot IN ('safelane','mid','offlane') AND n = 1) <> 3
        """
    ).fetchall()
    r.check(not bad_mix, "every team has 1 safelane/1 mid/1 offlane/2 supports",
            f"bad: {[t[0] for t in bad_mix]}" if bad_mix else "")

    # player_game_stats coverage
    total, parsed_pct = con.execute(
        """SELECT count(*), round(100.0 * avg(CASE WHEN parsed THEN 1 ELSE 0 END), 1)
           FROM fan.player_game_stats"""
    ).fetchone()
    r.check(total > 5000, "player_game_stats has substantial backfill", f"{total} rows")
    r.check((parsed_pct or 0) >= 85, "parsed replay coverage >= 85%", f"{parsed_pct}%")
    thin = con.execute(
        """
        SELECT p.nickname, count(s.match_id) AS games
        FROM fan.players p
        LEFT JOIN fan.player_game_stats s USING (account_id)
        GROUP BY 1 HAVING count(s.match_id) < 10 ORDER BY games
        """
    ).fetchall()
    r.check(not thin, "every roster player has >= 10 backfilled games",
            f"thin: {thin[:6]}" if thin else "")

    # scoring view sanity: recompute one parsed row in python
    row = con.execute(
        """
        SELECT s.kills, s.deaths, s.last_hits, s.denies, s.gpm, s.madstone_proxy,
               s.tower_kills, s.obs_placed, s.camps_stacked, s.rune_pickups,
               s.watchers_taken, s.smokes_used, s.roshan_kills, s.teamfight_participation,
               s.stuns_s, s.tormentor_kills, s.first_blood, s.courier_kills,
               f.pts_total
        FROM fan.player_game_stats s
        JOIN fan.fantasy_points f USING (match_id, account_id)
        WHERE s.parsed ORDER BY s.match_id DESC LIMIT 1
        """
    ).fetchone()
    if row:
        (k, d, lh, dn, gpm, mad, tw, obs, cs_, ru, wa, sm, ro, tf, st, to, fb, co, pts) = row
        expected = (
            k * 107.0 + (1950.0 - 195.0 * d) + (lh + dn) * 3.0 + gpm * 2.0 + (mad or 0) * 13.0
            + (tw or 0) * 352.0 + (obs or 0) * 117.0 + (cs_ or 0) * 234.0 + (ru or 0) * 141.0
            + (wa or 0) * 147.0 + (sm or 0) * 293.0 + (ro or 0) * 1172.0 + (tf or 0) * 2124.0
            + (st or 0) * 10.0 + (to or 0) * 879.0 + (1934.0 if fb else 0.0) + (co or 0) * 703.0
        )
        r.check(abs(expected - pts) < 0.01, "fantasy_points matches hand-computed total",
                f"view {pts:.1f} vs manual {expected:.1f}")
    else:
        r.check(False, "found a parsed row for scoring spot-check")

    # bracket topology
    slots = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE side='UB'), count(*) FILTER (WHERE side='LB'), count(*) FILTER (WHERE side='GF') FROM pred.bracket_slots"
    ).fetchone()
    r.check(slots == (14, 7, 6, 1), "bracket topology = 14 slots (7 UB / 6 LB / 1 GF)", str(slots))

    # ratings coverage
    latest = con.execute(
        """SELECT count(*) FILTER (WHERE team_key IS NOT NULL)
           FROM pred.team_ratings
           WHERE source = 'liquipedia'
             AND snapshot_date = (SELECT max(snapshot_date) FROM pred.team_ratings WHERE source='liquipedia')"""
    ).fetchone()[0]
    r.check(latest >= 10, "Glicko-2 ratings resolve to >= 10 TI teams",
            f"{latest}/16 (RD<100 + rename resets keep some off the board)")

    # games/series
    n_games, n_keyed = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE radiant_team_key IS NOT NULL OR dire_team_key IS NOT NULL) FROM pred.games"
    ).fetchone()
    r.check(n_games > 500, "pred.games backfilled", f"{n_games} games, {n_keyed} with TI team")
    n_series = con.execute("SELECT count(*) FROM pred.series").fetchone()[0]
    r.check(n_series > 100, "pred.series synthesized", f"{n_series} series")

    # roster-majority attribution floor: every unit must be visible to the models.
    # Catches HULIGANI-class silent data loss (id drift) — 55 was the min on 2026-07-31.
    maj_rows = con.execute(
        """
        WITH sides AS (
            SELECT radiant_team_key AS team_key FROM pred.games
            WHERE radiant_key_source = 'roster_majority'
            UNION ALL
            SELECT dire_team_key FROM pred.games
            WHERE dire_key_source = 'roster_majority'
        )
        SELECT t.team_key, count(s.team_key) AS n
        FROM pred.teams t LEFT JOIN sides s USING (team_key)
        GROUP BY 1 ORDER BY n
        """
    ).fetchall()
    thin_maj = [(k, n) for k, n in maj_rows if n < 40]
    r.check(not thin_maj, "every team has >= 40 roster-majority games",
            f"thin: {thin_maj}" if thin_maj else f"min = {maj_rows[0][1]} ({maj_rows[0][0]})")

    # draft data
    n_heroes = con.execute("SELECT count(*) FROM meta.heroes").fetchone()[0]
    r.check(n_heroes >= 120, "meta.heroes loaded", f"{n_heroes} heroes")
    n_draft, n_draft_matches = con.execute(
        "SELECT count(*), count(DISTINCT match_id) FROM pred.draft_actions"
    ).fetchone()
    r.check(n_draft > 20000 and n_draft_matches > 1000, "draft actions backfilled",
            f"{n_draft} actions across {n_draft_matches} matches")
    orphan_heroes = con.execute(
        "SELECT count(*) FROM pred.draft_actions d LEFT JOIN meta.heroes h USING (hero_id) WHERE h.hero_id IS NULL"
    ).fetchone()[0]
    r.check(orphan_heroes == 0, "every draft action maps to a known hero", f"{orphan_heroes} orphans")

    # enrichment sources (soft: tolerate never-ingested, but validate when present)
    n_ms = con.execute("SELECT count(*) FROM pred.match_series").fetchone()[0]
    if n_ms:
        n_sz = con.execute(
            "SELECT count(*) FROM pred.series WHERE source = 'stratz_series'").fetchone()[0]
        cov = con.execute(
            """SELECT round(100.0 * count(ms.match_id) / count(*), 1)
               FROM pred.games g LEFT JOIN pred.match_series ms USING (match_id)"""
        ).fetchone()[0]
        r.check(n_sz > 200, "STRATZ series ground truth applied to pred.series",
                f"{n_ms} matches ({cov}% of games) -> {n_sz} real series")
    else:
        r.info("no STRATZ series backfill yet (`ti ingest stratz --series-backfill`)")
    n_mf, mf_cov = con.execute(
        """SELECT count(*),
                  round(100.0 * (SELECT count(*) FROM fan.player_game_stats s
                                 JOIN pred.match_flags mf USING (match_id) WHERE s.parsed)
                      / nullif((SELECT count(*) FROM fan.player_game_stats WHERE parsed), 0), 1)
           FROM pred.match_flags"""
    ).fetchone()
    r.check(bool(n_mf) and (mf_cov or 0) >= 90.0,
            "coach-title match flags (FB time / tormentor deaths) cover the pool",
            f"{n_mf} matches, {mf_cov}% of parsed player-games")
    n_mo, n_mo_team = con.execute(
        "SELECT count(*), count(team_key) FROM pred.market_odds").fetchone()
    if n_mo:
        r.check(n_mo_team >= 10, "Polymarket winner markets resolve to TI teams",
                f"{n_mo} rows, {n_mo_team} team-resolved")
    else:
        r.info("no market odds yet (`ti ingest polymarket`)")
    n_news = con.execute("SELECT count(*) FROM meta.news").fetchone()[0]
    r.info(f"meta.news items: {n_news}")

    # gold layer (derived; absent until `ti build-gold` runs after a silver rebuild)
    has_gold = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'gold'"
    ).fetchone()[0] > 0
    if has_gold:
        n_mp = con.execute("SELECT count(*) FROM gold.matchup_probs").fetchone()[0]
        r.check(n_mp == 240, "gold.matchup_probs is a full 16x16 matrix", f"{n_mp} rows")
        bad_sym = con.execute(
            """SELECT count(*) FROM gold.matchup_probs a JOIN gold.matchup_probs b
               ON a.team_a = b.team_b AND a.team_b = b.team_a
               WHERE abs(a.p_map + b.p_map - 1.0) > 1e-6"""
        ).fetchone()[0]
        r.check(bad_sym == 0, "matchup matrix symmetric (p_ab + p_ba = 1)", f"{bad_sym} bad")
        n_ts = con.execute("SELECT count(*) FROM gold.team_strength").fetchone()[0]
        r.check(n_ts == 16, "gold.team_strength covers all 16 teams", f"{n_ts}")
        latest_grp = con.execute(
            """SELECT run_id FROM gold.sim_meta
               WHERE run_id IN (SELECT DISTINCT run_id FROM gold.sim_group_draws)
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        if latest_grp:
            bad_struct = con.execute(
                """SELECT count(*) FROM (
                       SELECT draw_id FROM gold.sim_group_draws WHERE run_id = ?
                       GROUP BY draw_id
                       HAVING count(*) FILTER (WHERE record = '4-0') <> 1
                           OR count(*) FILTER (WHERE record = '4-1') <> 2
                           OR count(*) FILTER (WHERE made_playoffs) <> 8
                   )""",
                [latest_grp[0]],
            ).fetchone()[0]
            r.check(bad_struct == 0, "every sim draw obeys structural Swiss counts",
                    f"{bad_struct} bad draws")
    else:
        r.info("gold layer not built (run `ti build-gold`)")

    # tormentor cross-check (datdota blocked -> table may be empty; informational)
    n_x = con.execute("SELECT count(*) FROM fan.tormentor_xcheck").fetchone()[0]
    if n_x:
        agree = con.execute(
            """SELECT round(100.0 * avg(CASE WHEN x.tormentor_kills = s.tormentor_kills THEN 1 ELSE 0 END), 1)
               FROM fan.tormentor_xcheck x JOIN fan.player_game_stats s USING (match_id, account_id)"""
        ).fetchone()[0]
        r.check((agree or 0) >= 99, "tormentor kills agree OpenDota vs datdota", f"{agree}%")
    else:
        r.info("tormentor cross-check skipped (datdota blocked by Cloudflare)")

    # TI-stage checks activate once data exists
    n_swiss = con.execute("SELECT count(*) FROM pred.swiss_standings").fetchone()[0]
    r.info(f"swiss_standings rows: {n_swiss} (0 until Aug 13)")

    con.close()
    print(("ALL CHECKS PASSED" if r.failures == 0 else f"{r.failures} CHECK(S) FAILED"))
    return r.failures == 0
