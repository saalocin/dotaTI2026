"""Rating-model math: series formulas, shock integration, Bradley-Terry fit sanity."""

from datetime import datetime, timedelta

import numpy as np
import pytest

from ti2026.gold import ratings


def test_series_formulas_at_half():
    assert ratings.p_bo3(0.5) == pytest.approx(0.5)
    assert ratings.p_bo5(0.5) == pytest.approx(0.5)


def test_series_formulas_monotone_and_amplifying():
    grid = np.linspace(0.01, 0.99, 50)
    b3, b5 = ratings.p_bo3(grid), ratings.p_bo5(grid)
    assert np.all(np.diff(b3) > 0) and np.all(np.diff(b5) > 0)
    fav = grid > 0.5
    assert np.all(b3[fav] > grid[fav])       # longer series help the favorite
    assert np.all(b5[fav] > b3[fav])


def test_shocked_zero_rho_is_exact_and_symmetric():
    z = np.array([-1.0, 0.0, 2.0])
    direct = ratings.p_bo3(1 / (1 + np.exp(-z)))
    assert np.allclose(ratings.shocked(ratings.p_bo3, z, 0.0), direct)
    assert ratings.shocked(lambda p: p, 0.0, 0.7) == pytest.approx(0.5)  # symmetric shock


def test_fit_orders_teams_and_respects_priors():
    t0 = datetime(2026, 6, 1)
    games = []
    for i in range(10):
        games.append(("a", "b", i < 8, t0 + timedelta(days=i)))  # a wins 8 of 10
    strengths, diag = ratings.fit(games, ["a", "b", "c"], {"c": (0.9, "manual")},
                                  halflife=1000.0, lam_ti=1.0)
    assert strengths["a"] > strengths["b"]
    assert strengths["c"] == pytest.approx(0.9)   # no games -> exactly the prior
    assert diag["a"][0] == 10 and diag["c"][0] == 0


def test_fit_recency_weighting_flips_stale_dominance():
    t0 = datetime(2026, 1, 1)
    games = []
    for i in range(20):  # ancient: a dominates
        games.append(("a", "b", True, t0 + timedelta(days=i)))
    for i in range(10):  # recent: b dominates
        games.append(("a", "b", False, datetime(2026, 7, 1) + timedelta(days=i)))
    fresh, _ = ratings.fit(games, ["a", "b"], {}, halflife=20.0, lam_ti=0.5)
    stale, _ = ratings.fit(games, ["a", "b"], {}, halflife=100000.0, lam_ti=0.5)
    assert fresh["b"] > fresh["a"]        # short memory: recent form wins
    assert stale["a"] > stale["b"]        # infinite memory: volume wins
