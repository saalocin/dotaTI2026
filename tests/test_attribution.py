"""Roster-majority team attribution: >=3 of a team's seeded 5 on a side beats ids
(ids drift on re-registration and persist across roster swaps)."""

import duckdb

from ti2026.silver import predictions

ID2K = {2163: ("liquid", "team_id"), 7554697: ("nigma", "team_id_alt")}


def test_majority_wins_over_conflicting_id():
    # 3 nigma roster players on a side registered under Liquid's id -> nigma
    assert predictions.attribute_side({"nigma": 3, "liquid": 1}, 2163, ID2K) == (
        "nigma", "roster_majority",
    )


def test_two_players_are_not_a_majority():
    # 2 roster players (stand-in situation) is ambiguous -> fall back to the id
    assert predictions.attribute_side({"huligani": 2}, 2163, ID2K) == ("liquid", "team_id")


def test_alt_id_fallback_is_labelled():
    assert predictions.attribute_side({}, 7554697, ID2K) == ("nigma", "team_id_alt")


def test_no_information():
    assert predictions.attribute_side(None, 999, ID2K) == (None, None)
    assert predictions.attribute_side({}, None, ID2K) == (None, None)


def test_id2key_ext_primary_beats_alt():
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA pred")
    con.execute(
        "CREATE TABLE pred.teams (team_key VARCHAR, opendota_team_id BIGINT, opendota_alt_ids BIGINT[])"
    )
    # team b's primary id 3 collides with team a's alt id 3 -> primary must win
    con.execute("INSERT INTO pred.teams VALUES ('a', 1, [2, 3]), ('b', 3, NULL)")
    m = predictions.id2key_ext(con)
    con.close()
    assert m[1] == ("a", "team_id")
    assert m[2] == ("a", "team_id_alt")
    assert m[3] == ("b", "team_id")
