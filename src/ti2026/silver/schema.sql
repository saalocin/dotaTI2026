-- Silver DDL. Full-rebuild contract: build.py deletes the DB file and re-executes this.
-- Lineage: seeds/* -> pred.teams, fan.players, pred.bracket_slots, meta.*;
-- Liquipedia -> pred.series (TI stages), pred.swiss_standings, pred.team_ratings, bracket results;
-- OpenDota -> pred.games, pred.series (pre_ti), fan.player_game_stats;
-- datdota -> fan.tormentor_xcheck; odds seeds/scrape -> pred.odds.

CREATE SCHEMA IF NOT EXISTS meta;
CREATE SCHEMA IF NOT EXISTS pred;
CREATE SCHEMA IF NOT EXISTS fan;

-- ===================== meta =====================
CREATE TABLE meta.build_info (
    built_at TIMESTAMP NOT NULL,
    bronze_snapshots VARCHAR  -- JSON: {source: snapshot_id used}
);

CREATE TABLE meta.event_calendar (
    period_id VARCHAR PRIMARY KEY,
    label VARCHAR,
    starts_at TIMESTAMP,
    ends_at TIMESTAMP,
    lock_at TIMESTAMP
);

CREATE TABLE meta.stat_calibration (
    stat VARCHAR PRIMARY KEY,
    multiplier DOUBLE DEFAULT 1.0,
    offset_pts DOUBLE DEFAULT 0.0,
    fitted_from VARCHAR,
    note VARCHAR
);

CREATE TABLE meta.news (            -- official Dota 2 feed via ISteamNews
    gid VARCHAR PRIMARY KEY,
    posted_at TIMESTAMP,
    title VARCHAR,
    url VARCHAR,
    feedlabel VARCHAR
);

CREATE TABLE meta.heroes (          -- opendota /heroes constants
    hero_id INTEGER PRIMARY KEY,
    name VARCHAR,                   -- npc_dota_hero_*
    localized_name VARCHAR,
    primary_attr VARCHAR,
    attack_type VARCHAR,
    roles VARCHAR[]
);

-- ===================== pred (teams domain) =====================
CREATE TABLE pred.teams (
    team_key VARCHAR PRIMARY KEY,
    display_name VARCHAR NOT NULL,
    liquipedia_page VARCHAR NOT NULL,
    opendota_team_id BIGINT,
    datdota_team_id BIGINT,
    opendota_alt_ids BIGINT[],   -- verified same-unit prior registrations (renames/re-registers);
                                 -- fetch coverage + id-fallback only, NEVER sole attribution truth
    aliases VARCHAR[],
    region VARCHAR,
    seed_note VARCHAR
);

-- Hand-set strength priors for teams the Liquipedia Glicko board cannot rate
-- (off-board or reset by rename). prior_z = strength vs the TI-16 field in
-- standard deviations (0 = average). Consumed by the gold Bradley-Terry fit.
CREATE TABLE pred.manual_priors (
    team_key VARCHAR PRIMARY KEY,
    prior_z DOUBLE,
    basis VARCHAR
);

CREATE TABLE pred.team_ratings (
    snapshot_date DATE,
    source VARCHAR,              -- 'liquipedia' | 'datdota' | 'opendota'
    raw_name VARCHAR,
    team_key VARCHAR,            -- NULL when a rated team is not at TI
    rank INTEGER,
    rating DOUBLE,
    rating_deviation DOUBLE,     -- liquipedia only, when displayed
    is_reset_suspect BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (snapshot_date, source, raw_name)
);

CREATE TABLE pred.series (
    series_id VARCHAR PRIMARY KEY,   -- liquipedia match2 id, or 'od:<min match_id>' synthetic
    stage VARCHAR,                   -- 'pre_ti' | 'group_swiss' | 'group_elim' | 'main_event'
    round_label VARCHAR,
    best_of INTEGER,
    team1_key VARCHAR,
    team2_key VARCHAR,
    team1_raw VARCHAR,
    team2_raw VARCHAR,
    team1_score INTEGER,
    team2_score INTEGER,
    winner_key VARCHAR,
    scheduled_at TIMESTAMP,
    completed BOOLEAN,
    source VARCHAR
);

-- team_key attribution: roster-majority (>=3 of a team's seeded 5 on the side) is
-- authoritative — team ids drift on re-registration (HULIGANI) and persist across
-- roster swaps (OG), so ids alone mislead in both directions. key_source records
-- which rule fired: 'roster_majority' | 'team_id' | 'team_id_alt'.
CREATE TABLE pred.games (
    match_id BIGINT PRIMARY KEY,
    series_id VARCHAR,
    game_no INTEGER,
    radiant_team_id BIGINT,
    dire_team_id BIGINT,
    radiant_team_key VARCHAR,
    dire_team_key VARCHAR,
    radiant_key_source VARCHAR,
    dire_key_source VARCHAR,
    radiant_win BOOLEAN,
    duration_s INTEGER,
    league_id INTEGER,
    league_name VARCHAR,
    start_time TIMESTAMP,
    source VARCHAR
);

CREATE TABLE pred.swiss_standings (
    snapshot_ts TIMESTAMP,
    team_key VARCHAR,
    raw_name VARCHAR,
    swiss_round INTEGER,
    wins INTEGER,
    losses INTEGER,
    status VARCHAR,       -- 'active'|'advanced'|'to_elim'|'eliminated'
    final_bucket VARCHAR, -- joins seeds/swiss_buckets.csv once the client reveals buckets
    PRIMARY KEY (snapshot_ts, raw_name)
);

CREATE TABLE pred.bracket_slots (
    slot_id VARCHAR PRIMARY KEY,     -- R1M1..R5M1, 14 rows
    round_no INTEGER,
    side VARCHAR,                    -- 'UB'|'LB'|'GF'
    best_of INTEGER,
    feeds_winner_slot VARCHAR,
    feeds_loser_slot VARCHAR,
    note VARCHAR,
    team1_key VARCHAR,
    team2_key VARCHAR,
    winner_key VARCHAR,
    series_id VARCHAR
);

-- Draft actions (captains-mode picks & bans) for matches involving TI-roster players.
-- team: 0 = radiant, 1 = dire (Valve convention). One row per hero per match.
CREATE TABLE pred.draft_actions (
    match_id BIGINT,
    ord INTEGER,
    is_pick BOOLEAN,
    hero_id INTEGER,
    team INTEGER,
    team_key VARCHAR,       -- resolved via pred.games radiant/dire keys
    source VARCHAR DEFAULT 'opendota_explorer',
    PRIMARY KEY (match_id, hero_id)
);

-- STRATZ ground truth: which real series a match belongs to and its best-of.
-- Replaces the Bo2-league *heuristic* wherever coverage exists.
CREATE TABLE pred.match_series (
    match_id BIGINT PRIMARY KEY,
    series_id BIGINT,
    best_of INTEGER,
    source VARCHAR DEFAULT 'stratz'
);

-- Coach-title condition inputs (rules §2.3): match-level facts the fantasy title
-- multipliers key on. first_blood_time_s: API clamps pre-horn kills to 0.
CREATE TABLE pred.match_flags (
    match_id BIGINT PRIMARY KEY,
    first_blood_time_s INTEGER,
    tormentor_victims SMALLINT,      -- players who died to npc_dota_miniboss
    source VARCHAR DEFAULT 'opendota_explorer'
);

-- Coach titles (seeds/coach_titles.csv, transcribed from the in-client dialog).
CREATE TABLE meta.coach_titles (
    title VARCHAR PRIMARY KEY,
    side VARCHAR NOT NULL,           -- prefix | suffix
    pct DOUBLE NOT NULL,             -- bonus percent when the condition is met
    cond_type VARCHAR NOT NULL,
    cond_param VARCHAR,
    note VARCHAR
);

-- Valve fantasy hero tags for prefix conditions (seeds/hero_tags.csv).
-- Client-only data with no public API: empty until captured from in-client
-- tooltips or a community datamine. Gold degrades gracefully while empty.
CREATE TABLE meta.hero_tags (
    hero_id INTEGER PRIMARY KEY,
    hero VARCHAR,
    tags VARCHAR[],                  -- red|blue|green|purple|yellow|brown|aquatic|...
    source VARCHAR
);

-- Prediction-market probabilities (Polymarket, keyless public API). One row per
-- (snapshot, market); drift across snapshots is the point.
CREATE TABLE pred.market_odds (
    fetched_at TIMESTAMP,
    source VARCHAR DEFAULT 'polymarket',
    event_slug VARCHAR,
    market_question VARCHAR,
    team_key VARCHAR,            -- resolved via crosswalk aliases; NULL if no match
    prob DOUBLE,                 -- market-implied probability of YES
    volume DOUBLE,
    PRIMARY KEY (fetched_at, event_slug, market_question)
);

CREATE TABLE pred.odds (
    fetched_at TIMESTAMP,
    source VARCHAR,
    event_label VARCHAR,
    series_id VARCHAR,
    team1_key VARCHAR,
    team2_key VARCHAR,
    odds1 DOUBLE,
    odds2 DOUBLE,
    implied1 DOUBLE,     -- proportionally de-vigged
    implied2 DOUBLE,
    best_of INTEGER,
    stage VARCHAR,
    is_closing BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (fetched_at, source, event_label)
);

CREATE TABLE pred.odds_historical (
    year INTEGER,
    stage VARCHAR,
    team1 VARCHAR,
    team2 VARCHAR,
    closing_odds1 DOUBLE,
    closing_odds2 DOUBLE,
    winner INTEGER,
    source VARCHAR
);

-- ===================== fan (players domain) =====================
CREATE TABLE fan.players (
    account_id BIGINT PRIMARY KEY,
    nickname VARCHAR,
    od_name VARCHAR,
    team_key VARCHAR,
    position INTEGER,          -- 1..5
    fantasy_slot VARCHAR,      -- 'safelane'|'mid'|'offlane'|'support'
    liquipedia_page VARCHAR,
    is_ti2026 BOOLEAN DEFAULT TRUE,
    source VARCHAR
);

-- One row per player-game. Stat columns NULL when the replay is unparsed (parsed=false)
-- or the stat has no source (lotuses_grabbed is ALWAYS NULL — no source anywhere).
CREATE TABLE fan.player_game_stats (
    match_id BIGINT,
    account_id BIGINT,
    hero_id INTEGER,
    is_radiant BOOLEAN,
    win BOOLEAN,
    team_id BIGINT,
    opp_team_id BIGINT,
    team_key VARCHAR,
    opp_team_key VARCHAR,
    duration_s INTEGER,
    start_time TIMESTAMP,
    league_id INTEGER,
    league_name VARCHAR,
    patch VARCHAR,
    series_id VARCHAR,
    kills INTEGER,                    -- opendota: kills
    deaths INTEGER,                   -- deaths
    last_hits INTEGER,                -- last_hits
    denies INTEGER,                   -- denies
    gpm INTEGER,                      -- gold_per_min
    madstone_proxy INTEGER,           -- item_uses.madstone_bundle  ** PROXY — calibrate **
    tower_kills INTEGER,              -- towers_killed
    obs_placed INTEGER,               -- obs_placed
    camps_stacked INTEGER,            -- camps_stacked
    rune_pickups INTEGER,             -- rune_pickups
    watchers_taken INTEGER,           -- ability_uses.ability_lamp_use
    smokes_used INTEGER,              -- item_uses.smoke_of_deceit
    lotuses_grabbed INTEGER,          -- NO SOURCE — always NULL
    lotus_proxy_famango INTEGER,      -- item_uses.famango + great_famango (consumption proxy)
    roshan_kills INTEGER,             -- roshans_killed
    teamfight_participation DOUBLE,   -- 0..1
    stuns_s DOUBLE,                   -- stuns (seconds)
    tormentor_kills INTEGER,          -- killed['npc_dota_miniboss']
    first_blood BOOLEAN,              -- firstblood_claimed
    courier_kills INTEGER,            -- courier_kills
    parsed BOOLEAN,
    source VARCHAR DEFAULT 'opendota',
    PRIMARY KEY (match_id, account_id)
);

-- Bio facts from Liquipedia player infoboxes (CC-BY-SA) + OpenDota /proPlayers.
CREATE TABLE fan.player_bios (
    account_id BIGINT PRIMARY KEY,
    real_name VARCHAR,
    birth_date DATE,
    country VARCHAR,          -- Liquipedia nationality (full name)
    country_code VARCHAR,     -- OpenDota ISO code
    history VARCHAR,          -- JSON [[period, team], ...] from the infobox
    source VARCHAR DEFAULT 'liquipedia+opendota'
);

-- pub-matchmaking rank (OpenDota /players): rank_tier tens = medal (8 = Immortal),
-- ones = stars; leaderboard_rank = Immortal ladder position where public.
CREATE TABLE fan.player_profiles (
    account_id BIGINT PRIMARY KEY,
    rank_tier INTEGER,
    leaderboard_rank INTEGER,
    plus BOOLEAN,
    steam_name VARCHAR,
    source VARCHAR DEFAULT 'opendota_players'
);

CREATE TABLE fan.tormentor_xcheck (
    match_id BIGINT,
    account_id BIGINT,
    tormentor_kills INTEGER,
    source VARCHAR DEFAULT 'datdota',
    PRIMARY KEY (match_id, account_id)
);

CREATE TABLE fan.stratz_supplement (
    match_id BIGINT,
    account_id BIGINT,
    runes_bottled INTEGER,
    runes_taken INTEGER,
    raw VARCHAR,
    PRIMARY KEY (match_id, account_id)
);
