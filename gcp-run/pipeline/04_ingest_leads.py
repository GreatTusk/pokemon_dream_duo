import re
import logging
import argparse
from tqdm import tqdm

from .config import FLUSH_THRESHOLD
from .warehouse_client import WarehouseClient
from .storage_client import StorageClient
from .db import SCHEMA_MAP, TABLE_LEADS, TABLE_DISCOVERED_SOURCES

logger = logging.getLogger(__name__)


def load_leads_text(storage, month, format_id, elo_tier):
    cache_name = storage.cache_path("leads", month, format_id, elo_tier, "txt")
    if storage.exists(cache_name):
        logger.debug("Cache hit: gs://%s/%s", storage.bucket_name, cache_name)
        return storage.download_text(cache_name)
    logger.debug("Not found in GCS: gs://%s/%s", storage.bucket_name, cache_name)
    return None


def parse_leads_table(text):
    if not text:
        return []
    header_seen = False
    results = []
    for line in text.splitlines():
        if "| Rank | Pokemon" in line:
            header_seen = True
            continue
        if header_seen:
            if re.match(r"^\+\s*---", line):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                try:
                    rank = int(parts[1])
                except ValueError:
                    continue
                try:
                    usage_pct = float(parts[3].rstrip("%"))
                except ValueError:
                    usage_pct = 0.0
                try:
                    raw_count = int(parts[4].replace(",", ""))
                except ValueError:
                    raw_count = 0
                results.append((rank, parts[2], usage_pct, raw_count))
    return results


def run(format_filter=None):
    wh = WarehouseClient()
    storage = StorageClient()
    wh.ensure_table(TABLE_LEADS, SCHEMA_MAP[TABLE_LEADS])

    rows = wh.query(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_DISCOVERED_SOURCES)}` WHERE source_type = 'leads'"
    )
    if format_filter:
        rows = [r for r in rows if r["format_id"] == format_filter]

    existing = wh.query_set(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_LEADS)}`"
    )
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows
            if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All leads data already ingested")
        return
    logger.info("Ingesting %d leads files", len(todo))
    accum = []
    for month, fmt, elo in tqdm(todo, desc="Leads"):
        text = load_leads_text(storage, month, fmt, elo)
        if not text:
            continue
        parsed = parse_leads_table(text)
        if not parsed:
            continue
        for r, p, u, rc in parsed:
            accum.append({"month": month, "format_id": fmt, "elo_tier": elo,
                          "pokemon": p, "rank": r, "usage_pct": u, "raw_count": rc})
        if len(accum) >= FLUSH_THRESHOLD:
            wh.write_rows(TABLE_LEADS, SCHEMA_MAP[TABLE_LEADS], accum)
            accum = []
    if accum:
        wh.write_rows(TABLE_LEADS, SCHEMA_MAP[TABLE_LEADS], accum)
    logger.info("Leads ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
