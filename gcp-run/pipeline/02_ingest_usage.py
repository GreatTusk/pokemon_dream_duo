import re
import logging
import requests
import argparse
import gzip
from tqdm import tqdm

from .config import SMOGON_BASE, FLUSH_THRESHOLD
from .warehouse_client import WarehouseClient
from .storage_client import StorageClient
from .db import SCHEMA_MAP, TABLE_USAGE_STATS, TABLE_DISCOVERED_SOURCES

logger = logging.getLogger(__name__)


def fetch_usage_text(storage, month, format_id, elo_tier):
    cache_name = storage.cache_path("usage", month, format_id, elo_tier, "txt")
    if storage.exists(cache_name):
        logger.debug("Cache hit: gs://%s/%s", storage.bucket_name, cache_name)
        return storage.download_text(cache_name)
    for ext in [".txt", ".txt.gz"]:
        url = f"{SMOGON_BASE}/{month}/{format_id}-{elo_tier}{ext}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                raw = resp.content
                if ext == ".txt.gz":
                    text = gzip.decompress(raw).decode("utf-8")
                else:
                    text = raw.decode("utf-8")
                storage.upload_text(text, cache_name)
                return text
        except requests.RequestException:
            continue
    return None


def parse_usage_table(text):
    if not text:
        return [], None
    lines = text.splitlines()
    total_battles = None
    header_seen = False
    data_lines = []
    for line in lines:
        tm = re.search(r"Total battles:\s*(\d+)", line)
        if tm:
            total_battles = int(tm.group(1))
        if "| Rank | Pokemon" in line:
            header_seen = True
            continue
        if header_seen:
            if re.match(r"^\+\s*---", line):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 7:
                try:
                    rank = int(parts[1])
                except ValueError:
                    continue
                pokemon = parts[2]
                try:
                    usage_pct = float(parts[3].rstrip("%"))
                except ValueError:
                    usage_pct = 0.0
                try:
                    raw_count = int(parts[4].replace(",", ""))
                except ValueError:
                    raw_count = 0
                try:
                    raw_pct = float(parts[5].rstrip("%"))
                except ValueError:
                    raw_pct = 0.0
                try:
                    real_count = int(parts[6].replace(",", ""))
                except ValueError:
                    real_count = 0
                try:
                    real_pct = float(parts[7].rstrip("%"))
                except ValueError:
                    real_pct = 0.0
                data_lines.append((rank, pokemon, usage_pct, raw_count, raw_pct, real_count, real_pct))
    return data_lines, total_battles


def run(format_filter=None):
    wh = WarehouseClient()
    storage = StorageClient()
    wh.ensure_table(TABLE_USAGE_STATS, SCHEMA_MAP[TABLE_USAGE_STATS])

    rows = wh.query(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_DISCOVERED_SOURCES)}` WHERE source_type = 'usage'"
    )
    if format_filter:
        rows = [r for r in rows if r["format_id"] == format_filter]

    existing = wh.query_set(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_USAGE_STATS)}`"
    )
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows
            if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All usage data already ingested")
        return
    logger.info("Ingesting %d usage stats files", len(todo))
    accum = []
    for month, fmt, elo in tqdm(todo, desc="Usage stats"):
        text = fetch_usage_text(storage, month, fmt, elo)
        if not text:
            logger.warning("No data for %s %s-%d", month, fmt, elo)
            continue
        data_lines, _ = parse_usage_table(text)
        if not data_lines:
            continue
        for rank, pokemon, usage_pct, raw_count, raw_pct, real_count, real_pct in data_lines:
            accum.append({
                "month": month, "format_id": fmt, "elo_tier": elo,
                "pokemon": pokemon, "rank": rank,
                "usage_pct": usage_pct, "raw_count": raw_count,
                "raw_pct": raw_pct, "real_count": real_count, "real_pct": real_pct,
            })
        if len(accum) >= FLUSH_THRESHOLD:
            wh.write_rows(TABLE_USAGE_STATS, SCHEMA_MAP[TABLE_USAGE_STATS], accum)
            accum = []
    if accum:
        wh.write_rows(TABLE_USAGE_STATS, SCHEMA_MAP[TABLE_USAGE_STATS], accum)
    logger.info("Usage ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter (e.g., gen9ou)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
