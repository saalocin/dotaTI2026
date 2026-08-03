"""Pinned-match regression: all 18 stat mappings against a real parsed pro match.

Match 8868891396 — TI2026 EU Regional Qualifier, player ssnovv1 (320017600).
Pinned values were cross-checked against the OpenDota explorer payload for the
same match; both ingestion paths must agree.
"""

from ti2026.parse import opendota_match

ACCOUNT = 320017600


def _rec(match_json):
    recs = opendota_match.from_match_json(match_json)
    return next(r for r in recs if r["account_id"] == ACCOUNT)


def test_all_stat_mappings(match_json):
    r = _rec(match_json)
    assert r["match_id"] == 8868891396
    assert r["parsed"] is True
    assert r["kills"] == 13
    assert r["deaths"] == 3
    assert r["last_hits"] == 830
    assert r["denies"] == 32
    assert r["gpm"] == 827
    assert r["madstone_proxy"] == 30
    assert r["tower_kills"] == 5
    assert r["obs_placed"] == 1
    assert r["camps_stacked"] == 3
    assert r["rune_pickups"] == 8
    assert r["watchers_taken"] == 3
    assert r["smokes_used"] == 1
    assert r["lotuses_grabbed"] is None  # no source anywhere, by design
    assert r["lotus_proxy_famango"] == 1
    assert r["roshan_kills"] == 2
    assert abs(r["teamfight_participation"] - 0.777778) < 1e-4
    assert r["stuns_s"] == 0
    assert r["tormentor_kills"] == 2
    assert r["first_blood"] is True
    assert r["courier_kills"] == 0


def test_all_players_extracted(match_json):
    recs = opendota_match.from_match_json(match_json)
    assert len(recs) == 10
    assert len({r["account_id"] for r in recs}) == 10
    assert sum(1 for r in recs if r["is_radiant"]) == 5


def test_drafts_extracted(match_json):
    drafts = opendota_match.drafts_from_match_json(match_json)
    assert len(drafts) == 24  # captains mode: 14 bans + 10 picks
    assert sum(1 for d in drafts if d["is_pick"]) == 10
    assert {d["team"] for d in drafts} <= {0, 1}
    assert len({d["hero_id"] for d in drafts}) == 24  # no hero twice in a draft
