import re
import logging
import requests
import gzip
import argparse
from tqdm import tqdm

from .config import SMOGON_BASE, FLUSH_THRESHOLD
from .warehouse_client import WarehouseClient
from .storage_client import StorageClient
from .db import SCHEMA_MAP, TABLE_METAGAME, TABLE_DISCOVERED_SOURCES

logger = logging.getLogger(__name__)


def fetch_metagame_text(storage, month, format_id, elo_tier):
    cache_name = storage.cache_path("metagame", month, format_id, elo_tier, "txt")
    if storage.exists(cache_name):
        logger.debug("Cache hit: gs://%s/%s", storage.bucket_name, cache_name)
        return storage.download_text(cache_name)
    for ext in [".txt", ".txt.gz"]:
        url = f"{SMOGON_BASE}/{month}/metagame/{format_id}-{elo_tier}{ext}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                raw = resp.content
                text = gzip.decompress(raw).decode("utf-8") if ext == ".txt.gz" else raw.decode("utf-8")
                storage.upload_text(text, cache_name)
                return text
        except requests.RequestException:
            continue
    return None


def parse_metagame_text(text):
    if not text:
        return []
    results = []
    for line in text.splitlines():
        m = re.match(r"(\w[\w\s-]*?)\.{3,}\s*([\d.]+)%", line)
        if m:
            results.append((m.group(1).strip(), float(m.group(2))))
    return results


def run(format_filter=None):
    wh = WarehouseClient()
    storage = StorageClient()
    wh.ensure_table(TABLE_METAGAME, SCHEMA_MAP[TABLE_METAGAME])

    rows = wh.query(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_DISCOVERED_SOURCES)}` WHERE source_type = 'metagame'"
    )
    if format_filter:
        rows = [r for r in rows if r["format_id"] == format_filter]

    existing = wh.query_set(
        f"SELECT DISTINCT month, format_id, elo_tier FROM `{wh.table_ref(TABLE_METAGAME)}`"
    )
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows
            if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All metagame data already ingested")
        return
    logger.info("Ingesting %d metagame files", len(todo))
    accum = []
    for month, fmt, elo in tqdm(todo, desc="Metagame"):
        text = fetch_metagame_text(storage, month, fmt, elo)
        if not text:
            continue
        parsed = parse_metagame_text(text)
        if not parsed:
            continue
        for playstyle, pct in parsed:
            accum.append({"month": month, "format_id": fmt, "elo_tier": elo,
                          "playstyle": playstyle, "usage_pct": pct})
        if len(accum) >= FLUSH_THRESHOLD:
            wh.write_rows(TABLE_METAGAME, SCHEMA_MAP[TABLE_METAGAME], accum)
            accum = []
    if accum:
        wh.write_rows(TABLE_METAGAME, SCHEMA_MAP[TABLE_METAGAME], accum)
    logger.info("Metagame ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
