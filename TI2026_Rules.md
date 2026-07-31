# TI 2026 — Official Event Rules Reference

Source of truth for the Dota 2 The International 2026 in-client event rules, transcribed from the
screenshots in `assests/`:

| Screenshot | Content |
|---|---|
| `Preditions_How_to_play.png` | Predictions: Group Stage + The International bracket point tables |
| `fantasy_howtoplay_01.png` | Fantasy: crafting basics, rolling, coach titles, scoring, emblem colors/quality |
| `fantasy_howtoplay_02.png` | Fantasy: full per-stat point values, emblem traits |
| `fantasy_howtoplay_03.png` | Fantasy: rewards (percentile payout table) |

There are two separate point-earning systems: **Predictions** (two parts: Group Stage + The International)
and **Fantasy**.

---

## 1. Predictions

### 1.1 Group Stage Predictions

Predict the performance of the teams competing in the **swiss and elimination stages** of the Group
Stage. After the Group Stage, you get escalating points based on how many predictions were correct.

| Correct predictions | Points |
|---:|---:|
| 1 | 30 |
| 2 | 60 |
| 3 | 120 |
| 4 | 360 |
| 5 | 720 |
| 6 | 1,200 |
| 7 | 1,800 |
| 8 | 2,520 |
| 9 | 3,360 |
| 10 | 4,320 |
| 11 | 5,400 |
| 12 | 6,600 |
| 13 | 7,920 |
| 14 | 9,360 |
| 15 | 10,920 |
| 16 | 12,000 |

- 16 predictions total, max **12,000 points**.
- Escalating table: each additional correct prediction is worth more than the previous one, so
  expected value should be optimized on *total number correct*, not on individual upset value
  (there is no bonus for "bold" picks).

### 1.2 The International Predictions (Bracket)

Once the Group Stage is complete, fill out the **tournament bracket**: pick the winner of each
series, all the way to the winner of TI. Scored after The International ends.

| Correct predictions | Points |
|---:|---:|
| 1 | 120 |
| 2 | 360 |
| 3 | 720 |
| 4 | 1,200 |
| 5 | 1,800 |
| 6 | 2,520 |
| 7 | 3,360 |
| 8 | 4,320 |
| 9 | 5,400 |
| 10 | 6,600 |
| 11 | 7,920 |
| 12 | 9,360 |
| 13 | 10,920 |
| 14 | 12,000 |

- 14 series predictions, max **12,000 points** (14 series is consistent with a classic 8-team
  double-elimination bracket).
- The bracket is filled out **after** groups complete, so bracket picks can use full group-stage data.
- Note: the TI table is exactly the Group Stage table shifted by two (TI row *n* = Group row *n+2*).

**Predictions max combined: 24,000 points.**

---

## 2. Fantasy

### 2.1 Crafting Basics

- Craft your own team from The International competitors: choose the **core, mid, and support**
  players from three teams.
- Use **roll tokens** to mutate and improve your team (via War Banner emblems) to increase fantasy score.
- New crafting options appear **after each period** — check in every period.

### 2.2 Rolling

- You always have **3 unique roll options** for emblems, the same options for every War Banner.
- Each roll costs **1 roll token**, affects **only the currently selected War Banner**, and
  **replaces all available roll options** when used.

### 2.3 Coaching Titles

- You choose a **prefix and a suffix** (called Titles) that give bonuses to **all** your players.
- Each title gives a **percentage increase to the final score in a game if a condition is met**.
- Titles can be changed **freely, without spending roll tokens**.
- (The full list of titles/conditions is not in the screenshots — see Open Questions.)

### 2.4 Scoring Pipeline

1. Once matches for a period begin, a **snapshot of your roster** is saved and used for scoring.
2. For each role, each player's score is calculated **individually in every game** they play.
3. Players earn points **only for the stats present on their War Banner**, amplified by any coach
   Title whose condition is met.
4. The scores of all players for a role are **averaged** → the role's final score for that game.
5. The **top two scoring games within a series** are used for the role's final score for the match.
6. If a role plays **more than one series in a period, the best scoring series** is used.

### 2.5 Base Stat Values

| Stat | Points | Emblem color |
|---|---|---|
| Kills | +107.00 per kill | Red |
| Deaths | 1,950.00 starting points, −195.00 per death | Red |
| Creep Score | +3.00 per last hit or deny | Red |
| GPM | player's GPM × 2.00 | Red |
| Madstone Collected | +13.00 per Madstone | Red |
| Tower Kills | +352.00 per tower last hit | Red |
| Wards Placed | +117.00 per observer ward placed | Blue |
| Camps Stacked | +234.00 per camp stacked | Blue |
| Runes Grabbed | +141.00 per rune bottled or taken | Blue |
| Watchers Taken | +147.00 per captured watcher | Blue |
| Smokes Used | +293.00 per Smoke of Deceit used | Blue |
| Lotuses Grabbed | +176.00 per lotus taken | Blue |
| Roshan Kills | +1,172.00 per Roshan kill | Green |
| Teamfight Participation | max 2,124.00 points | Green |
| Stuns | +10.00 per second of stun | Green |
| Tormentor Kills | +879.00 per Tormentor kill | Green |
| First Blood | 1,934.00 points if the player gets first blood | Green |
| Courier Kills | +703.00 per Courier kill | Green |

### 2.6 Emblems (War Banners)

Modifying War Banners is the primary way to influence fantasy score. Each banner is made of
**emblems**; each emblem = one fantasy stat, adjusted by its **quality** and **trait**. Optimize the
banner around the specific player chosen.

**Emblem color → possible stats** (rerolling a stat guarantees a new stat; **no duplicate stats on
one War Banner**):

- **Red:** Kills, Deaths, Creep Score, GPM, Madstone Collected, Tower Kills
- **Blue:** Wards Placed, Camps Stacked, Runes Grabbed, Watchers Taken, Smokes Used, Lotuses Grabbed
- **Green:** Roshan Kills, Teamfight Participation, Stuns, Tormentor Kills, First Blood, Courier Kills

**Emblem quality** (boost to the emblem's base stat score; higher tiers are rarer when crafting):

| Quality | Boost |
|---|---:|
| Tier I | +10% |
| Tier II | +30% |
| Tier III | +60% |
| Tier IV | +100% |
| Tier V | +150% |

**Emblem traits** (amplify the emblem's stat bonus, sometimes conditionally; rerolling a trait
guarantees a different trait):

| Trait | Effect |
|---|---|
| Fractal | +60% to the stat bonus if all emblem qualities on the War Banner are different |
| Benevolent | +20% bonus to the stat value of **adjacent** emblems |
| Vampiric | +50% to this emblem's stat value, but −10% to adjacent emblems' stat value |
| Unique | +30% to the stat bonus if this is the only Unique emblem on the War Banner |
| Friendly | +50% to the stat bonus if there are at least 3 Friendly emblems on the War Banner |

### 2.7 Fantasy Rewards (per period)

At the end of each period, your roster's fantasy score is compared against **everyone else who
submitted a roster for that period** — points are awarded by percentile:

| Percentile | Points |
|---:|---:|
| 100th | 12,000 |
| 99th | 11,400 |
| 95th | 10,000 |
| 90th | 8,400 |
| 80th | 5,800 |
| 60th | 3,300 |
| 40th | 1,700 |
| 20th | 400 |
| 10th | 200 |

Fantasy is **relative** scoring: what matters is beating other players' rosters, not the absolute
score. Payouts repeat each period.

---

## 3. Open Questions / Not Covered by the Screenshots

Capture these from the client or updated screenshots when available:

- Exact roster construction (how many players per role slot; constraint of "from three teams").
- Number of emblems per War Banner (traits reference "adjacent" emblems — likely 3+, unconfirmed).
- Number of periods, their date boundaries, and roll-token income per period.
- Full list of Coaching Titles (prefixes/suffixes) and their conditions/percentages.
- What exactly the 16 Group Stage prediction questions are (e.g., swiss records, elimination
  outcomes) — the table only shows the count and payouts.
- TI 2026 bracket format confirmation (14 series implies 8-team double elimination).

## 4. Transcription Notes

- In-game text contains typos ("preformance"); meaning, not typos, was transcribed.
- The folder is named `assests/` (sic) — keep that spelling in paths.
