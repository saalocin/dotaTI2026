"""Player infobox parsing: date format variants, history rows, missing fields."""

from ti2026.parse import liquipedia_player as lp

SAMPLE = """
{{Infobox player
|id=TestStar
|name=Иван Иванов
|romanized_name=Ivan Ivanov
|birth_date={{Birth date and age|2001|03|17}}
|country=Russia
|role=Carry
|history={{TH|2021-06-01 — 2022-10-15|[[Old Squad]]}}{{TH|2022-10-16 — '''Present'''|[[New Squad]]}}
}}
Some article text.
"""


def test_full_infobox():
    b = lp.bio_from_wikitext(SAMPLE)
    assert b["real_name"] == "Ivan Ivanov"
    assert b["birth_date"] == "2001-03-17"
    assert b["country"] == "Russia"
    assert b["history"] == [["2021-06-01 — 2022-10-15", "Old Squad"],
                            ["2022-10-16 — Present", "New Squad"]]


def test_plain_date_and_missing_fields():
    b = lp.bio_from_wikitext("{{Infobox player|id=X|birth_date=1999-1-5|country=Peru<br>Chile}}")
    assert b["birth_date"] == "1999-01-05"
    assert b["country"] == "Peru"
    assert b["history"] == [] and b["real_name"] is None


def test_no_infobox():
    b = lp.bio_from_wikitext("just an article")
    assert b == {"real_name": None, "birth_date": None, "country": None, "history": []}


def test_pages_from_query():
    payload = {"query": {"pages": [
        {"title": "A", "revisions": [{"slots": {"main": {"content": "wikitext-a"}}}]},
        {"title": "B", "missing": True},
    ]}}
    assert lp.pages_from_query(payload) == {"A": "wikitext-a"}


def test_redirect_map():
    payload = {"query": {"redirects": [{"from": "Ame", "to": "Ame (Chinese player)"}],
                         "pages": []}}
    assert lp.redirect_map(payload) == {"Ame": "Ame (Chinese player)"}
    assert lp.redirect_map({"query": {}}) == {}
