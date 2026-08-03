"""`ti build-silver` — full rebuild of data/silver/ti2026.duckdb from bronze + seeds."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import manifest
from . import db, fantasy, predictions, seeds_load


def run(domain: str = "all") -> None:
    con = db.fresh_connection()
    try:
        seeds_load.load(con)
        if domain in ("all", "predictions"):
            predictions.build(con)
        if domain in ("all", "fantasy"):
            fantasy.build(con)
        db.create_views(con)
        snapshots = {
            src: (manifest.snapshot_ids(src) or [None])[-1]
            for src in ("liquipedia", "opendota", "datdota", "stratz", "odds")
        }
        con.execute(
            "INSERT INTO meta.build_info VALUES (?, ?)",
            [datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(snapshots)],
        )
        for tbl in (
            "pred.teams", "pred.team_ratings", "pred.series", "pred.games",
            "pred.swiss_standings", "pred.bracket_slots", "pred.odds",
            "fan.players", "fan.player_game_stats",
        ):
            n = con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
            print(f"{tbl:26s} {n:7d} rows")
    finally:
        con.close()
    print("silver rebuilt.")
