"""Slate optimizers for the two prediction tracks.

The escalating point tables are convex, so a slate's value is E[table[#correct]]
over the JOINT outcome distribution — evaluated against stored simulation draws
(common random numbers), never as a product of per-pick marginals. Group buckets
are capacity-constrained (exactly one team can go 4-0); bracket picks correlate
through advancement. Both optimizers must dominate the naive marginal slate on
the same draws — asserted, not assumed.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .. import config

GROUP_TABLE = np.array(
    [0, 30, 60, 120, 360, 720, 1200, 1800, 2520, 3360, 4320,
     5400, 6600, 7920, 9360, 10920, 12000], dtype=float)
BRACKET_TABLE = np.array(
    [0, 120, 360, 720, 1200, 1800, 2520, 3360, 4320, 5400,
     6600, 7920, 9360, 10920, 12000], dtype=float)

# rehearsal taxonomy from the structural Swiss + announced format; replaced by
# seeds/swiss_buckets.csv the moment the client reveals the real bucket set
REHEARSAL_BUCKETS = [
    ("4-0", 1, "record=4-0", "Swiss 4-0"),
    ("4-1", 2, "record=4-1", "Swiss 4-1"),
    ("advance-elim", 5, "elim_survived", "Advance via elimination round"),
    ("out-elim", 5, "elim_lost", "Eliminated in elimination round"),
    ("bottom3", 3, "rank>=14", "14th-16th (out after Swiss)"),
]


def load_buckets() -> list[tuple[str, int, str, str]]:
    path = config.SEEDS_DIR / "swiss_buckets.csv"
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            rows = [(r["bucket_id"], int(r["capacity"]), r["rule"], r.get("label") or r["bucket_id"])
                    for r in csv.DictReader(f) if r.get("bucket_id")]
        if rows:
            return rows
    return REHEARSAL_BUCKETS


def _predicate(rule: str, rec: np.ndarray, rank: np.ndarray,
               surv_true: np.ndarray, surv_false: np.ndarray) -> np.ndarray:
    """surv_true/surv_false are plain bools: won/lost the elimination round
    (both False for teams that never played it)."""
    if rule.startswith("record="):
        return rec == rule.split("=", 1)[1]
    if rule == "elim_survived":
        return surv_true
    if rule == "elim_lost":
        return surv_false
    if rule == "playoffs":
        return (rank <= 3) | surv_true
    if rule == "eliminated":
        return ~((rank <= 3) | surv_true)
    if rule.startswith("rank>="):
        return rank >= int(rule[6:])
    if rule.startswith("rank<="):
        return rank <= int(rule[6:])
    raise ValueError(f"unknown bucket rule: {rule}")


# ------------------------------------------------------------------ evaluators

def eval_assignment(M: np.ndarray, assign: np.ndarray, table: np.ndarray):
    """M: (B, T, D) 0/1 correctness; assign: (T,) bucket per team.
    Returns (EV, k per draw)."""
    k = M[assign, np.arange(M.shape[1]), :].sum(axis=0)
    return float(table[k].mean()), k


def _hist(k: np.ndarray, n_max: int) -> list[float]:
    return [round(float((k == i).mean()), 5) for i in range(n_max + 1)]


# -------------------------------------------------------------- group buckets

def group_core(M: np.ndarray, caps: list[int], table: np.ndarray,
               restarts: int = 50, seed: int = 0, lam_risk: float = 0.0,
               tail: int | None = None):
    """Pure-numpy optimizer: greedy inits + pairwise-swap hill-climb.

    Objective: pure EV by default; `lam_risk` adds a std term; `tail=T` switches to
    maximizing P(K >= T) — the honest risk dial (std barely separates slates here,
    tail targets force scenario-coherent picks). Returns
    (best_assign, best_score, naive, naive_score) under the SAME objective."""
    B, T, D = M.shape
    probs = M.mean(axis=2)  # (B, T) marginal prob team t lands in bucket b
    rng = np.random.default_rng(seed)

    def score_of(k: np.ndarray) -> float:
        if tail is not None:
            return float((k >= tail).mean())
        v = table[k]
        return float(v.mean() + lam_risk * v.std()) if lam_risk else float(v.mean())

    def greedy(order: np.ndarray) -> np.ndarray:
        left = list(caps)
        assign = np.full(T, -1)
        for t in order:
            for b in np.argsort(-probs[:, t]):
                if left[b] > 0:
                    assign[t] = b
                    left[b] -= 1
                    break
        return assign

    def climb(assign: np.ndarray):
        _, k = eval_assignment(M, assign, table)
        ev = score_of(k)
        improved = True
        while improved:
            improved = False
            for i in range(T):
                for j in range(i + 1, T):
                    bi, bj = assign[i], assign[j]
                    if bi == bj:
                        continue
                    k2 = k - M[bi, i] - M[bj, j] + M[bj, i] + M[bi, j]
                    ev2 = score_of(k2)
                    if ev2 > ev + 1e-9:
                        assign[i], assign[j] = bj, bi
                        ev, k = ev2, k2
                        improved = True
            # loop until a full sweep finds nothing
        return assign, ev

    # naive baseline: teams in max-marginal order, no climbing
    naive_order = np.argsort(-probs.max(axis=0))
    naive = greedy(naive_order)
    naive_ev = score_of(eval_assignment(M, naive, table)[1])

    best_assign, best_ev = None, -1.0
    for r in range(restarts):
        order = naive_order if r == 0 else rng.permutation(T)
        a, ev = climb(greedy(order))
        if ev > best_ev:
            best_assign, best_ev = a.copy(), ev
    return best_assign, best_ev, naive, naive_ev


def latest_run(con, table: str) -> str:
    row = con.execute(
        f"""SELECT run_id FROM gold.sim_meta
            WHERE run_id IN (SELECT DISTINCT run_id FROM gold.{table})
            ORDER BY created_at DESC LIMIT 1"""
    ).fetchone()
    if not row:
        raise SystemExit(f"no draws in gold.{table} — run `ti build-gold` or `ti sim` first")
    return row[0]


def load_group_matrices(con, run_id: str):
    """(teams, M correctness tensor (B,T,D), buckets, caps) for a stored run."""
    df = con.execute(
        """SELECT team_key, draw_id, record, swiss_rank, survived_elim
           FROM gold.sim_group_draws WHERE run_id = ? ORDER BY team_key, draw_id""",
        [run_id],
    ).df()
    teams = sorted(df.team_key.unique())
    T = len(teams)
    D = df.draw_id.nunique()
    rec = df.record.to_numpy().reshape(T, D)
    rank = df.swiss_rank.to_numpy().reshape(T, D)
    surv_true = df.survived_elim.eq(True).fillna(False).to_numpy(dtype=bool).reshape(T, D)
    surv_false = df.survived_elim.eq(False).fillna(False).to_numpy(dtype=bool).reshape(T, D)

    buckets = load_buckets()
    caps = [b[1] for b in buckets]
    if sum(caps) != T:
        raise SystemExit(f"bucket capacities {caps} must sum to {T}")
    M = np.stack([_predicate(rule, rec, rank, surv_true, surv_false)
                  for _, _, rule, _ in buckets]).astype(np.int8)
    return teams, M, buckets, caps


def optimize_group(con, run_id: str | None = None, restarts: int = 50, seed: int = 0,
                   write: bool = True) -> dict:
    run_id = run_id or latest_run(con, "sim_group_draws")
    teams, M, buckets, caps = load_group_matrices(con, run_id)
    T = len(teams)

    best, best_ev, naive, naive_ev = group_core(M, caps, GROUP_TABLE, restarts, seed)
    if best_ev + 1e-6 < naive_ev:
        raise AssertionError(f"optimizer ({best_ev:.1f}) lost to naive ({naive_ev:.1f})")

    _, k_best = eval_assignment(M, best, GROUP_TABLE)
    picks = {teams[t]: buckets[best[t]][0] for t in range(T)}
    result = {
        "run_id": run_id, "ev": best_ev, "naive_ev": naive_ev,
        "e_correct": float(k_best.mean()), "p13plus": float((k_best >= 13).mean()),
        "hist": _hist(k_best, 16), "picks": picks,
        "pick_probs": {teams[t]: float(M[best[t], t].mean()) for t in range(T)},
        "buckets": buckets,
    }
    if write:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for sid, payload, ev, k in (
            (f"group-{run_id}-hillclimb", picks, best_ev, k_best),
            (f"group-{run_id}-naive", {teams[t]: buckets[naive[t]][0] for t in range(T)},
             naive_ev, eval_assignment(M, naive, GROUP_TABLE)[1]),
        ):
            con.execute("DELETE FROM gold.slates WHERE slate_id = ?", [sid])
            con.execute(
                "INSERT INTO gold.slates VALUES (?,?,?,?,?,?,?,?)",
                [sid, "group", run_id, json.dumps(payload), ev,
                 json.dumps(_hist(k, 16)),
                 "hillclimb" if sid.endswith("hillclimb") else "greedy_marginal", now],
            )
    return result


# ------------------------------------------------------------------- bracket

def bracket_core(topology, entry: dict[str, tuple[int, int]],
                 corr: dict[str, dict[int, np.ndarray]], D: int,
                 table: np.ndarray, top_n: int = 5):
    """Exhaustive DFS over all 2^14 consistent fills; correctness added
    incrementally so each edge costs one vector op. Returns top fills."""
    order = [s for s, _, _, _ in topology]
    feeds = {s: (fw, fl) for s, _, fw, fl in topology}
    incoming: dict[str, list[int]] = {s: [a, b] for s, (a, b) in entry.items()}
    k = np.zeros(D, dtype=np.int8)
    results: list[tuple[float, dict[str, int]]] = []
    picks: dict[str, int] = {}

    def dfs(i: int):
        if i == len(order):
            ev = float(table[k].mean())
            if len(results) < top_n or ev > results[-1][0]:
                results.append((ev, dict(picks)))
                results.sort(key=lambda r: -r[0])
                del results[top_n:]
            return
        slot = order[i]
        t1, t2 = incoming[slot][0], incoming[slot][1]
        fw, fl = feeds[slot]
        for winner, loser in ((t1, t2), (t2, t1)):
            c = corr[slot][winner]
            np.add(k, c, out=k)
            picks[slot] = winner
            if fw:
                incoming.setdefault(fw, []).append(winner)
            if fl:
                incoming.setdefault(fl, []).append(loser)
            dfs(i + 1)
            if fw:
                incoming[fw].pop()
            if fl:
                incoming[fl].pop()
            np.subtract(k, c, out=k)
        picks.pop(slot, None)

    dfs(0)
    return results


def optimize_bracket(con, run_id: str | None = None, top_n: int = 5,
                     write: bool = True) -> dict:
    run_id = run_id or latest_run(con, "sim_bracket_draws")
    bdf = con.execute(
        """SELECT slot_id, draw_id, team1_key, team2_key, winner_key
           FROM gold.sim_bracket_draws WHERE run_id = ? ORDER BY slot_id, draw_id""",
        [run_id],
    ).df()
    r1 = bdf[bdf.slot_id.isin(["R1M1", "R1M2", "R1M3", "R1M4"])]
    fixed = (r1.groupby("slot_id")[["team1_key", "team2_key"]].nunique() == 1).all().all()
    if not fixed:
        raise SystemExit(
            "bracket entrants vary across draws (pre-groups chained run) — a submittable "
            "bracket slate needs a fixed-entrant run: `ti sim --stage post_groups` "
            "(real bracket, post Aug 16) or --entrants for a rehearsal"
        )

    teams = sorted(set(bdf.team1_key) | set(bdf.team2_key))
    tidx = {t: i for i, t in enumerate(teams)}
    slots = sorted(bdf.slot_id.unique())
    D = bdf.draw_id.nunique()
    winners = {s: bdf[bdf.slot_id == s].winner_key.map(tidx).to_numpy() for s in slots}
    corr = {s: {i: (winners[s] == i).astype(np.int8) for i in range(len(teams))} for s in slots}

    topo_rows = con.execute(
        """SELECT slot_id, best_of, feeds_winner_slot, feeds_loser_slot
           FROM pred.bracket_slots ORDER BY round_no, slot_id"""
    ).fetchall()
    entry = {}
    for s in ("R1M1", "R1M2", "R1M3", "R1M4"):
        row = bdf[bdf.slot_id == s].iloc[0]
        entry[s] = (tidx[row.team1_key], tidx[row.team2_key])

    results = bracket_core(topo_rows, entry, corr, D, BRACKET_TABLE, top_n)

    # chalk baseline: stronger team (by model strength) wins every series
    strength = dict(con.execute(
        "SELECT team_key, strength_logit FROM gold.team_strength").fetchall())
    incoming = {s: [a, b] for s, (a, b) in entry.items()}
    chalk: dict[str, int] = {}
    k = np.zeros(D, dtype=np.int8)
    for s, _bo, fw, fl in topo_rows:
        t1, t2 = incoming[s][0], incoming[s][1]
        w = t1 if strength.get(teams[t1], 0) >= strength.get(teams[t2], 0) else t2
        l_ = t2 if w == t1 else t1
        chalk[s] = w
        k += corr[s][w]
        if fw:
            incoming.setdefault(fw, []).append(w)
        if fl:
            incoming.setdefault(fl, []).append(l_)
    chalk_ev = float(BRACKET_TABLE[k].mean())
    if results[0][0] + 1e-6 < chalk_ev:
        raise AssertionError(f"optimizer ({results[0][0]:.1f}) lost to chalk ({chalk_ev:.1f})")

    out = {
        "run_id": run_id, "chalk_ev": chalk_ev,
        "fills": [
            {"ev": ev, "picks": {s: teams[w] for s, w in p.items()},
             "champion": teams[p["R5M1"]]}
            for ev, p in results
        ],
    }
    if write:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for rank_i, fill in enumerate(out["fills"], 1):
            sid = f"bracket-{run_id}-top{rank_i}"
            con.execute("DELETE FROM gold.slates WHERE slate_id = ?", [sid])
            con.execute(
                "INSERT INTO gold.slates VALUES (?,?,?,?,?,?,?,?)",
                [sid, "bracket", run_id, json.dumps(fill["picks"]), fill["ev"],
                 None, "exhaustive", now],
            )
    return out
