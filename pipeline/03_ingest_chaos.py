import re
import logging
import json
import requests
import gzip
import argparse
from tqdm import tqdm

from .config import SMOGON_BASE, DATA_DIR, BATCH_SIZE
from .db import get_conn

logger = logging.getLogger(__name__)


def fetch_chaos_json(month, format_id, elo_tier):
    cache_dir = DATA_DIR / "chaos" / month
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{format_id}-{elo_tier}.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    for url_ext in [".json", ".json.gz"]:
        url = f"{SMOGON_BASE}/{month}/chaos/{format_id}-{elo_tier}{url_ext}"
        try:
            resp = requests.get(url, timeout=120)
            if resp.status_code == 200:
                raw = resp.content
                data = json.loads(gzip.decompress(raw) if url_ext == ".json.gz" else raw)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                return data
        except requests.RequestException:
            continue
    return None


def parse_spread_key(key):
    m = re.match(r"(\w+):([\d/]+)", key.strip())
    if not m:
        return None, (0, 0, 0, 0, 0, 0)
    nature = m.group(1)
    evs = [int(x) if x.strip() else 0 for x in m.group(2).split("/")]
    evs = (evs + [0] * 6)[:6]
    return nature, evs


def process_chaos_data(data, month, format_id, elo_tier, conn):
    if not data:
        return
    pokemon_data = data.get("data", {})
    if not pokemon_data:
        return
    batch_details = []
    batch_abilities = []
    batch_items = []
    batch_moves = []
    batch_spreads = []
    batch_tera = []
    batch_teammates = []
    batch_checks = []
    for poke_name, info in pokemon_data.items():
        if not isinstance(info, dict):
            continue
        raw_count = info.get("Raw count")
        vc_arr = info.get("Viability Ceiling")
        viability = vc_arr[1] if isinstance(vc_arr, list) and len(vc_arr) >= 2 else None
        if isinstance(raw_count, str):
            try:
                raw_count = int(raw_count.replace(",", ""))
            except (ValueError, AttributeError):
                raw_count = None
        batch_details.append((month, format_id, elo_tier, poke_name, raw_count, None, viability))
        for section in ("Abilities", "Items", "Moves", "Tera Types", "Teammates", "Spreads", "Checks and Counters"):
            data_section = info.get(section)
            if not isinstance(data_section, dict):
                continue
            for key, val in data_section.items():
                if section == "Checks and Counters":
                    if isinstance(val, dict):
                        batch_checks.append((
                            month, format_id, elo_tier, poke_name, key,
                            float(val.get("n", 0)), float(val.get("p", 0)), float(val.get("d", 0))
                        ))
                    elif isinstance(val, (int, float)):
                        batch_checks.append((month, format_id, elo_tier, poke_name, key, float(val), None, None))
                    continue
                if not isinstance(val, (int, float)):
                    continue
                score = float(val)
                if section == "Abilities":
                    batch_abilities.append((month, format_id, elo_tier, poke_name, key, score))
                elif section == "Items":
                    batch_items.append((month, format_id, elo_tier, poke_name, key, score))
                elif section == "Moves":
                    batch_moves.append((month, format_id, elo_tier, poke_name, key, score))
                elif section == "Tera Types":
                    batch_tera.append((month, format_id, elo_tier, poke_name, key, score))
                elif section == "Spreads":
                    nature, evs = parse_spread_key(key)
                    batch_spreads.append((month, format_id, elo_tier, poke_name, nature, *evs, key, score))
                elif section == "Teammates":
                    p1, p2 = sorted([poke_name, key])
                    batch_teammates.append((month, format_id, elo_tier, p1, p2, score))
    inserts = [
        (batch_details, "INSERT OR REPLACE INTO pokemon_details (month, format_id, elo_tier, pokemon, raw_count, avg_weight, viability_ceiling) VALUES (?,?,?,?,?,?,?)"),
        (batch_abilities, "INSERT OR REPLACE INTO abilities (month, format_id, elo_tier, pokemon, ability, usage_pct) VALUES (?,?,?,?,?,?)"),
        (batch_items, "INSERT OR REPLACE INTO items (month, format_id, elo_tier, pokemon, item, usage_pct) VALUES (?,?,?,?,?,?)"),
        (batch_moves, "INSERT OR REPLACE INTO moves (month, format_id, elo_tier, pokemon, move, usage_pct) VALUES (?,?,?,?,?,?)"),
        (batch_spreads, "INSERT OR REPLACE INTO spreads (month, format_id, elo_tier, pokemon, nature, hp, atk, def, spa, spd, spe, spread_str, usage_pct) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"),
        (batch_tera, "INSERT OR REPLACE INTO tera_types (month, format_id, elo_tier, pokemon, tera_type, usage_pct) VALUES (?,?,?,?,?,?)"),
        (batch_teammates, "INSERT OR REPLACE INTO teammates (month, format_id, elo_tier, pokemon1, pokemon2, usage_pct) VALUES (?,?,?,?,?,?)"),
        (batch_checks, "INSERT OR REPLACE INTO checks_counters (month, format_id, elo_tier, pokemon, counter_pokemon, score, ko_pct, switch_pct) VALUES (?,?,?,?,?,?,?,?)"),
    ]
    for batch, sql in inserts:
        for i in range(0, len(batch), BATCH_SIZE):
            conn.executemany(sql, batch[i : i + BATCH_SIZE])


def run(format_filter=None):
    with get_conn() as conn:
        if format_filter:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'chaos' AND format_id = ?",
                (format_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'chaos'"
            ).fetchall()
        existing = set()
        for r in conn.execute("SELECT DISTINCT month, format_id, elo_tier FROM pokemon_details"):
            existing.add((r["month"], r["format_id"], r["elo_tier"]))
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All chaos data already ingested")
        return
    logger.info("Ingesting %d chaos JSON files", len(todo))
    for month, fmt, elo in tqdm(todo, desc="Chaos JSON"):
        data = fetch_chaos_json(month, fmt, elo)
        if not data:
            logger.warning("No chaos data for %s %s-%d", month, fmt, elo)
            continue
        with get_conn() as conn:
            process_chaos_data(data, month, fmt, elo, conn)
    logger.info("Chaos ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter (e.g., gen9ou)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
