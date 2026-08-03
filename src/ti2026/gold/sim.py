"""Tournament Monte Carlo: Swiss -> elimination round -> double-elim bracket.

Vectorized numpy over draws. Format assumptions (parameterized, recorded in
gold.sim_meta.params_json, re-verify on Liquipedia once TI2026 pages go live):

- Swiss: 16 teams, win-target 4 / loss-limit 4, 5 rounds, all Bo3. Score-group
  sizes are structurally fixed each round (R1 [16], R2 [8,8], R3 [4,8,4],
  R4 [2,6,6,2], R5 [4,6,4]) and terminal records are exactly
  1x4-0, 2x4-1, 5x3-2, 5x2-3, 2x1-4, 1x0-4 — asserted every run; a day-1
  deviation means the real format differs and everything must be re-run.
- Pairing: random within score group (variant: seeded pots in round 1);
  rematch avoidance NOT modeled (random pairing; effect on outcome marginals
  is second-order — covered by the pairing-variant sensitivity protocol).
- Ranks 1-16 by (wins, losses, random tiebreak). Top 3 direct to playoffs;
  4th-13th to the elimination round paired 4v13 5v12 6v11 7v10 8v9 (Bo3),
  5 winners advance; 14th-16th out.
- Playoff seeding: 1-3 = direct advancers, 4-8 = elim winners by swiss rank;
  UB QFs: R1M1=(1v8) R1M2=(4v5) R1M3=(2v7) R1M4=(3v6). Propagation follows
  seeds/bracket_topology.csv exactly. Bo3 everywhere, Bo5 grand final.
- Per-series strength shock: logit + rho*eps (rho from gold.matchup_probs).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

N_TEAMS = 16
QF_SEEDS = {"R1M1": (1, 8), "R1M2": (4, 5), "R1M3": (2, 7), "R1M4": (3, 6)}
PERIOD_OF_STAGE = {"group_swiss": "p1", "group_elim": "p1", "main_event": "p2"}


def load_matchups(con) -> tuple[list[str], np.ndarray, float]:
    teams = [k for (k,) in con.execute("SELECT team_key FROM pred.teams ORDER BY 1").fetchall()]
    idx = {k: i for i, k in enumerate(teams)}
    P = np.full((N_TEAMS, N_TEAMS), 0.5)
    rows = con.execute("SELECT team_a, team_b, p_map, rho FROM gold.matchup_probs").fetchall()
    if not rows:
        raise SystemExit("gold.matchup_probs is empty — run the ratings build first")
    rho = rows[0][3] or 0.0
    for a, b, p, _ in rows:
        P[idx[a], idx[b]] = p
    return teams, P, float(rho)


def _play(p_map: np.ndarray, bo: int, rng: np.random.Generator, rho: float):
    """Vectorized series: p_map = A's map-win prob per series. Returns
    (a_won, n_games, a_score, b_score), all shaped like p_map."""
    p = np.clip(p_map, 1e-6, 1 - 1e-6)
    if rho:
        z = np.log(p / (1 - p)) + rho * rng.standard_normal(p.shape)
        p = 1.0 / (1.0 + np.exp(-z))
    need = bo // 2 + 1
    wins = np.zeros(p.shape, dtype=np.int8)
    losses = np.zeros(p.shape, dtype=np.int8)
    ngames = np.zeros(p.shape, dtype=np.int8)
    for _ in range(bo):
        live = (wins < need) & (losses < need)
        res = rng.random(p.shape) < p
        wins += (res & live).astype(np.int8)
        losses += (~res & live).astype(np.int8)
        ngames += live.astype(np.int8)
    return wins >= need, ngames, wins, losses


class _PathLog:
    """Long-form series log for fantasy paths (only kept for the path draw set)."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self.rows: list[tuple] = []  # (draw, team_i, stage, order, opp_i, won, n_games, best_of)

    def add(self, draws, a_idx, b_idx, a_won, ngames, stage, order, bo=3):
        if not self.enabled:
            return
        d = np.asarray(draws).ravel()
        a = np.asarray(a_idx).ravel()
        b = np.asarray(b_idx).ravel()
        w = np.asarray(a_won).ravel()
        g = np.asarray(ngames).ravel()
        for i in range(d.shape[0]):
            self.rows.append((int(d[i]), int(a[i]), stage, order, int(b[i]), bool(w[i]), int(g[i]), bo))
            self.rows.append((int(d[i]), int(b[i]), stage, order, int(a[i]), not bool(w[i]), int(g[i]), bo))


def simulate_group(P, rho, n_draws, rng, r1_variant="random", strength_order=None,
                   paths: _PathLog | None = None) -> dict[str, np.ndarray]:
    """Swiss + elimination round. Returns per-(draw, team) arrays."""
    D = n_draws
    paths = paths or _PathLog(False)
    wins = np.zeros((D, N_TEAMS), dtype=np.int8)
    losses = np.zeros((D, N_TEAMS), dtype=np.int8)
    drows = np.arange(D)[:, None]

    for rnd in range(1, 6):
        active = (wins < 4) & (losses < 4)
        n_active = int(active[0].sum())
        if r1_variant == "seeded_pots" and rnd == 1 and strength_order is not None:
            pots = np.array(strength_order)  # strongest first
            a_idx = np.tile(pots[:8], (D, 1))
            perm = np.argsort(rng.random((D, 8)), axis=1)
            b_idx = pots[8:][perm]
        else:
            key = np.where(active, -wins * 10 + rng.random((D, N_TEAMS)), 999.0)
            order = np.argsort(key, axis=1)
            seats = order[:, :n_active]
            a_idx, b_idx = seats[:, 0::2], seats[:, 1::2]
        a_won, ngames, _, _ = _play(P[a_idx, b_idx], 3, rng, rho)
        np.add.at(wins, (np.broadcast_to(drows, a_idx.shape), a_idx), a_won.astype(np.int8))
        np.add.at(losses, (np.broadcast_to(drows, a_idx.shape), a_idx), (~a_won).astype(np.int8))
        np.add.at(wins, (np.broadcast_to(drows, b_idx.shape), b_idx), (~a_won).astype(np.int8))
        np.add.at(losses, (np.broadcast_to(drows, b_idx.shape), b_idx), a_won.astype(np.int8))
        paths.add(np.broadcast_to(drows, a_idx.shape), a_idx, b_idx, a_won, ngames,
                  "group_swiss", rnd)

    # structural invariants — a violation means the format assumption broke
    for w, l, expect in ((4, 0, 1), (4, 1, 2), (3, 2, 5), (2, 3, 5), (1, 4, 2), (0, 4, 1)):
        counts = ((wins == w) & (losses == l)).sum(axis=1)
        if not np.all(counts == expect):
            raise AssertionError(f"swiss structure violated: record {w}-{l} count != {expect}")

    # ranks 1..16: wins desc, losses asc, random tiebreak
    # (cast first: int8 * 100 overflows and silently scrambles the sort)
    rank_key = (-wins.astype(np.int32) * 100 + losses.astype(np.int32) * 10
                + rng.random((D, N_TEAMS)))
    order = np.argsort(rank_key, axis=1)          # (D, 16) team index by rank position
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.arange(1, N_TEAMS + 1)[None, :].repeat(D, 0), axis=1)

    # elimination round: rank positions 4..13 -> 4v13 5v12 6v11 7v10 8v9
    hi = order[:, 3:8]                            # ranks 4..8
    lo = order[:, 8:13][:, ::-1]                  # ranks 13..9
    e_won, e_games, _, _ = _play(P[hi, lo], 3, rng, rho)
    paths.add(np.broadcast_to(drows, hi.shape), hi, lo, e_won, e_games, "group_elim", 10)

    survived = np.zeros((D, N_TEAMS), dtype=bool)
    elim_opp = np.full((D, N_TEAMS), -1, dtype=np.int16)
    np.put_along_axis(survived, hi, e_won, axis=1)
    np.put_along_axis(survived, lo, ~e_won, axis=1)
    np.put_along_axis(elim_opp, hi, lo.astype(np.int16), axis=1)
    np.put_along_axis(elim_opp, lo, hi.astype(np.int16), axis=1)

    made_playoffs = (rank <= 3) | survived
    if not np.all(made_playoffs.sum(axis=1) == 8):
        raise AssertionError("playoff entrant count != 8")

    # playoff seeds 1..8: swiss top3 then elim survivors by swiss rank
    seed_key = np.where(made_playoffs, rank, 99)
    seed_order = np.argsort(seed_key, axis=1)[:, :8]      # team idx by seed
    playoff_seed = np.full((D, N_TEAMS), -1, dtype=np.int8)
    np.put_along_axis(playoff_seed, seed_order, np.arange(1, 9)[None, :].repeat(D, 0), axis=1)

    return {
        "wins": wins, "losses": losses, "rank": rank,
        "survived": survived, "elim_opp": elim_opp,
        "made_playoffs": made_playoffs, "playoff_seed": playoff_seed,
        "seed_order": seed_order,
    }


def load_topology(con) -> list[tuple[str, int, str, str | None, str | None]]:
    rows = con.execute(
        """SELECT slot_id, best_of, feeds_winner_slot, feeds_loser_slot, round_no
           FROM pred.bracket_slots ORDER BY round_no, slot_id"""
    ).fetchall()
    return [(sid, bo, fw, fl) for sid, bo, fw, fl, _ in rows]


def simulate_bracket(P, rho, seed_order, rng, topology,
                     paths: _PathLog | None = None):
    """seed_order: (D, 8) team indices, seed 1 first. Returns
    {slot_id: (team1, team2, winner)} arrays of shape (D,)."""
    D = seed_order.shape[0]
    paths = paths or _PathLog(False)
    drows = np.arange(D)
    incoming: dict[str, list[np.ndarray]] = {}
    for slot, (s_hi, s_lo) in QF_SEEDS.items():
        incoming[slot] = [seed_order[:, s_hi - 1], seed_order[:, s_lo - 1]]

    out: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for order_i, (slot, bo, feeds_w, feeds_l) in enumerate(topology):
        t1, t2 = incoming[slot][0], incoming[slot][1]
        a_won, ngames, _, _ = _play(P[t1, t2], bo, rng, rho)
        winner = np.where(a_won, t1, t2)
        loser = np.where(a_won, t2, t1)
        out[slot] = (t1, t2, winner)
        paths.add(drows, t1, t2, a_won, ngames, "main_event", 20 + order_i, bo=bo)
        if feeds_w:
            incoming.setdefault(feeds_w, []).append(winner)
        if feeds_l:
            incoming.setdefault(feeds_l, []).append(loser)
    return out


def _fixed_seed_order(con, teams: list[str], entrants: list[str] | None, n: int) -> np.ndarray:
    """Post-groups: seed1..seed8 either passed explicitly or read from the real
    bracket fill (pred.bracket_slots R1M1..R1M4, populated from Liquipedia)."""
    if entrants is None:
        seeds: dict[int, str] = {}
        for slot, (s_hi, s_lo) in QF_SEEDS.items():
            row = con.execute(
                "SELECT team1_key, team2_key FROM pred.bracket_slots WHERE slot_id = ?", [slot]
            ).fetchone()
            if not row or not row[0] or not row[1]:
                raise SystemExit(
                    f"bracket slot {slot} has no teams yet — pass --entrants or wait for "
                    "the Liquipedia bracket fill (post Aug 16)"
                )
            seeds[s_hi], seeds[s_lo] = row
        entrants = [seeds[i] for i in range(1, 9)]
    if len(entrants) != 8 or len(set(entrants)) != 8:
        raise SystemExit(f"need exactly 8 distinct entrants, got {entrants}")
    idx = {k: i for i, k in enumerate(teams)}
    return np.tile(np.array([idx[k] for k in entrants]), (n, 1))


def run(con, n_draws=50000, n_paths=5000, rng_seed=42, stage_state="pre_groups",
        entrants: list[str] | None = None, r1_variant: str = "random") -> str:
    """Simulate, assert invariants, write sim_meta + draw tables. Returns run_id."""
    teams, P, rho = load_matchups(con)
    topology = load_topology(con)
    strengths = dict(con.execute("SELECT team_key, strength_logit FROM gold.team_strength").fetchall())
    strength_order = sorted(range(N_TEAMS), key=lambda i: -strengths.get(teams[i], 0.0))
    as_of = con.execute("SELECT max(as_of) FROM gold.team_strength").fetchone()[0]
    params = {
        "pairing": r1_variant, "rematch_avoidance": False,
        "elim_pairs": "4v13..8v9", "qf_seeds": {k: list(v) for k, v in QF_SEEDS.items()},
        "rho": rho, "period_of_stage": PERIOD_OF_STAGE, "n_paths": n_paths,
        "entrants": entrants,
    }
    suffix = "" if r1_variant == "random" else f"-{r1_variant}"
    run_id = f"{stage_state}-s{rng_seed}-n{n_draws}{suffix}"

    rng = np.random.default_rng(rng_seed)
    rng_p = np.random.default_rng(rng_seed + 1)
    plog = _PathLog(True)
    if stage_state == "post_groups":
        grp = None
        brk = simulate_bracket(P, rho, _fixed_seed_order(con, teams, entrants, n_draws),
                               rng, topology)
        simulate_bracket(P, rho, _fixed_seed_order(con, teams, entrants, n_paths),
                         rng_p, topology, paths=plog)
    else:
        grp = simulate_group(P, rho, n_draws, rng, r1_variant=r1_variant,
                             strength_order=strength_order)
        brk = simulate_bracket(P, rho, grp["seed_order"], rng, topology)
        # path-detailed reduced set (separate stream, same model)
        grp_p = simulate_group(P, rho, n_paths, rng_p, r1_variant=r1_variant,
                               strength_order=strength_order, paths=plog)
        simulate_bracket(P, rho, grp_p["seed_order"], rng_p, topology, paths=plog)

    # ---- write
    for tbl in ("sim_group_draws", "sim_bracket_draws", "sim_fantasy_paths"):
        con.execute(f"DELETE FROM gold.{tbl} WHERE run_id = ?", [run_id])
    con.execute("DELETE FROM gold.sim_meta WHERE run_id = ?", [run_id])
    con.execute(
        "INSERT INTO gold.sim_meta VALUES (?,?,?,?,?,?,?)",
        [run_id, datetime.now(timezone.utc).replace(tzinfo=None), n_draws, rng_seed,
         as_of, stage_state, json.dumps(params)],
    )

    D = n_draws
    if grp is not None:
        draw_col = np.repeat(np.arange(D), N_TEAMS)
        team_col = np.tile(np.arange(N_TEAMS), D)
        w = grp["wins"].ravel()
        l_ = grp["losses"].ravel()
        elim_opp = grp["elim_opp"].ravel()
        gdf = pd.DataFrame({
            "run_id": run_id, "draw_id": draw_col,
            "team_key": np.array(teams)[team_col],
            "wins": w, "losses": l_,
            "record": pd.Series(w.astype(str)).str.cat(l_.astype(str), sep="-"),
            "swiss_rank": grp["rank"].ravel(),
            "elim_opponent": np.where(elim_opp >= 0, np.array(teams)[np.clip(elim_opp, 0, None)], None),
            "survived_elim": np.where((grp["rank"].ravel() >= 4) & (grp["rank"].ravel() <= 13),
                                      grp["survived"].ravel(), None),
            "made_playoffs": grp["made_playoffs"].ravel(),
            "playoff_seed": np.where(grp["playoff_seed"].ravel() > 0, grp["playoff_seed"].ravel(), None),
        })
        con.register("gdf", gdf)
        con.execute("INSERT INTO gold.sim_group_draws SELECT * FROM gdf")
        con.unregister("gdf")

    brows = []
    for slot, (t1, t2, wn) in brk.items():
        brows.append(pd.DataFrame({
            "run_id": run_id, "draw_id": np.arange(D), "slot_id": slot,
            "team1_key": np.array(teams)[t1], "team2_key": np.array(teams)[t2],
            "winner_key": np.array(teams)[wn],
        }))
    bdf = pd.concat(brows, ignore_index=True)
    con.register("bdf", bdf)
    con.execute("INSERT INTO gold.sim_bracket_draws SELECT * FROM bdf")
    con.unregister("bdf")

    pdf = pd.DataFrame(plog.rows,
                       columns=["draw_id", "ti", "stage", "ord", "oi", "won", "n_games", "best_of"])
    pdf["team_key"] = np.array(teams)[pdf["ti"].to_numpy()]
    pdf["opponent_key"] = np.array(teams)[pdf["oi"].to_numpy()]
    pdf["period_id"] = pdf["stage"].map(PERIOD_OF_STAGE)
    pdf = pdf.sort_values(["draw_id", "team_key", "period_id", "ord"])
    pdf["series_no"] = pdf.groupby(["draw_id", "team_key", "period_id"]).cumcount() + 1
    pdf["run_id"] = run_id
    pdf = pdf[["run_id", "draw_id", "team_key", "period_id", "series_no",
               "stage", "opponent_key", "won", "n_games", "best_of"]]
    con.register("pdf", pdf)
    con.execute("INSERT INTO gold.sim_fantasy_paths SELECT * FROM pdf")
    con.unregister("pdf")

    return run_id
