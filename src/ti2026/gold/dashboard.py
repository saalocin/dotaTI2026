"""`ti dashboard` — render the gold layer into one self-contained TI-styled HTML page.

All data is embedded at generation time (no server, no external requests), so the
page is re-generated after every `ti build-gold` and stays truthful to the DB it
came from. The risk sliders are honest: group slates are re-optimized in Python at
five risk temperatures (objective = EV + lam*sd, convex payouts reward variance),
and the fantasy slider re-ranks the full 4,096-roster table client-side.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np

from .. import config
from . import fantasy, ratings, slates
from . import sim as gsim

TEMPLATE = Path(__file__).parent / "dashboard_template.html"
OUT_DEFAULT = config.DATA_DIR / "gold" / "dashboard.html"

# Risk dial. Tail objectives barely move the slate here (bucket capacities pin the
# joint structure and the optimizer keeps agreeing with itself), so the right side
# is CONTRARIAN DISTANCE: the best slate forced >= m picks away from consensus —
# guaranteed variation, honestly priced in EV.
RISK_STOPS = [
    {"label": "BUNKER", "tail": 4, "contra": 0, "goal": "max P(≥4 correct)"},
    {"label": "MAX EV", "tail": None, "contra": 0, "goal": "max expected points"},
    {"label": "VARIANT II", "tail": None, "contra": 2, "goal": "best slate ≥2 picks off consensus"},
    {"label": "VARIANT IV", "tail": None, "contra": 4, "goal": "best slate ≥4 picks off consensus"},
    {"label": "VARIANT VI", "tail": None, "contra": 6, "goal": "best slate ≥6 picks off consensus"},
    {"label": "FULL SEND", "tail": None, "contra": 8, "goal": "best slate ≥8 picks off consensus"},
]
SEARCH_DRAWS = 20000   # hill-climb on a prefix; final stats on all draws

# time-machine cutoffs: model refit on games < cut with priors as-of cut
# (same walk-forward machinery as `ti backtest`); the last entry is "now"
TIME_CUTS = ["2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01",
             "2026-06-01", "2026-07-01", None]
TIME_DRAWS = 10000

FORM_START = "2026-02-01"   # weekly strength refits from here (data thins out earlier)
FORM_STEP_DAYS = 7


def _round(x, nd=4):
    return None if x is None else round(float(x), nd)


def contrarian(M, caps, base: np.ndarray, m: int, table, restarts=12, seed=11):
    """Best assignment with Hamming distance >= m from the consensus slate.

    Seeds distance with random swaps off the base, then swap-climbs EV while
    rejecting any move that would drop back under the distance floor."""
    B, T, D = M.shape
    rng = np.random.default_rng(seed)
    best, best_ev = None, -1.0
    for _ in range(restarts):
        a = base.copy()
        guard = 0
        while int((a != base).sum()) < m and guard < 400:
            i, j = rng.integers(0, T, 2)
            if a[i] != a[j]:
                a[i], a[j] = a[j], a[i]
            guard += 1
        ev, k = slates.eval_assignment(M, a, table)
        improved = True
        while improved:
            improved = False
            for i in range(T):
                for j in range(i + 1, T):
                    bi, bj = a[i], a[j]
                    if bi == bj:
                        continue
                    d_now = int(a[i] != base[i]) + int(a[j] != base[j])
                    d_new = int(bj != base[i]) + int(bi != base[j])
                    if int((a != base).sum()) - d_now + d_new < m:
                        continue
                    k2 = k - M[bi, i] - M[bj, j] + M[bj, i] + M[bi, j]
                    ev2 = float(table[k2].mean())
                    if ev2 > ev + 1e-9:
                        a[i], a[j] = bj, bi
                        ev, k = ev2, k2
                        improved = True
        if ev > best_ev:
            best, best_ev = a.copy(), ev
    return best


def _snapshot(con, order, buckets, caps, rho, cut: str | None, seed: int) -> dict:
    """Refit the team model on data before `cut`, re-simulate, re-derive the
    consensus slate — one time-machine frame, entirely in memory."""
    cutoff = datetime.strptime(cut, "%Y-%m-%d") if cut else None
    ti_keys = list(order)
    games = ratings.load_map_games(con)
    if cutoff:
        games = [g for g in games if g[3] < cutoff]
    priors = ratings.load_priors(con, as_of=cutoff.date() if cutoff else None)
    strengths, diag = ratings.fit(games, ti_keys, priors, anchor=cutoff)

    z = np.array([strengths[k] for k in order])
    zz = z[:, None] - z[None, :]
    P = np.asarray(ratings.shocked(lambda p: p, zz, rho), dtype=float)
    B3 = np.asarray(ratings.shocked(ratings.p_bo3, zz, rho), dtype=float)
    np.fill_diagonal(P, 0.5)
    np.fill_diagonal(B3, 0.5)

    rng = np.random.default_rng(seed)
    grp = gsim.simulate_group(P, rho, TIME_DRAWS, rng)
    topo = gsim.load_topology(con)
    brk = gsim.simulate_bracket(P, rho, grp["seed_order"], rng, topo)

    D = TIME_DRAWS
    rank = grp["rank"]
    rec40 = (grp["wins"] == 4) & (grp["losses"] == 0)
    rec41 = (grp["wins"] == 4) & (grp["losses"] == 1)
    in_elim = (rank >= 4) & (rank <= 13)
    surv = grp["survived"]
    marginals = {}
    for i, k in enumerate(order):
        p40 = float(rec40[:, i].mean())
        p41 = float(rec41[:, i].mean())
        ppo = float(grp["made_playoffs"][:, i].mean())
        pb3 = float((rank[:, i] >= 14).mean())
        marginals[k] = {"p40": _round(p40), "p41": _round(p41),
                        "adv_elim": _round(ppo - p40 - p41),
                        "out_elim": _round(1 - ppo - pb3),
                        "bottom3": _round(pb3), "playoffs": _round(ppo)}
    gf_t1, gf_t2, gf_w = brk["R5M1"]
    champion = {k: _round((gf_w == i).mean()) for i, k in enumerate(order)}
    reach_gf = {k: _round(((gf_t1 == i) | (gf_t2 == i)).mean()) for i, k in enumerate(order)}
    slot_marg = {}
    for slot, (_t1, _t2, w) in brk.items():
        counts = np.bincount(w, minlength=len(order))
        top = np.argsort(-counts)[:3]
        slot_marg[slot] = [[order[int(i)], _round(counts[int(i)] / D, 3)]
                           for i in top if counts[int(i)] > 0]

    # consensus (MAX EV) slate on this snapshot's draws
    rec = np.char.add(np.char.add(grp["wins"].astype(str), "-"),
                      grp["losses"].astype(str)).T
    surv_true = (in_elim & surv).T
    surv_false = (in_elim & ~surv).T
    M = np.stack([slates._predicate(rule, rec, rank.T, surv_true, surv_false)
                  for _, _, rule, _ in buckets]).astype(np.int8)
    assign, _, _, _ = slates.group_core(M, caps, slates.GROUP_TABLE, restarts=8, seed=7)
    _, kv = slates.eval_assignment(M, assign, slates.GROUP_TABLE)
    pts = slates.GROUP_TABLE[kv]

    return {
        "date": cut or "now",
        "strengths": [_round(strengths[k], 3) for k in order],
        "games_fit": [int(diag.get(k, (0, 0))[0]) for k in order],
        "p_field": [_round(float(np.delete(P[i], i).mean())) for i in range(len(order))],
        "p_map": [[_round(v) for v in row] for row in P.tolist()],
        "p_bo3": [[_round(v) for v in row] for row in B3.tolist()],
        "marginals": marginals,
        "champion": champion,
        "reach_gf": reach_gf,
        "slot_marg": slot_marg,
        "maxev": {
            "ev": _round(pts.mean(), 1), "e_correct": _round(kv.mean(), 2),
            "p5": _round((kv >= 5).mean()), "p8": _round((kv >= 8).mean()),
            "sd": _round(pts.std(), 1),
            "hist": [_round((kv == i).mean()) for i in range(17)],
            "picks": {order[t]: buckets[assign[t]][0] for t in range(len(order))},
            "probs": {order[t]: _round(M[assign[t], t].mean(), 3)
                      for t in range(len(order))},
        },
    }


_STAT_LABELS = [
    ("kills", "Kills", "kills"), ("deaths", "Deaths", "deaths"),
    ("cs", "Creep Score", "last_hits + denies"), ("gpm", "GPM", "gpm"),
    ("madstone", "Madstone (proxy)", "madstone_proxy"), ("towers", "Tower Kills", "tower_kills"),
    ("wards", "Wards Placed", "obs_placed"), ("stacks", "Camps Stacked", "camps_stacked"),
    ("runes", "Runes Grabbed", "rune_pickups"), ("watchers", "Watchers Taken", "watchers_taken"),
    ("smokes", "Smokes Used", "smokes_used"), ("lotus", "Lotuses Grabbed", "NULL"),
    ("roshan", "Roshan Kills", "roshan_kills"), ("tfp", "Teamfight Part.", "teamfight_participation"),
    ("stuns", "Stun Seconds", "stuns_s"), ("tormentor", "Tormentor Kills", "tormentor_kills"),
    ("firstblood", "First Blood", "CASE WHEN first_blood THEN 1.0 ELSE 0.0 END"),
    ("courier", "Courier Kills", "courier_kills"),
]


def _team_logos(order: list[str]) -> dict[str, str]:
    """team_key -> 44px PNG data URI, thumbnailed from the bronze logo images."""
    import base64
    import io

    from PIL import Image

    from .. import manifest as bronze_manifest

    files = {p.stem: p for p in bronze_manifest.all_files("players", "logos/*.img")}
    out = {}
    for k in order:
        p = files.get(k)
        if not p:
            continue
        try:
            im = Image.open(io.BytesIO(p.read_bytes())).convert("RGBA")
            im.thumbnail((44, 44))
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            out[k] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception as exc:  # noqa: BLE001 — fall back to the monogram chip
            print(f"  [warn] logo thumbnail {k}: {exc}")
    return out


def _players(con) -> dict:
    """Player drill-down payload: bio + avatar, the 18 rules stats (raw per-game
    and fantasy points), extra stats, top heroes, per-game fantasy series."""
    import base64

    from .. import manifest as bronze_manifest

    pts_mean = {}
    for acc, stat, mean in con.execute(
        "SELECT account_id, stat, mean_pts FROM gold.player_stat_profile WHERE context = 'all'"
    ).fetchall():
        pts_mean.setdefault(int(acc), {})[stat] = round(float(mean), 1)

    raw_selects = ", ".join(
        f"avg({expr}) AS r_{key}" for key, _lbl, expr in _STAT_LABELS if expr != "NULL"
    )
    raw = {int(r[0]): r[1:] for r in con.execute(
        f"SELECT account_id, {raw_selects} FROM fan.player_game_stats WHERE parsed GROUP BY 1"
    ).fetchall()}
    raw_keys = [k for k, _l, e in _STAT_LABELS if e != "NULL"]

    heroes = {}
    for acc, hero, n, wr in con.execute(
        """SELECT account_id, coalesce(h.localized_name, 'hero_' || s.hero_id),
                  count(*), avg(CASE WHEN s.win THEN 1.0 ELSE 0.0 END)
           FROM fan.player_game_stats s LEFT JOIN meta.heroes h USING (hero_id)
           GROUP BY 1, 2 ORDER BY count(*) DESC"""
    ).fetchall():
        heroes.setdefault(int(acc), [])
        if len(heroes[int(acc)]) < 5:
            heroes[int(acc)].append([hero, int(n), _round(wr, 2)])

    epoch = datetime(2026, 1, 1)
    series: dict[int, list] = {}
    for acc, t, pts, win in con.execute(
        """SELECT f.account_id, s.start_time, f.pts_total, s.win
           FROM fan.fantasy_points f JOIN fan.player_game_stats s USING (match_id, account_id)
           WHERE f.parsed AND s.start_time IS NOT NULL ORDER BY s.start_time"""
    ).fetchall():
        series.setdefault(int(acc), []).append(
            [int((t - epoch).days), int(round(pts)), 1 if win else 0])

    extras = {int(r[0]): r[1:] for r in con.execute(
        """SELECT account_id, count(*),
                  avg(CASE WHEN win THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN opp_team_key IS NOT NULL THEN
                      CASE WHEN win THEN 1.0 ELSE 0.0 END END),
                  count(*) FILTER (WHERE opp_team_key IS NOT NULL),
                  avg(duration_s) / 60.0
           FROM fan.player_game_stats GROUP BY 1"""
    ).fetchall()}

    bios = {int(r[0]): r[1:] for r in con.execute(
        "SELECT account_id, real_name, birth_date, country, country_code, history"
        " FROM fan.player_bios"
    ).fetchall()}

    profs = {int(a): [rt, lb] for a, rt, lb in con.execute(
        "SELECT account_id, rank_tier, leaderboard_rank FROM fan.player_profiles"
    ).fetchall()}

    avatar_files = {int(p.stem): p for p in bronze_manifest.all_files("players", "avatars/*.jpg")}
    today = datetime.now(timezone.utc).date()

    # 2026 career-high games (marquee stats + best fantasy game) with context
    tnames = dict(con.execute("SELECT team_key, display_name FROM pred.teams").fetchall())
    highs: dict[int, dict] = {}
    for row in con.execute(
        """SELECT f.account_id, f.pts_total, s.kills, s.gpm, s.obs_placed,
                  s.watchers_taken, s.stuns_s, coalesce(h.localized_name, '?'),
                  s.opp_team_key, s.start_time, s.win
           FROM fan.fantasy_points f
           JOIN fan.player_game_stats s USING (match_id, account_id)
           LEFT JOIN meta.heroes h ON h.hero_id = s.hero_id
           WHERE f.parsed"""
    ).fetchall():
        h = highs.setdefault(int(row[0]), {})
        for key, ix in (("best", 1), ("kills", 2), ("gpm", 3), ("wards", 4),
                        ("watchers", 5), ("stuns", 6)):
            v = float(row[ix] or 0)
            if key not in h or v > h[key][0]:
                h[key] = [round(v), row[7], tnames.get(row[8]), str(row[9])[:10],
                          1 if row[10] else 0]

    # peer rank per stat: by mean fantasy pts within the same fantasy-slot cohort
    # (pts direction handles inverted stats like deaths correctly)
    slot_of = dict(con.execute("SELECT account_id, fantasy_slot FROM fan.players").fetchall())
    ranks: dict[int, dict[str, int]] = {int(a): {} for a in slot_of}
    for stat in {k for k, _l, _e in _STAT_LABELS}:
        for slotname in ("support", "safelane", "offlane", "mid"):
            cohort = [(int(a), pts_mean.get(int(a), {}).get(stat) or 0.0)
                      for a in slot_of if slot_of[a] == slotname]
            cohort.sort(key=lambda x: -x[1])
            for i, (a, _v) in enumerate(cohort, 1):
                ranks[a][stat] = i

    # coach-prefix affinity from the 2026 hero mix x client hero tags
    pref = [(t, float(pct), set((param or "").split("|"))) for t, side, pct, param in
            con.execute("SELECT title, side, pct, coalesce(cond_param,'')"
                        " FROM meta.coach_titles").fetchall() if side == "prefix"]
    htags = {hid: set(tags or []) for hid, tags in
             con.execute("SELECT hero_id, tags FROM meta.hero_tags").fetchall()}
    aff_cnt: dict[int, list] = {}
    aff_tot: dict[int, int] = {}
    for acc_, hid, n in con.execute(
        "SELECT account_id, hero_id, count(*) FROM fan.player_game_stats"
        " WHERE parsed GROUP BY 1, 2"
    ).fetchall():
        a = int(acc_)
        aff_tot[a] = aff_tot.get(a, 0) + n
        arr = aff_cnt.setdefault(a, [0] * len(pref))
        ht = htags.get(hid, set())
        for i, (_t, _p, tags) in enumerate(pref):
            if ht & tags:
                arr[i] += n
    aff: dict[int, list] = {}
    for a, arr in aff_cnt.items():
        n = aff_tot[a] or 1
        scored = sorted(((arr[i] / n, pref[i][0], pref[i][1]) for i in range(len(pref))),
                        key=lambda x: -(x[0] * x[2]))     # rate x bonus = effective lift
        aff[a] = [[t, _round(r, 2), p] for r, t, p in scored[:3] if r > 0.05]

    # (career earnings intentionally absent: Liquipedia renders them from LPDB via
    # Lua, not infobox wikitext — verified across all 78 bio pages, no such field.)

    players = {}
    for acc, nick, team, pos, slot in con.execute(
        "SELECT account_id, nickname, team_key, position, fantasy_slot FROM fan.players"
        " ORDER BY team_key, position"
    ).fetchall():
        acc = int(acc)
        real, bdate, country, ccode, hist = bios.get(acc, (None, None, None, None, "[]"))
        age = None
        if bdate:
            age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
        avatar = None
        if acc in avatar_files:
            b = avatar_files[acc].read_bytes()
            if len(b) < 40000:
                avatar = "data:image/jpeg;base64," + base64.b64encode(b).decode()
        n_g, wr, ti_wr, ti_n, dur = extras.get(acc, (0, None, None, 0, None))
        players[acc] = {
            "nick": nick, "team": team, "pos": pos, "slot": slot,
            "real": real, "age": age, "country": country, "cc": (ccode or "").upper(),
            "history": json.loads(hist or "[]"),
            "avatar": avatar,
            "games": int(n_g), "wr": _round(wr), "ti_wr": _round(ti_wr),
            "ti_games": int(ti_n or 0), "avg_min": _round(dur, 1),
            "stats": {k: {"raw": _round(raw[acc][i], 2) if acc in raw else None,
                          "pts": pts_mean.get(acc, {}).get(k),
                          "rk": ranks.get(acc, {}).get(k)}
                      for i, k in enumerate(raw_keys)},
            "heroes": heroes.get(acc, []),
            "series": series.get(acc, []),
            "hi": highs.get(acc, {}),
            "aff": aff.get(acc, []),
            "rank": profs.get(acc),
        }
    return {
        "labels": {k: lbl for k, lbl, _e in _STAT_LABELS},
        "order": [k for k, _l, _e in _STAT_LABELS],
        "players": players,
    }


def _form(con, order, rho) -> dict:
    """FORM tab payload: weekly-refit strength trajectories (fits are milliseconds —
    only sims are expensive) + every roster-majority game per team for the
    win/loss strip and rolling win rate."""
    from datetime import timedelta

    all_games = ratings.load_map_games(con)
    end = max(g[3] for g in all_games)
    cuts = []
    c = datetime.strptime(FORM_START, "%Y-%m-%d")
    while c < end:
        cuts.append(c)
        c += timedelta(days=FORM_STEP_DAYS)
    cuts.append(end + timedelta(days=1))   # final point = everything

    traj: dict[str, list] = {k: [] for k in order}
    for cut in cuts:
        train = [g for g in all_games if g[3] < cut]
        priors = ratings.load_priors(con, as_of=cut.date())
        strengths, _ = ratings.fit(train, list(order), priors, anchor=cut)
        z = np.array([strengths[k] for k in order])
        P = 1.0 / (1.0 + np.exp(-(z[:, None] - z[None, :])))
        for i, k in enumerate(order):
            traj[k].append(_round(float(np.delete(P[i], i).mean()), 3))

    epoch = datetime(2026, 1, 1)
    tidx = {k: i for i, k in enumerate(order)}
    games_by_team: dict[str, list] = {k: [] for k in order}
    rows = con.execute(
        """
        SELECT radiant_team_key, dire_team_key, radiant_win, start_time, duration_s, 1
        FROM pred.games
        WHERE radiant_key_source = 'roster_majority'
          AND radiant_win IS NOT NULL AND start_time IS NOT NULL
        UNION ALL
        SELECT dire_team_key, radiant_team_key, NOT radiant_win, start_time, duration_s, 0
        FROM pred.games
        WHERE dire_key_source = 'roster_majority'
          AND radiant_win IS NOT NULL AND start_time IS NOT NULL
        ORDER BY start_time
        """
    ).fetchall()
    for team, opp, win, t, dur, rad in rows:
        if team not in games_by_team:
            continue
        day = (t - epoch).days
        # [day, win, ti-opp idx, duration_s, played radiant]
        games_by_team[team].append([int(day), 1 if win else 0, tidx.get(opp, -1),
                                    int(dur or 0), int(rad)])

    return {
        "dates": [c.strftime("%Y-%m-%d") for c in cuts],
        "traj": traj,
        "games": games_by_team,
    }


def _teamsheet(con, order) -> dict:
    """Team Sheet enrichment: named game records, Bo3+ series/sweep stats, and
    objective appetite (rosh/torm/kills/FB) from the roster's parsed games."""
    from collections import defaultdict

    sheet: dict[str, dict] = {k: {} for k in order}
    names = dict(con.execute("SELECT team_key, display_name FROM pred.teams").fetchall())
    rows = con.execute(
        """
        SELECT radiant_team_key, dire_team_key, radiant_win, duration_s, start_time
        FROM pred.games WHERE radiant_key_source = 'roster_majority'
          AND radiant_win IS NOT NULL AND duration_s IS NOT NULL
        UNION ALL
        SELECT dire_team_key, radiant_team_key, NOT radiant_win, duration_s, start_time
        FROM pred.games WHERE dire_key_source = 'roster_majority'
          AND radiant_win IS NOT NULL AND duration_s IS NOT NULL
        """
    ).fetchall()
    per = defaultdict(list)
    for tk, opp, win, dur, t in rows:
        if tk in sheet:
            per[tk].append((int(dur), names.get(opp, opp or "a non-TI team"), bool(win), str(t)[:10]))
    for tk, gs in per.items():
        fmt = lambda g: [g[0], g[1], g[3], 1 if g[2] else 0]  # noqa: E731
        sheet[tk]["longest"] = fmt(max(gs, key=lambda x: x[0]))
        wins = [x for x in gs if x[2]]
        if wins:
            sheet[tk]["shortwin"] = fmt(min(wins, key=lambda x: x[0]))

    # Bo3+ series record + 2-0 sweep rate (real best-of via STRATZ where known)
    agg = {k: [0, 0, 0, 0] for k in order}   # [series W, L, 2-0 sweeps, Bo3 wins]
    for t1, t2, s1, s2, wk, bo in con.execute(
        """SELECT team1_key, team2_key, team1_score, team2_score, winner_key, best_of
           FROM pred.series
           WHERE completed AND winner_key IS NOT NULL AND best_of >= 3
             AND team1_score IS NOT NULL AND team2_score IS NOT NULL"""
    ).fetchall():
        for me, opp_score in ((t1, s2), (t2, s1)):
            if me not in agg:
                continue
            won = wk == me
            agg[me][0 if won else 1] += 1
            if won and bo == 3:
                agg[me][3] += 1
                if opp_score == 0:
                    agg[me][2] += 1
    for k in order:
        sheet[k]["series"] = agg[k]

    for tk, nm, rosh, torm, kills, fb in con.execute(
        """SELECT team_key, count(DISTINCT match_id), sum(roshan_kills),
                  sum(tormentor_kills), sum(kills),
                  sum(CASE WHEN first_blood THEN 1 ELSE 0 END)
           FROM fan.player_game_stats WHERE parsed GROUP BY 1"""
    ).fetchall():
        if tk in sheet and nm:
            sheet[tk]["obj"] = [_round(rosh / nm, 2), _round(torm / nm, 2),
                                _round(kills / nm, 1), _round(fb / nm, 2)]
    return sheet


def collect(con) -> dict:
    grp_run = slates.latest_run(con, "sim_group_draws")
    n_draws = con.execute(
        "SELECT n_draws FROM gold.sim_meta WHERE run_id = ?", [grp_run]).fetchone()[0]
    # post-groups: a fixed-entrant bracket run supersedes the chained pre-groups
    # draws for everything main-event (champion odds, slot marginals, fantasy P2)
    br_run = slates.latest_run(con, "sim_bracket_draws", stage="post_groups")
    br_draws = n_draws
    bracket_src = grp_run
    if br_run:
        bracket_src = br_run
        br_draws = con.execute(
            "SELECT n_draws FROM gold.sim_meta WHERE run_id = ?", [br_run]).fetchone()[0]

    # ---- teams, ordered by model strength
    rows = con.execute(
        """SELECT t.team_key, t.display_name, t.region, s.strength_logit,
                  s.prior_source, s.n_games_fit
           FROM pred.teams t JOIN gold.team_strength s USING (team_key)
           ORDER BY s.strength_logit DESC"""
    ).fetchall()
    teams = [{"key": k, "name": n, "region": r or "", "strength": _round(sl, 3),
              "prior": ps, "games": ng} for k, n, r, sl, ps, ng in rows]
    order = [t["key"] for t in teams]
    tidx = {k: i for i, k in enumerate(order)}

    # ---- matchup matrices in that order
    p_map = [[0.5] * 16 for _ in range(16)]
    p_bo3 = [[0.5] * 16 for _ in range(16)]
    rho = 0.0
    for a, b, pm, p3, r_ in con.execute(
            "SELECT team_a, team_b, p_map, p_bo3, rho FROM gold.matchup_probs").fetchall():
        p_map[tidx[a]][tidx[b]] = _round(pm)
        p_bo3[tidx[a]][tidx[b]] = _round(p3)
        rho = r_
    for t in teams:  # map-win prob vs the average of the field
        i = tidx[t["key"]]
        t["p_field"] = _round(sum(p_map[i][j] for j in range(16) if j != i) / 15.0)

    # ---- group outcome marginals -> exclusive stack
    marg = {}
    for k, p40, p41, pd_, ppo, pb3 in con.execute(
            """SELECT team_key, p_4_0, p_4_1, p_direct_advance, p_playoffs, p_bottom3
               FROM gold.team_outcome_marginals""").fetchall():
        marg[k] = {"p40": p40, "p41": p41, "adv_elim": _round(ppo - pd_),
                   "out_elim": _round(1.0 - ppo - pb3), "bottom3": pb3,
                   "playoffs": ppo}

    # ---- champion / reach-GF odds (post-groups run when available, else chained)
    champion = {k: c / br_draws for k, c in con.execute(
        """SELECT winner_key, count(*) FROM gold.sim_bracket_draws
           WHERE run_id = ? AND slot_id = 'R5M1' GROUP BY 1""", [bracket_src]).fetchall()}
    reach_gf = {k: c / br_draws for k, c in con.execute(
        """SELECT team_key, count(*) FROM (
               SELECT team1_key AS team_key FROM gold.sim_bracket_draws
               WHERE run_id = ? AND slot_id = 'R5M1'
               UNION ALL
               SELECT team2_key FROM gold.sim_bracket_draws
               WHERE run_id = ? AND slot_id = 'R5M1'
           ) GROUP BY 1""", [bracket_src, bracket_src]).fetchall()}

    # ---- bracket topology (+ the real fill once Liquipedia seeds it)
    slots = [{"slot": s, "round": r, "side": side, "bo": bo, "fw": fw, "fl": fl,
              "t1": t1, "t2": t2, "w": w}
             for s, r, side, bo, fw, fl, t1, t2, w in con.execute(
                 "SELECT slot_id, round_no, side, best_of, feeds_winner_slot, "
                 "feeds_loser_slot, team1_key, team2_key, winner_key "
                 "FROM pred.bracket_slots ORDER BY round_no, slot_id").fetchall()]
    slot_marg: dict[str, list] = {}
    for s, w, c in con.execute(
            """SELECT slot_id, winner_key, count(*) FROM gold.sim_bracket_draws
               WHERE run_id = ? GROUP BY 1, 2 ORDER BY 1, 3 DESC""",
            [bracket_src]).fetchall():
        slot_marg.setdefault(s, [])
        if len(slot_marg[s]) < 3:
            slot_marg[s].append([w, _round(c / br_draws, 3)])

    # ---- series exposure per period (fantasy context; post-groups run = real P2)
    exposure: dict[str, dict] = {}
    for k, p, e in con.execute(
            """SELECT team_key, period_id, avg(k) FROM (
                   SELECT draw_id, team_key, period_id, count(*) AS k
                   FROM gold.sim_fantasy_paths WHERE run_id = ? GROUP BY 1, 2, 3)
               GROUP BY 1, 2""", [br_run or grp_run]).fetchall():
        exposure.setdefault(k, {})[p] = _round(e, 2)

    # ---- group slates. Pre-lock: honest re-optimization across risk temperatures.
    # Post-lock: the pre-lock slates are FROZEN in seeds/locked_group_slates.json —
    # re-optimizing a resolved Swiss on a model that has since trained on those very
    # games would leak outcome knowledge into the "consensus" and flatter the score.
    g_teams, M, buckets, caps = slates.load_group_matrices(con, grp_run)
    g_slates = []
    locked_path = config.SEEDS_DIR / "locked_group_slates.json"
    if locked_path.exists():
        frozen = json.loads(locked_path.read_text(encoding="utf-8"))
        b_idx = {b[0]: i for i, b in enumerate(buckets)}
        t_idx = {t: i for i, t in enumerate(g_teams)}
        for fs in frozen["slates"]:
            assign = np.full(len(g_teams), -1)
            for tk, bid in fs["picks"].items():
                assign[t_idx[tk]] = b_idx[bid]
            _, k = slates.eval_assignment(M, assign, slates.GROUP_TABLE)
            pts = slates.GROUP_TABLE[k]
            g_slates.append({
                "label": fs["label"], "goal": fs["goal"], "lam": 0,
                "locked": True, "pre_lock_ev": fs.get("pre_lock_ev"),
                "ev": _round(pts.mean(), 1), "sd": _round(pts.std(), 1),
                "e_correct": _round(k.mean(), 2),
                "p8": _round((k >= 8).mean()), "p5": _round((k >= 5).mean()),
                "hist": [_round((k == i).mean()) for i in range(17)],
                "picks": dict(fs["picks"]),
                "probs": {tk: _round(M[b_idx[bid], t_idx[tk]].mean(), 3)
                          for tk, bid in fs["picks"].items()},
            })
    else:
        M_search = M[:, :, :SEARCH_DRAWS] if M.shape[2] > SEARCH_DRAWS else M
        base_assign, _, _, _ = slates.group_core(M_search, caps, slates.GROUP_TABLE,
                                                 restarts=20, seed=7)
        for stop in RISK_STOPS:
            if stop["contra"]:
                assign = contrarian(M_search, caps, base_assign, stop["contra"],
                                    slates.GROUP_TABLE)
            elif stop["tail"] is None:
                assign = base_assign
            else:
                assign, _, _, _ = slates.group_core(M_search, caps, slates.GROUP_TABLE,
                                                    restarts=20, seed=7, tail=stop["tail"])
            _, k = slates.eval_assignment(M, assign, slates.GROUP_TABLE)
            pts = slates.GROUP_TABLE[k]
            g_slates.append({
                "label": stop["label"], "goal": stop["goal"],
                "lam": 0 if not stop["contra"] and stop["tail"] is None else 1,
                "ev": _round(pts.mean(), 1), "sd": _round(pts.std(), 1),
                "e_correct": _round(k.mean(), 2),
                "p8": _round((k >= 8).mean()), "p5": _round((k >= 5).mean()),
                "hist": [_round((k == i).mean()) for i in range(17)],
                "picks": {g_teams[t]: buckets[assign[t]][0] for t in range(len(g_teams))},
                "probs": {g_teams[t]: _round(M[assign[t], t].mean(), 3)
                          for t in range(len(g_teams))},
            })

    # ---- real group-stage outcome + every slate variant scored against it
    actual = slates.actual_buckets(con)
    actuals = None
    if actual:
        scored = []
        for s in g_slates:
            n_c, pts_won = slates.score_slate(s["picks"], actual)
            s["actual_correct"] = n_c
            s["actual_points"] = pts_won
            scored.append({"label": s["label"], "n_correct": n_c, "points": pts_won})
        actuals = {"buckets": actual, "slates_scored": scored}

    # ---- time machine: refit + resim at monthly data cutoffs
    history = [
        _snapshot(con, order, buckets, caps, rho, cut, seed=1000 + i)
        for i, cut in enumerate(TIME_CUTS[:-1])
    ]
    maxev_now = next(s for s in g_slates if s["label"] == "MAX EV")
    history.append({
        "date": "now",
        "strengths": [t["strength"] for t in teams],
        "games_fit": [t["games"] for t in teams],
        "p_field": [t["p_field"] for t in teams],
        "p_map": p_map, "p_bo3": p_bo3,
        "marginals": marg,
        "champion": {k: _round(v) for k, v in champion.items()},
        "reach_gf": {k: _round(v) for k, v in reach_gf.items()},
        "slot_marg": slot_marg,
        "maxev": {k: maxev_now[k] for k in
                  ("ev", "e_correct", "p5", "p8", "sd", "hist", "picks", "probs")},
    })

    # ---- prediction-market benchmark (latest Polymarket snapshot, winner markets)
    market_probs = {k: _round(p) for k, p in con.execute(
        """SELECT team_key, prob FROM pred.market_odds
           WHERE team_key IS NOT NULL
             AND fetched_at = (SELECT max(fetched_at) FROM pred.market_odds)"""
    ).fetchall()}
    m_meta = con.execute(
        """SELECT max(fetched_at), coalesce(sum(volume), 0) FROM pred.market_odds
           WHERE fetched_at = (SELECT max(fetched_at) FROM pred.market_odds)"""
    ).fetchone()
    raw_sum = sum(market_probs.values())
    if raw_sum > 0:   # proportional de-vig: winner markets carry overround
        market_probs = {k: _round(p / raw_sum) for k, p in market_probs.items()}
    market = {"probs": market_probs,
              "raw_sum": _round(raw_sum, 3),
              "fetched": str(m_meta[0]) if m_meta[0] else None,
              "volume": _round(m_meta[1], 0)}

    news = [{"t": str(t)[:10], "title": ti_, "url": u} for t, ti_, u in con.execute(
        "SELECT posted_at, title, url FROM meta.news ORDER BY posted_at DESC LIMIT 6"
    ).fetchall()]

    # Slate Lab: packed 2,000-draw sample (bucket idx per team per draw, teams in
    # strength order) so the client can score hand-built slates on JOINT draws —
    # the escalating table is convex, so marginals alone must never price a slate.
    rule_case = []
    for bi, (_bid, _cap, rule, _lbl) in enumerate(slates.load_buckets()):
        if rule.startswith("record="):
            rule_case.append(f"WHEN record = '{rule.split('=', 1)[1]}' THEN {bi}")
        elif rule == "elim_survived":
            rule_case.append(f"WHEN survived_elim THEN {bi}")
        elif rule == "elim_lost":
            rule_case.append(f"WHEN survived_elim = FALSE THEN {bi}")
    case_sql = "CASE " + " ".join(rule_case) + " ELSE 9 END"
    grid: dict[int, dict[str, int]] = {}
    for d, tk, b in con.execute(
        f"SELECT draw_id, team_key, {case_sql} FROM gold.sim_group_draws "
        "WHERE run_id = ? AND draw_id < 2000", [grp_run]
    ).fetchall():
        grid.setdefault(int(d), {})[tk] = int(b)
    draw_sample = ["".join(str(grid[d][k]) for k in order) for d in sorted(grid)]

    # 16x6 bucket-probability matrix over the REAL buckets (group-stage hand-tuning aid)
    bucket_matrix = {tk: [_round(a), _round(b), _round(c), _round(d), _round(e), _round(f)]
                     for tk, a, b, c, d, e, f in con.execute(
        """SELECT team_key,
                  avg(CASE WHEN record = '4-0' THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN record = '4-1' THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN survived_elim THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN survived_elim = FALSE THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN record = '1-4' THEN 1.0 ELSE 0.0 END),
                  avg(CASE WHEN record = '0-4' THEN 1.0 ELSE 0.0 END)
           FROM gold.sim_group_draws WHERE run_id = ? GROUP BY 1""", [grp_run]).fetchall()}

    # ---- fantasy: the P1 board stays pinned to the chained pre-groups run (the
    # archive of the locked decision); the P2 board re-optimizes on the
    # post-groups run when one exists (real 8 entrants, 5-emblem banners)
    def _card_pack(v: dict) -> dict:
        return {"stats": v["stats"], "n": v["n_games"], "joint": bool(v["joint"]),
                "players": v["players"],
                "ev1": _round(v["ev"].get("p1", 0.0), 0),
                "ev2": _round(v["ev"].get("p2", 0.0), 0),
                "slots": v["slots"], "sp": v["stat_pts"], "alts": v["alts"],
                "fit": v["fit_cost"], "opts": v["slot_opts"],
                "t1": [round(x) for x in v["ev64"].get("p1", [])],
                "t2": [round(x) for x in v["ev64"].get("p2", [])]}

    pre_frun = slates.latest_run(con, "sim_fantasy_paths", stage="pre_groups")
    fres = fantasy.optimize_rosters(con, pre_frun, top=1, write=False)
    rosters = [[tidx[r["support_team"]], tidx[r["core_team"]], tidx[r["mid_team"]],
                round(r["ev_p1"]), round(r["sd_p1"]), round(r["q90_p1"]),
                round(r.get("ev_p2", 0.0))]
               for r in fres["table"]]
    cards = {ck: _card_pack(v) for ck, v in fres["cards_meta"].items()}

    rosters2 = None
    cards2 = None
    if br_run:
        fres2 = fantasy.optimize_rosters(con, br_run, top=1, write=False)
        rosters2 = [[tidx[r["support_team"]], tidx[r["core_team"]], tidx[r["mid_team"]],
                     round(r["ev_p2"]), round(r["sd_p2"]), round(r["q90_p2"])]
                    for r in fres2["table"]]
        cards2 = {ck: _card_pack(v) for ck, v in fres2["cards_meta"].items()}

    # ---- realized P1 fantasy production per player (who actually performed)
    p1_actuals = None
    p1_rows = con.execute(
        """SELECT f.account_id, count(*), round(sum(f.pts_total)), round(avg(f.pts_total))
           FROM fan.fantasy_points f
           JOIN pred.games g USING (match_id)
           JOIN pred.series se ON se.series_id = g.series_id
           WHERE se.stage IN ('group_swiss', 'group_elim') AND f.parsed
           GROUP BY 1"""
    ).fetchall()
    if p1_rows:
        best_series = dict(con.execute(
            """SELECT p.account_id, round(max(p.pts_series_top2))
               FROM fan.player_series_points p
               JOIN pred.series se ON se.series_id = p.series_id
               WHERE se.stage IN ('group_swiss', 'group_elim')
               GROUP BY 1"""
        ).fetchall())
        p1_actuals = {int(a): [int(g), int(tot), int(avg), int(best_series.get(a, 0))]
                      for a, g, tot, avg in p1_rows}

    # ---- Bracket Lab payload: real entrants, packed joint draws, optimizer fills
    bracket = None
    if br_run:
        bres = slates.optimize_bracket(con, br_run, top_n=5, write=False)
        r1_rows = con.execute(
            """SELECT slot_id, any_value(team1_key), any_value(team2_key)
               FROM gold.sim_bracket_draws WHERE run_id = ?
                 AND slot_id IN ('R1M1','R1M2','R1M3','R1M4') GROUP BY 1""",
            [br_run]).fetchall()
        entrants = sorted({t for _s, t1, t2 in r1_rows for t in (t1, t2)})
        qf_pairs = {s: [t1, t2] for s, t1, t2 in r1_rows}
        eidx = {t: i for i, t in enumerate(entrants)}
        slot_order = [s["slot"] for s in slots]
        soi = {s: i for i, s in enumerate(slot_order)}
        packed = [[None] * len(slot_order) for _ in range(2000)]
        for d, s, w in con.execute(
                """SELECT draw_id, slot_id, winner_key FROM gold.sim_bracket_draws
                   WHERE run_id = ? AND draw_id < 2000""", [br_run]).fetchall():
            packed[int(d)][soi[s]] = str(eidx[w])
        bracket = {
            "run": br_run, "entrants": entrants, "slot_order": slot_order, "qf": qf_pairs,
            "table": [float(x) for x in slates.BRACKET_TABLE],
            "sample": ["".join(row) for row in packed if None not in row],
            "chalk_ev": _round(bres["chalk_ev"], 1),
            "fills": [{"ev": _round(f["ev"], 1), "picks": f["picks"],
                       "champion": f["champion"]} for f in bres["fills"]],
        }

    # coach-title condition hit rates: pool-wide (match-level) + the sim's own
    # decider rate (carries rho); fb/torm coverage guards against silent gaps
    tr = con.execute(
        """SELECT round(avg((dur < 1500)::INT), 3), round(avg((dur % 10 = 8)::INT), 3),
                  round(avg((fb <= 0)::INT), 3), round(avg((fb > 600)::INT), 3),
                  round(avg((tv > 0)::INT), 3), round(avg((fb IS NOT NULL)::INT), 3)
           FROM (SELECT match_id, any_value(duration_s) AS dur,
                        any_value(first_blood_time_s) AS fb,
                        any_value(tormentor_victims) AS tv
                 FROM gold.fantasy_game_pool GROUP BY 1)"""
    ).fetchone()
    dec_rate = con.execute(
        "SELECT round(avg((n_games = best_of)::INT), 3) FROM gold.sim_fantasy_paths "
        "WHERE run_id = ?", [fres["run_id"]]
    ).fetchone()[0]
    tag_src = con.execute("SELECT max(source) FROM meta.hero_tags").fetchone()[0]
    titles = {**fres["titles"], "tag_source": tag_src,
              "rates": {"dur": tr[0], "clock8": tr[1], "fbpre": tr[2], "fblate": tr[3],
                        "torm": tr[4], "coverage": tr[5], "decider": dec_rate}}

    # ---- backtest scoreboard (if `ti backtest` ran since the last build-gold)
    bt = {v: [_round(ll), _round(br), n] for v, ll, br, n in con.execute(
        """SELECT model_variant,
                  avg(-(outcome * ln(greatest(p_pred, 1e-9))
                        + (1 - outcome) * ln(greatest(1 - p_pred, 1e-9)))),
                  avg(pow(p_pred - outcome, 2)), count(*)
           FROM gold.backtest_preds WHERE level = 'series' GROUP BY 1""").fetchall()} or None

    as_of = con.execute("SELECT max(as_of) FROM gold.team_strength").fetchone()[0]
    return {
        "meta": {
            "as_of": str(as_of), "generated": datetime.now(timezone.utc).isoformat(),
            "group_run": grp_run, "fantasy_run": fres["run_id"],
            "stage": "post_groups" if br_run else "pre_groups",
            "bracket_run": br_run, "bracket_draws": int(br_draws) if br_run else None,
            "fantasy_run2": br_run,
            "n_draws": int(n_draws), "n_paths": int(fres["n_draws"]),
            "rho": _round(rho, 2), "lock_utc": config.GROUP_LOCK_UTC,
            "bracket_lock_utc": config.BRACKET_LOCK_UTC,
            "model": {"halflife": ratings.HALFLIFE_DAYS, "lam_ti": ratings.LAM_TI,
                      "alpha": ratings.ALPHA},
            "backtest": bt,
        },
        "teams": teams,
        "p_map": p_map,
        "p_bo3": p_bo3,
        "marginals": marg,
        "champion": {k: _round(v) for k, v in champion.items()},
        "reach_gf": {k: _round(v) for k, v in reach_gf.items()},
        "slots": slots,
        "slot_marg": slot_marg,
        "exposure": exposure,
        "buckets": [{"id": b[0], "cap": b[1], "label": b[3]} for b in buckets],
        "group_slates": g_slates,
        "history": history,
        "form": _form(con, order, rho),
        "sheet": _teamsheet(con, order),
        "roster": _players(con),
        "logos": _team_logos(order),
        "market": market,
        "news": news,
        "rosters": rosters,
        "cards": cards,
        "rosters2": rosters2,
        "cards2": cards2,
        "titles": titles,
        "bucket_matrix": bucket_matrix,
        "group_table": list(slates.GROUP_TABLE),
        "draw_sample": draw_sample,
        "actuals": actuals,
        "p1_actuals": p1_actuals,
        "bracket": bracket,
    }


def render(data: dict) -> str:
    html = TEMPLATE.read_text(encoding="utf-8")
    token = "/*__DATA__*/null"
    if token not in html:
        raise RuntimeError("dashboard template is missing the data token")
    return html.replace(token, json.dumps(data, separators=(",", ":")))


def run(out_path: Path | None = None) -> Path:
    if not config.SILVER_DB.exists():
        raise SystemExit("silver DB missing — run `ti build-silver` + `ti build-gold` first")
    con = duckdb.connect(str(config.SILVER_DB), read_only=True)
    try:
        data = collect(con)
    finally:
        con.close()
    out = out_path or OUT_DEFAULT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    return out
