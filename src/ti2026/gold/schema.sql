-- Gold DDL. Contract: `ti build-gold` drops and recreates the whole schema —
-- gold is derived from silver and disposable, exactly like silver is from bronze.
-- Every decision artifact carries a run_id so a submitted pick can be traced to
-- the model + draws that produced it.

CREATE SCHEMA IF NOT EXISTS gold;

-- one row per team per build: the single authoritative strength number
CREATE TABLE gold.team_strength (
    team_key VARCHAR,
    as_of DATE,
    strength_logit DOUBLE,        -- final: BT fit shrunk toward prior
    elo_logit DOUBLE,             -- raw BT fit component
    n_games_fit INTEGER,          -- majority-attributed games in the fit
    eff_games DOUBLE,             -- recency-weighted game count
    prior_logit DOUBLE,
    prior_source VARCHAR,         -- 'glicko' | 'manual' | 'global'
    note VARCHAR,
    PRIMARY KEY (team_key, as_of)
);

-- directed 16x16 matchup matrix (240 rows) — sims and picks read ONLY this
CREATE TABLE gold.matchup_probs (
    team_a VARCHAR,
    team_b VARCHAR,
    p_map DOUBLE,                 -- P(a beats b on one map)
    p_bo3 DOUBLE,
    p_bo5 DOUBLE,
    rho DOUBLE,                   -- per-series strength-shock sd (0 = iid maps)
    as_of DATE,
    PRIMARY KEY (team_a, team_b)
);

-- provenance for every draw set / decision
CREATE TABLE gold.sim_meta (
    run_id VARCHAR PRIMARY KEY,
    created_at TIMESTAMP,
    n_draws INTEGER,
    rng_seed BIGINT,
    model_as_of DATE,
    stage_state VARCHAR,          -- 'pre_groups' | 'post_groups'
    params_json VARCHAR           -- pairing variant, elim seeding map, rho, ...
);

-- group stage: one row per (draw, team)
CREATE TABLE gold.sim_group_draws (
    run_id VARCHAR,
    draw_id INTEGER,
    team_key VARCHAR,
    wins INTEGER,
    losses INTEGER,
    record VARCHAR,               -- '4-0' .. '0-4'
    swiss_rank INTEGER,           -- 1..16 after tiebreak assumption
    elim_opponent VARCHAR,        -- NULL for direct advancers / bottom 3
    survived_elim BOOLEAN,
    made_playoffs BOOLEAN,
    playoff_seed INTEGER          -- 1..8, NULL if out
);

-- main event: one row per (draw, slot)
CREATE TABLE gold.sim_bracket_draws (
    run_id VARCHAR,
    draw_id INTEGER,
    slot_id VARCHAR,              -- R1M1..R5M1 per seeds/bracket_topology.csv
    team1_key VARCHAR,
    team2_key VARCHAR,
    winner_key VARCHAR
);

-- reduced-N path detail for fantasy: every series a team plays, in order
CREATE TABLE gold.sim_fantasy_paths (
    run_id VARCHAR,
    draw_id INTEGER,
    team_key VARCHAR,
    period_id VARCHAR,            -- joins meta.event_calendar
    series_no INTEGER,            -- 1..k within (draw, team, period)
    stage VARCHAR,                -- 'group_swiss' | 'group_elim' | 'main_event'
    opponent_key VARCHAR,
    won BOOLEAN,
    n_games INTEGER,              -- 2 or 3 (Bo3), 3..5 (Bo5)
    best_of INTEGER               -- 3 or 5 — decides games-won split for sampling
);

-- walk-forward backtest log (append-only across `ti backtest` runs)
CREATE TABLE gold.backtest_preds (
    as_of_month VARCHAR,          -- fit cutoff, e.g. '2026-05'
    series_id VARCHAR,
    match_id BIGINT,              -- map-level rows; NULL for series-level rows
    model_variant VARCHAR,        -- 'bt' | 'glicko_only' | 'coin'
    level VARCHAR,                -- 'map' | 'series'
    p_pred DOUBLE,                -- P(team1/radiant side wins)
    outcome INTEGER,              -- 1/0
    stage VARCHAR
);

-- weighted per-stat profile per player (diagnostic / stat priority boards)
CREATE TABLE gold.player_stat_profile (
    account_id BIGINT,
    context VARCHAR,              -- 'all' | 'win' | 'loss'
    stat VARCHAR,                 -- pts_* column name from fan.fantasy_points
    mean_pts DOUBLE,
    sd_pts DOUBLE,
    n_eff DOUBLE,                 -- recency/patch-weighted game count
    PRIMARY KEY (account_id, context, stat)
);

-- every evaluated candidate slate, all three tracks
CREATE TABLE gold.slates (
    slate_id VARCHAR PRIMARY KEY,
    track VARCHAR,                -- 'group' | 'bracket' | 'fantasy'
    run_id VARCHAR,               -- draws it was evaluated on
    payload_json VARCHAR,         -- picks: team->bucket map / slot->team map / roster+cards
    ev_points DOUBLE,
    p_hist_json VARCHAR,          -- P(K=k) vector over #correct (group/bracket)
    method VARCHAR,               -- 'greedy_marginal' | 'hillclimb' | 'exhaustive' | ...
    created_at TIMESTAMP
);

-- ===================== views =====================

-- sanity dashboard: per-team outcome marginals from the latest group run
CREATE VIEW gold.team_outcome_marginals AS
WITH latest AS (
    SELECT run_id, n_draws FROM gold.sim_meta
    WHERE run_id IN (SELECT DISTINCT run_id FROM gold.sim_group_draws)
    ORDER BY created_at DESC LIMIT 1
)
SELECT d.team_key,
       round(avg(CASE WHEN d.record = '4-0' THEN 1.0 ELSE 0.0 END), 4) AS p_4_0,
       round(avg(CASE WHEN d.record = '4-1' THEN 1.0 ELSE 0.0 END), 4) AS p_4_1,
       round(avg(CASE WHEN d.swiss_rank <= 3 THEN 1.0 ELSE 0.0 END), 4) AS p_direct_advance,
       round(avg(CASE WHEN d.made_playoffs THEN 1.0 ELSE 0.0 END), 4) AS p_playoffs,
       round(avg(CASE WHEN d.record IN ('1-4', '0-4') THEN 1.0 ELSE 0.0 END), 4) AS p_bottom3
FROM gold.sim_group_draws d JOIN latest USING (run_id)
GROUP BY 1 ORDER BY p_playoffs DESC;

-- per-slot marginals from the latest bracket run (baseline for the optimizer check)
CREATE VIEW gold.slot_marginals AS
WITH latest AS (
    SELECT run_id FROM gold.sim_meta
    WHERE run_id IN (SELECT DISTINCT run_id FROM gold.sim_bracket_draws)
    ORDER BY created_at DESC LIMIT 1
)
SELECT d.slot_id, d.winner_key AS team_key,
       count(*) AS n_wins,
       round(count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY d.slot_id), 4) AS p_win_slot
FROM gold.sim_bracket_draws d JOIN latest USING (run_id)
GROUP BY 1, 2 ORDER BY slot_id, p_win_slot DESC;

-- fantasy appearance exposure: how many series a team plays per period
CREATE VIEW gold.team_series_exposure AS
SELECT run_id, team_key, period_id,
       avg(k) AS e_series,
       min(k) AS min_series,
       max(k) AS max_series
FROM (
    SELECT run_id, draw_id, team_key, period_id, count(*) AS k
    FROM gold.sim_fantasy_paths
    GROUP BY 1, 2, 3, 4
)
GROUP BY 1, 2, 3;

-- the fantasy bootstrap's sampling frame: junk-filtered, weighted per-game points.
-- weight = recency decay (90d halflife) x patch boost (current patch upweighted).
CREATE VIEW gold.fantasy_game_pool AS
WITH cur AS (SELECT max(patch) AS p FROM fan.player_game_stats WHERE patch LIKE '7.%'),
     real_series AS (        -- synthesized series with >5 games are junk groupings
         SELECT series_id FROM fan.player_game_stats
         WHERE series_id IS NOT NULL GROUP BY 1 HAVING count(DISTINCT match_id) <= 5
     )
SELECT f.*,                                               -- carries win/duration/hero/patch
       mf.first_blood_time_s, mf.tormentor_victims,        -- coach-title conditions
       power(0.5, date_diff('day', f.start_time, now()::TIMESTAMP) / 90.0)
           * (CASE WHEN f.patch = (SELECT p FROM cur) THEN 1.5 ELSE 1.0 END) AS weight
FROM fan.fantasy_points f
LEFT JOIN pred.match_flags mf USING (match_id)
WHERE f.patch LIKE '7.%'                                  -- drops junk patch rows
  AND f.parsed
  AND (f.series_id IS NULL OR f.series_id IN (SELECT series_id FROM real_series));

-- backtest scoreboard
CREATE VIEW gold.backtest_metrics AS
SELECT model_variant, level, as_of_month,
       count(*) AS n,
       round(avg(-(outcome * ln(greatest(p_pred, 1e-9))
             + (1 - outcome) * ln(greatest(1 - p_pred, 1e-9)))), 4) AS logloss,
       round(avg(pow(p_pred - outcome, 2)), 4) AS brier
FROM gold.backtest_preds
GROUP BY 1, 2, 3 ORDER BY level, as_of_month, model_variant;
