"""Polymarket gamma payloads -> market probability records.

Winner-event markets are one per team ("Will X win The International 2026?")
with `outcomes` / `outcomePrices` as JSON-encoded string arrays; the YES price
is the market-implied probability.
"""

from __future__ import annotations

import json


def _jarr(v) -> list:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return v or []


def market_rows(event: dict) -> list[dict]:
    out = []
    for m in event.get("markets") or []:
        outcomes = [str(o).lower() for o in _jarr(m.get("outcomes"))]
        prices = _jarr(m.get("outcomePrices"))
        prob = None
        if outcomes and prices and len(outcomes) == len(prices):
            try:
                idx = outcomes.index("yes") if "yes" in outcomes else 0
                prob = float(prices[idx])
            except (ValueError, TypeError):
                prob = None
        out.append({
            "question": m.get("question") or m.get("groupItemTitle") or "",
            "group_title": m.get("groupItemTitle") or "",
            "prob": prob,
            "volume": float(m.get("volumeNum") or m.get("volume") or 0.0),
        })
    return out
