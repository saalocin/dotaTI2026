# TI 2026 — Official Event Rules Reference

Source of truth for the Dota 2 The International 2026 in-client event rules, transcribed from the
screenshots in `assests/`:

| Screenshot | Content |
|---|---|
| `Preditions_How_to_play.png` | Predictions: Group Stage + The International bracket point tables |
| `fantasy_howtoplay_01.png` | Fantasy: crafting basics, rolling, coach titles, scoring, emblem colors/quality |
| `fantasy_howtoplay_02.png` | Fantasy: full per-stat point values, emblem traits |
| `fantasy_howtoplay_03.png` | Fantasy: rewards (percentile payout table) |
| `fantasy_title_bonuses.png` | Fantasy: CHANGE TITLES dialog — full prefix/suffix list with bonuses |
| `fantasy_tutorial_01..06.png` | Fantasy: in-client tutorial — team-per-role drafting, emblem attributes (color/stat/quality/trait), War Banner mechanics |

There are two separate point-earning systems: **Predictions** (two parts: Group Stage + The International)
and **Fantasy**.

---

## 0. TI 2026 Tournament Facts (researched 2026-07-31)

- **Dates/venue:** Aug 13–23, 2026 — Oriental Sports Center, Shanghai. Organizer Valve + PGL.
- **Format:** 16 teams. **Group Stage Aug 13–16**: Swiss, all matches Bo3 — top 3 → playoffs,
  4th–13th → **Elimination Round** (5 of 10 advance), 14th–16th out. **Main Event Aug 20–23**:
  8-team double elimination (Bo3, Grand Final Bo5) = exactly **14 series** = the 14 bracket picks.
- **Predictions lock:** Aug 13, 10:00 CST (02:00 UTC) — first match start. **Bracket picks
  open Aug 17 01:00 UTC and lock Aug 20 02:00 UTC** (client compendium definition, league
  19719: `prediction_ranges`; series slots carry `league_node_id` 14–27).
- **Group Stage prediction shape — CAPTURED from the client compendium definition**
  (`scripts/compendiums/19719/compendium_definition.txt`, bronze `dotaclient`): place all 16
  teams into **six record buckets** — **4-0 (1) · 4-1 (2) · Qualified via Elimination Round
  (5) · Eliminated via Elimination Round (5) · 1-4 (2) · 0-4 (1)**. Machine-readable in
  `seeds/swiss_buckets.csv`; note the bottom three are split 1-4 vs 0-4, and the middle ten
  are judged by **elimination-round results**, not Swiss seeding.
- **All 16 teams are known** (7 invites + regionals; see `seeds/team_crosswalk.csv`). Mind the
  renames: BetBoom → **BoomBoys**, 1w/ex-Tundra → **Iron Wing**, ex-L1GA TEAM → **HULIGANI**.

### Fantasy roster construction (Valve announcement, Jul 30 2026)

Draft **5 players by choosing a TEAM per role slot**: both supports from the chosen support
team, safelaner + offlaner from the chosen core team, midlaner from the chosen mid team —
the roles map to Liquipedia positions 1 (safelane), 2 (mid), 3 (offlane), 4+5 (supports).
**The same team may be chosen for more than one slot** (confirmed in-client 2026-08-01: the
Choose Team selector accepts repeats; Valve's "three teams" phrasing describes the slot
structure, not a distinctness rule — a full one-team stack of 5 players is legal). Valve also
changed prefixes/suffixes and added coach self-modifiers vs TI2025 — the in-client screenshots
in `assests/` are the TI2026-current source of truth (TI2025 point values were different; do
not mix years).

### Carry-over knowledge from TI2024/TI2025 (likely but unconfirmed for TI2026)

- War Banner = **3 emblems** + prefix/suffix titles per player card ("adjacent" traits: middle
  slot has 2 neighbors, edge slots 1).
- Scoring **periods are stage-based** (TI2025: 2 periods with roster locks; TI2024: 3). Roll
  tokens granted per period (TI2025: 40) and unused tokens carry over.
- Series scoring: best 2 maps of a series count (already in §2.4); best series per period per role.

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
  players via three team slots (the same team may fill several slots, §0).
- Confirmed by the in-client tutorial (`fantasy_tutorial_02.png`): **you pick a TEAM per role**,
  not individual players — core and support roles score **both** players of the chosen team.
  The chosen team can be **freely changed** ("Choose Team" on the War Banner) until the
  period's roster snapshot (§2.4) locks it. **Role slots are independent — the same team can
  hold several slots** (client-confirmed 2026-08-01).
- Each role slot has **one War Banner shared by the role's player(s)** — a duo is scored by the
  same banner.
- Use **roll tokens** to mutate and improve your team (via War Banner emblems) to increase fantasy score.
- New crafting options appear **after each period** — check in every period.

### 2.2 Rolling

- You always have **3 unique roll options** for emblems, the same options for every War Banner.
- Each roll costs **1 roll token**, affects **only the currently selected War Banner**, and
  **replaces all available roll options** when used.

### 2.3 Coaching Titles

- You choose **one prefix and one suffix** (called Titles) that give bonuses to **all** your players.
- Each title gives a **percentage increase to the final score in a game if a condition is met**.
- Titles can be changed **freely, without spending roll tokens**.

Full TI2026 title list (`assests/fantasy_title_bonuses.png`; machine-readable copy in
`seeds/coach_titles.csv`):

**Prefixes** — condition is an attribute of the hero the player plays that game:

| Prefix | Bonus | Condition |
|---|---:|---|
| Crimson | +6% | playing a red hero |
| Cerulean | +11% | playing a blue hero |
| Emerald | +6% | playing a green hero |
| Royal | +10% | playing a purple hero |
| Golden | +8% | playing a yellow or brown hero |
| Elemental | +8% | playing an Aquatic, Fiery, or Icy hero |
| Otherworldly | +7% | playing an Undead, Demon, or Spirit hero |
| Heroic | +9% | playing a Caped or Masked hero |

**Suffixes** — condition is a game event/state:

| Suffix | Bonus | Condition |
|---|---:|---|
| the Tormented | +23% | any player dies to a Tormentor |
| the Flayed Twins Acolyte | +9% | any player gets first blood before the starting horn |
| the Patient | +23% | first blood does not happen until after 10 minutes |
| the Underdog | +6% | in games where the player loses |
| the Decisive | +24% | in games that last less than 25 minutes |
| the Clutch | +16% | when playing the last possible match of a series |
| the Lucky | +21% | if the match time ends with an 8 |
| the Cruel | +13% | if a player is killed while in their own fountain |

Modeling notes (assumptions where Valve is silent):

- Hero "colors"/attributes are Valve's fantasy hero tags, shipped per hero in the client's
  `npc_heroes.txt` ("Adjectives" blocks) — no public API. `ti ingest dotaclient` extracts them
  from the local install into `seeds/hero_tags.csv` (raw file kept in bronze `dotaclient`;
  the client tags Mask/Masked inconsistently — normalized to `masked`). Re-run after patches.
- Prefix × suffix stacking on the same game assumed **multiplicative** (unverified; vs additive
  the difference is <1.5% at these magnitudes).
- "Match time ends with an 8" read as the final displayed clock digit (`duration_s % 10 == 8`,
  ≈10%); the minutes-digit reading is also ≈10%, so the ambiguity is immaterial.
- "Own fountain" deaths (the Cruel) are unobservable in our data — league-constant estimate.

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

Per the in-client tutorial (`fantasy_tutorial_03..06.png`), each emblem has exactly four
attributes — **Color, Stat, Quality, Trait** — and:

- An emblem is **Red, Green, or Blue**; the **color distribution of a War Banner cannot be
  modified and is set by the player's Role** → stat choice is color-constrained per role.
  Role layouts (TI2025 precedent, TI2026 counts unconfirmed): core **2R+1G**,
  support **2B+1G**, mid **R+G+B** — machine-readable in `seeds/banner_layouts.csv`.
- **Only stats you hold an emblem for score points** (tutorial_04, matches §2.4).
- **Quality** "provides a percentage bonus base fantasy score" and **Trait** "can provide an
  additional percentage bonus to the base fantasy score" → quality and trait effects read as
  **additive percentages of the base stat score** (a background emblem shows Tier II (+30%)
  with a −10% adjacency effect displayed as a net **120%**). Modeled additively.

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

## 3. Open Questions / Still To Capture In-Client

Resolved by research (see §0): roster construction (5 players / 3 teams / 2+2+1), bracket format
(8-team double elim, 14 series), group-prediction shape (team-allocation buckets).

Still open — screenshot these from the client when visible:

- TI2026 emblem count per card (3 at TI2024/25; one unverified claim of 5) and the exact color
  split of the duo-card banners — layouts in `seeds/banner_layouts.csv` are the TI2025
  precedent until confirmed. **Screenshot each role's full War Banner** (all emblem slots
  visible) to settle both; the tutorial background shows a core banner with ≥3 emblems
  (red/…/red) consistent with 2R+1G.
- ~~Full list of TI2026 Coaching Titles~~ — **captured** (`fantasy_title_bonuses.png`, §2.3).
  ~~Per-hero tag list~~ — **captured from the client itself** (2026-08-01 update:
  `ti ingest dotaclient` reads npc_heroes.txt "Adjectives", incl. the new
  purple/yellow/brown/masked). Still open on titles: prefix/suffix stacking rule (additive
  vs multiplicative); hovering the dialog's underlined terms can spot-check a few tags.
- The "coach self-modifiers" mentioned in Valve's announcement, if they are a separate
  mechanic from the titles above — nothing else visible in the dialog yet.
- TI2026 period boundaries, roster lock times, and roll-token grant per period.
- ~~The exact Swiss bucket set~~ — **captured** from the client compendium definition
  (league 19719) into `seeds/swiss_buckets.csv`; sanity-check in-client on Aug 13.

## 3.1 Data caveats for modeling (from source research)

- **Lotuses Grabbed has no data source anywhere** (OpenDota's pluck event is always 0; only
  consumption is observable via famango item uses). Stored as NULL; treat lotus emblems as
  unmodelable (minor stat at +176).
- **Madstone** is proxied by `item_uses.madstone_bundle` (bundle pickups ≠ necessarily stones);
  calibrate `meta.stat_calibration` against the in-client scoreboard in TI's first days.
- **Valve's own pipeline can differ from replay parsing**: TI2025 shipped a stun-scoring bugfix
  (compensated 3 tokens/emblem); teamfight participation is an undocumented Valve formula with a
  cap — OpenDota's `teamfight_participation` is a parser heuristic. Calibrate both.
- **Ratings traps**: Liquipedia Glicko-2 resets on team rename → Iron Wing (ex-Tundra) is badly
  under-rated; TEAM VISION ≠ PARIVISION (world #1, not at TI); several TI teams sit outside the
  top-20 board (RD < 100 listing rule). Join ratings only via the crosswalk aliases.
- **Team ids ≠ five-man units** (verified 2026-07-31): OpenDota ids drift on re-registration
  (HULIGANI's current unit played 159 games across 6+ ids; its seeded id had zero) AND persist
  across roster swaps (OG's id carries 141 stale-lineup games). Silver therefore attributes a
  side to a team iff **≥3 of its seeded 5 players are on it** (`key_source='roster_majority'`);
  the model trains only on majority-attributed games.
- **Historical ratings exist only in web archives**: the live board is current-state-only, so
  time-correct priors come from Wayback captures of Portal:Rankings (2025-08-08, 2026-03-12,
  where **Tundra = Iron Wing's unit rated #2 at 1790**, z=+1.05 — vs z=−1.78 on the post-rename
  live board). `seeds/manual_priors.csv` freezes such values for teams the board can't rate.
- **Honest model expectation** (walk-forward Feb–Jul 2026, 248 series): tuned model logloss
  0.6833 vs coin 0.6931 — near-peer pro series are close to coin flips. The edge comes from
  calibration + joint-slate optimization (convex point tables), not from big per-series calls.

## 4. Transcription Notes

- In-game text contains typos ("preformance"); meaning, not typos, was transcribed.
- The folder is named `assests/` (sic) — keep that spelling in paths.
