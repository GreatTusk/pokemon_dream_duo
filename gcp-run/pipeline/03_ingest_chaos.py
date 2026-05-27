import re
import logging
import json
import argparse
from tqdm import tqdm

from .config import FLUSH_THRESHOLD
from .warehouse_client import WarehouseClient
from .storage_client import StorageClient
from .db import (
    SCHEMA_MAP, TABLE_POKEMON_DETAILS, TABLE_ABILITIES, TABLE_ITEMS,
    TABLE_MOVES, TABLE_SPREADS, TABLE_TERA_TYPES, TABLE_TEAMMATES,
    TABLE_CHECKS_COUNTERS, TABLE_DISCOVERED_SOURCES,
)

CHAOS_TABLES = [
    TABLE_POKEMON_DETAILS, TABLE_ABILITIES, TABLE_ITEMS, TABLE_MOVES,
    TABLE_SPREADS, TABLE_TERA_TYPES, TABLE_TEAMMATES, TABLE_CHECKS_COUNTERS,
]

logger = logging.getLogger(__name__)


def load_chaos_json(storage, month, format_id, elo_tier):
    cache_name = storage.cache_path("chaos", month, format_id, elo_tier, "json")
    if storage.exists(cache_name):
        logger.debug("Cache hit: gs://%s/%s", storage.bucket_name, cache_name)
        raw = storage.download_bytes(cache_name)
        return json.loads(raw)
    logger.debug("Not found in GCS: gs://%s/%s", storage.bucket_name, cache_name)
    return None


def parse_spread_key(key):
    m = re.match(r"(\w+):([\d/]+)", key.strip())
    if not m:
        return None, (0, 0, 0, 0, 0, 0)
    nature = m.group(1)
    evs = [int(x) if x.strip() else 0 for x in m.group(2).split("/")]
    evs = (evs + [0] * 6)[:6]
    return nature, evs


def process_chaos_data(data, month, format_id, elo_tier):
    if not data:
        return None
    pokemon_data = data.get("data", {})
    if not pokemon_data:
        return None
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
    return {
        TABLE_POKEMON_DETAILS: batch_details,
        TABLE_ABILITIES: batch_abilities,
        TABLE_ITEMS: batch_items,
        TABLE_MOVES: batch_moves,
        TABLE_SPREADS: batch_spreads,
        TABLE_TERA_TYPES: batch_tera,
        TABLE_TEAMMATES: batch_teammates,
        TABLE_CHECKS_COUNTERS: batch_checks,
    }


def run(format_filter=None):
    wh = WarehouseClient()
    storage = StorageClient()
    for tname in CHAOS_TABLES:
        wh.ensure_table(tname, SCHEMA_MAP[tname])

    rows = wh.query(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_DISCOVERED_SOURCES)}` WHERE source_type = 'chaos'"
    )
    if format_filter:
        rows = [r for r in rows if r["format_id"] == format_filter]

    existing = wh.query_set(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_POKEMON_DETAILS)}`"
    )
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows
            if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All chaos data already ingested")
        return
    logger.info("Ingesting %d chaos JSON files", len(todo))

    accum = {tname: [] for tname in CHAOS_TABLES}
    for month, fmt, elo in tqdm(todo, desc="Chaos JSON"):
        data = load_chaos_json(storage, month, fmt, elo)
        if not data:
            logger.warning("No chaos data for %s %s-%d", month, fmt, elo)
            continue
        result = process_chaos_data(data, month, fmt, elo)
        if result is None:
            continue
        for tname in CHAOS_TABLES:
            accum[tname].extend(result[tname])
        for tname in CHAOS_TABLES:
            if len(accum[tname]) >= FLUSH_THRESHOLD:
                wh.write_rows(tname, SCHEMA_MAP[tname], accum[tname])
                accum[tname] = []

    for tname in CHAOS_TABLES:
        if accum[tname]:
            wh.write_rows(tname, SCHEMA_MAP[tname], accum[tname])
    logger.info("Chaos ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter (e.g., gen9ou)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
