"""Fantasy roster EV: empirical card-level bootstrap on simulated tournament paths.

Cards (per TI2026 roster rules): support duo / core duo (safelane+offlane) / mid,
each from a different team. Rules §2.4: role members' game scores are AVERAGED,
top-2 games of a series count, and only the BEST series per period scores — so a
card's period score = max over its team's series of (top-2 of card-avg game scores).

Method per card:
  1. Bank: the card's real 2026 games from gold.fantasy_game_pool where ALL card
     members played the same match (preserves duo-mate correlation), averaged into
     one 18-dim pts vector per match, split by win/loss.
  2. Banner = the card's top-3 stats by weighted mean (base values; quality/trait
     multipliers scale candidates near-uniformly, so roster CHOICE is robust to
     them — banner_score() below carries the exact math for offer comparisons).
  3. For every sim path series: sample that many win-games/loss-games from the
     bank (weighted by recency/patch), score = top-2 games; period = best series.
Rosters = ordered team triples with repeats allowed (16^3 = 4,096 — the client's
Choose Team selector accepts the same team in several role slots, confirmed
in-client 2026-08-01); correlation is preserved by summing card score arrays on
the SAME draws, so same-team stacks inherit the full shared-series-path swing.
(Their swing is still somewhat understated: cards sample game scores
independently within a shared series.)

Coach titles (rules §2.3): one prefix + one suffix multiply a player's FINAL game
score when their condition hits. Conditions are properties of the sampled
historical game (duration, clock digit, FB timing, tormentor deaths, hero tag) or
of the simulated series position (loss game, deciding game) — so multipliers are
applied per sampled game INSIDE the bootstrap, before top-2/best-series selection.
That keeps the real correlations (short games score fewer raw points, loss games
rarely win best-series) that a flat expected-multiplier would miss. Assumptions:
prefix and suffix stack multiplicatively; duo prefix effect uses the member hit
fraction (exact when duo-mates score equally, error is second-order); "the Cruel"
(own-fountain death) is unobservable -> constant CRUEL_P hit rate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

STAT_COLS = [
    "pts_kills", "pts_deaths", "pts_cs", "pts_gpm", "pts_madstone", "pts_towers",
    "pts_wards", "pts_stacks", "pts_runes", "pts_watchers", "pts_smokes", "pts_lotus",
    "pts_roshan", "pts_tfp", "pts_stuns", "pts_tormentor", "pts_firstblood", "pts_courier",
]
# rules §2.5/2.6: emblem color -> stat pool (first 6 red, next 6 blue, last 6 green)
STAT_COLOR = {c: ("red" if i < 6 else "blue" if i < 12 else "green")
              for i, c in enumerate(STAT_COLS)}
QUALITY_BOOST = {1: 0.10, 2: 0.30, 3: 0.60, 4: 1.00, 5: 1.50}
MIN_JOINT_GAMES = 15


def load_banner_layouts() -> dict[str, dict[str, list[str]]]:
    """seeds/banner_layouts.csv: period -> role -> ordered emblem slot colors.

    Color split AND slot order are fixed per role (tutorial_03; order matters for
    adjacency traits). TI2026 client-confirmed 2026-08-17: Group Stage (p1) banners
    have 3 emblems, Main Event (p2) banners have 5 — the first three carry over and
    two slots are added. Rows without a period column read as p1 (legacy shape)."""
    import csv

    from .. import config

    path = config.SEEDS_DIR / "banner_layouts.csv"
    layouts = {
        "p1": {"core": ["red", "green", "red"], "mid": ["red", "blue", "green"],
               "support": ["blue", "green", "blue"]},
        "p2": {"core": ["red", "green", "red", "green", "red"],
               "mid": ["red", "blue", "green", "red", "green"],
               "support": ["blue", "green", "blue", "green", "blue"]},
    }
    if path.exists():
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("role") and r.get("slots"):
                    layouts.setdefault(r.get("period") or "p1", {})[r["role"]] = \
                        r["slots"].split("|")
    return layouts

# ------------------------------------------------------------- coach titles
CRUEL_P = 0.02          # P(any player killed in own fountain) — unobservable, estimate
F_BASE = ["f_dur", "f_clock8", "f_fbpre", "f_fblate", "f_torm"]
_SUFFIX_FLAG = {         # cond_type -> flag column (game-fact conditions)
    "duration_lt_s": "f_dur", "clock_ends_8": "f_clock8", "fb_pre_horn": "f_fbpre",
    "fb_after_s": "f_fblate", "tormentor_death": "f_torm",
}


def load_titles(con) -> dict:
    """meta.coach_titles + meta.hero_tags -> bootstrap config.

    Order of the prefix/suffix lists fixes the ev64 combo layout (p*n_suf + s);
    every consumer must take both from the same result payload."""
    rows = con.execute(
        "SELECT title, side, pct, cond_type, coalesce(cond_param, ''), coalesce(note, '') "
        "FROM meta.coach_titles"
    ).fetchall()
    prefixes = [{"name": t, "pct": pct / 100.0, "tags": param.split("|"), "note": note}
                for t, side, pct, ct, param, note in rows if side == "prefix"]
    suffixes = [{"name": t, "pct": pct / 100.0, "cond": ct, "param": param, "note": note}
                for t, side, pct, ct, param, note in rows if side == "suffix"]
    hero_tags = {hid: set(tags or []) for hid, tags in
                 con.execute("SELECT hero_id, tags FROM meta.hero_tags").fetchall()}
    fcols = F_BASE + [f"f_p{i}" for i in range(len(prefixes))]
    return {"prefixes": prefixes, "suffixes": suffixes, "hero_tags": hero_tags,
            "fcols": fcols, "tagged_heroes": len(hero_tags)}


def _add_flags(df: pd.DataFrame, titles: dict) -> None:
    """Per player-game condition columns (floats so duo-averaging -> hit fractions)."""
    params = {s["cond"]: s["param"] for s in titles["suffixes"]}
    dur = df.duration_s.fillna(0).astype(int)
    fb = pd.to_numeric(df.first_blood_time_s, errors="coerce")
    df["f_dur"] = (dur < int(params.get("duration_lt_s", 1500))).astype(float)
    df["f_clock8"] = (dur % 10 == 8).astype(float)
    df["f_fbpre"] = fb.le(0).fillna(False).astype(float)   # API clamps pre-horn FB to 0
    df["f_fblate"] = fb.gt(int(params.get("fb_after_s", 600))).fillna(False).astype(float)
    df["f_torm"] = pd.to_numeric(df.tormentor_victims, errors="coerce").gt(0) \
        .fillna(False).astype(float)
    for i, p in enumerate(titles["prefixes"]):
        want = set(p["tags"])
        tagged = {hid for hid, hts in titles["hero_tags"].items() if hts & want}
        df[f"f_p{i}"] = df.hero_id.isin(tagged).astype(float) if tagged else 0.0


# ------------------------------------------------------------- banner algebra

def banner_score(pts: dict[str, float], emblems: list[tuple[str, int, str | None]]) -> float:
    """Exact War Banner math for a single game's stat points.

    emblems: (stat, quality_tier 1-5, trait) per slot — 3 in the group stage, 5 in
    the main event — slot order = adjacency chain (inner slots neighbor both
    sides, edge slots one). Quality and trait effects COMBINE
    ADDITIVELY as percentages of the base stat score (tutorial_05/06 wording +
    the in-client emblem header showing Tier II (+30%) with a −10% adjacency
    effect as a net 120%)."""
    n = len(emblems)
    qualities = [q for _, q, _ in emblems]
    traits = [t for _, _, t in emblems]
    bonus = [QUALITY_BOOST[q] for q in qualities]      # additive % of base
    for i, t in enumerate(traits):
        neighbors = [j for j in (i - 1, i + 1) if 0 <= j < n]
        if t == "benevolent":
            for j in neighbors:
                bonus[j] += 0.20
        elif t == "vampiric":
            bonus[i] += 0.50
            for j in neighbors:
                bonus[j] -= 0.10
        elif t == "fractal":
            if len(set(qualities)) == n:
                bonus[i] += 0.60
        elif t == "unique":
            if traits.count("unique") == 1:
                bonus[i] += 0.30
        elif t == "friendly":
            if traits.count("friendly") >= 3:
                bonus[i] += 0.50
    return sum(pts.get(stat, 0.0) * (1.0 + bonus[i])
               for i, (stat, _, _) in enumerate(emblems))


# ------------------------------------------------------------------ card data

def load_pool(con, titles: dict | None = None) -> pd.DataFrame:
    df = con.execute(
        "SELECT match_id, account_id, win, weight, hero_id, duration_s, "
        f"first_blood_time_s, tormentor_victims, {', '.join(STAT_COLS)} "
        "FROM gold.fantasy_game_pool WHERE win IS NOT NULL"
    ).df()
    df[STAT_COLS] = df[STAT_COLS].fillna(0.0)
    if titles is not None:
        _add_flags(df, titles)
    return df


def cards_from_roster(con) -> list[dict]:
    """48 cards: (team, kind) with member account ids."""
    rows = con.execute(
        "SELECT team_key, account_id, fantasy_slot, nickname FROM fan.players ORDER BY team_key"
    ).fetchall()
    by_team: dict[str, dict[str, list]] = {}
    names: dict[int, str] = {}
    for team, acc, slot, nick in rows:
        by_team.setdefault(team, {}).setdefault(slot, []).append(acc)
        names[acc] = nick
    cards = []
    for team, slots in by_team.items():
        cards.append({"team": team, "kind": "support", "accounts": slots["support"]})
        cards.append({"team": team, "kind": "core",
                      "accounts": [slots["safelane"][0], slots["offlane"][0]]})
        cards.append({"team": team, "kind": "mid", "accounts": slots["mid"]})
    return cards, names


def kind_bank(pool: pd.DataFrame, accounts: list[int], fcols: list[str] | None = None):
    """Position-wide prior bank: every player-game row of every player of this
    kind, unaveraged — pseudo card-games used only to regularize thin duos."""
    sub = pool[pool.account_id.isin(accounts)]
    W = sub[sub.win]
    L = sub[~sub.win]
    out = {
        "W": W[STAT_COLS].to_numpy(), "wW": W.weight.to_numpy(),
        "L": L[STAT_COLS].to_numpy(), "wL": L.weight.to_numpy(),
        "joint": True, "n_w": len(W), "n_l": len(L),
    }
    if fcols:
        out["FW"] = W[fcols].to_numpy()
        out["FL"] = L[fcols].to_numpy()
    return out


def blend_bank(own, kind, kappa: float = 15.0):
    """Empirical-Bayes mixture: sample from the card's own games with probability
    n/(n+kappa) per side, else from the position-wide bank — a 20-game duo gets
    ~45% prior, a 170-game one ~8%. Shrinks noisy means AND variances."""
    out = dict(own)
    for side, wcol, fkey in (("W", "wW", "FW"), ("L", "wL", "FL")):
        n_own = len(own[side])
        if not len(kind[side]):
            continue
        if not n_own:
            out[side], out[wcol] = kind[side], kind[wcol]
            if fkey in kind:
                out[fkey] = kind[fkey]
            continue
        s_own = own[wcol].sum()
        target = s_own * kappa / n_own          # total prior weight -> P(prior) = kappa/(n+kappa)
        kw = kind[wcol] * (target / kind[wcol].sum())
        out[side] = np.vstack([own[side], kind[side]])
        out[wcol] = np.concatenate([own[wcol], kw])
        if fkey in own and fkey in kind:
            out[fkey] = np.vstack([own[fkey], kind[fkey]])
    return out


def card_bank(pool: pd.DataFrame, accounts: list[int], fcols: list[str] | None = None):
    """Per-match card-average pts vectors split by win/loss.

    Joint rows (all members in the same match) preserve duo correlation; if the
    duo has too few joint games (fresh pairing), fall back to per-player rows
    (documented approximation, flagged in the result). Flag columns average into
    member hit fractions (match-level flags stay 0/1; hero tags become 0/.5/1)."""
    sub = pool[pool.account_id.isin(accounts)]
    counts = sub.groupby("match_id").account_id.nunique()
    joint_ids = counts[counts == len(accounts)].index
    joint = len(joint_ids) >= MIN_JOINT_GAMES or len(accounts) == 1
    if joint:
        sub = sub[sub.match_id.isin(joint_ids)]
    agg = {**{c: "mean" for c in STAT_COLS}, "weight": "mean",
           **{c: "mean" for c in (fcols or [])}}
    g = sub.groupby(["match_id", "win"], as_index=False).agg(agg)
    W = g[g.win]
    L = g[~g.win]
    out = {
        "W": W[STAT_COLS].to_numpy(), "wW": W.weight.to_numpy(),
        "L": L[STAT_COLS].to_numpy(), "wL": L.weight.to_numpy(),
        "joint": joint, "n_w": len(W), "n_l": len(L),
    }
    if fcols:
        out["FW"] = W[fcols].to_numpy()
        out["FL"] = L[fcols].to_numpy()
    return out


def _pool_means(bank) -> np.ndarray:
    X = np.vstack([bank["W"], bank["L"]])
    w = np.concatenate([bank["wW"], bank["wL"]])
    if not len(X):
        return np.zeros(len(STAT_COLS))
    return (X * w[:, None]).sum(0) / w.sum()


def slot_options(means: np.ndarray, layout: list[str], extra: int = 2) -> list[dict]:
    """Ranked stat options PER COLOR GROUP of the layout.

    Rolling is per-emblem, so the useful view is per color: a group with n
    slots lists its top n+extra stats — the first n are the roll targets, the
    rest price the fallbacks (support 2B+1G -> 4 blue options, 3 green)."""
    from collections import Counter

    counts = Counter(layout)
    groups, seen = [], set()
    for color in layout:                     # unique colors in layout order
        if color in seen:
            continue
        seen.add(color)
        n = counts[color]
        ranked = [i for i in np.argsort(-means)
                  if STAT_COLOR[STAT_COLS[i]] == color][: n + extra]
        groups.append({"color": color, "n": n,
                       "opts": [[STAT_COLS[i][4:], round(float(means[i]))]
                                for i in ranked]})
    return groups


def choose_stats(bank, k: int = 3, layout: list[str] | None = None) -> list[int]:
    """Stats by pooled weighted mean points. Without a layout: free top-k
    (legacy). With a layout: the banner's color slots are FIXED per role
    (tutorial_03), so each slot takes the best remaining stat of its color."""
    mean = _pool_means(bank)
    if layout is None:
        return list(np.argsort(-mean)[:k])
    order = {c: [i for i in np.argsort(-mean) if STAT_COLOR[STAT_COLS[i]] == c]
             for c in ("red", "blue", "green")}
    taken: list[int] = []
    for color in layout:
        nxt = next(i for i in order[color] if i not in taken)
        taken.append(nxt)
    return taken


# ----------------------------------------------------- period score bootstrap

def card_period_scores(bank, stats_idx, paths: pd.DataFrame, n_draws: int,
                       rng: np.random.Generator, titles: dict | None = None):
    """paths: this team's sim_fantasy_paths rows. Returns period -> (n_draws,)
    bootstrapped card period scores (0 where the team plays no series).

    With `titles`, returns (base_out, ev64_out): ev64_out[period] is the (n_p*n_s,)
    EV per prefix x suffix combo, multipliers applied per sampled game BEFORE the
    top-2 / best-series order statistics (combo index = p * n_s + s)."""
    sw = bank["W"][:, stats_idx].sum(1) if bank["n_w"] else np.zeros(1)
    sl = bank["L"][:, stats_idx].sum(1) if bank["n_l"] else np.zeros(1)
    pw = bank["wW"] / bank["wW"].sum() if bank["n_w"] else np.ones(1)
    pl = bank["wL"] / bank["wL"].sum() if bank["n_l"] else np.ones(1)

    tcfg = None
    if titles is not None:
        n_p, n_s = len(titles["prefixes"]), len(titles["suffixes"])
        FW = bank["FW"] if bank["n_w"] else np.zeros((1, len(titles["fcols"])))
        FL = bank["FL"] if bank["n_l"] else np.zeros((1, len(titles["fcols"])))
        fidx = {c: i for i, c in enumerate(titles["fcols"])}
        pref_pct = np.array([p["pct"] for p in titles["prefixes"]], dtype=np.float32)
        tcfg = (n_p, n_s, FW, FL, fidx, pref_pct)

    out: dict[str, np.ndarray] = {}
    out64: dict[str, np.ndarray] = {}
    need = np.where(paths.best_of.to_numpy() >= 5, 3, 2)
    gw_all = np.where(paths.won.to_numpy(), need, paths.n_games.to_numpy() - need)
    gl_all = paths.n_games.to_numpy() - gw_all
    dec_all = paths.n_games.to_numpy() == paths.best_of.to_numpy()  # deciding game played
    for period in sorted(paths.period_id.unique()):
        mask = (paths.period_id == period).to_numpy()
        draws = paths.draw_id.to_numpy()[mask]
        gw, gl, dec = gw_all[mask], gl_all[mask], dec_all[mask]
        series_scores = np.zeros(mask.sum())
        series64 = np.zeros((mask.sum(), tcfg[0] * tcfg[1]), dtype=np.float32) if tcfg else None
        for w_ct, l_ct, is_dec in sorted(set(zip(gw.tolist(), gl.tolist(), dec.tolist()))):
            m = (gw == w_ct) & (gl == l_ct) & (dec == is_dec)
            cnt = int(m.sum())
            if not cnt:
                continue
            parts, fparts = [], []
            if w_ct:
                iw = rng.choice(len(sw), size=(cnt, int(w_ct)), p=pw)
                parts.append(sw[iw])
                if tcfg:
                    fparts.append(FW[iw])
            if l_ct:
                il = rng.choice(len(sl), size=(cnt, int(l_ct)), p=pl)
                parts.append(sl[il])
                if tcfg:
                    fparts.append(FL[il])
            games = np.hstack(parts) if parts else np.zeros((cnt, 1))
            if tcfg and parts:
                n_p, n_s, _, _, fidx, pref_pct = tcfg
                F = np.concatenate(fparts, axis=1)          # (cnt, g, n_flags)
                g_n = games.shape[1]
                smult = np.ones((cnt, g_n, n_s), dtype=np.float32)
                for si, s in enumerate(titles["suffixes"]):
                    ct = s["cond"]
                    if ct in _SUFFIX_FLAG:
                        smult[:, :, si] += s["pct"] * F[:, :, fidx[_SUFFIX_FLAG[ct]]]
                    elif ct == "player_loss":
                        if l_ct:
                            smult[:, int(w_ct):, si] += s["pct"]
                    elif ct == "deciding_game":
                        if is_dec:  # winner takes the last game of the series
                            col = int(w_ct) - 1 if w_ct > l_ct else g_n - 1
                            smult[:, col, si] += s["pct"]
                    else:  # own_fountain_death and any future unobservable
                        smult[:, :, si] += s["pct"] * CRUEL_P
                pmult = 1.0 + F[:, :, len(F_BASE):len(F_BASE) + n_p] * pref_pct[None, None, :]
                adj = (games.astype(np.float32)[:, :, None, None]
                       * pmult[:, :, :, None] * smult[:, :, None, :])   # (cnt, g, n_p, n_s)
                adj.sort(axis=1)
                series64[m] = adj[:, -2:, :, :].sum(axis=1).reshape(cnt, n_p * n_s)
            games.sort(axis=1)
            series_scores[m] = games[:, -2:].sum(1)  # top-2 (or the single game)
        per_draw = np.zeros(n_draws)
        np.maximum.at(per_draw, draws, series_scores)  # best series per period
        out[period] = per_draw
        if tcfg is not None:
            pd64 = np.zeros((n_draws, tcfg[0] * tcfg[1]), dtype=np.float32)
            np.maximum.at(pd64, draws, series64)
            out64[period] = pd64.mean(axis=0)
    return out if titles is None else (out, out64)


# -------------------------------------------------------------------- rosters

def optimize_rosters(con, run_id: str | None = None, seed: int = 1, top: int = 15,
                     write: bool = True) -> dict:
    from . import slates

    run_id = run_id or slates.latest_run(con, "sim_fantasy_paths")
    n_draws = con.execute(
        "SELECT max(draw_id) + 1 FROM gold.sim_fantasy_paths WHERE run_id = ?", [run_id]
    ).fetchone()[0]
    pdf = con.execute(
        """SELECT team_key, draw_id, period_id, won, n_games, best_of
           FROM gold.sim_fantasy_paths WHERE run_id = ?""", [run_id]
    ).df()
    titles = load_titles(con)
    layouts = load_banner_layouts()
    pool = load_pool(con, titles)
    all_cards, _names = cards_from_roster(con)
    rng = np.random.default_rng(seed)

    periods = sorted(pdf.period_id.unique())
    primary = periods[0]      # 'p1' on chained pre-groups runs, 'p2' on post-groups
    # post-groups paths only carry the 8 bracket entrants — eliminated teams score 0
    # by definition, so drop their cards (16^3 -> 8^3 triples on a post-groups run)
    alive = set(pdf.team_key.unique())
    cards = [c for c in all_cards if c["team"] in alive]
    n_combo = len(titles["prefixes"]) * len(titles["suffixes"])
    # position-wide priors stay built from ALL rosters, not just alive teams
    kind_accounts: dict[str, list[int]] = {"support": [], "core": [], "mid": []}
    for card in all_cards:
        kind_accounts[card["kind"]].extend(card["accounts"])
    kind_banks = {k: kind_bank(pool, accs, titles["fcols"])
                  for k, accs in kind_accounts.items()}

    per_card: dict[tuple[str, str], dict] = {}
    for card in cards:
        own = card_bank(pool, card["accounts"], titles["fcols"])
        bank = blend_bank(own, kind_banks[card["kind"]])
        means = _pool_means(bank)
        tpaths = pdf[pdf.team_key == card["team"]]
        scores: dict[str, np.ndarray] = {}
        ev64: dict[str, np.ndarray] = {}
        pmeta: dict[str, dict] = {}
        # banner layouts differ per period (3 emblems in p1, 5 in p2) -> stats are
        # chosen and scored per period
        for period in periods:
            layout = layouts.get(period, layouts["p1"])[card["kind"]]
            stats_idx = choose_stats(bank, layout=layout)  # color-locked per role
            s, e = card_period_scores(bank, stats_idx,
                                      tpaths[tpaths.period_id == period],
                                      n_draws, rng, titles)
            scores[period] = s.get(period, np.zeros(n_draws))
            ev64[period] = e.get(period, np.zeros(n_combo, dtype=np.float32))
            free_idx = choose_stats(bank, k=len(layout))   # unconstrained reference
            alts = []
            for si, color in enumerate(layout):
                pool_i = [i for i in np.argsort(-means)
                          if STAT_COLOR[STAT_COLS[i]] == color and i not in stats_idx]
                alts.append([STAT_COLS[pool_i[0]][4:],
                             round(float(means[stats_idx[si]] - means[pool_i[0]]))]
                            if pool_i else None)
            pmeta[period] = {
                "stats": [STAT_COLS[i][4:] for i in stats_idx], "slots": layout,
                "stat_pts": [round(float(means[i])) for i in stats_idx],
                "alts": alts,
                "fit_cost": round(float(means[free_idx].sum()
                                        - means[np.array(stats_idx)].sum())),
                "slot_opts": slot_options(means, layout),
            }
        per_card[(card["team"], card["kind"])] = {
            "scores": scores, **pmeta[primary],
            "joint": bank["joint"], "n_games": bank["n_w"] + bank["n_l"],
            "accounts": card["accounts"],
            "players": [_names.get(a, str(a)) for a in card["accounts"]],
            "ev": {p: float(np.mean(arr)) for p, arr in scores.items()},
            "ev64": {p: arr.astype(float) for p, arr in ev64.items()},
        }

    teams = sorted({c["team"] for c in cards})
    rows = []
    # repeats allowed: the client's Choose Team selector accepts the same team in
    # several role slots (confirmed in-client 2026-08-01) -> ordered triples over
    # the alive teams (16^3 pre-groups, 8^3 post-groups). Same-team cards share the
    # team's simulated series path, so their per-draw sums are correlated exactly
    # as the tournament correlates them.
    for ts in teams:
        s_arr = per_card[(ts, "support")]["scores"]
        for tc in teams:
            c_arr = per_card[(tc, "core")]["scores"]
            for tm in teams:
                m_arr = per_card[(tm, "mid")]["scores"]
                tot0 = s_arr[primary] + c_arr[primary] + m_arr[primary]
                row = {"support_team": ts, "core_team": tc, "mid_team": tm,
                       f"ev_{primary}": float(tot0.mean()),
                       f"sd_{primary}": float(tot0.std()),
                       f"q90_{primary}": float(np.quantile(tot0, 0.9))}
                for p in periods[1:]:
                    tot = s_arr.get(p, 0) + c_arr.get(p, 0) + m_arr.get(p, 0)
                    row[f"ev_{p}"] = float(np.mean(tot))
                rows.append(row)
    table = (pd.DataFrame(rows).sort_values(f"ev_{primary}", ascending=False)
             .reset_index(drop=True))

    def _combo64(ts: str, tc: str, tm: str, period: str) -> np.ndarray:
        z = np.zeros(n_combo)
        for key in ((ts, "support"), (tc, "core"), (tm, "mid")):
            z = z + per_card[key]["ev64"].get(period, 0)
        return z

    top_rows = table.head(top).to_dict("records")
    detail = []
    for r in top_rows:
        combo = _combo64(r["support_team"], r["core_team"], r["mid_team"], primary)
        bi = int(np.argmax(combo))
        n_s = len(titles["suffixes"])
        detail.append({
            **r,
            "best_title": {"prefix": titles["prefixes"][bi // n_s]["name"],
                           "suffix": titles["suffixes"][bi % n_s]["name"],
                           "combo_idx": bi, "ev_titled": float(combo[bi])},
            "cards": {
                "support": {"team": r["support_team"],
                            **{k: per_card[(r["support_team"], "support")][k]
                               for k in ("stats", "joint", "n_games")}},
                "core": {"team": r["core_team"],
                         **{k: per_card[(r["core_team"], "core")][k]
                            for k in ("stats", "joint", "n_games")}},
                "mid": {"team": r["mid_team"],
                        **{k: per_card[(r["mid_team"], "mid")][k]
                           for k in ("stats", "joint", "n_games")}},
            },
        })
    if write:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for i, d in enumerate(detail, 1):
            sid = f"fantasy-{run_id}-{primary}-top{i}"
            con.execute("DELETE FROM gold.slates WHERE slate_id = ?", [sid])
            con.execute(
                "INSERT INTO gold.slates VALUES (?,?,?,?,?,?,?,?)",
                [sid, "fantasy", run_id, json.dumps(d, default=str),
                 d[f"ev_{primary}"], None, "card_bootstrap", now],
            )
    return {
        "run_id": run_id, "n_draws": int(n_draws), "top": detail, "periods": periods,
        "primary": primary,
        # full ranking + card metadata for downstream consumers (dashboard risk slider)
        "table": table.round(1).to_dict("records"),
        "cards_meta": {f"{t}|{k}": {"stats": v["stats"], "joint": v["joint"],
                                    "n_games": v["n_games"], "players": v["players"],
                                    "ev": v["ev"],
                                    "slots": v["slots"], "stat_pts": v["stat_pts"],
                                    "alts": v["alts"], "fit_cost": v["fit_cost"],
                                    "slot_opts": v["slot_opts"],
                                    "ev64": {p: [round(float(x), 1) for x in arr]
                                             for p, arr in v["ev64"].items()}}
                       for (t, k), v in per_card.items()},
        "titles": {"prefixes": [{"name": p["name"], "pct": p["pct"], "tags": p["tags"],
                                 "note": p["note"]} for p in titles["prefixes"]],
                   "suffixes": [{"name": s["name"], "pct": s["pct"], "cond": s["cond"],
                                 "note": s["note"]} for s in titles["suffixes"]],
                   "tagged_heroes": titles["tagged_heroes"], "cruel_p": CRUEL_P},
    }


def build_profiles(con) -> int:
    """gold.player_stat_profile: weighted per-stat mean/sd per player and context."""
    pool = load_pool(con)
    rows = []
    for acc, sub in pool.groupby("account_id"):
        for ctx, s in (("all", sub), ("win", sub[sub.win]), ("loss", sub[~sub.win])):
            if not len(s):
                continue
            w = s.weight.to_numpy()
            X = s[STAT_COLS].to_numpy()
            mean = (X * w[:, None]).sum(0) / w.sum()
            sd = np.sqrt(np.clip((((X - mean) ** 2) * w[:, None]).sum(0) / w.sum(), 0, None))
            n_eff = float(w.sum())
            for i, c in enumerate(STAT_COLS):
                rows.append((int(acc), ctx, c[4:], float(mean[i]), float(sd[i]), n_eff))
    con.execute("DELETE FROM gold.player_stat_profile")
    con.executemany("INSERT INTO gold.player_stat_profile VALUES (?,?,?,?,?,?)", rows)
    return len(rows)
