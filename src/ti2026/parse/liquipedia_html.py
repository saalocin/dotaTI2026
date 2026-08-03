"""Liquipedia rendered-HTML parsers (PRIMARY path).

Wikitext {{Match}} templates are empty placeholders even on completed tournaments —
series data lives in LPDB and only exists in the rendered HTML (action=parse
prop=text). Verified against the completed TI2025 Group_Stage / Main_Event pages.

Structures handled:
- bracket matches:   .brkts-match          (playoff + elimination brackets)
- matchlist matches: .brkts-matchlist-match (swiss round Bo3 lists)
- swiss standings:   table.table2__table with Participant/Matches/Round N columns
Game-level Valve match ids come free from the popup's stratz/datdota links.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

MATCH_ID_RE = re.compile(r"(?:stratz\.com/match|datdota\.com/matches)/(\d+)")
WL_RE = re.compile(r"(\d+)\s*-\s*(\d+)")


@dataclass
class SeriesRec:
    team1_raw: str
    team2_raw: str
    score1: int | None
    score2: int | None
    winner_raw: str | None
    timestamp: int | None
    round_label: str | None
    game_match_ids: list[int] = field(default_factory=list)
    depth: int | None = None  # bracket DOM nesting (see assign_bracket_slots)

    @property
    def has_teams(self) -> bool:
        return bool(self.team1_raw and self.team2_raw)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _int(text: str) -> int | None:
    text = (text or "").strip()
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _game_ids(el) -> list[int]:
    ids = {int(m.group(1)) for a in el.select("a[href]") if (m := MATCH_ID_RE.search(a["href"]))}
    return sorted(ids)  # ids are monotonic in time -> game order


def _timestamp(el) -> int | None:
    t = el.select_one(".timer-object[data-timestamp]")
    return int(t["data-timestamp"]) if t else None


def _matchlist_title(el) -> str | None:
    container = el.find_parent(class_="brkts-matchlist")
    h = container.select_one(".brkts-matchlist-title") if container else None
    if not h:
        return None
    parts = [p.strip() for p in h.stripped_strings]
    return max(parts, key=len) if parts else None


def bracket_series(html: str) -> list[SeriesRec]:
    """All bracket match nodes in DOM order, INCLUDING empty TBD slots (needed so
    slot assignment stays aligned on partially-filled live brackets)."""
    out = []
    for m in _soup(html).select(".brkts-match"):
        entries = m.select(".brkts-opponent-entry")
        names = [(e.get("aria-label") or "").strip() for e in entries[:2]]
        while len(names) < 2:
            names.append("")
        scores, wins = [], []
        for e in entries[:2]:
            sc = e.select_one(".brkts-opponent-score-inner")
            scores.append(_int(sc.get_text(strip=True)) if sc else None)
            wins.append(e.select_one(".brkts-opponent-win") is not None)
        while len(scores) < 2:
            scores.append(None)
            wins.append(False)
        winner = names[0] if wins[0] and not wins[1] else names[1] if wins[1] and not wins[0] else None
        if winner is None and None not in scores and scores[0] != scores[1]:
            winner = names[scores.index(max(s for s in scores if s is not None))]
        out.append(
            SeriesRec(
                team1_raw=names[0],
                team2_raw=names[1],
                score1=scores[0],
                score2=scores[1],
                winner_raw=winner or None,
                timestamp=_timestamp(m),
                round_label=None,  # headers render in a separate strip; label set via slot
                game_match_ids=_game_ids(m),
                depth=len(m.find_parents(class_="brkts-round-body")),
            )
        )
    return out


def matchlist_series(html: str) -> list[SeriesRec]:
    out = []
    for m in _soup(html).select(".brkts-matchlist-match"):
        opp_cells = m.select(".brkts-matchlist-opponent")
        score_cells = m.select(".brkts-matchlist-score")
        if len(opp_cells) != 2:
            continue
        names = [(c.get("aria-label") or "").strip() for c in opp_cells]
        scores = [_int(c.get_text(strip=True)) for c in score_cells[:2]]
        winner_cell = m.select_one(".brkts-matchlist-slot-winner")
        winner = (winner_cell.get("aria-label") or "").strip() if winner_cell else None
        out.append(
            SeriesRec(
                team1_raw=names[0],
                team2_raw=names[1],
                score1=scores[0] if scores else None,
                score2=scores[1] if len(scores) > 1 else None,
                winner_raw=winner or None,
                timestamp=_timestamp(m),
                round_label=_matchlist_title(m),
                game_match_ids=_game_ids(m),
            )
        )
    return out


def swiss_standings(html: str) -> list[dict]:
    """Rows: {rank, raw_name, wins, losses, game_wins, game_losses, rounds: [(w,l)|None]}."""
    soup = _soup(html)
    rows = []
    for table in soup.find_all("table"):
        header = table.find("tr")
        if not header:
            continue
        head_txt = header.get_text(" ", strip=True)
        if "Participant" not in head_txt or "Matches" not in head_txt:
            continue
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all("td")
            if len(cells) < 4:
                continue
            rank = _int(cells[0].get_text(strip=True))
            name_el = cells[1].select_one("[data-team-name]")
            if name_el is not None:
                raw_name = name_el["data-team-name"].strip()
            else:
                link = cells[1].find("a")
                raw_name = (link.get("title") or link.get_text(strip=True)) if link else cells[1].get_text(" ", strip=True)
            wl = WL_RE.search(cells[2].get_text(" ", strip=True))
            gw = WL_RE.search(cells[3].get_text(" ", strip=True))
            rounds = []
            for c in cells[4:]:
                pair = re.search(r"(\d+)\s*:\s*(\d+)", c.get_text(" ", strip=True))
                rounds.append((int(pair.group(1)), int(pair.group(2))) if pair else None)
            if raw_name and wl:
                rows.append(
                    {
                        "rank": rank,
                        "raw_name": raw_name,
                        "wins": int(wl.group(1)),
                        "losses": int(wl.group(2)),
                        "game_wins": int(gw.group(1)) if gw else None,
                        "game_losses": int(gw.group(2)) if gw else None,
                        "rounds": rounds,
                    }
                )
        if rows:
            break  # first standings table is the swiss table
    return rows


# Bracket/8U4L2DSL1D slot layout, derived from DOM nesting depth (verified on the
# completed TI2025 bracket): depth = count of .brkts-round-body ancestors, and within
# a depth the upper-bracket matches precede the lower-bracket ones in DOM order.
#   depth 5 -> LB R1        depth 4 -> UB QF x4 then LB QF x2
#   depth 3 -> UB SF x2 then LB SF   depth 2 -> UB Final then LB Final   depth 1 -> GF
DEPTH_SLOTS_8DE = {
    5: ["R1M5", "R1M6"],
    4: ["R1M1", "R1M2", "R1M3", "R1M4", "R2M3", "R2M4"],
    3: ["R2M1", "R2M2", "R3M1"],
    2: ["R4M1", "R4M2"],
    1: ["R5M1"],
}

SLOT_LABELS = {
    "R1M1": "UB Quarterfinal", "R1M2": "UB Quarterfinal", "R1M3": "UB Quarterfinal",
    "R1M4": "UB Quarterfinal", "R2M1": "UB Semifinal", "R2M2": "UB Semifinal",
    "R4M1": "UB Final", "R1M5": "LB Round 1", "R1M6": "LB Round 1",
    "R2M3": "LB Quarterfinal", "R2M4": "LB Quarterfinal", "R3M1": "LB Semifinal",
    "R4M2": "LB Final", "R5M1": "Grand Final",
}


def assign_bracket_slots(series: list[SeriesRec]) -> dict[str, SeriesRec]:
    """Map bracket series to R{n}M{n} slots via nesting depth + DOM order.

    Works on partially-filled brackets because bracket_series keeps TBD nodes,
    preserving positional alignment. Also stamps round_label from the slot.
    """
    assigned: dict[str, SeriesRec] = {}
    counters: dict[int, int] = {}
    for rec in series:
        slots = DEPTH_SLOTS_8DE.get(rec.depth or 0)
        if not slots:
            continue
        i = counters.get(rec.depth, 0)
        if i < len(slots):
            slot = slots[i]
            rec.round_label = SLOT_LABELS[slot]
            assigned[slot] = rec
            counters[rec.depth] = i + 1
    return assigned
