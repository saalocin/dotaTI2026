"""`ti build-gold` — drop + recreate the gold schema from silver.

Gold is derived and disposable (same contract silver has with bronze). It lives in
the same DuckDB file, so `ti build-silver` (which deletes the file) implicitly
deletes gold too — always rebuild gold after silver.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .. import config
from . import fantasy, ratings, sim

SQL_DIR = Path(__file__).parent


def export_decision(track: str, run_id: str, payload: dict) -> Path:
    """Every submitted pick should trace to a file + run_id, not a terminal scrollback."""
    out_dir = config.DATA_DIR / "gold" / "decisions"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    p = out_dir / f"{track}-{run_id}-{stamp}.json"
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p


def connect():
    if not config.SILVER_DB.exists():
        raise SystemExit("silver DB missing — run `ti build-silver` first")
    return duckdb.connect(str(config.SILVER_DB))


def reset_schema(con) -> None:
    con.execute("DROP SCHEMA IF EXISTS gold CASCADE")
    con.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))


def ensure_schema(con) -> None:
    try:
        con.execute("SELECT 1 FROM gold.backtest_preds LIMIT 1")
    except duckdb.CatalogException:
        reset_schema(con)


def run() -> None:
    con = connect()
    try:
        reset_schema(con)
        info = ratings.build(con)
        print(
            f"gold.team_strength      16 teams  (fit: {info['maps_fit']} maps, "
            f"{info['ti_vs_ti_maps']} TI-vs-TI, {info['entities']} entities, rho={info['rho']:.2f})"
        )
        n = con.execute("SELECT count(*) FROM gold.matchup_probs").fetchone()[0]
        print(f"gold.matchup_probs     {n:4d} rows")
        run_id = sim.run(con)
        for tbl in ("sim_group_draws", "sim_bracket_draws", "sim_fantasy_paths"):
            n = con.execute(f"SELECT count(*) FROM gold.{tbl}").fetchone()[0]
            print(f"gold.{tbl:<18s} {n:8d} rows")
        print(f"sim run_id: {run_id}")
        n = fantasy.build_profiles(con)
        print(f"gold.player_stat_profile {n:6d} rows")
    finally:
        con.close()
    print("gold rebuilt.")
