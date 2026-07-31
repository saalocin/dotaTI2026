# Dota 2 — The International 2026 Prediction Workspace

## Vision

This workspace exists to build well-researched, data-driven picks for the Dota 2 TI 2026 in-client
event, with the goal of maximizing total event points across all three point-earning tracks:

1. **Group Stage Predictions** — predict team performance in the swiss + elimination stages
   (16 predictions, up to 12,000 pts).
2. **The International Predictions** — fill out the playoff bracket series-by-series through the
   TI winner (14 predictions, up to 12,000 pts).
3. **Fantasy** — draft players by role, optimize War Banners (emblems, quality, traits) and coach
   titles each period; scored by percentile vs. all other players (up to 12,000 pts per period).

Work here includes gathering team/player data, modeling win probabilities and player stat
distributions, and turning those into concrete picks and fantasy roster/banner decisions.

## Rules Reference

The official rules — transcribed from the in-client screenshots in `assests/` — live in
[TI2026_Rules.md](TI2026_Rules.md). That file is the **source of truth** for all scoring math and
constraints; consult it before any prediction, scoring, or optimization work, and update it (plus
its Open Questions section) when new rule details or screenshots appear.

@TI2026_Rules.md

## Layout

- `assests/` — original in-client rule screenshots (folder name spelling is intentional)
- `TI2026_Rules.md` — transcribed rules reference (source of truth)
