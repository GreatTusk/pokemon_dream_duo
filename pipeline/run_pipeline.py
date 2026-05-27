import argparse
import importlib
import logging
import time

from .db import get_conn, init_db

logger = logging.getLogger(__name__)

STEP_MODULES = [
    "01_discover",
    "02_ingest_usage",
    "03_ingest_chaos",
    "04_ingest_leads",
    "05_ingest_metagame",
    "06_ingest_replays",
]


def run(format_filter=None, skip_discover=False):
    init_db()
    logger.info("=" * 55)
    logger.info("  SMOGON BIG DATA ETL PIPELINE")
    if format_filter:
        logger.info("  Format filter: %s", format_filter)
    logger.info("=" * 55)
    for mod_name in STEP_MODULES:
        if skip_discover and mod_name == "01_discover":
            logger.info(">>> SKIP: %s", mod_name)
            continue
        try:
            mod = importlib.import_module(f"pipeline.{mod_name}")
        except Exception as e:
            logger.warning("Could not import %s: %s, skipping", mod_name, e)
            continue
        logger.info(">>> STEP: %s", mod_name)
        start = time.time()
        try:
            mod.run(format_filter=format_filter) if "format_filter" in mod.run.__code__.co_varnames else mod.run()
        except Exception:
            logger.exception("Step %s failed", mod_name)
            raise
        elapsed = time.time() - start
        logger.info("<<< %s completed in %.1fs", mod_name, elapsed)
    with get_conn() as conn:
        logger.info("=" * 55)
        logger.info("  PIPELINE COMPLETE - TABLE COUNTS")
        logger.info("=" * 55)
        for table in [
            "formats", "months", "usage_stats", "pokemon_details",
            "abilities", "items", "moves", "spreads", "tera_types",
            "teammates", "checks_counters", "leads", "metagame",
            "replays", "replay_teams",
        ]:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
            logger.info("  %-25s %d", table, row["cnt"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smogon Big Data ETL Pipeline")
    parser.add_argument("--format", help="Process only this format (e.g., gen9ou)")
    parser.add_argument("--skip-discover", action="store_true", help="Skip discovery step")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    run(format_filter=args.format, skip_discover=args.skip_discover)
