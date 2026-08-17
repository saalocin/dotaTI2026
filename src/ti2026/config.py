"""Central config: paths, credentials, rate limits, event constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
SILVER_DB = SILVER_DIR / "ti2026.duckdb"
SEEDS_DIR = ROOT / "seeds"

OPENDOTA_API_KEY = os.getenv("OPENDOTA_API_KEY") or None
STRATZ_TOKEN = os.getenv("STRATZ_TOKEN") or None
CONTACT = os.getenv("CONTACT_EMAIL", "unknown@example.com")

# Liquipedia terms require a UA identifying the tool with contact info.
PIPELINE_UA = f"ti2026-pipeline/0.1 (personal TI2026 analysis; contact: {CONTACT})"
# datdota / odds sites block default HTTP-client UAs; a browser UA is required.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

EVENT_TZ = "Asia/Shanghai"  # all storage is UTC; this is for display/lock math only
GROUP_LOCK_UTC = "2026-08-13T02:00:00Z"  # 10:00 CST Aug 13
BRACKET_LOCK_UTC = "2026-08-20T02:00:00Z"  # first main-event series (compendium lock_date)
BACKFILL_SINCE = "2026-01-01"

# Minimum seconds between requests, per bucket. Conservative vs published limits.
RATE_BUCKETS: dict[str, float] = {
    "opendota": 1.1,  # ~55/min vs 60/min limit
    "liquipedia": 2.0,  # api.php general: 1 req / 2 s
    "liquipedia_parse": 30.0,  # action=parse: 1 req / 30 s
    "datdota": 2.0,  # courtesy — no published limit
    "stratz": 1.0,
    "odds": 5.0,  # polite scraping
    "wayback": 3.0,  # archive.org courtesy
    "steam": 0.4,  # avatar CDN + ISteamNews
    "polymarket": 1.0,  # public gamma API, keyless
}
