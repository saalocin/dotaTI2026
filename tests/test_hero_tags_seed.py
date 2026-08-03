"""seeds/hero_tags.csv sanity: the title prefixes must find their tag tokens.

The seed is generated from the client's npc_heroes.txt (ti ingest dotaclient);
these checks catch a bad regeneration (vocabulary drift, missing normalization)
before it silently neutralizes prefixes in the model.
"""

import csv
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "seeds" / "hero_tags.csv"
PREFIX_TAGS = {  # every tag referenced by seeds/coach_titles.csv cond_params
    "red", "blue", "green", "purple", "yellow", "brown",
    "aquatic", "fiery", "icy", "undead", "demon", "spirit", "caped", "masked",
}
KNOWN_EXTRAS = {  # rest of Valve's Adjectives vocabulary (kept for future prefixes)
    "arachnophobic", "badteeth", "bearded", "cute", "female", "flying", "fuzzy",
    "horns", "nicepecs", "parent", "potbelly", "steed", "wings",
}


def _rows():
    with SEED.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_seed_is_populated():
    rows = _rows()
    assert len(rows) >= 120          # full hero roster
    assert all(r["hero_id"].isdigit() for r in rows)


def test_tags_are_normalized_and_known():
    counts: dict[str, int] = {}
    for r in _rows():
        for t in filter(None, r["tags"].split("|")):
            counts[t] = counts.get(t, 0) + 1
    unknown = set(counts) - PREFIX_TAGS - KNOWN_EXTRAS
    assert not unknown, f"unnormalized/unknown tags: {sorted(unknown)}"
    assert "mask" not in counts and "cape" not in counts   # normalization applied


def test_every_prefix_condition_matches_heroes():
    counts: dict[str, int] = {}
    for r in _rows():
        for t in filter(None, r["tags"].split("|")):
            counts[t] = counts.get(t, 0) + 1
    for tag in PREFIX_TAGS:
        assert counts.get(tag, 0) >= 5, f"prefix tag {tag!r} matches too few heroes"
