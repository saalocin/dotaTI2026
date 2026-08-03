"""Bronze snapshot layout + manifest.

data/bronze/<source>/<snapshot_id>/<relpath>, snapshot_id = UTC date (or minute during
the live event). Files are immutable: write() skips paths that already exist, which
doubles as the on-disk response cache for idempotent re-runs. Every write appends a
line to the snapshot's manifest.jsonl.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Snapshot:
    def __init__(self, source: str, snapshot_id: str | None = None, intraday: bool = False):
        if snapshot_id is None:
            fmt = "%Y-%m-%dT%H%M" if intraday else "%Y-%m-%d"
            snapshot_id = utcnow().strftime(fmt)
        self.source = source
        self.id = snapshot_id
        self.dir = config.BRONZE_DIR / source / snapshot_id

    def path(self, relpath: str) -> Path:
        return self.dir / relpath

    def has(self, relpath: str) -> bool:
        return self.path(relpath).exists()

    def read_json(self, relpath: str):
        return json.loads(self.path(relpath).read_text(encoding="utf-8"))

    def write(
        self,
        relpath: str,
        content: bytes,
        *,
        url: str = "",
        params: dict | None = None,
        status: int = 200,
    ) -> Path:
        p = self.path(relpath)
        if p.exists():
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        rec = {
            "relpath": relpath.replace("\\", "/"),
            "url": url,
            "params": params or {},
            "fetched_at": utcnow().isoformat(),
            "http_status": status,
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
        with open(self.dir / "manifest.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return p


def snapshot_ids(source: str) -> list[str]:
    src_dir = config.BRONZE_DIR / source
    if not src_dir.exists():
        return []
    return sorted(d.name for d in src_dir.iterdir() if d.is_dir())


def latest(source: str) -> Snapshot | None:
    ids = snapshot_ids(source)
    return Snapshot(source, ids[-1]) if ids else None


def all_files(source: str, pattern: str) -> list[Path]:
    """Glob across ALL snapshots of a source (for immutable payloads like matches).

    Sorted by (snapshot_id, name) so later snapshots win when deduping by key.
    """
    src_dir = config.BRONZE_DIR / source
    if not src_dir.exists():
        return []
    return sorted(src_dir.glob(f"*/{pattern}"))
