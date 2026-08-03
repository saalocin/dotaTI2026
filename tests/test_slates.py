"""Point tables, slate scorer hand case, optimizer dominance, exhaustive bracket."""

import csv
from pathlib import Path

import numpy as np
import pytest

from ti2026.gold import sim as gsim
from ti2026.gold import slates

SEEDS = Path(__file__).parents[1] / "seeds"


def test_point_tables_pinned():
    assert len(slates.GROUP_TABLE) == 17 and len(slates.BRACKET_TABLE) == 15
    assert slates.GROUP_TABLE[1] == 30 and slates.GROUP_TABLE[16] == 12000
    assert slates.BRACKET_TABLE[1] == 120 and slates.BRACKET_TABLE[14] == 12000
    assert np.all(np.diff(slates.GROUP_TABLE) > 0)
    assert np.all(np.diff(slates.BRACKET_TABLE) > 0)
    # rules doc identity: TI row n = group row n+2
    for n in range(1, 15):
        assert slates.BRACKET_TABLE[n] == slates.GROUP_TABLE[n + 2]


def test_eval_assignment_hand_case():
    # 2 buckets, 2 teams, 2 draws. team0 in bucket0 correct in draw0 only;
    # team1 in bucket1 correct in both draws.
    M = np.array([
        [[1, 0], [0, 0]],   # bucket0: team0 [d0,d1], team1
        [[0, 1], [1, 1]],   # bucket1
    ], dtype=np.int8)
    ev, k = slates.eval_assignment(M, np.array([0, 1]), slates.GROUP_TABLE)
    assert list(k) == [2, 1]                      # d0: both correct, d1: one
    assert ev == pytest.approx((60 + 30) / 2)


def test_convexity_prefers_correlated_slates():
    # Identical marginals (three picks, 50% each): slate A all-or-nothing
    # (k = 3 or 0), slate B spread (k = 2 or 1). The table is convex from k=3
    # (30,30 then 60,240,... increments), so A must score higher.
    MA = np.array([[[1, 0], [1, 0], [1, 0]]], dtype=np.int8)
    MB = np.array([[[1, 0], [1, 0], [0, 1]]], dtype=np.int8)
    a = np.array([0, 0, 0])
    ev_a, ka = slates.eval_assignment(MA, a, slates.GROUP_TABLE)
    ev_b, kb = slates.eval_assignment(MB, a, slates.GROUP_TABLE)
    assert list(ka) == [3, 0] and list(kb) == [2, 1]
    assert ev_a == pytest.approx(60.0) and ev_b == pytest.approx(45.0)
    assert ev_a > ev_b


def test_group_core_dominates_naive():
    # structurally consistent draws from the real simulator, graded strengths
    z = np.linspace(1.2, -1.2, 16)
    P = 1 / (1 + np.exp(-(z[:, None] - z[None, :])))
    grp = gsim.simulate_group(P, 0.0, 3000, np.random.default_rng(9))
    rec = np.char.add(np.char.add(grp["wins"].astype(str), "-"), grp["losses"].astype(str)).T
    rank = grp["rank"].T
    in_elim = (rank >= 4) & (rank <= 13)
    surv_true = in_elim & grp["survived"].T
    surv_false = in_elim & ~grp["survived"].T
    buckets = slates.REHEARSAL_BUCKETS
    M = np.stack([slates._predicate(rule, rec, rank, surv_true, surv_false)
                  for _, _, rule, _ in buckets]).astype(np.int8)
    best, best_ev, naive, naive_ev = slates.group_core(
        M, [b[1] for b in buckets], slates.GROUP_TABLE, restarts=8, seed=1)
    assert best_ev >= naive_ev - 1e-9
    counts = np.bincount(best, minlength=len(buckets))
    assert list(counts) == [b[1] for b in buckets]  # capacities respected


def _topology():
    with open(SEEDS / "bracket_topology.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (int(r["round_no"]), r["slot_id"]))
    return [
        (r["slot_id"], int(r["best_of"]),
         r["feeds_winner_slot"] or None, r["feeds_loser_slot"] or None)
        for r in rows
    ]


def test_bracket_core_exhaustive_finds_deterministic_truth():
    # deterministic draws: team i beats j for i < j; the optimum must predict the
    # exact realized bracket and score all 14 -> 12,000 EV
    topo = _topology()
    P = (np.arange(16)[:, None] < np.arange(16)[None, :]).astype(float)
    seed_order = np.tile(np.arange(8), (8, 1))
    out = gsim.simulate_bracket(P, 0.0, seed_order, np.random.default_rng(2), topo)
    D = 8
    corr = {s: {i: (out[s][2] == i).astype(np.int8) for i in range(16)} for s in out}
    entry = {s: (int(out[s][0][0]), int(out[s][1][0]))
             for s in ("R1M1", "R1M2", "R1M3", "R1M4")}
    results = slates.bracket_core(topo, entry, corr, D, slates.BRACKET_TABLE, top_n=3)
    best_ev, best_picks = results[0]
    assert best_ev == pytest.approx(12000.0)
    for s in out:
        assert best_picks[s] == int(out[s][2][0])
