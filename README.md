# Smogon Big Data ETL Pipeline

A modular 6-step ETL pipeline that ingests Pokemon Showdown competitive battle data from Smogon's public statistics archives, processes it into a normalized relational schema, and stores it in a local SQLite database ready for BI dashboards (Looker Studio) or cloud migration (BigQuery).

## Architecture

```
01_discover.py       →  Scrapes smogon.com/stats to discover all available months, formats, and Elo tiers
02_ingest_usage.py   →  Downloads root-level usage .txt files → `usage_stats` table
03_ingest_chaos.py   →  Downloads chaos/*.json → 8 tables (abilities, items, moves, spreads, tera_types, teammates, checks_counters, pokemon_details)
04_ingest_leads.py   →  Downloads leads/*.txt → `leads` table
05_ingest_metagame.py →  Downloads metagame/*.txt → `metagame` table
06_ingest_replays.py →  Fetches live replays from Pokemon Showdown API → `replays` + `replay_teams` tables
```

Each script is independently runnable, incrementally skips already-ingested data, and caches raw downloads locally.

## Datasets Consumed

### 1) Smogon Monthly Usage Stats
- **URL pattern**: `https://www.smogon.com/stats/{YYYY-MM}/{format}-{elo}.txt[.gz]`
- **Example**: `https://www.smogon.com/stats/2026-04/gen9ou-1825.txt`
- **Format**: Tabular text with columns: Rank, Pokemon, Usage %, Raw count, Raw %, Real count, Real %
- **Available**: Nov 2014 – present (120+ months)
- **Number of files**: ~46 Gen 9 formats × 4–5 Elo tiers × 42 months ≈ 8,000+

### 2) Smogon Chaos JSON
- **URL pattern**: `https://www.smogon.com/stats/{YYYY-MM}/chaos/{format}-{elo}.json[.gz]`
- **Example**: `https://www.smogon.com/stats/2026-04/chaos/gen9ou-1825.json`
- **Format**: Nested JSON with per-Pokemon data:
  - `Raw count` — number of times the Pokemon appeared
  - `Viability Ceiling` — [raw_count_in_tier, viability_score, ...]
  - `Abilities` — weighted occurrence scores per ability
  - `Items` — weighted occurrence scores per item
  - `Moves` — weighted occurrence scores per move
  - `Spreads` — weighted occurrence scores per EV spread
  - `Tera Types` — weighted occurrence scores per tera type
  - `Teammates` — co-occurrence matrix of weighted scores
  - `Checks and Counters` — `{counter: {n: raw_encounters, p: KO_prob, d: switch_prob}}`
- **Notes**: Values are raw weighted occurrence counts, not percentages. Item/ability names are compact lowercase (e.g. `assaultvest`, `goodasgold`).
- **Typical size**: 100KB–25MB per file uncompressed

### 3) Smogon Leads Stats
- **URL pattern**: `https://www.smogon.com/stats/{YYYY-MM}/leads/{format}-{elo}.txt[.gz]`
- **Format**: Tabular text: Rank, Pokemon, Usage %, Raw count
- **What it captures**: Which Pokemon are most commonly sent out first in battle

### 4) Smogon Metagame Stats
- **URL pattern**: `https://www.smogon.com/stats/{YYYY-MM}/metagame/{format}-{elo}.txt[.gz]`
- **Format**: Key-value text: playstyle and usage percentage
- **Examples**: `offense 38.76%`, `balance 35.02%`, `stall 9.55%`, `weatherless 90.21%`
- **Includes**: Stalliness histogram, weather/terrain breakdowns, team style distribution

### 5) Pokemon Showdown Replay API
- **Search endpoint**: `https://replay.pokemonshowdown.com/search.json?format=gen9ou&page=N`
- **Replay logs**: `https://replay.pokemonshowdown.com/{replay_id}.log`
- **Format**: Battle protocol text with `|player|`, `|switch|`, `|win|` lines
- **What it contains**: Live match metadata (format, rating, players, upload time) and full battle logs from which team rosters and winners can be reconstructed

## Database Schema

### Dimension Tables
| Table | Columns | Description |
|-------|---------|-------------|
| `formats` | format_id, generation, tier, name | Format metadata (e.g. gen9ou, gen9ubers) |
| `months` | month, total_battles | Monthly snapshot metadata |
| `elo_tiers` | elo_tier | Rating cutoffs (0, 1500, 1695, 1760, 1825) |

### Fact Tables
| Table | Rows* | Key Columns | Source |
|-------|-------|-------------|--------|
| `usage_stats` | 3,052 | month, format_id, elo_tier, pokemon, rank, usage_pct, raw_count, raw_pct, real_count, real_pct | Root .txt |
| `pokemon_details` | 1,409 | month, format_id, elo_tier, pokemon, raw_count, viability_ceiling | Chaos JSON |
| `abilities` | 3,048 | month, format_id, elo_tier, pokemon, ability, usage_pct | Chaos JSON |
| `items` | 68,131 | month, format_id, elo_tier, pokemon, item, usage_pct | Chaos JSON |
| `moves` | 78,093 | month, format_id, elo_tier, pokemon, move, usage_pct | Chaos JSON |
| `spreads` | 771,069 | month, format_id, elo_tier, pokemon, nature, hp/atk/def/spa/spd/spe, spread_str, usage_pct | Chaos JSON |
| `tera_types` | 24,313 | month, format_id, elo_tier, pokemon, tera_type, usage_pct | Chaos JSON |
| `teammates` | 353,826 | month, format_id, elo_tier, pokemon1, pokemon2, usage_pct | Chaos JSON |
| `checks_counters` | 101,050 | month, format_id, elo_tier, pokemon, counter_pokemon, score, ko_pct, switch_pct | Chaos JSON |
| `leads` | 3,000 | month, format_id, elo_tier, pokemon, rank, usage_pct, raw_count | Leads .txt |
| `metagame` | 212 | month, format_id, elo_tier, playstyle, usage_pct | Metagame .txt |
| `replays` | — | replay_id, format_id, rating, player1, player2, uploadtime, month | Replay API |
| `replay_teams` | — | replay_id, side, pokemon, won | Replay API |

*Per format (gen9ou), one month, all Elo tiers. Full dataset across all formats and months scales to 100M+ rows.

## Requirements

Python 3.11+ with:
```
requests, aiohttp, tqdm
```
(All standard packages; no heavy data-science dependencies.)

## Quick Start

```bash
# 1. Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Run the full pipeline for one format (recommended first run):
#    This discovers all months, ingests usage, chaos, leads, metagame, and replays
#    for gen9ou only (~45 min depending on connection speed)
python3 -m pipeline.run_pipeline --format gen9ou

# 3. Run for all discovered formats (this will take hours):
#    The pipeline skips already-ingested data on re-runs
python3 -m pipeline.run_pipeline

# 4. Run individual steps:
python3 -m pipeline.01_discover
python3 -m pipeline.02_ingest_usage --format gen9ou
python3 -m pipeline.03_ingest_chaos --format gen9ou
python3 -m pipeline.04_ingest_leads --format gen9ou
python3 -m pipeline.05_ingest_metagame --format gen9ou
python3 -m pipeline.06_ingest_replays --format gen9ou --pages 3
```

### Targeted Runs

Each script accepts `--format` to process a single format:

```bash
# Ingest just OverUsed and Ubers
python3 -m pipeline.02_ingest_usage --format gen9ou
python3 -m pipeline.03_ingest_chaos --format gen9ubers
```

## What Gets Generated

### SQLite Database
`./pokemon_stats.db` — the full processed data warehouse (14+ tables). Currently contains 1.4M+ rows for gen9ou (one month × 4 Elo tiers).

### Raw Data Cache
`./data/{type}/{month}/` — downloaded files cached to avoid re-fetching. Delete to force re-download.

## Looker Studio Integration

Export tables to CSV, upload to Google Sheets, and connect Looker Studio:

```bash
# Export key views
sqlite3 pokemon_stats.db -header -csv \
  "SELECT * FROM usage_stats WHERE format_id='gen9ou'" > gen9ou_usage.csv

sqlite3 pokemon_stats.db -header -csv \
  "SELECT * FROM teammates WHERE format_id='gen9ou'" > gen9ou_teammates.csv

sqlite3 pokemon_stats.db -header -csv \
  "SELECT * FROM metagame WHERE format_id='gen9ou'" > gen9ou_metagame.csv
```

**Dashboard dimensions for Looker Studio:**
- Pokemon usage trends over time (line charts)
- Metagame style evolution (area charts)
- Teammate network analysis (force-directed graphs)
- Ability/item/move distribution (pie/bar charts)
- Checks & counters heatmap tables
- Elo tier comparisons (usage shifts at different skill levels)

## Cloud Migration Path (Google Cloud)

| Component | Local | GCP Equivalent |
|-----------|-------|----------------|
| Database | SQLite | BigQuery |
| File storage | `./data/` | Cloud Storage (GCS) |
| Execution | Local Python | Cloud Run / Dataflow |
| Orchestration | `run_pipeline.py` | Cloud Workflows / Composer |

Migration steps:
1. Replace SQLite DDL with BigQuery DDL (standard SQL)
2. Upload raw files to GCS buckets
3. Deploy scripts as Cloud Run jobs with GCS triggers
4. Replace `requests` downloads with `google-cloud-storage` reads

## Database Query Examples

```sql
-- Top 10 most used Pokemon in Gen 9 OU (2026-04, all Elo)
SELECT pokemon, usage_pct, raw_count
FROM usage_stats
WHERE format_id = 'gen9ou' AND month = '2026-04' AND elo_tier = 0
ORDER BY usage_pct DESC
LIMIT 10;

-- Most common teammates of Great Tusk
SELECT pokemon2, usage_pct
FROM teammates
WHERE pokemon1 = 'Great Tusk' AND format_id = 'gen9ou' AND month = '2026-04' AND elo_tier = 1825
ORDER BY usage_pct DESC
LIMIT 10;

-- Metagame breakdown for a given month
SELECT playstyle, usage_pct
FROM metagame
WHERE format_id = 'gen9ou' AND month = '2026-04' AND elo_tier = 1825
ORDER BY usage_pct DESC;

-- Most common items on Gholdengo
SELECT item, usage_pct
FROM items
WHERE pokemon = 'Gholdengo' AND format_id = 'gen9ou' AND month = '2026-04' AND elo_tier = 1825
ORDER BY usage_pct DESC
LIMIT 10;
```
