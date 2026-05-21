import re
import logging
import requests
import argparse
from tqdm import tqdm

from .config import SMOGON_BASE, DATA_DIR, BATCH_SIZE
from .db import get_conn

logger = logging.getLogger(__name__)


def fetch_usage_text(month, format_id, elo_tier):
    cache_dir = DATA_DIR / "usage" / month
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{format_id}-{elo_tier}.txt"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return f.read()
    for ext in [".txt", ".txt.gz"]:
        url = f"{SMOGON_BASE}/{month}/{format_id}-{elo_tier}{ext}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                raw = resp.content
                if ext == ".txt.gz":
                    import gzip
                    text = gzip.decompress(raw).decode("utf-8")
                else:
                    text = raw.decode("utf-8")
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(text)
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
    with get_conn() as conn:
        if format_filter:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'usage' AND format_id = ?",
                (format_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'usage'"
            ).fetchall()
        existing = set()
        for r in conn.execute("SELECT DISTINCT month, format_id, elo_tier FROM usage_stats"):
            existing.add((r["month"], r["format_id"], r["elo_tier"]))
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows if (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All usage data already ingested")
        return
    logger.info("Ingesting %d usage stats files", len(todo))
    for month, fmt, elo in tqdm(todo, desc="Usage stats"):
        text = fetch_usage_text(month, fmt, elo)
        if not text:
            logger.warning("No data for %s %s-%d", month, fmt, elo)
            continue
        data_lines, total_battles = parse_usage_table(text)
        if not data_lines:
            continue
        with get_conn() as conn:
            if total_battles:
                conn.execute("UPDATE months SET total_battles = ? WHERE month = ?", (total_battles, month))
            batch = []
            for rank, pokemon, usage_pct, raw_count, raw_pct, real_count, real_pct in data_lines:
                batch.append((month, fmt, elo, pokemon, rank, usage_pct, raw_count, raw_pct, real_count, real_pct))
                if len(batch) >= BATCH_SIZE:
                    conn.executemany(
                        "INSERT OR REPLACE INTO usage_stats (month, format_id, elo_tier, pokemon, rank, usage_pct, raw_count, raw_pct, real_count, real_pct) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        batch,
                    )
                    batch = []
            if batch:
                conn.executemany(
                    "INSERT OR REPLACE INTO usage_stats (month, format_id, elo_tier, pokemon, rank, usage_pct, raw_count, raw_pct, real_count, real_pct) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    batch,
                )
    logger.info("Usage ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter (e.g., gen9ou)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
