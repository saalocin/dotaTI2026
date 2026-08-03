"""Scoring views against hand-computed values, on an in-memory DuckDB."""

import duckdb
import pytest

from ti2026.silver import db


@pytest.fixture()
def con():
    con = duckdb.connect(":memory:")
    con.execute((db.SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    con.execute(
        "INSERT INTO meta.stat_calibration (stat, multiplier) VALUES"
        " ('madstone_proxy', 1.0), ('stuns_s', 1.0), ('teamfight_participation', 1.0)"
    )
    yield con
    con.close()


STAT_COLS = (
    "match_id, account_id, kills, deaths, last_hits, denies, gpm, madstone_proxy, "
    "tower_kills, obs_placed, camps_stacked, rune_pickups, watchers_taken, smokes_used, "
    "lotuses_grabbed, lotus_proxy_famango, roshan_kills, teamfight_participation, stuns_s, "
    "tormentor_kills, first_blood, courier_kills, parsed, series_id"
)


def test_fantasy_points_hand_computed(con):
    con.execute(
        f"INSERT INTO fan.player_game_stats ({STAT_COLS}) VALUES "
        "(1, 42, 10, 2, 300, 20, 600, 12, 1, 8, 4, 6, 5, 3, NULL, 2, 1, 0.8, 25.5, 1, TRUE, 2, TRUE, NULL)"
    )
    con.execute((db.SQL_DIR / "views.sql").read_text(encoding="utf-8"))
    row = con.execute(
        "SELECT pts_total, lotus_missing, pts_deaths, pts_tfp FROM fan.fantasy_points"
    ).fetchone()
    expected = (
        10 * 107.0 + (1950.0 - 195.0 * 2) + (300 + 20) * 3.0 + 600 * 2.0 + 12 * 13.0
        + 1 * 352.0 + 8 * 117.0 + 4 * 234.0 + 6 * 141.0 + 5 * 147.0 + 3 * 293.0
        + 0.0  # lotus NULL -> contributes 0, flagged
        + 1 * 1172.0 + 0.8 * 2124.0 + 25.5 * 10.0 + 1 * 879.0 + 1934.0 + 2 * 703.0
    )
    assert row[0] == pytest.approx(expected)
    assert row[1] is True
    assert row[2] == pytest.approx(1950.0 - 390.0)
    assert row[3] == pytest.approx(0.8 * 2124.0)


def test_player_series_points_top2(con):
    # three games in one series, only kills differ: 1950 / 3020 / 4090 pts
    for match_id, kills in ((1, 0), (2, 10), (3, 20)):
        con.execute(
            f"INSERT INTO fan.player_game_stats ({STAT_COLS}) VALUES "
            f"({match_id}, 7, {kills}, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, NULL, 0, 0, 0.0, 0.0, 0, FALSE, 0, TRUE, 'S1')"
        )
    con.execute((db.SQL_DIR / "views.sql").read_text(encoding="utf-8"))
    row = con.execute(
        "SELECT games_played, pts_series_top2 FROM fan.player_series_points WHERE series_id = 'S1'"
    ).fetchone()
    assert row[0] == 3
    assert row[1] == pytest.approx(4090.0 + 3020.0)  # top-2 of 1950/3020/4090
