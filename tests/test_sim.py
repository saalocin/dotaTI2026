"""Tournament simulator invariants: structural Swiss counts, symmetry under equal
strengths, and hand-computed double-elim propagation under degenerate probabilities."""

import csv
from pathlib import Path

import numpy as np

from ti2026.gold import sim as gsim

SEEDS = Path(__file__).parents[1] / "seeds"


def _topology():
    with open(SEEDS / "bracket_topology.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (int(r["round_no"]), r["slot_id"]))
    return [
        (r["slot_id"], int(r["best_of"]),
         r["feeds_winner_slot"] or None, r["feeds_loser_slot"] or None)
        for r in rows
    ]


def test_swiss_structure_and_series_counts():
    P = np.full((16, 16), 0.5)
    grp = gsim.simulate_group(P, 0.0, 400, np.random.default_rng(7))  # internal asserts too
    games = grp["wins"] + grp["losses"]
    assert games.min() == 4 and games.max() == 5
    four_oh = (grp["wins"] == 4) & (grp["losses"] == 0)
    zero_four = (grp["wins"] == 0) & (grp["losses"] == 4)
    assert np.all(games[four_oh] == 4) and np.all(games[zero_four] == 4)
    assert np.all(grp["made_playoffs"].sum(axis=1) == 8)


def test_equal_strengths_are_symmetric():
    P = np.full((16, 16), 0.5)
    grp = gsim.simulate_group(P, 0.0, 20000, np.random.default_rng(11))
    p40 = ((grp["wins"] == 4) & (grp["losses"] == 0)).mean(axis=0)
    assert np.allclose(p40, 1 / 16, atol=0.012)
    p_po = grp["made_playoffs"].mean(axis=0)
    assert np.allclose(p_po, 0.5, atol=0.02)  # 8 of 16 advance


def test_degenerate_swiss_ranks_strongest_first():
    # team i always beats team j for i < j -> team 0 must always finish 4-0 rank 1,
    # team 15 always 0-4 rank 16 (guards the int8-overflow rank-sort regression)
    P = (np.arange(16)[:, None] < np.arange(16)[None, :]).astype(float)
    grp = gsim.simulate_group(P, 0.0, 128, np.random.default_rng(5))
    assert np.all(grp["rank"][:, 0] == 1)
    assert np.all(grp["rank"][:, 15] == 16)
    assert np.all(grp["wins"][:, 0] == 4) and np.all(grp["losses"][:, 0] == 0)
    # ranks 1-3 must be the 4-0 and 4-1 teams
    top3 = grp["rank"] <= 3
    assert np.all(grp["wins"][top3] == 4)


def test_degenerate_bracket_matches_hand_computation():
    # team i always beats team j when i < j; seeds 1..8 = teams 0..7
    P = (np.arange(16)[:, None] < np.arange(16)[None, :]).astype(float)
    seed_order = np.tile(np.arange(8), (64, 1))
    out = gsim.simulate_bracket(P, 0.0, seed_order, np.random.default_rng(3), _topology())
    expected_winner = {
        "R1M1": 0, "R1M2": 3, "R1M3": 1, "R1M4": 2,   # UB QFs (1v8, 4v5, 2v7, 3v6)
        "R1M5": 4, "R1M6": 5,                          # LB R1 among QF losers
        # UB SF losers CROSS into the opposite half's LB QF (TI2025-verified):
        # R2M3 = w(R1M5)=4 vs l(R2M2)=2 -> 2; R2M4 = w(R1M6)=5 vs l(R2M1)=3 -> 3
        "R2M1": 0, "R2M2": 1, "R2M3": 2, "R2M4": 3,
        "R3M1": 2, "R4M1": 0, "R4M2": 1, "R5M1": 0,    # champion = strongest team
    }
    assert set(out) == set(expected_winner)
    for slot, (_t1, _t2, w) in out.items():
        assert np.all(w == expected_winner[slot]), slot
