"""War Banner trait/quality math (hand-computed) and the top-2 / best-series
order statistics with degenerate banks."""

import numpy as np
import pandas as pd
import pytest

from ti2026.gold import fantasy

PTS = {"pts_kills": 100.0, "pts_deaths": 200.0, "pts_tfp": 300.0}


def test_quality_boosts_only():
    v = fantasy.banner_score(PTS, [("pts_kills", 1, None), ("pts_deaths", 2, None),
                                   ("pts_tfp", 3, None)])
    assert v == pytest.approx(100 * 1.1 + 200 * 1.3 + 300 * 1.6)


# quality + trait percentages combine ADDITIVELY on the base score (tutorial_05/06
# wording; in-client header shows Tier II (+30%) with a -10% adjacency as net 120%)

def test_vampiric_hits_neighbors_only():
    v = fantasy.banner_score(PTS, [("pts_kills", 1, "vampiric"), ("pts_deaths", 2, None),
                                   ("pts_tfp", 3, None)])
    # kills +10%+50%, deaths (adjacent) +30%-10%, tfp (edge slot, not adjacent) untouched
    assert v == pytest.approx(100 * 1.6 + 200 * 1.2 + 300 * 1.6)


def test_benevolent_from_middle_boosts_both_edges():
    v = fantasy.banner_score(PTS, [("pts_kills", 1, None), ("pts_deaths", 2, "benevolent"),
                                   ("pts_tfp", 3, None)])
    assert v == pytest.approx(100 * (1 + .10 + .20) + 200 * 1.3 + 300 * (1 + .60 + .20))


def test_fractal_needs_distinct_qualities():
    with_distinct = fantasy.banner_score(
        PTS, [("pts_kills", 1, "fractal"), ("pts_deaths", 2, None), ("pts_tfp", 3, None)])
    assert with_distinct == pytest.approx(100 * (1 + .10 + .60) + 200 * 1.3 + 300 * 1.6)
    with_dupes = fantasy.banner_score(
        PTS, [("pts_kills", 1, "fractal"), ("pts_deaths", 1, None), ("pts_tfp", 3, None)])
    assert with_dupes == pytest.approx(100 * 1.1 + 200 * 1.1 + 300 * 1.6)


def test_unique_and_friendly_conditions():
    one_unique = fantasy.banner_score(
        PTS, [("pts_kills", 1, "unique"), ("pts_deaths", 2, None), ("pts_tfp", 3, None)])
    assert one_unique == pytest.approx(100 * (1 + .10 + .30) + 200 * 1.3 + 300 * 1.6)
    two_uniques = fantasy.banner_score(
        PTS, [("pts_kills", 1, "unique"), ("pts_deaths", 2, "unique"), ("pts_tfp", 3, None)])
    assert two_uniques == pytest.approx(100 * 1.1 + 200 * 1.3 + 300 * 1.6)
    friendly3 = fantasy.banner_score(
        PTS, [("pts_kills", 1, "friendly"), ("pts_deaths", 2, "friendly"),
              ("pts_tfp", 3, "friendly")])
    assert friendly3 == pytest.approx(100 * (1 + .10 + .50) + 200 * (1 + .30 + .50)
                                      + 300 * (1 + .60 + .50))


def test_slot_options_per_color():
    """Support 2B+1G: 4 ranked blue options + 3 ranked green options, colors legal."""
    means = np.zeros(len(fantasy.STAT_COLS))
    idx = {c: i for i, c in enumerate(fantasy.STAT_COLS)}
    means[idx["pts_wards"]] = 900        # blue
    means[idx["pts_smokes"]] = 800       # blue
    means[idx["pts_watchers"]] = 700     # blue
    means[idx["pts_stacks"]] = 650       # blue
    means[idx["pts_runes"]] = 640        # blue (rank 5 -> must be cut)
    means[idx["pts_tfp"]] = 600          # green
    means[idx["pts_stuns"]] = 350        # green
    groups = fantasy.slot_options(means, ["blue", "blue", "green"])
    assert [(g["color"], g["n"], len(g["opts"])) for g in groups] == [
        ("blue", 2, 4), ("green", 1, 3)]
    blue, green = groups
    assert [o[0] for o in blue["opts"]] == ["wards", "smokes", "watchers", "stacks"]
    assert [o[1] for o in blue["opts"]] == sorted([o[1] for o in blue["opts"]], reverse=True)
    assert green["opts"][0] == ["tfp", 600]
    for g in groups:
        assert all(fantasy.STAT_COLOR["pts_" + o[0]] == g["color"] for o in g["opts"])


def test_color_locked_stat_selection():
    """A support-shaped bank whose best three stats are all blue must still fit
    the blue|blue|green layout: two blues + the best green."""
    W = np.zeros((1, len(fantasy.STAT_COLS)))
    idx = {c: i for i, c in enumerate(fantasy.STAT_COLS)}
    W[0, idx["pts_wards"]] = 900       # blue
    W[0, idx["pts_smokes"]] = 800      # blue
    W[0, idx["pts_watchers"]] = 700    # blue (must be displaced by a green)
    W[0, idx["pts_tfp"]] = 600         # best green
    bank = {"W": W, "wW": np.ones(1), "L": np.zeros((0, len(fantasy.STAT_COLS))),
            "wL": np.zeros(0), "joint": True, "n_w": 1, "n_l": 0}
    free = fantasy.choose_stats(bank)
    locked = fantasy.choose_stats(bank, layout=["blue", "blue", "green"])
    assert [fantasy.STAT_COLS[i] for i in free[:3]] == ["pts_wards", "pts_smokes",
                                                        "pts_watchers"]
    assert [fantasy.STAT_COLS[i] for i in locked] == ["pts_wards", "pts_smokes", "pts_tfp"]


def _degenerate_bank(win_score: float, loss_score: float):
    # one stat column carries everything; single row per pool
    W = np.zeros((1, len(fantasy.STAT_COLS)))
    W[0, 0] = win_score
    L = np.zeros((1, len(fantasy.STAT_COLS)))
    L[0, 0] = loss_score
    return {"W": W, "wW": np.ones(1), "L": L, "wL": np.ones(1),
            "joint": True, "n_w": 1, "n_l": 1}


def test_top2_and_best_series_order_stats():
    # draw 0: two series (2-1 win, 1-2 loss); draw 1: one bo5 3-2 win
    paths = pd.DataFrame({
        "draw_id":   [0, 0, 1],
        "period_id": ["p1", "p1", "p1"],
        "won":       [True, False, True],
        "n_games":   [3, 3, 5],
        "best_of":   [3, 3, 5],
    })
    bank = _degenerate_bank(10.0, 4.0)
    out = fantasy.card_period_scores(bank, [0], paths, n_draws=2,
                                     rng=np.random.default_rng(0))
    # draw 0: win series games [10,10,4] -> top2 20; loss series [10,4,4] -> 14; best = 20
    # draw 1: 3-2 bo5 -> games [10,10,10,4,4] -> top2 20
    assert out["p1"][0] == pytest.approx(20.0)
    assert out["p1"][1] == pytest.approx(20.0)


def test_no_series_means_zero_period_score():
    paths = pd.DataFrame({
        "draw_id": [0], "period_id": ["p2"], "won": [True], "n_games": [2], "best_of": [3],
    })
    bank = _degenerate_bank(7.0, 3.0)
    out = fantasy.card_period_scores(bank, [0], paths, n_draws=3,
                                     rng=np.random.default_rng(0))
    assert out["p2"][0] == pytest.approx(14.0)   # 2-0 sweep: [7,7] -> 14
    assert out["p2"][1] == 0.0 and out["p2"][2] == 0.0


# ------------------------------------------------------------- coach titles

def _titles(prefix_pct=0.10):
    prefixes = [{"name": "P", "pct": prefix_pct, "tags": ["red"], "note": ""}]
    suffixes = [
        {"name": "D", "pct": 0.24, "cond": "duration_lt_s", "param": "1500", "note": ""},
        {"name": "U", "pct": 0.06, "cond": "player_loss", "param": "", "note": ""},
        {"name": "C", "pct": 0.16, "cond": "deciding_game", "param": "", "note": ""},
        {"name": "K", "pct": 0.13, "cond": "own_fountain_death", "param": "", "note": ""},
    ]
    return {"prefixes": prefixes, "suffixes": suffixes, "hero_tags": {1: {"red"}},
            "fcols": fantasy.F_BASE + ["f_p0"], "tagged_heroes": 1}


def _titled_bank(win_score, loss_score, win_flags, loss_flags):
    bank = _degenerate_bank(win_score, loss_score)
    bank["FW"] = np.array([win_flags], dtype=float)
    bank["FL"] = np.array([loss_flags], dtype=float)
    return bank


def test_title_combo_evs_hand_case():
    """2-1 Bo3 win; wins are short red-hero games (10 pts), the loss is neither (4).

    prefix +10% hits both wins; per suffix: Decisive x1.24 on wins, Underdog only
    on the (non-top-2) loss, Clutch on the deciding game (a win), Cruel constant."""
    titles = _titles()
    bank = _titled_bank(10.0, 4.0, [1, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0])
    paths = pd.DataFrame({"draw_id": [0], "period_id": ["p1"], "won": [True],
                          "n_games": [3], "best_of": [3]})
    base, ev64 = fantasy.card_period_scores(bank, [0], paths, n_draws=1,
                                            rng=np.random.default_rng(0), titles=titles)
    assert base["p1"][0] == pytest.approx(20.0)
    d, u, c, k = ev64["p1"]
    assert d == pytest.approx(2 * 10 * 1.1 * 1.24, rel=1e-5)
    assert u == pytest.approx(2 * 10 * 1.1, rel=1e-5)          # loss stays out of top-2
    assert c == pytest.approx(10 * 1.1 * 1.16 + 10 * 1.1, rel=1e-5)
    assert k == pytest.approx(2 * 10 * 1.1 * (1 + 0.13 * fantasy.CRUEL_P), rel=1e-5)


def test_title_boost_reorders_top2():
    """A boosted loss game must be allowed to displace a win from the top-2."""
    titles = _titles(prefix_pct=0.0)
    # only the LOSS is a short game: 9 * 1.24 = 11.16 > 10
    bank = _titled_bank(10.0, 9.0, [0] * 6, [1, 0, 0, 0, 0, 0])
    paths = pd.DataFrame({"draw_id": [0], "period_id": ["p1"], "won": [True],
                          "n_games": [3], "best_of": [3]})
    base, ev64 = fantasy.card_period_scores(bank, [0], paths, n_draws=1,
                                            rng=np.random.default_rng(0), titles=titles)
    assert base["p1"][0] == pytest.approx(20.0)                # untitled: [10,10,9]
    assert ev64["p1"][0] == pytest.approx(10 + 9 * 1.24, rel=1e-5)


def test_optimize_rosters_post_groups_p2_only():
    """A post-groups run has p2-only paths and only the alive teams. The
    optimizer must not KeyError (the old s_arr['p1'] bug), must label outputs
    by the real period, and must use the 5-emblem P2 banner layouts."""
    import duckdb

    con = duckdb.connect()
    con.execute("CREATE SCHEMA fan"); con.execute("CREATE SCHEMA gold")
    con.execute("CREATE SCHEMA meta")
    con.execute("""CREATE TABLE fan.players (
        team_key VARCHAR, account_id BIGINT, fantasy_slot VARCHAR, nickname VARCHAR)""")
    acc = 0
    for team in ("alpha", "beta", "gamma"):     # gamma is eliminated (no p2 paths)
        for slot in ("safelane", "mid", "offlane", "support", "support"):
            acc += 1
            con.execute("INSERT INTO fan.players VALUES (?,?,?,?)",
                        [team, acc, slot, f"{team}_{slot}_{acc}"])
    stat_cols = ", ".join(f"{c} DOUBLE" for c in fantasy.STAT_COLS)
    con.execute(f"""CREATE TABLE gold.fantasy_game_pool (
        match_id BIGINT, account_id BIGINT, win BOOLEAN, weight DOUBLE,
        hero_id INT, duration_s INT, first_blood_time_s INT,
        tormentor_victims INT, {stat_cols})""")
    rng_vals = {c: 50.0 + 10*i for i, c in enumerate(fantasy.STAT_COLS)}
    stat_sql = ", ".join(str(rng_vals[c]) for c in fantasy.STAT_COLS)
    mid = 0
    for a in range(1, acc + 1):
        for win in (True, True, False, False):
            mid += 1
            con.execute(f"""INSERT INTO gold.fantasy_game_pool VALUES
                ({mid}, {a}, {win}, 1.0, 1, 2000, 300, 0, {stat_sql})""")
    con.execute("""CREATE TABLE meta.coach_titles (
        title VARCHAR, side VARCHAR, pct DOUBLE, cond_type VARCHAR,
        cond_param VARCHAR, note VARCHAR)""")
    con.execute("INSERT INTO meta.coach_titles VALUES ('P','prefix',10,'hero_tag','red','')")
    con.execute("INSERT INTO meta.coach_titles VALUES ('S','suffix',24,'duration_lt_s','1500','')")
    con.execute("CREATE TABLE meta.hero_tags (hero_id INT, tags VARCHAR[])")
    con.execute("INSERT INTO meta.hero_tags VALUES (1, ['red'])")
    con.execute("""CREATE TABLE gold.sim_fantasy_paths (
        run_id VARCHAR, draw_id INT, team_key VARCHAR, period_id VARCHAR,
        won BOOLEAN, n_games INT, best_of INT)""")
    for d in range(8):
        for team, won in (("alpha", True), ("beta", False)):
            con.execute("INSERT INTO gold.sim_fantasy_paths VALUES (?,?,?,?,?,?,?)",
                        ["pg-test", d, team, "p2", won, 3, 3])

    res = fantasy.optimize_rosters(con, "pg-test", write=False)
    assert res["primary"] == "p2"
    assert res["periods"] == ["p2"]
    assert len(res["table"]) == 8                     # 2^3 alive-team triples only
    seen = {r[k] for r in res["table"] for k in ("support_team", "core_team", "mid_team")}
    assert seen == {"alpha", "beta"}                  # gamma excluded
    assert {"ev_p2", "sd_p2", "q90_p2"} <= set(res["table"][0])
    assert res["top"][0]["best_title"]["prefix"] == "P"
    for meta_ in res["cards_meta"].values():          # P2 banners carry 5 emblems
        assert len(meta_["slots"]) == 5
        assert len(meta_["stats"]) == 5
        assert len(set(meta_["stats"])) == 5          # no duplicate stats


def test_add_flags_semantics():
    titles = _titles()
    df = pd.DataFrame({
        "duration_s": [1499, 1508, 2000],
        "first_blood_time_s": [0, 700, None],
        "tormentor_victims": [2, 0, None],
        "hero_id": [1, 2, 1],
    })
    fantasy._add_flags(df, titles)
    assert df.f_dur.tolist() == [1, 0, 0]
    assert df.f_clock8.tolist() == [0, 1, 0]      # 1508 ends in 8
    assert df.f_fbpre.tolist() == [1, 0, 0]       # fb=0 means pre-horn (API clamps)
    assert df.f_fblate.tolist() == [0, 1, 0]
    assert df.f_torm.tolist() == [1, 0, 0]        # NULL flags read as no-hit
    assert df.f_p0.tolist() == [1, 0, 1]          # hero 1 is tagged red
