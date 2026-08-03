-- Scoring + analysis views. Constants are the TI2026 values from TI2026_Rules.md — do
-- not "fix" them from other years (TI2025 used different numbers).

CREATE OR REPLACE VIEW fan.fantasy_points AS
SELECT
    s.match_id, s.account_id, s.team_key, s.opp_team_key, s.hero_id, s.is_radiant, s.win,
    s.duration_s, s.start_time, s.league_id, s.league_name, s.patch, s.series_id, s.parsed,
    s.kills * 107.0                                                        AS pts_kills,
    1950.0 - 195.0 * s.deaths                                              AS pts_deaths,
    (s.last_hits + s.denies) * 3.0                                         AS pts_cs,
    s.gpm * 2.0                                                            AS pts_gpm,
    s.madstone_proxy * 13.0 * coalesce(cal_mad.multiplier, 1.0)            AS pts_madstone,
    s.tower_kills * 352.0                                                  AS pts_towers,
    s.obs_placed * 117.0                                                   AS pts_wards,
    s.camps_stacked * 234.0                                                AS pts_stacks,
    s.rune_pickups * 141.0                                                 AS pts_runes,
    s.watchers_taken * 147.0 * coalesce(cal_watch.multiplier, 1.0)         AS pts_watchers,
    s.smokes_used * 293.0                                                  AS pts_smokes,
    s.lotuses_grabbed * 176.0                                              AS pts_lotus,
    s.roshan_kills * 1172.0                                                AS pts_roshan,
    s.teamfight_participation * 2124.0 * coalesce(cal_tfp.multiplier, 1.0) AS pts_tfp,
    s.stuns_s * 10.0 * coalesce(cal_stun.multiplier, 1.0)                  AS pts_stuns,
    s.tormentor_kills * 879.0                                              AS pts_tormentor,
    CASE WHEN s.first_blood THEN 1934.0 ELSE 0.0 END                       AS pts_firstblood,
    s.courier_kills * 703.0                                                AS pts_courier,
    (s.lotuses_grabbed IS NULL)                                            AS lotus_missing,
    coalesce(pts_kills, 0) + coalesce(pts_deaths, 0) + coalesce(pts_cs, 0)
      + coalesce(pts_gpm, 0) + coalesce(pts_madstone, 0) + coalesce(pts_towers, 0)
      + coalesce(pts_wards, 0) + coalesce(pts_stacks, 0) + coalesce(pts_runes, 0)
      + coalesce(pts_watchers, 0) + coalesce(pts_smokes, 0) + coalesce(pts_lotus, 0)
      + coalesce(pts_roshan, 0) + coalesce(pts_tfp, 0) + coalesce(pts_stuns, 0)
      + coalesce(pts_tormentor, 0) + coalesce(pts_firstblood, 0) + coalesce(pts_courier, 0)
                                                                           AS pts_total
FROM fan.player_game_stats s
LEFT JOIN meta.stat_calibration cal_mad ON cal_mad.stat = 'madstone_proxy'
LEFT JOIN meta.stat_calibration cal_tfp ON cal_tfp.stat = 'teamfight_participation'
LEFT JOIN meta.stat_calibration cal_stun ON cal_stun.stat = 'stuns_s'
LEFT JOIN meta.stat_calibration cal_watch ON cal_watch.stat = 'watchers_taken';

-- Per (player, series): sum of the top-2 game scores — the building block the in-client
-- rule uses ("top two scoring games within a series"). Games with no series mapping
-- count as their own single-game series.
CREATE OR REPLACE VIEW fan.player_series_points AS
WITH ranked AS (
    SELECT
        fp.account_id,
        coalesce(fp.series_id, g.series_id, 'solo:' || fp.match_id) AS series_id,
        fp.match_id, fp.pts_total, fp.start_time, fp.league_id, fp.league_name,
        row_number() OVER (
            PARTITION BY fp.account_id, coalesce(fp.series_id, g.series_id, 'solo:' || fp.match_id)
            ORDER BY fp.pts_total DESC
        ) AS rn
    FROM fan.fantasy_points fp
    LEFT JOIN pred.games g USING (match_id)
    WHERE fp.parsed
)
SELECT
    account_id,
    series_id,
    count(*)                                        AS games_played,
    sum(pts_total) FILTER (WHERE rn <= 2)           AS pts_series_top2,
    avg(pts_total)                                  AS pts_avg_game,
    min(start_time)                                 AS series_start,
    any_value(league_name)                          AS league_name
FROM ranked
GROUP BY account_id, series_id;

-- Team form over trailing windows (relative to now; recompute-on-query is fine).
CREATE OR REPLACE VIEW pred.team_form AS
WITH team_games AS (
    SELECT t.team_key, g.match_id, g.start_time,
           (g.radiant_team_key = t.team_key) = g.radiant_win AS won
    FROM pred.teams t
    JOIN pred.games g
      ON t.team_key IN (g.radiant_team_key, g.dire_team_key)
)
SELECT team_key, w.days AS window_days,
       count(*) AS games,
       sum(CASE WHEN won THEN 1 ELSE 0 END) AS wins,
       round(avg(CASE WHEN won THEN 1.0 ELSE 0.0 END), 3) AS winrate
FROM team_games, (VALUES (30), (90), (365)) AS w(days)
WHERE start_time >= now()::TIMESTAMP - to_days(w.days)
GROUP BY team_key, w.days;

-- Hero draft-priority board: picks/bans/contest rate across TI-pro matches, and
-- team-level winrate when picked (via pred.games radiant_win).
CREATE OR REPLACE VIEW pred.hero_contest AS
WITH drafted_matches AS (
    SELECT count(DISTINCT match_id) AS n FROM pred.draft_actions
)
SELECT
    d.hero_id,
    coalesce(h.localized_name, 'hero_' || d.hero_id)   AS hero,
    count(*) FILTER (WHERE d.is_pick)                  AS picks,
    count(*) FILTER (WHERE NOT d.is_pick)              AS bans,
    round(100.0 * count(*) / (SELECT n FROM drafted_matches), 1)          AS contest_rate_pct,
    round(100.0 * avg(CASE WHEN d.is_pick AND g.radiant_win IS NOT NULL
                           THEN CASE WHEN (d.team = 0) = g.radiant_win THEN 1.0 ELSE 0.0 END
                      END), 1)                                            AS pick_winrate_pct,
    max(g.start_time)                                  AS last_contested
FROM pred.draft_actions d
LEFT JOIN pred.games g USING (match_id)
LEFT JOIN meta.heroes h USING (hero_id)
GROUP BY 1, 2;

-- Head-to-head among the 16 TI teams (game-level).
CREATE OR REPLACE VIEW pred.h2h AS
SELECT
    least(g.radiant_team_key, g.dire_team_key)    AS team_a,
    greatest(g.radiant_team_key, g.dire_team_key) AS team_b,
    count(*) AS games,
    sum(CASE WHEN (g.radiant_team_key = least(g.radiant_team_key, g.dire_team_key)) = g.radiant_win
             THEN 1 ELSE 0 END) AS a_wins,
    max(g.start_time) AS last_played
FROM pred.games g
WHERE g.radiant_team_key IS NOT NULL AND g.dire_team_key IS NOT NULL
  AND g.radiant_team_key <> g.dire_team_key
GROUP BY 1, 2;
