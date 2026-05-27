import argparse
import logging
import re

import requests
from tqdm import tqdm

from .config import SMOGON_BASE, DATA_DIR
from .db import get_conn

logger = logging.getLogger(__name__)


def fetch_metagame_text(month, format_id, elo_tier):
    cache_dir = DATA_DIR / "metagame" / month
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{format_id}-{elo_tier}.txt"
    if cache_path.exists():
        return open(cache_path, encoding="utf-8").read()
    for ext in [".txt", ".txt.gz"]:
        url = f"{SMOGON_BASE}/{month}/metagame/{format_id}-{elo_tier}{ext}"
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
    with get_conn() as conn:
        if format_filter:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'metagame' AND format_id = ?",
                (format_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT month, format_id, elo_tier FROM discovered_sources WHERE source_type = 'metagame'"
            ).fetchall()
        existing = set()
        for r in conn.execute("SELECT DISTINCT month, format_id, elo_tier FROM metagame"):
            existing.add((r["month"], r["format_id"], r["elo_tier"]))
    todo = [(r["month"], r["format_id"], r["elo_tier"]) for r in rows if
            (r["month"], r["format_id"], r["elo_tier"]) not in existing]
    if not todo:
        logger.info("All metagame data already ingested")
        return
    logger.info("Ingesting %d metagame files", len(todo))
    for month, fmt, elo in tqdm(todo, desc="Metagame"):
        text = fetch_metagame_text(month, fmt, elo)
        if not text:
            continue
        rows = parse_metagame_text(text)
        if not rows:
            continue
        with get_conn() as conn:
            for playstyle, pct in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO metagame (month, format_id, elo_tier, playstyle, usage_pct) VALUES (?,?,?,?,?)",
                    (month, fmt, elo, playstyle, pct)
                )
    logger.info("Metagame ingestion complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", help="Format filter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(format_filter=args.format)
