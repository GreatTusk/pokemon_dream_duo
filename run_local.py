#!/usr/bin/env python
"""
run_local.py — Run the Smogon ETL pipeline locally without Docker.
Uses airflow/pipeline/ modules (SQLite-backed) with a single command.

Usage:
    python run_local.py --format gen9ou          # single format
    python run_local.py                          # all formats
    python run_local.py --skip-discover          # skip discovery step
    python run_local.py --test                   # run tests instead
"""
import argparse
import logging
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.join(ROOT, "airflow", "pipeline")


def ensure_deps():
    deps = {"requests", "tqdm"}
    missing = [d for d in deps if not _importable(d)]
    if missing:
        print(f"Installing missing deps: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing]
        )


def _importable(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def run_tests():
    ensure_deps()
    extra = {"pytest", "responses", "pytest-asyncio", "aioresponses"}
    missing = [d for d in extra if not _importable(d)]
    if missing:
        print(f"Installing test deps: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing]
        )
    tests_dir = os.path.join(ROOT, "airflow", "tests")
    sys.exit(subprocess.call(
        [sys.executable, "-m", "pytest", tests_dir, "-v", "--tb=short", "--no-header"],
        cwd=ROOT,
    ))


def run_pipeline(format_filter=None, skip_discover=False):
    sys.path.insert(0, os.path.join(ROOT, "airflow"))
    import pipeline.run_pipeline
    pipeline.run_pipeline.run(format_filter=format_filter, skip_discover=skip_discover)


def main():
    parser = argparse.ArgumentParser(description="Smogon ETL Pipeline — Local Runner")
    parser.add_argument("--format", default=None, help="Format filter (e.g., gen9ou)")
    parser.add_argument("--skip-discover", action="store_true", help="Skip discovery step")
    parser.add_argument("--test", action="store_true", help="Run test suite")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.test:
        run_tests()
    else:
        ensure_deps()
        run_pipeline(
            format_filter=args.format,
            skip_discover=args.skip_discover,
        )


if __name__ == "__main__":
    main()
