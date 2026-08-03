"""Recency-weighted regularized Bradley-Terry team strength + walk-forward backtest.

Training set: map-level outcomes from pred.games. A side enters as its TI team_key
ONLY when attributed by roster majority (>=3 of the seeded 5 together); id-attributed
sides become anonymous 'id:<team_id>' nuisance entities. So stale-lineup games under
a persistent org id (OG) never inform the current unit's rating, while games under
drifted ids (HULIGANI) do. Nuisance entities are shrunk hard toward the field mean.

Priors on the logit scale (prior = alpha * z):
  pred.manual_priors.prior_z          hand-set, off-board / reset teams — beats
  standardized latest Liquipedia Glicko (rated TI teams), else 0.

Map -> series: p_bo3 = p^2(3-2p); p_bo5 = p^3(10-15p+6p^2). Optional per-series
strength shock rho (sd on the logit, Gauss-Hermite integrated) fitted by matching
the predicted vs empirical 2-0 share of TI-vs-TI Bo3s — iid maps understate
sweeps when series have momentum/form shocks.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np

# Defaults set by `ti backtest --tune` on 2026-07-31 with time-correct (wayback)
# priors: hl=45/lam=6/alpha=0.35 -> 0.6833 series logloss vs prior-only 0.6836 and
# coin 0.6931 (n=248; differences are within noise — near-peer pro series are close
# to coin flips, so the fit's job is calibration + in-event responsiveness, not magic).
HALFLIFE_DAYS = 45.0
LAM_TI = 6.0        # ridge toward prior for the 16 TI units
LAM_OPP = 8.0       # heavy shrink for nuisance opponents
ALPHA = 0.35        # prior logit per 1 field-sd (sigmoid(0.35) ~ 0.59 vs average)
RHO_GRID = [round(0.05 * i, 2) for i in range(17)]  # 0.00 .. 0.80

_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(7)
_GH_W = _GH_W / _GH_W.sum()

BACKTEST_MONTHS = ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def p_bo3(p):
    p = np.asarray(p, dtype=float)
    return p * p * (3 - 2 * p)


def p_bo5(p):
    p = np.asarray(p, dtype=float)
    return p**3 * (10 - 15 * p + 6 * p * p)


def shocked(fn, z, rho: float):
    """E[fn(sigmoid(z + rho*eps))] with eps ~ N(0,1); exact at rho=0."""
    if not rho:
        return fn(_sigmoid(z))
    zz = np.asarray(z, dtype=float)[..., None] + rho * _GH_X
    return (fn(_sigmoid(zz)) * _GH_W).sum(axis=-1)


# ---------------------------------------------------------------- data loading

def load_map_games(con) -> list[tuple[str, str, bool, datetime]]:
    """(ent_radiant, ent_dire, radiant_won, start_time) per decided map.

    Games where neither side is a majority-attributed TI unit are dropped —
    they connect only nuisance entities."""
    rows = con.execute(
        """
        SELECT CASE WHEN radiant_key_source = 'roster_majority' THEN radiant_team_key END,
               radiant_team_id,
               CASE WHEN dire_key_source = 'roster_majority' THEN dire_team_key END,
               dire_team_id,
               radiant_win, start_time
        FROM pred.games
        WHERE radiant_win IS NOT NULL AND start_time IS NOT NULL
        """
    ).fetchall()
    out = []
    for rk, rid, dk, did, rwin, t in rows:
        ea = rk or (f"id:{rid}" if rid else None)
        eb = dk or (f"id:{did}" if did else None)
        if not ea or not eb or ea == eb or not (rk or dk):
            continue
        out.append((ea, eb, bool(rwin), t))
    return out


def load_priors(con, alpha: float = ALPHA, as_of: date | None = None) -> dict[str, tuple[float, str]]:
    """team_key -> (prior_logit, source), from the latest rating snapshot <= as_of.

    Wayback-archived snapshots make this time-correct for walk-forward backtests;
    pre-rename rows (Tundra Esports, L1GA TEAM) resolve via crosswalk aliases, so a
    team can be rated under its old name. Manual priors fill teams the selected
    snapshot does not rate, and override reset-suspect boards (their values are
    frozen from real archived ratings — see seeds/manual_priors.csv basis)."""
    pri: dict[str, tuple[float, str]] = {}
    date_filter = "AND snapshot_date <= ?" if as_of else ""
    params = [as_of] if as_of else []
    # max(rating) per team: on historical boards two raw names can alias to one
    # team_key (Tundra Esports AND 1w Team -> iron_wing in 2026-03); renames/resets
    # bias ratings DOWN, so the higher row is the established lineage.
    g = con.execute(
        f"""
        SELECT team_key, max(rating) FROM pred.team_ratings
        WHERE source = 'liquipedia' AND team_key IS NOT NULL
          AND snapshot_date = (SELECT max(snapshot_date) FROM pred.team_ratings
                               WHERE source = 'liquipedia' {date_filter})
        GROUP BY team_key
        """,
        params,
    ).fetchall()
    if g:
        vals = np.array([r for _, r in g], dtype=float)
        sd = float(vals.std()) or 1.0
        for k, r in g:
            pri[k] = (alpha * (float(r) - float(vals.mean())) / sd, "glicko")
    for k, z in con.execute("SELECT team_key, prior_z FROM pred.manual_priors").fetchall():
        if k not in pri or _reset_suspect(con, k):
            pri[k] = (alpha * float(z), "manual")
    return pri


def _reset_suspect(con, team_key: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM pred.teams WHERE team_key = ? AND seed_note ILIKE '%reset%'", [team_key]
    ).fetchone()
    return row is not None


# ------------------------------------------------------------------------ fit

def fit(
    games: list[tuple[str, str, bool, datetime]],
    ti_keys: list[str],
    priors: dict[str, tuple[float, str]],
    halflife: float = HALFLIFE_DAYS,
    lam_ti: float = LAM_TI,
    lam_opp: float = LAM_OPP,
    anchor: datetime | None = None,
) -> tuple[dict[str, float], dict[str, tuple[int, float]]]:
    """Newton-solved penalized Bradley-Terry.

    Returns (entity -> strength logit, entity -> (n_games, recency-weighted games)).
    TI teams with zero games get their prior (regularization makes this exact-ish)."""
    if not games:
        raise ValueError("no games to fit")
    anchor = anchor or max(t for *_, t in games)
    ents = sorted({e for a, b, _, _ in games for e in (a, b)} | set(ti_keys))
    idx = {e: i for i, e in enumerate(ents)}
    n = len(ents)

    a = np.array([idx[g[0]] for g in games])
    b = np.array([idx[g[1]] for g in games])
    y = np.array([1.0 if g[2] else 0.0 for g in games])
    age = np.array([(anchor - g[3]).total_seconds() / 86400.0 for g in games])
    w = 0.5 ** (np.maximum(age, 0.0) / halflife)

    prior = np.zeros(n)
    lam = np.full(n, lam_opp)
    for k in ti_keys:
        lam[idx[k]] = lam_ti
        prior[idx[k]] = priors.get(k, (0.0, "global"))[0]

    s = prior.copy()
    diag_idx = np.arange(n)
    for _ in range(100):
        p = _sigmoid(s[a] - s[b])
        gvec = w * (p - y)
        grad = np.zeros(n)
        np.add.at(grad, a, gvec)
        np.add.at(grad, b, -gvec)
        grad += 2 * lam * (s - prior)
        wpq = w * p * (1 - p) + 1e-12
        H = np.zeros((n, n))
        np.add.at(H, (a, a), wpq)
        np.add.at(H, (b, b), wpq)
        np.add.at(H, (a, b), -wpq)
        np.add.at(H, (b, a), -wpq)
        H[diag_idx, diag_idx] += 2 * lam
        step = np.linalg.solve(H, grad)
        s -= step
        if float(np.abs(step).max()) < 1e-9:
            break

    n_games = np.zeros(n, dtype=int)
    eff = np.zeros(n)
    np.add.at(n_games, a, 1)
    np.add.at(n_games, b, 1)
    np.add.at(eff, a, w)
    np.add.at(eff, b, w)
    return (
        {e: float(s[idx[e]]) for e in ents},
        {e: (int(n_games[idx[e]]), float(eff[idx[e]])) for e in ents},
    )


# Bo2 series (common in DreamLeague/ESL groups) poison sweep-share fitting: a Bo2
# ending 1-1 has no winner, so conditioning on "someone reached 2" over-samples
# sweeps. Where STRATZ ground truth exists (pred.series.best_of), trust it and keep
# only real Bo3+; where it doesn't, fall back to the league heuristic — a league
# that ever produced a completed 1-1 TI-vs-TI series is treated as a Bo2 league.
_BO3_LEAGUE_SERIES = """
    WITH s AS (
        SELECT se.series_id, se.team1_key, se.team2_key,
               se.team1_score AS s1, se.team2_score AS s2, se.winner_key,
               se.scheduled_at, se.best_of, any_value(g.league_id) AS lid
        FROM pred.series se JOIN pred.games g USING (series_id)
        WHERE se.stage = 'pre_ti' AND se.completed
          AND se.team1_key IS NOT NULL AND se.team2_key IS NOT NULL
        GROUP BY 1, 2, 3, 4, 5, 6, 7, 8
    ),
    bo2_leagues AS (SELECT DISTINCT lid FROM s
                    WHERE best_of IS NULL AND s1 = 1 AND s2 = 1 AND lid IS NOT NULL)
    SELECT * FROM s
    WHERE best_of >= 3
       OR (best_of IS NULL AND lid IS NOT NULL
           AND lid NOT IN (SELECT lid FROM bo2_leagues))
"""


def fit_rho(con, strengths: dict[str, float]) -> float:
    """Overdispersion: match the empirical 2-0 share of TI-vs-TI Bo3s (Bo3 leagues only)."""
    rows = con.execute(
        f"""
        SELECT team1_key, team2_key, s1, s2 FROM ({_BO3_LEAGUE_SERIES})
        WHERE (s1 = 2 AND s2 IN (0, 1)) OR (s2 = 2 AND s1 IN (0, 1))
        """
    ).fetchall()
    obs, zs = [], []
    for k1, k2, s1, s2 in rows:
        if k1 not in strengths or k2 not in strengths:
            continue
        obs.append(1.0 if (s1, s2) in ((2, 0), (0, 2)) else 0.0)
        zs.append(strengths[k1] - strengths[k2])
    if len(obs) < 60:
        return 0.0  # not enough series to identify rho
    obs_share = float(np.mean(obs))
    z = np.array(zs)
    sweep = lambda p: p * p + (1 - p) * (1 - p)  # noqa: E731
    best = min(RHO_GRID, key=lambda r: abs(float(shocked(sweep, z, r).mean()) - obs_share))
    return float(best)


# ------------------------------------------------------------------ gold build

def build(con, as_of: date | None = None) -> dict:
    """Fit on everything, write gold.team_strength + gold.matchup_probs."""
    as_of = as_of or date.today()
    ti_keys = [k for (k,) in con.execute("SELECT team_key FROM pred.teams ORDER BY 1").fetchall()]
    games = load_map_games(con)
    priors = load_priors(con)
    strengths, diag = fit(games, ti_keys, priors)
    rho = fit_rho(con, strengths)

    con.execute("DELETE FROM gold.team_strength")
    con.execute("DELETE FROM gold.matchup_probs")
    for k in ti_keys:
        prior_logit, src = priors.get(k, (0.0, "global"))
        n_g, eff = diag.get(k, (0, 0.0))
        con.execute(
            "INSERT INTO gold.team_strength VALUES (?,?,?,?,?,?,?,?,?)",
            [k, as_of, strengths[k], strengths[k], n_g, round(eff, 1), prior_logit, src,
             "prior-only (no majority games)" if n_g == 0 else None],
        )
    rows = []
    for ka in ti_keys:
        for kb in ti_keys:
            if ka == kb:
                continue
            z = strengths[ka] - strengths[kb]
            rows.append((
                ka, kb,
                float(shocked(lambda p: p, z, rho)),
                float(shocked(p_bo3, z, rho)),
                float(shocked(p_bo5, z, rho)),
                rho, as_of,
            ))
    con.executemany("INSERT INTO gold.matchup_probs VALUES (?,?,?,?,?,?,?)", rows)
    n_ti_games = sum(1 for a, b, _, _ in games if not a.startswith("id:") and not b.startswith("id:"))
    return {"entities": len(strengths), "maps_fit": len(games),
            "ti_vs_ti_maps": n_ti_games, "rho": rho}


# -------------------------------------------------------------------- backtest

def _series_rows(con):
    return con.execute(
        f"""
        SELECT series_id, team1_key, team2_key, s1, s2, winner_key,
               strftime(scheduled_at, '%Y-%m') AS month
        FROM ({_BO3_LEAGUE_SERIES})
        WHERE winner_key IS NOT NULL AND scheduled_at IS NOT NULL
          AND s1 + s2 BETWEEN 1 AND 5  -- drop junk groupings
        """
    ).fetchall()


def _p_series(z, s1: int, s2: int, rho: float) -> float:
    n = max(int(s1 or 0) + int(s2 or 0), 1)
    if n <= 1:
        return float(shocked(lambda p: p, z, rho))
    if n <= 3:
        return float(shocked(p_bo3, z, rho))
    return float(shocked(p_bo5, z, rho))


def backtest(
    con,
    months: list[str] | None = None,
    halflife: float = HALFLIFE_DAYS,
    lam_ti: float = LAM_TI,
    alpha: float = ALPHA,
    write: bool = True,
    ti_only: bool = False,
) -> dict[str, tuple[float, float, int]]:
    """Walk-forward: fit on data before month m with priors from the latest rating
    snapshot before m (time-correct via wayback), score month m's series. Returns
    variant -> (series logloss, series brier, n); optionally logs every prediction
    into gold.backtest_preds. ti_only drops nuisance-opponent maps from the fit."""
    months = months or BACKTEST_MONTHS
    ti_keys = [k for (k,) in con.execute("SELECT team_key FROM pred.teams").fetchall()]
    all_games = load_map_games(con)
    if ti_only:
        all_games = [g for g in all_games
                     if not g[0].startswith("id:") and not g[1].startswith("id:")]
    series = _series_rows(con)

    log_rows = []
    agg: dict[str, list[tuple[float, int]]] = {}

    for month in months:
        cutoff = datetime.strptime(month + "-01", "%Y-%m-%d")
        train = [g for g in all_games if g[3] < cutoff]
        if len(train) < 100:
            continue
        priors_m = load_priors(con, alpha, as_of=cutoff.date())
        prior_logit_m = {k: priors_m.get(k, (0.0, "global"))[0] for k in ti_keys}
        strengths, _ = fit(train, ti_keys, priors_m, halflife=halflife,
                           lam_ti=lam_ti, anchor=cutoff)
        rho = fit_rho(con, strengths)  # rho from full history (scalar, low leakage risk)

        for sid, k1, k2, s1, s2, wk, m in series:
            if m != month or k1 not in strengths or k2 not in strengths:
                continue
            outcome = 1 if wk == k1 else 0
            variants = {
                "bt": _p_series(strengths[k1] - strengths[k2], s1, s2, rho),
                "prior_only": _p_series(prior_logit_m.get(k1, 0.0) - prior_logit_m.get(k2, 0.0),
                                        s1, s2, 0.0),
                "coin": 0.5,
            }
            for variant, p in variants.items():
                log_rows.append((month, sid, None, variant, "series", p, outcome, "pre_ti"))
                agg.setdefault(variant, []).append((p, outcome))

    if write:
        con.execute("DELETE FROM gold.backtest_preds")
        con.executemany("INSERT INTO gold.backtest_preds VALUES (?,?,?,?,?,?,?,?)", log_rows)

    out = {}
    for variant, po in agg.items():
        p = np.array([x for x, _ in po])
        y = np.array([o for _, o in po], dtype=float)
        ll = float(np.mean(-(y * np.log(np.clip(p, 1e-9, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-9, 1)))))
        br = float(np.mean((p - y) ** 2))
        out[variant] = (ll, br, len(po))
    return out


def tune(con) -> list[tuple]:
    """Grid over (halflife, lam_ti, alpha); returns rows sorted by series logloss."""
    results = []
    for hl in (45.0, 60.0, 90.0, 120.0):
        for lt in (1.0, 3.0, 6.0):
            for al in (0.2, 0.35, 0.5):
                m = backtest(con, halflife=hl, lam_ti=lt, alpha=al, write=False)
                if "bt" in m:
                    results.append((hl, lt, al, round(m["bt"][0], 4), round(m["bt"][1], 4), m["bt"][2]))
    results.sort(key=lambda r: r[3])
    return results
