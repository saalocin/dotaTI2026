"""Liquipedia player-page wikitext -> bio record.

Player infoboxes ({{Infobox player}}) carry birth date, country, and the team
history as {{TH|period|team}} rows. Formats drift page to page, so every field
is parsed tolerantly and missing values stay None/empty.
"""

from __future__ import annotations

import re

import mwparserfromhell

_DATE = re.compile(r"(\d{4})\s*[|\-/]\s*(\d{1,2})\s*[|\-/]\s*(\d{1,2})")


def _clean(wikicode) -> str:
    return mwparserfromhell.parse(str(wikicode)).strip_code().strip()


def bio_from_wikitext(text: str) -> dict:
    """One page's wikitext -> {real_name, birth_date, country, history[]} (best effort)."""
    out = {"real_name": None, "birth_date": None, "country": None, "history": []}
    code = mwparserfromhell.parse(text or "")
    box = None
    for tpl in code.filter_templates():
        if tpl.name.strip().lower().startswith("infobox player"):
            box = tpl
            break
    if box is None:
        return out

    def get(*names) -> str | None:
        for n in names:
            if box.has(n):
                v = _clean(box.get(n).value)
                if v:
                    return v
        return None

    out["real_name"] = get("romanized_name", "name")
    for n in ("country", "nationality"):
        if box.has(n):
            first = re.split(r"<br\s*/?>", str(box.get(n).value), flags=re.I)[0]
            c = _clean(first)
            if c:
                out["country"] = c.splitlines()[0].strip()
            break

    if box.has("birth_date"):
        m = _DATE.search(str(box.get("birth_date").value))
        if m:
            y, mo, d = (int(g) for g in m.groups())
            if 1970 <= y <= 2015:
                out["birth_date"] = f"{y:04d}-{mo:02d}-{d:02d}"

    def th_rows(node) -> list:
        rows = []
        for th in node.filter_templates():
            if th.name.strip().upper() not in ("TH", "TEAM HISTORY LINE"):
                continue
            parts = [_clean(p.value) for p in th.params if not p.showkey or str(p.name).isdigit()]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                rows.append([parts[0], parts[1]])
        return rows

    if box.has("history"):
        out["history"] = th_rows(mwparserfromhell.parse(str(box.get("history").value)))
    if not out["history"]:
        # many pages keep the {{TH}} rows outside the infobox (History section)
        out["history"] = th_rows(code)
    return out


def redirect_map(payload: dict) -> dict[str, str]:
    """{from_title: to_title} for redirects the API followed (redirects=1)."""
    return {r["from"]: r["to"]
            for r in (payload.get("query") or {}).get("redirects", [])
            if r.get("from") and r.get("to")}


def pages_from_query(payload: dict) -> dict[str, str]:
    """action=query&prop=revisions payload -> {title: wikitext} (skips missing pages)."""
    out = {}
    for page in (payload.get("query") or {}).get("pages", []):
        if page.get("missing"):
            continue
        revs = page.get("revisions") or []
        if not revs:
            continue
        content = ((revs[0].get("slots") or {}).get("main") or {}).get("content")
        if content:
            out[page["title"]] = content
    return out
