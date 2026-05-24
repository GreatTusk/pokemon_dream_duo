-- =============================================================================
-- LOOKER STUDIO DASHBOARD VIEWS
-- Pre-joined, aggregated views for fast dashboard rendering
-- =============================================================================

-- V1: Pokemon usage trends over time (line charts)
CREATE OR REPLACE VIEW smogon_dw.v_pokemon_trends AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  rank,
  usage_pct,
  raw_count,
  real_count
FROM smogon_dw.fact_usage
WHERE elo_tier = 0  -- All tiers
ORDER BY month, format_id, rank;

-- V2: Top N Pokemon per format/month (bar charts)
CREATE OR REPLACE VIEW smogon_dw.v_top_pokemon AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  usage_pct,
  raw_count,
  RANK() OVER (PARTITION BY month, format_id, elo_tier ORDER BY usage_pct DESC) AS rank_bq
FROM smogon_dw.fact_usage
WHERE usage_pct > 0
ORDER BY month, format_id, elo_tier, usage_pct DESC;

-- V3: Metagame style evolution (area charts)
CREATE OR REPLACE VIEW smogon_dw.v_metagame_trends AS
SELECT
  month,
  format_id,
  elo_tier,
  playstyle,
  usage_pct
FROM smogon_dw.fact_metagame
ORDER BY month, format_id, elo_tier, usage_pct DESC;

-- V4: Teammate network (for Sankey / force-directed graphs)
CREATE OR REPLACE VIEW smogon_dw.v_teammates AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon1,
  pokemon2,
  usage_pct
FROM smogon_dw.fact_teammates
WHERE usage_pct > 1.0  -- filter noise
ORDER BY usage_pct DESC;

-- V5: Item / ability / move distribution (pie charts)
CREATE OR REPLACE VIEW smogon_dw.v_item_distribution AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  item,
  usage_pct
FROM smogon_dw.fact_items
ORDER BY month, format_id, pokemon, usage_pct DESC;

CREATE OR REPLACE VIEW smogon_dw.v_ability_distribution AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  ability,
  usage_pct
FROM smogon_dw.fact_abilities
ORDER BY month, format_id, pokemon, usage_pct DESC;

CREATE OR REPLACE VIEW smogon_dw.v_move_distribution AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  move,
  usage_pct
FROM smogon_dw.fact_moves
ORDER BY month, format_id, pokemon, usage_pct DESC;

-- V6: Checks & counters heatmap
CREATE OR REPLACE VIEW smogon_dw.v_checks_counters AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  counter_pokemon,
  score,
  ko_pct,
  switch_pct
FROM smogon_dw.fact_checks_counters
ORDER BY score DESC;

-- V7: Elo tier comparison (different skill levels)
CREATE OR REPLACE VIEW smogon_dw.v_usage_by_elo AS
SELECT
  month,
  format_id,
  elo_tier,
  pokemon,
  usage_pct,
  raw_count
FROM smogon_dw.fact_usage
WHERE elo_tier > 0
ORDER BY month, format_id, elo_tier, usage_pct DESC;

-- V8: Replay win rates by Pokemon
CREATE OR REPLACE VIEW smogon_dw.v_replay_winrates AS
SELECT
  rp.format_id,
  rp.month,
  rt.pokemon,
  COUNT(*) AS total_games,
  SUM(rt.won) AS wins,
  ROUND(SAFE_DIVIDE(SUM(rt.won), COUNT(*)) * 100, 2) AS win_rate_pct
FROM smogon_dw.fact_replay_teams rt
JOIN smogon_dw.fact_replays rp ON rt.replay_id = rp.replay_id
GROUP BY rp.format_id, rp.month, rt.pokemon
ORDER BY total_games DESC;

-- V9: Format comparison overview
CREATE OR REPLACE VIEW smogon_dw.v_format_overview AS
SELECT
  format_id,
  month,
  COUNT(DISTINCT pokemon) AS unique_pokemon,
  ROUND(AVG(usage_pct), 3) AS avg_usage_pct,
  MAX(raw_count) AS max_raw_count
FROM smogon_dw.fact_usage
WHERE elo_tier = 0
GROUP BY format_id, month
ORDER BY format_id, month;
