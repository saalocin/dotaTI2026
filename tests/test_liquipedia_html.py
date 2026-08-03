"""Liquipedia HTML parsers against the completed TI2025 pages (known results)."""

from ti2026.parse import liquipedia_html


def test_bracket_series_full_bracket(ti2025_main_html):
    series = liquipedia_html.bracket_series(ti2025_main_html)
    assert len(series) == 14
    assert all(s.has_teams for s in series)  # completed bracket: no TBD nodes
    assert all(s.winner_raw in (s.team1_raw, s.team2_raw) for s in series)
    assert all(len(s.game_match_ids) >= 2 for s in series)  # every series has game links


def test_bracket_slot_assignment(ti2025_main_html):
    series = liquipedia_html.bracket_series(ti2025_main_html)
    slots = liquipedia_html.assign_bracket_slots(series)
    assert len(slots) == 14
    gf = slots["R5M1"]
    assert {gf.team1_raw, gf.team2_raw} == {"Team Falcons", "Xtreme Gaming"}
    assert (gf.score1, gf.score2) in ((3, 2), (2, 3))
    assert gf.winner_raw == "Team Falcons"  # TI2025 champion
    # known TI2025 structure: XG-Tundra was a UB QF, PARIVISION-Falcons the UB Final
    assert {slots["R1M1"].team1_raw, slots["R1M1"].team2_raw} == {"Xtreme Gaming", "Tundra Esports"}
    assert {slots["R4M1"].team1_raw, slots["R4M1"].team2_raw} == {"PARIVISION", "Team Falcons"}
    assert slots["R4M1"].round_label == "UB Final"
    # winner-feed consistency: every winner appears in its feeds_winner slot
    topo = {
        "R1M1": "R2M1", "R1M2": "R2M1", "R1M3": "R2M2", "R1M4": "R2M2",
        "R1M5": "R2M3", "R1M6": "R2M4", "R2M1": "R4M1", "R2M2": "R4M1",
        "R2M3": "R3M1", "R2M4": "R3M1", "R3M1": "R4M2", "R4M1": "R5M1", "R4M2": "R5M1",
    }
    for src, dst in topo.items():
        w = slots[src].winner_raw
        assert w in (slots[dst].team1_raw, slots[dst].team2_raw), f"{src} winner {w} not in {dst}"


def test_swiss_standings(ti2025_group_html):
    rows = liquipedia_html.swiss_standings(ti2025_group_html)
    assert len(rows) == 16
    top = rows[0]
    assert top["raw_name"] == "Xtreme Gaming"
    assert (top["wins"], top["losses"]) == (4, 0)
    assert {r["rank"] for r in rows} <= set(range(1, 17))


def test_matchlist_series(ti2025_group_html):
    series = liquipedia_html.matchlist_series(ti2025_group_html)
    assert len(series) == 39
    first = series[0]
    assert {first.team1_raw, first.team2_raw} == {"Aurora Gaming", "Xtreme Gaming"}
    assert first.winner_raw == "Xtreme Gaming"
    assert len(first.game_match_ids) == 2
