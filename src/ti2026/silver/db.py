"""DuckDB connection + full-rebuild plumbing. Silver is disposable: bronze is truth."""

from __future__ import annotations

from pathlib import Path

import duckdb

from .. import config

SQL_DIR = Path(__file__).parent


def fresh_connection() -> duckdb.DuckDBPyConnection:
    config.SILVER_DIR.mkdir(parents=True, exist_ok=True)
    if config.SILVER_DB.exists():
        config.SILVER_DB.unlink()
    con = duckdb.connect(str(config.SILVER_DB))
    con.execute((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    return con


def create_views(con: duckdb.DuckDBPyConnection) -> None:
    con.execute((SQL_DIR / "views.sql").read_text(encoding="utf-8"))
