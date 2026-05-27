import argparse
import logging
import re

import requests
from tqdm import tqdm

from .config import SMOGON_BASE, DATA_DIR, BATCH_SIZE
from .db import get_conn

logger = logging.getLogger(__name__)


def fetch_leads_text(month, format_id, elo_tier):
    cache_dir = DATA_DIR / "leads" / month
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{format_id}-{elo_tier}.txt"
    if cache_path.exists():
        return open(cache_path, encoding="utf-8").read()
    for ext in [".txt", ".txt.gz"]:
        url = f"{SMOGON_BASE}/{month}/leads/{format_id}-{elo_tier}{ext}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                raw = resp.content
                text = gzip.decompress(raw).decode("utf-8") if ext == ".txt.gz" else raw.decode("utf-8")
                open(cache_path, "w", encoding="utf-8").write(text)
                return text
        except ImportError:
            import gzip
            text = gzip.decompress(resp.content).decode("utf-8")
            open(cache_path, "w", encoding="utf-8").write(text)
            return text
        except requests.RequestException:
            continue
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
    with get_conn() as conn:
        if format_filter:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'leads' AND format_id = ?",
                (format_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'leads'"
            ).fetchall()
        existing = set()
        for r in conn.execute("SELECT DISTINCT month, format_id, elo_tier FROM leads"):
            existing.add((r["month"], r["format_id"], r["elo_tier"]))
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows if
            (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All leads data already ingested")
        return
    logger.info("Ingesting %d leads files", len(todo))
    for month, fmt, elo in tqdm(todo, desc="Leads"):
        text = fetch_leads_text(month, fmt, elo)
        if not text:
            continue
        rows = parse_leads_table(text)
        if not rows:
            continue
        with get_conn() as conn:
            batch = [(month, fmt, elo, p, r, u, rc) for r, p, u, rc in rows]
            for i in range(0, len(batch), BATCH_SIZE):
                conn.executemany(
                    "INSERT OR REPLACE INTO leads (month, format_id, elo_tier, pokemon, rank, usage_pct, raw_count) VALUES (?,?,?,?,?,?,?)",
                    batch[i:i + BATCH_SIZE]
                )
    logger.info("Leads ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
