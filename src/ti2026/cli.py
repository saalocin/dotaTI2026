"""CLI: `ti ingest <source> ...`, `ti build-silver`, `ti status`, `ti verify`, `ti live`."""

from __future__ import annotations

import json

import typer

from . import config, manifest

app = typer.Typer(no_args_is_help=True, add_completion=False)
ingest_app = typer.Typer(no_args_is_help=True)
app.add_typer(ingest_app, name="ingest", help="Fetch raw data into data/bronze/<source>/")
pick_app = typer.Typer(no_args_is_help=True)
app.add_typer(pick_app, name="pick", help="Optimize prediction slates against stored sim draws")


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _crosswalk_team_ids() -> list[int]:
    """Primary + alt opendota ids — alt ids are prior registrations of the same unit,
    so their match logs belong in bronze too."""
    import csv

    path = config.SEEDS_DIR / "team_crosswalk.csv"
    ids: list[int] = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("opendota_team_id"):
                ids.append(int(r["opendota_team_id"]))
            for a in (r.get("opendota_alt_ids") or "").split("|"):
                if a.strip():
                    ids.append(int(a.strip()))
    return ids


def _roster_account_ids() -> list[int]:
    import csv

    path = config.SEEDS_DIR / "player_roster.csv"
    with open(path, encoding="utf-8-sig") as f:
        return [int(r["account_id"]) for r in csv.DictReader(f) if r.get("account_id")]


@ingest_app.command("liquipedia")
def ingest_liquipedia(
    pages: str = typer.Option("main,group,bracket,rankings", help="comma-separated page keys"),
    extra: str = typer.Option("", help="comma-separated raw page titles (e.g. fixtures)"),
    intraday: bool = typer.Option(False, help="minute-resolution snapshot (live event)"),
):
    from .bronze import liquipedia

    snap = manifest.Snapshot("liquipedia", intraday=intraday)
    liquipedia.ingest(_csv(pages), snap=snap, extra_pages=_csv(extra))
    typer.echo(f"liquipedia -> {snap.dir}")


@ingest_app.command("dotaclient")
def ingest_dotaclient(
    install: str = typer.Option("", help="path to 'dota 2 beta' (default: scan Steam libraries)"),
):
    """Hero fantasy tags (title prefixes) from the local client's npc_heroes.txt."""
    from .bronze import dotaclient

    dotaclient.ingest(install or None)


@ingest_app.command("opendota")
def ingest_opendota(
    bootstrap: bool = typer.Option(False, help="proPlayers + leagues + recent proMatches"),
    teams: bool = typer.Option(False, help="/teams/{id} + matches for crosswalk team ids"),
    explorer_backfill: bool = typer.Option(False, help="bulk player_matches via /explorer"),
    since: str = typer.Option(config.BACKFILL_SINCE),
    match_ids: str = typer.Option("", help="comma-separated match ids to fetch"),
    league_id: int = typer.Option(0, help="fetch all proMatches of this league + match details"),
    request_parse: bool = typer.Option(False, help="queue replay parse for unparsed fetched matches"),
):
    from .bronze import opendota

    snap = manifest.Snapshot("opendota")
    if bootstrap:
        opendota.ingest_bootstrap(snap)
    if teams:
        for tid in _crosswalk_team_ids():
            opendota.ingest_team(snap, tid)
    if explorer_backfill:
        opendota.explorer_backfill(snap, _roster_account_ids(), since)
    for mid in _csv(match_ids):
        opendota.ingest_match(snap, int(mid))
    if league_id:
        rows = opendota.fetch_json(
            snap, f"league_matches/{league_id}.json", f"/leagues/{league_id}/matches"
        )
        for row in rows:
            opendota.ingest_match(snap, row["match_id"])
    if request_parse:
        n = 0
        for path in manifest.all_files("opendota", "matches/*.json"):
            m = json.loads(path.read_text(encoding="utf-8"))
            if m.get("version") is None:
                opendota.request_parse(m["match_id"])
                n += 1
        typer.echo(f"queued parse for {n} matches")
    typer.echo(f"opendota -> {snap.dir}")


@ingest_app.command("players")
def ingest_players():
    """Player bios (Liquipedia wikitext, 2 batched requests) + Steam avatars."""
    import csv

    from .bronze import players as players_mod

    snap = manifest.Snapshot("players")
    with open(config.SEEDS_DIR / "player_roster.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    pages = [r["liquipedia_page"] for r in rows if r.get("liquipedia_page")]
    accounts = {int(r["account_id"]) for r in rows if r.get("account_id")}
    n_b = players_mod.ingest_bios(snap, pages)
    n_a = players_mod.ingest_avatars(snap, accounts)
    n_p = players_mod.ingest_profiles(snap, sorted(accounts))
    typer.echo(f"profiles: {n_p}/{len(accounts)}")
    with open(config.SEEDS_DIR / "team_crosswalk.csv", encoding="utf-8-sig") as f:
        teams = [
            (t["team_key"],
             [int(t["opendota_team_id"])] +
             [int(a) for a in (t.get("opendota_alt_ids") or "").split("|") if a.strip()])
            for t in csv.DictReader(f) if t.get("opendota_team_id")
        ]
    n_l = players_mod.ingest_logos(snap, teams)
    typer.echo(f"players -> {snap.dir} ({n_b} bio batch(es), {n_a} avatars, {n_l} logos)")


@ingest_app.command("wayback")
def ingest_wayback(stamps: str = typer.Option("", help="YYYYMMDD list; default = monthly 2026")):
    """Archived Portal:Rankings snapshots — time-correct rating priors for backtests."""
    from .bronze import wayback

    snap = manifest.Snapshot("wayback")
    got = wayback.ingest(snap, _csv(stamps) or None)
    typer.echo(f"wayback -> {snap.dir} ({len(got)} snapshots)")


@ingest_app.command("datdota")
def ingest_datdota(endpoints: str = typer.Option("tormentors")):
    from .bronze import datdota

    snap = manifest.Snapshot("datdota")
    datdota.ingest(snap, _csv(endpoints))
    typer.echo(f"datdota -> {snap.dir}")


@ingest_app.command("stratz")
def ingest_stratz(
    match_ids: str = typer.Option("", help="comma-separated match ids (full match pull)"),
    series_backfill: bool = typer.Option(False, help="seriesId + best-of for every pred.games match"),
):
    from .bronze import stratz

    snap = manifest.Snapshot("stratz")
    if match_ids:
        stratz.ingest_matches(snap, [int(m) for m in _csv(match_ids)])
    if series_backfill:
        import duckdb

        con = duckdb.connect(str(config.SILVER_DB), read_only=True)
        mids = [int(m) for (m,) in con.execute(
            "SELECT match_id FROM pred.games WHERE match_id IS NOT NULL").fetchall()]
        con.close()
        n = stratz.ingest_series(snap, mids)
        typer.echo(f"  series backfill: {len(mids)} matches in {n} new request(s)")
    typer.echo(f"stratz -> {snap.dir}")


@ingest_app.command("polymarket")
def ingest_polymarket():
    """Open TI/Dota prediction markets (keyless public API; intraday snapshots)."""
    from .bronze import polymarket

    snap = manifest.Snapshot("polymarket", intraday=True)
    n = polymarket.ingest(snap)
    typer.echo(f"polymarket -> {snap.dir} ({n} open events)")


@ingest_app.command("steamnews")
def ingest_steamnews():
    """Official Dota 2 news feed (patches, TI announcements)."""
    from .bronze import steamnews

    snap = manifest.Snapshot("steamnews")
    n = steamnews.ingest(snap)
    typer.echo(f"steamnews -> {snap.dir} ({n} items)")


@ingest_app.command("odds")
def ingest_odds(egamersworld: bool = typer.Option(True)):
    from .bronze import odds

    snap = manifest.Snapshot("odds", intraday=True)
    if egamersworld:
        odds.ingest_egamersworld(snap)
    typer.echo(f"odds -> {snap.dir} (manual entries go in seeds/manual_odds.csv)")


@app.command("build-silver")
def build_silver(domain: str = typer.Option("all", help="all|predictions|fantasy")):
    from .silver import build

    build.run(domain)


@app.command("build-gold")
def build_gold():
    """Rebuild the gold decision marts (ratings, matchup matrix; sims to come)."""
    from .gold import build as gold_build

    gold_build.run()


@app.command()
def sim(
    n_draws: int = typer.Option(50000),
    paths: int = typer.Option(5000, help="path-detailed draws for fantasy"),
    seed: int = typer.Option(42),
    stage: str = typer.Option("pre_groups", help="pre_groups|post_groups"),
    entrants: str = typer.Option("", help="post_groups only: 8 team keys, seed order 1..8 "
                                          "(default: read the real bracket fill)"),
    pairing: str = typer.Option("random", help="swiss round-1 variant: random|seeded_pots "
                                               "(run both = sensitivity check)"),
):
    """Re-run tournament simulations against the current gold model."""
    from .gold import build as gold_build
    from .gold import sim as gold_sim

    con = gold_build.connect()
    try:
        gold_build.ensure_schema(con)
        run_id = gold_sim.run(con, n_draws=n_draws, n_paths=paths,
                              rng_seed=seed, stage_state=stage,
                              entrants=_csv(entrants) or None, r1_variant=pairing)
        typer.echo(f"sim run_id: {run_id}")
        if stage != "post_groups":
            for row in con.execute(
                "SELECT team_key, p_4_0, p_4_1, p_direct_advance, p_playoffs, p_bottom3"
                " FROM gold.team_outcome_marginals"
            ).fetchall():
                typer.echo(f"  {row[0]:12s} 4-0 {row[1]:.3f}  4-1 {row[2]:.3f}  "
                           f"direct {row[3]:.3f}  playoffs {row[4]:.3f}  bottom3 {row[5]:.3f}")
    finally:
        con.close()


@pick_app.command("group")
def pick_group(
    run_id: str = typer.Option("", help="sim run to evaluate against (default: latest)"),
    restarts: int = typer.Option(50),
):
    """Optimize the 16-team bucket allocation (Group Stage Predictions)."""
    from .gold import build as gold_build
    from .gold import slates

    con = gold_build.connect()
    try:
        res = slates.optimize_group(con, run_id or None, restarts=restarts)
        typer.echo(f"run {res['run_id']}   EV {res['ev']:.0f} pts "
                   f"(naive {res['naive_ev']:.0f})   E[#correct] {res['e_correct']:.2f}   "
                   f"P(>=13 correct) {res['p13plus']:.3f}")
        by_bucket: dict[str, list[str]] = {}
        for team, bucket in res["picks"].items():
            by_bucket.setdefault(bucket, []).append(team)
        for bucket_id, cap, _rule, label in res["buckets"]:
            members = sorted(by_bucket.get(bucket_id, []),
                             key=lambda t: -res["pick_probs"][t])
            names = ", ".join(f"{t} ({res['pick_probs'][t]:.2f})" for t in members)
            typer.echo(f"  {label} [{cap}]: {names}")
        typer.echo(f"exported: {gold_build.export_decision('group', res['run_id'], res)}")
    finally:
        con.close()


@pick_app.command("bracket")
def pick_bracket(
    run_id: str = typer.Option("", help="fixed-entrant sim run (post_groups)"),
    top: int = typer.Option(5),
):
    """Exhaustively optimize the 14-series bracket fill (needs a post-groups run)."""
    from .gold import build as gold_build
    from .gold import slates

    con = gold_build.connect()
    try:
        res = slates.optimize_bracket(con, run_id or None, top_n=top)
        typer.echo(f"run {res['run_id']}   chalk EV {res['chalk_ev']:.0f}")
        for i, fill in enumerate(res["fills"], 1):
            typer.echo(f"  #{i} EV {fill['ev']:.0f}  champion {fill['champion']}")
        best = res["fills"][0]["picks"]
        for slot in sorted(best):
            typer.echo(f"    {slot}: {best[slot]}")
        typer.echo(f"exported: {gold_build.export_decision('bracket', res['run_id'], res)}")
    finally:
        con.close()


@pick_app.command("fantasy")
def pick_fantasy(
    run_id: str = typer.Option("", help="sim run with fantasy paths (default: latest)"),
    top: int = typer.Option(10),
):
    """Rank fantasy team-triples (repeats allowed; alive teams only) by the run's
    primary-period EV — p1 on chained pre-groups runs, p2 on post-groups runs."""
    from .gold import build as gold_build
    from .gold import fantasy

    con = gold_build.connect()
    try:
        res = fantasy.optimize_rosters(con, run_id or None, top=top)
        pr = res["primary"]
        typer.echo(f"run {res['run_id']}  ({res['n_draws']} paths, periods {res['periods']})")
        for i, r in enumerate(res["top"], 1):
            extra = "".join(f"  {k[3:]} EV {v:.0f}" for k, v in r.items()
                            if k.startswith("ev_") and k != f"ev_{pr}")
            typer.echo(f"  #{i:2d} EV({pr}) {r[f'ev_{pr}']:.0f}  sd {r[f'sd_{pr}']:.0f}  "
                       f"q90 {r[f'q90_{pr}']:.0f}{extra}")
            if r.get("best_title"):
                bt_ = r["best_title"]
                typer.echo(f"       titles   {bt_['prefix']} + {bt_['suffix']}"
                           f"  -> titled EV {bt_['ev_titled']:.0f}")
            for kind in ("support", "core", "mid"):
                c = r["cards"][kind]
                flag = "" if c["joint"] else "  [!] thin duo sample — independent approx"
                typer.echo(f"       {kind:8s} {c['team']:12s} stats: {', '.join(c['stats'])}"
                           f"  ({c['n_games']} bank games){flag}")
        lean = {k: v for k, v in res.items() if k != "table"}  # keep decision files small
        typer.echo(f"exported: {gold_build.export_decision('fantasy', res['run_id'], lean)}")
    finally:
        con.close()


@app.command()
def dashboard(out: str = typer.Option("", help="output path (default data/gold/dashboard.html)")):
    """Render the gold layer into a self-contained TI-styled HTML dashboard."""
    from pathlib import Path

    from .gold import dashboard as dash

    path = dash.run(Path(out) if out else None)
    typer.echo(f"dashboard -> {path}")


@app.command()
def backtest(tune: bool = typer.Option(False, help="grid-search halflife/lam_ti/alpha")):
    """Walk-forward backtest of the matchup model vs prior-only and coin baselines."""
    from .gold import build as gold_build
    from .gold import ratings

    con = gold_build.connect()
    try:
        gold_build.ensure_schema(con)
        if tune:
            rows = ratings.tune(con)
            typer.echo(f"{'halflife':>8} {'lam_ti':>6} {'alpha':>5} {'logloss':>8} {'brier':>7} {'n':>5}")
            for hl, lt, al, ll, br, n in rows:
                typer.echo(f"{hl:8.0f} {lt:6.1f} {al:5.2f} {ll:8.4f} {br:7.4f} {n:5d}")
        else:
            metrics = ratings.backtest(con)
            typer.echo(f"{'variant':<12} {'logloss':>8} {'brier':>7} {'n':>5}   (walk-forward Feb-Jul, series level)")
            for variant, (ll, br, n) in sorted(metrics.items(), key=lambda kv: kv[1][0]):
                typer.echo(f"{variant:<12} {ll:8.4f} {br:7.4f} {n:5d}")
    finally:
        con.close()


@app.command()
def status():
    for source in ("liquipedia", "opendota", "datdota", "stratz", "odds"):
        ids = manifest.snapshot_ids(source)
        if not ids:
            typer.echo(f"{source:11s} —")
            continue
        latest_dir = config.BRONZE_DIR / source / ids[-1]
        n_files = sum(1 for p in latest_dir.rglob("*") if p.is_file())
        typer.echo(f"{source:11s} {len(ids)} snapshot(s), latest {ids[-1]} ({n_files} files)")
    if config.SILVER_DB.exists():
        import duckdb

        con = duckdb.connect(str(config.SILVER_DB), read_only=True)
        built = con.execute("SELECT built_at FROM meta.build_info ORDER BY built_at DESC LIMIT 1").fetchone()
        typer.echo(f"silver      built {built[0] if built else '?'} ({config.SILVER_DB})")
        for tbl in ("pred.series", "pred.games", "fan.player_game_stats"):
            n = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
            typer.echo(f"  {tbl:24s} {n:7d} rows")
        con.close()
    else:
        typer.echo("silver      — (run `ti build-silver`)")


@app.command()
def verify():
    from . import verify as verify_mod

    ok = verify_mod.run()
    raise typer.Exit(0 if ok else 1)


@app.command()
def live():
    """One-shot daily live sequence: ingest -> build-silver -> build-gold -> verify."""
    from .bronze import datdota as datdota_mod
    from .bronze import liquipedia as liquipedia_mod
    from .bronze import opendota as opendota_mod
    from .silver import build

    lp_snap = manifest.Snapshot("liquipedia", intraday=True)
    liquipedia_mod.ingest(["group", "bracket"], snap=lp_snap)

    od_snap = manifest.Snapshot("opendota")
    opendota_mod.ingest_bootstrap(od_snap, pro_match_pages=2)
    for tid in _crosswalk_team_ids():
        opendota_mod.ingest_team(od_snap, tid)
    # roster stats + drafts for new TI games (explorer lags replays 20-60 min)
    opendota_mod.explorer_backfill(od_snap, _roster_account_ids(), config.BACKFILL_SINCE)

    # enrichment: markets drift intraday; news is cheap; STRATZ series uses the
    # PREVIOUS silver build's match list (new matches catch up next cycle)
    from .bronze import polymarket as polymarket_mod
    from .bronze import steamnews as steamnews_mod

    polymarket_mod.ingest(manifest.Snapshot("polymarket", intraday=True))
    steamnews_mod.ingest(manifest.Snapshot("steamnews"))
    try:
        import duckdb

        from .bronze import stratz as stratz_mod

        con = duckdb.connect(str(config.SILVER_DB), read_only=True)
        mids = [int(m) for (m,) in con.execute(
            "SELECT match_id FROM pred.games WHERE match_id IS NOT NULL").fetchall()]
        con.close()
        stratz_mod.ingest_series(manifest.Snapshot("stratz"), mids)
    except Exception as exc:  # noqa: BLE001 — droppable enrichment
        typer.echo(f"[warn] stratz series skipped: {exc}")

    dd_snap = manifest.Snapshot("datdota")
    datdota_mod.ingest(dd_snap, ["tormentors"])

    build.run("all")           # deletes the DB file -> gold must rebuild after
    from .gold import build as gold_build

    gold_build.run()

    # post-groups: once the real bracket is seeded on Liquipedia, every live run
    # re-creates the fixed-entrant sim + slates (build-gold drops the gold schema,
    # so bracket/fantasy-P2 picks would otherwise vanish each refresh)
    from .gold import sim as gold_sim

    con = gold_build.connect()
    try:
        qf_filled = con.execute(
            """SELECT count(*) FROM pred.bracket_slots
               WHERE slot_id IN ('R1M1','R1M2','R1M3','R1M4')
                 AND team1_key IS NOT NULL AND team2_key IS NOT NULL"""
        ).fetchone()[0]
        if qf_filled == 4:
            from .gold import fantasy as fantasy_mod
            from .gold import slates as slates_mod

            run_id = gold_sim.run(con, n_paths=20000, stage_state="post_groups")
            typer.echo(f"post-groups sim: {run_id}")
            bres = slates_mod.optimize_bracket(con, run_id)
            typer.echo(f"  bracket slate EV {bres['fills'][0]['ev']:.0f} "
                       f"(chalk {bres['chalk_ev']:.0f})  "
                       f"champion {bres['fills'][0]['champion']}")
            gold_build.export_decision("bracket", run_id, bres)
            fres = fantasy_mod.optimize_rosters(con, run_id)
            pr = fres["primary"]
            top_r = fres["top"][0]
            typer.echo(f"  fantasy {pr} top roster: {top_r['support_team']} / "
                       f"{top_r['core_team']} / {top_r['mid_team']}  "
                       f"EV {top_r[f'ev_{pr}']:.0f}")
            gold_build.export_decision(
                "fantasy", run_id, {k: v for k, v in fres.items() if k != "table"})
        else:
            typer.echo("bracket not seeded yet ({}/4 QF slots) — post-groups sim skipped"
                       .format(qf_filled))
    finally:
        con.close()

    from . import verify as verify_mod

    verify_mod.run()


if __name__ == "__main__":
    app()
