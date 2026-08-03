"""Portal:Rankings (official Liquipedia Glicko-2) -> rating rows.

Table: table.ranking-table with columns Rank | +/- | Team | Points | Region.
Expansion sub-rows ("Rank Points" 5-week charts) are skipped. RD is not displayed
on the portal — rating_deviation stays NULL unless a future source provides it.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup


def ratings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.ranking-table")
    if table is None:
        return []
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        rank_txt = cells[0].get_text(strip=True)
        if not re.fullmatch(r"\d+", rank_txt):
            continue  # expansion / header rows
        team_cell = cells[2]
        name_el = team_cell.select_one("[data-team-name]")
        if name_el is not None:
            raw_name = name_el["data-team-name"].strip()
        else:
            link = team_cell.find("a")
            raw_name = (link.get("title") if link and link.get("title") else team_cell.get_text(" ", strip=True))
        points = re.search(r"\d+", cells[3].get_text(strip=True).replace(",", ""))
        if raw_name and points:
            rows.append(
                {
                    "rank": int(rank_txt),
                    "raw_name": raw_name.strip(),
                    "rating": float(points.group()),
                    "region": cells[4].get_text(strip=True) if len(cells) > 4 else None,
                }
            )
    return rows
