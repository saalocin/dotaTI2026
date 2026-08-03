"""Load hand-maintained seeds/*.csv into silver verbatim."""

from __future__ import annotations

from .. import config


def _p(name: str) -> str:
    return (config.SEEDS_DIR / name).as_posix()


def load(con) -> None:
    con.execute(
        f"""
        INSERT INTO pred.teams
        SELECT team_key, display_name, liquipedia_page, opendota_team_id, datdota_team_id,
               CASE WHEN opendota_alt_ids IS NULL OR opendota_alt_ids = '' THEN []::BIGINT[]
                    ELSE list_transform(string_split(opendota_alt_ids, '|'), x -> CAST(x AS BIGINT))
               END,
               string_split(aliases, '|'), region, seed_note
        FROM read_csv('{_p("team_crosswalk.csv")}', header = true,
                      types = {{'opendota_alt_ids': 'VARCHAR'}})
        """
    )
    if (config.SEEDS_DIR / "manual_priors.csv").exists():
        con.execute(
            f"""
            INSERT INTO pred.manual_priors
            SELECT team_key, prior_z, basis
            FROM read_csv('{_p("manual_priors.csv")}', header = true)
            """
        )
    con.execute(
        f"""
        INSERT INTO fan.players
        SELECT account_id, nickname, od_name, team_key, position, fantasy_slot,
               liquipedia_page, TRUE, 'seed'
        FROM read_csv('{_p("player_roster.csv")}', header = true)
        """
    )
    con.execute(
        f"""
        INSERT INTO pred.bracket_slots (slot_id, round_no, side, best_of,
                                        feeds_winner_slot, feeds_loser_slot, note)
        SELECT slot_id, round_no, side, best_of, feeds_winner_slot, feeds_loser_slot, note
        FROM read_csv('{_p("bracket_topology.csv")}', header = true)
        """
    )
    con.execute(
        f"""
        INSERT INTO meta.event_calendar
        SELECT period_id, label, starts_at, ends_at, lock_at
        FROM read_csv('{_p("event_calendar.csv")}', header = true,
                      types = {{'starts_at': 'TIMESTAMP', 'ends_at': 'TIMESTAMP', 'lock_at': 'TIMESTAMP'}})
        """
    )
    con.execute(
        f"""
        INSERT INTO meta.coach_titles
        SELECT title, side, pct, cond_type, cond_param, note
        FROM read_csv('{_p("coach_titles.csv")}', header = true,
                      types = {{'cond_param': 'VARCHAR', 'note': 'VARCHAR'}})
        """
    )
    con.execute(
        f"""
        INSERT INTO meta.hero_tags
        SELECT hero_id, hero, string_split(tags, '|'), source
        FROM read_csv('{_p("hero_tags.csv")}', header = true,
                      types = {{'hero_id': 'INTEGER', 'hero': 'VARCHAR',
                                'tags': 'VARCHAR', 'source': 'VARCHAR'}})
        """
    )
    con.execute(
        """
        INSERT INTO meta.stat_calibration (stat, multiplier, offset_pts, fitted_from, note) VALUES
        ('madstone_proxy', 1.0, 0.0, 'default', 'item_uses.madstone_bundle counts BUNDLE pickups; calibrate vs in-client scoreboard'),
        ('stuns_s', 1.0, 0.0, 'default', 'OpenDota parser seconds; Valve pipeline differed at TI2025 once'),
        ('teamfight_participation', 1.0, 0.0, 'default', 'OpenDota heuristic 0-1; Valve formula undocumented'),
        ('watchers_taken', 1.0, 0.0, 'default', 'ability_lamp_use counts every activation incl. re-flips; verify vs in-client "captured" definition — LGD-duo edge rides on this stat')
        """
    )
