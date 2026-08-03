"""Bronze: player bios + avatars.

- Liquipedia player-page WIKITEXT via action=query&prop=revisions, batched 50
  titles per request — this is the general 1 req/2s bucket, NOT the 30s parse
  bucket, so all 80 roster pages cost two requests. CC-BY-SA 3.0 attribution.
- Steam avatars (avatarmedium, 64px) from the URLs in OpenDota /proPlayers —
  small enough to embed in the dashboard as data URIs (the artifact CSP blocks
  remote images).
"""

from __future__ import annotations

import json

from .. import http, manifest
from ..manifest import Snapshot

API = "https://liquipedia.net/dota2/api.php"
BATCH = 50


def _latest_proplayers() -> list[dict]:
    files = manifest.all_files("opendota", "proPlayers.json")
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else []


def ingest_bios(snap: Snapshot, pages: list[str]) -> int:
    got = 0
    for i in range(0, len(pages), BATCH):
        chunk = pages[i:i + BATCH]
        rel = f"bios/pages_{i // BATCH}_r.json"   # _r = fetched with redirect following
        if snap.has(rel):
            got += 1
            continue
        params = {
            "action": "query", "prop": "revisions", "rvprop": "content",
            "rvslots": "main", "format": "json", "formatversion": "2",
            "redirects": "1", "titles": "|".join(chunk),
        }
        resp = http.get(API, bucket="liquipedia", params=params)
        snap.write(rel, resp.content, url=str(resp.request.url), params=params)
        got += 1
    return got


def ingest_profiles(snap: Snapshot, account_ids: list[int]) -> int:
    """OpenDota /players/{id}: pub-matchmaking rank (rank_tier + Immortal
    leaderboard position) and profile metadata. Keyless; one call per player."""
    from . import opendota as od

    n = 0
    for acc in account_ids:
        rel = f"profiles/{acc}.json"
        if snap.has(rel):
            n += 1
            continue
        try:
            od.fetch_json(snap, rel, f"/players/{acc}")
            n += 1
        except RuntimeError as e:
            print(f"WARN opendota /players/{acc}: {e}")
    return n


def profile_rows() -> dict[int, dict]:
    """Latest profile payload per account across snapshots."""
    out: dict[int, dict] = {}
    for path in manifest.all_files("players", "profiles/*.json"):
        try:
            out[int(path.stem)] = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def ingest_logos(snap: Snapshot, teams: list[tuple[str, list[int]]]) -> int:
    """Team emblems: logo_url from OpenDota team metadata (bronze first, live
    /teams/{id} fallback across alt ids), image stored as logos/<team_key>.img."""
    from . import opendota as od

    n = 0
    for key, ids in teams:
        rel = f"logos/{key}.img"
        if snap.has(rel):
            n += 1
            continue
        url = None
        for tid in ids:
            for path in manifest.all_files("opendota", f"teams/{tid}.json"):
                try:
                    url = (json.loads(path.read_text(encoding="utf-8")) or {}).get("logo_url")
                except Exception:  # noqa: BLE001 — a poisoned/empty meta file
                    url = None
                if url:
                    break
            if url:
                break
        if not url:
            for tid in ids:
                try:
                    meta = od.fetch_json(snap, f"team_meta/{tid}.json", f"/teams/{tid}")
                except Exception:  # noqa: BLE001
                    continue
                url = (meta or {}).get("logo_url")
                if url:
                    break
        if not url:
            print(f"  [warn] no logo_url found for {key}")
            continue
        try:
            resp = http.get(url, bucket="steam")
        except Exception as exc:  # noqa: BLE001
            print(f"  [warn] logo {key}: {exc}")
            continue
        snap.write(rel, resp.content, url=url)
        n += 1
    return n


def ingest_avatars(snap: Snapshot, account_ids: set[int]) -> int:
    by_acc = {p.get("account_id"): p for p in _latest_proplayers()}
    n = 0
    for acc in sorted(account_ids):
        rel = f"avatars/{acc}.jpg"
        if snap.has(rel):
            n += 1
            continue
        url = (by_acc.get(acc) or {}).get("avatarmedium") or (by_acc.get(acc) or {}).get("avatar")
        if not url:
            continue
        try:
            resp = http.get(url, bucket="steam")
        except Exception as exc:  # noqa: BLE001 — avatars are decorative
            print(f"  [warn] avatar {acc}: {exc}")
            continue
        snap.write(rel, resp.content, url=url)
        n += 1
    return n
