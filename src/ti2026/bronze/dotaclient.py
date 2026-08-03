"""Bronze: local Dota 2 client VPK — Valve's per-hero fantasy tags ("Adjectives").

The TI coach-title prefixes key on client-only hero tags (colors red/blue/green/
purple/yellow/brown + aquatic/fiery/icy/undead/demon/spirit/caped/masked). Valve
ships them per hero in scripts/npc/npc_heroes.txt inside game/dota/pak01_dir.vpk —
no public API carries them. This ingester copies the raw file into bronze (system
of record) and regenerates seeds/hero_tags.csv with normalized tag tokens.

Re-run after client updates: Valve can re-tag heroes or add tags mid-event
(`ti ingest dotaclient`), then rebuild silver+gold.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .. import config
from ..manifest import Snapshot

INNER = "scripts/npc/npc_heroes.txt"
VPK_CANDIDATES = ("game/dota/pak01_dir.vpk", "game/core/pak01_dir.vpk")
STEAM_VDFS = (
    r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf",
    r"C:\Program Files\Steam\steamapps\libraryfolders.vdf",
)
# Pure count/anatomy fields with no title semantics; everything else is kept so a
# future prefix (e.g. a Bearded one existed at TI2024) needs no re-extract.
SKIP_TAGS = {"legs", "nose"}
# The client is inconsistent: most heroes carry "Mask", Templar Assassin "Masked";
# "Cape" -> the dialog says "Caped or Masked Hero".
RENAME = {"cape": "caped", "mask": "masked"}

_KV = re.compile(r'^\s*"([^"]+)"\s+"([^"]*)"')
_SECTION = re.compile(r'^\s*"([^"]+)"\s*$')


def find_install(explicit: str | None = None) -> Path | None:
    """Locate 'dota 2 beta' via explicit path or Steam library folders."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    libs: list[str] = []
    for vdf_path in STEAM_VDFS:
        p = Path(vdf_path)
        if not p.exists():
            continue
        libs += re.findall(r'"path"\s+"([^"]+)"', p.read_text(encoding="utf-8", errors="replace"))
    for lib in libs:
        cand = Path(lib.replace("\\\\", "\\")) / "steamapps" / "common" / "dota 2 beta"
        if cand.exists():
            return cand
    return None


def extract_npc_heroes(install: Path) -> tuple[bytes, str]:
    """Read npc_heroes.txt out of the client VPKs (dota pak first — it is the
    one game updates touch)."""
    import vpk  # local import: only this ingester needs it

    for rel in VPK_CANDIDATES:
        p = install / rel
        if not p.exists():
            continue
        pak = vpk.open(str(p))
        try:
            return pak[INNER].read(), rel
        except KeyError:
            continue
    raise FileNotFoundError(f"{INNER} not found in {[str(install / r) for r in VPK_CANDIDATES]}")


def parse_hero_tags(raw: bytes) -> list[tuple[int, str, list[str]]]:
    """Tolerant line-based parse (the live file trips strict VDF tokenizers):
    per hero -> (hero_id, short name, normalized tag list)."""
    heroes: dict[str, dict] = {}
    depth, hero, pending, adj_depth = 0, None, None, None
    for ln in raw.decode("utf-8", errors="replace").splitlines():
        stripped = ln.split("//")[0]
        m_kv = _KV.match(stripped)
        m_sec = _SECTION.match(stripped) if not m_kv else None
        if m_sec:
            pending = m_sec.group(1)
        opens, closes = stripped.count("{"), stripped.count("}")
        if opens:
            depth += opens
            if pending is not None:
                if depth == 2 and pending.startswith("npc_dota_hero_"):
                    hero = pending
                    heroes[hero] = {"Adjectives": {}}
                elif depth == 3 and hero and pending == "Adjectives":
                    adj_depth = depth
                pending = None
        if m_kv and hero:
            k, v = m_kv.groups()
            if adj_depth is not None and depth == adj_depth:
                heroes[hero]["Adjectives"][k] = v
            elif depth == 2 and k == "HeroID":
                heroes[hero][k] = v
        if closes:
            if adj_depth is not None and depth - closes < adj_depth:
                adj_depth = None
            depth -= closes
            if depth < 2:
                hero = None

    heroes.pop("npc_dota_hero_base", None)
    rows = []
    for key, h in heroes.items():
        if "HeroID" not in h:
            continue
        tags = sorted({RENAME.get(a.lower(), a.lower())
                       for a, v in h["Adjectives"].items()
                       if a.lower() not in SKIP_TAGS and str(v) != "0"})
        rows.append((int(h["HeroID"]), key.replace("npc_dota_hero_", ""), tags))
    rows.sort()
    return rows


def ingest_compendium_defs(install: Path, snap: Snapshot) -> None:
    """Bronze every compendium_definition.txt of the two newest league ids —
    these carry the REAL prediction structure (bucket set, lock windows,
    bracket league_node_ids) well before the in-client reveal."""
    import vpk

    pak = vpk.open(str(install / "game/dota/pak01_dir.vpk"))
    paths = [p for p in pak if re.match(r"scripts/compendiums/\d+/", p)]
    ids = sorted({int(re.search(r"/(\d+)/", p).group(1)) for p in paths})
    for lid in ids[-2:]:
        for p in paths:
            if f"/{lid}/" not in p:
                continue
            rel = "compendium_" + p.replace("scripts/compendiums/", "").replace("/", "_")
            snap.write(rel, pak[p].read(),
                       url=f"vpk://game/dota/pak01_dir.vpk!{p}", params={"league_id": lid})
            print(f"dotaclient: bronzed {rel}")


def ingest(install_path: str | None = None) -> int:
    """Bronze the raw file + regenerate seeds/hero_tags.csv. Returns hero count."""
    install = find_install(install_path)
    if install is None:
        raise FileNotFoundError(
            "Dota 2 install not found (checked Steam library folders); "
            "pass --install <path to 'dota 2 beta'>")
    raw, src_vpk = extract_npc_heroes(install)
    snap = Snapshot("dotaclient")
    snap.write("npc_heroes.txt", raw, url=f"vpk://{src_vpk}!{INNER}",
               params={"install": str(install), "vpk": src_vpk})
    ingest_compendium_defs(install, snap)
    rows = parse_hero_tags(raw)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = config.SEEDS_DIR / "hero_tags.csv"
    lines = ["hero_id,hero,tags,source"]
    lines += [f"{hid},{name},{'|'.join(tags)},client-npc_heroes-{stamp}"
              for hid, name, tags in rows]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"dotaclient: {len(rows)} heroes -> {out.name} (bronze {snap.dir.name}, {src_vpk})")
    return len(rows)
