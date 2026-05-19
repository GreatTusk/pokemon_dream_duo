#!/usr/bin/env python3
"""
============================================================
 Pokemon Showdown Gen 9 OU - Dream Duos Analysis Pipeline
 Author: Fernando Belmar
 Description:
   Downloads Gen 9 OU replay data from two sources:
     1. Smogon Stats chaos JSON (teammate co-occurrence matrix)
     2. Pokemon Showdown Replay Search API (live replay logs)
   Filters for high-ladder games (>=1800 Elo equivalent),
   computes co-occurrence frequency + win rate for every
   Pokemon pair, and exports ranked "dream duos" to CSV.
============================================================
"""

import asyncio
import aiohttp
import requests
import re
import csv
import os
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Tuple, Optional

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
CONFIG = {
    # Smogon Stats endpoint (1695 Elo cutoff is the public high-ladder tier)
    "smogon_chaos_url": "https://www.smogon.com/stats/2024-12/chaos/gen9ou-1695.json",

    # Showdown Replay Search API
    "replay_search_url": "https://replay.pokemonshowdown.com/search.json",

    # Format to query
    "format": "gen9ou",

    # How many pages of replays to fetch (each page = ~50 replays)
    "replay_pages": 10,

    # Minimum replay rating to accept (Elo filter)
    "min_elo": 1800,

    # Minimum times a pair must appear to be included in final results
    "min_pair_occurrences": 5,

    # Number of top duos to export
    "top_n": 100,

    # Output CSV filename
    "output_csv": "pokemon_dream_duos_gen9ou_1800elo.csv",

    # Max concurrent replay log fetches
    "max_concurrent_replay_logs": 12,

    # Optional request delay in seconds (0 disables per-request delay)
    "request_delay": 0.0,
}

# ─────────────────────────────────────────────
#  SMOGON STATS SOURCE
# ─────────────────────────────────────────────

def fetch_smogon_chaos(url: str) -> Optional[dict]:
    """Download and parse the Smogon chaos JSON stats file."""
    print(f"[Smogon] Fetching chaos stats from: {url}")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()
        print(f"[Smogon] Downloaded stats for {len(data.get('data', {}))} Pokemon.")
        return data
    except requests.exceptions.HTTPError as e:
        print(f"[Smogon] HTTP error {e}. Trying fallback month...")
        return None
    except Exception as e:
        print(f"[Smogon] Error: {e}")
        return None


def find_latest_smogon_stats(base_format: str, min_elo_tier: int = 1695) -> Optional[dict]:
    """
    Try recent months until a valid Smogon chaos JSON is found.
    Smogon publishes stats monthly; the 1695-cutoff file is the
    closest publicly available proxy for 1800+ Elo play.
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    # Try up to 6 recent months
    for months_back in range(1, 7):
        target = now.replace(day=1) - timedelta(days=30 * months_back)
        month_str = target.strftime("%Y-%m")
        url = f"https://www.smogon.com/stats/{month_str}/chaos/{base_format}-{min_elo_tier}.json"
        data = fetch_smogon_chaos(url)
        if data:
            print(f"[Smogon] Using stats from: {month_str}")
            return data

    print("[Smogon] Could not fetch any Smogon stats. Skipping this source.")
    return None


def extract_teammate_pairs_from_smogon(chaos_data: dict) -> Dict[Tuple[str, str], Dict]:
    """
    Extract co-occurrence data from Smogon's 'Teammates' section.
    Each Pokemon entry lists its most common teammates with a usage score.
    We symmetrize the matrix to avoid double-counting.
    """
    pokemon_data = chaos_data.get("data", {})
    pair_scores = defaultdict(float)
    pair_counts = defaultdict(int)

    for poke_name, poke_info in pokemon_data.items():
        teammates = poke_info.get("Teammates", {})
        for teammate, score in teammates.items():
            if score <= 0:
                continue
            # Normalize pair order to avoid (A,B) and (B,A) duplicates
            pair = tuple(sorted([poke_name, teammate]))
            pair_scores[pair] += score
            pair_counts[pair] += 1

    return pair_scores, pair_counts


# ─────────────────────────────────────────────
#  SHOWDOWN REPLAY SOURCE
# ─────────────────────────────────────────────

async def fetch_replay_list(
    session: aiohttp.ClientSession, format_id: str, page: int = 1
) -> List[dict]:
    """
    Query the Showdown replay search API for a specific format.
    Returns a list of replay metadata dicts.
    API endpoint: https://replay.pokemonshowdown.com/search.json
    Params: format, page
    """
    url = CONFIG["replay_search_url"]
    params = {"format": format_id, "page": page}
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[Replay API] Error fetching page {page}: {e}")
        return []


async def fetch_replay_log(
    session: aiohttp.ClientSession,
    replay_id: str,
    semaphore: asyncio.Semaphore,
    request_delay: float = 0.0,
) -> Optional[str]:
    """
    Download the full battle log for a given replay ID.
    Replay logs are available at:
      https://replay.pokemonshowdown.com/{replay_id}.log
    """
    url = f"https://replay.pokemonshowdown.com/{replay_id}.log"
    try:
        async with semaphore:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                text = await resp.text()
            if request_delay > 0:
                await asyncio.sleep(request_delay)
            return text
    except Exception as e:
        print(f"[Replay API] Could not fetch log {replay_id}: {e}")
        return None


async def _fetch_replay_pages(session: aiohttp.ClientSession) -> List[dict]:
    all_replays = []
    for page in range(1, CONFIG["replay_pages"] + 1):
        replay_list = await fetch_replay_list(session, CONFIG["format"], page=page)
        if not replay_list:
            print(f"[Replay API] Page {page}: no results, stopping.")
            break
        print(f"[Replay API] Page {page}: {len(replay_list)} replays found.")
        all_replays.extend(replay_list)
    return all_replays


async def _fetch_and_parse_replay(
    session: aiohttp.ClientSession,
    meta: dict,
    semaphore: asyncio.Semaphore,
    request_delay: float,
) -> Optional[dict]:
    replay_id = meta.get("id", "")
    if not replay_id:
        return None
    log = await fetch_replay_log(session, replay_id, semaphore, request_delay)
    return parse_replay_log(log, meta)


def parse_replay_log(log: str, replay_meta: dict) -> Optional[dict]:
    """
    Parse a Showdown battle log to extract:
      - Player 1 team (Pokemon that switched in)
      - Player 2 team
      - Winner
      - Rating (Elo)

    Battle log protocol (simplified):
      |player|p1|Name|Rating|
      |switch|p1a: PokeName|...
      |win|PlayerName
    """
    if not log:
        return None

    team1 = []
    team2 = []
    winner = None
    rating_p1 = replay_meta.get("rating", 0) or 0
    p1_name = ""
    p2_name = ""

    for line in log.splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue

        tag = parts[1] if len(parts) > 1 else ""

        # ── Player declarations ──────────────────────
        if tag == "player" and len(parts) >= 4:
            side = parts[2]
            name = parts[3]
            rating_str = parts[4] if len(parts) > 4 else "0"
            try:
                r = int(rating_str)
            except ValueError:
                r = 0
            if side == "p1":
                p1_name = name
                if r > 0:
                    rating_p1 = r
            elif side == "p2":
                p2_name = name

        # ── Switch events → build team lists ─────────
        elif tag == "switch" and len(parts) >= 3:
            slot_ident = parts[2]  # e.g. "p1a: Cinderace"
            poke = _parse_pokemon_name(slot_ident)
            if poke:
                if slot_ident.startswith("p1"):
                    if poke not in team1:
                        team1.append(poke)
                elif slot_ident.startswith("p2"):
                    if poke not in team2:
                        team2.append(poke)

        # ── Winner ───────────────────────────────────
        elif tag == "win" and len(parts) >= 3:
            winner_name = parts[2].strip()
            if winner_name == p1_name:
                winner = "p1"
            elif winner_name == p2_name:
                winner = "p2"

    if not team1 and not team2:
        return None

    return {
        "team1": team1,
        "team2": team2,
        "winner": winner,
        "rating": rating_p1,
        "p1": p1_name,
        "p2": p2_name,
    }


def _parse_pokemon_name(slot_ident: str) -> str:
    """
    Extract clean species name from slot identifiers like:
      'p1a: Cinderace'
      'p2b: Iron Valiant, shiny'
    """
    if ": " in slot_ident:
        name = slot_ident.split(": ", 1)[1]
    else:
        return ""
    # Strip trailing comma-separated modifiers (shiny, gender, etc.)
    name = name.split(",")[0].strip()
    # Strip gender / level suffixes in parentheses
    name = re.sub(r"\s*\(.*?\)$", "", name).strip()
    return name


# ─────────────────────────────────────────────
#  CO-OCCURRENCE ENGINE
# ─────────────────────────────────────────────

class DreamDuosEngine:
    """
    Accumulates co-occurrence counts and win statistics
    across all parsed replay games.
    """

    def __init__(self):
        # (PokemonA, PokemonB) -> {count, wins}
        self.stats: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
            lambda: {"count": 0, "wins": 0}
        )
        self.replays_processed = 0
        self.replays_filtered = 0

    def ingest_replay(self, parsed: dict, min_elo: int = 1800):
        """Add a parsed replay's team data to the co-occurrence matrix."""
        if parsed["rating"] < min_elo:
            self.replays_filtered += 1
            return

        self.replays_processed += 1
        self._record_team(parsed["team1"], parsed["winner"] == "p1")
        self._record_team(parsed["team2"], parsed["winner"] == "p2")

    def _record_team(self, team: List[str], won: bool):
        for poke_a, poke_b in combinations(sorted(team), 2):
            key = (poke_a, poke_b)
            self.stats[key]["count"] += 1
            if won:
                self.stats[key]["wins"] += 1

    def ingest_smogon_pair_scores(
        self, pair_scores: Dict, pair_counts: Dict, scale: int = 1000
    ):
        """
        Merge Smogon teammate co-occurrence scores into the engine.
        Smogon teammate scores are not raw counts — they are normalized
        usage deltas. We scale them into pseudo-counts for ranking.
        Win rate from Smogon is unavailable, so it defaults to 0.5.
        """
        for pair, score in pair_scores.items():
            pseudo_count = max(1, int(score / scale))
            self.stats[pair]["count"] += pseudo_count
            # 50% assumed win rate for Smogon-sourced entries
            self.stats[pair]["wins"] += pseudo_count // 2

    def get_dream_duos(self, min_occurrences: int = 5, top_n: int = 100) -> List[dict]:
        """
        Return the ranked dream duos list.
        Primary sort: co-occurrence count (most used together)
        Secondary sort: win rate (best performing)
        """
        results = []
        for (poke_a, poke_b), s in self.stats.items():
            if s["count"] < min_occurrences:
                continue
            win_rate = s["wins"] / s["count"]
            results.append(
                {
                    "rank": 0,  # filled below
                    "pokemon_1": poke_a,
                    "pokemon_2": poke_b,
                    "co_occurrence_count": s["count"],
                    "wins": s["wins"],
                    "win_rate_pct": round(win_rate * 100, 2),
                }
            )

        # Sort by count desc, then win rate desc
        results.sort(key=lambda x: (-x["co_occurrence_count"], -x["win_rate_pct"]))
        results = results[:top_n]

        for i, row in enumerate(results, 1):
            row["rank"] = i

        return results

    def summary(self):
        print(f"{'='*55}")
        print(f"  PIPELINE SUMMARY")
        print(f"{'='*55}")
        print(f"  Replays accepted (Elo >= {CONFIG['min_elo']}): {self.replays_processed}")
        print(f"  Replays filtered (Elo < {CONFIG['min_elo']}):  {self.replays_filtered}")
        print(f"  Unique Pokemon pairs found:         {len(self.stats)}")
        print(f"{'='*55}")


# ─────────────────────────────────────────────
#  CSV EXPORT
# ─────────────────────────────────────────────

def export_csv(results: List[dict], filepath: str):
    if not results:
        print("[Export] No results to write.")
        return
    fieldnames = ["rank", "pokemon_1", "pokemon_2", "co_occurrence_count", "wins", "win_rate_pct"]
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"[Export] Saved {len(results)} rows → {filepath}")


# ─────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────

async def run_pipeline():
    print("\n" + "="*55)
    print(f"  Gen 9 OU Dream Duos Pipeline  |  Elo >= {CONFIG['min_elo']}")
    print("="*55 + "\n")

    engine = DreamDuosEngine()

    # ── SOURCE 1: Smogon Stats Chaos JSON ────────────────
    print(">>> SOURCE 1: Smogon Stats (Chaos JSON, 1695 cutoff)\n")
    chaos_data = find_latest_smogon_stats(CONFIG["format"], min_elo_tier=1695)
    if chaos_data:
        pair_scores, pair_counts = extract_teammate_pairs_from_smogon(chaos_data)
        engine.ingest_smogon_pair_scores(pair_scores, pair_counts)
        print(f"[Smogon] Ingested {len(pair_scores)} unique pairs from stats.\n")
    else:
        print("[Smogon] Skipped — no data available.\n")

    # ── SOURCE 2: Live Showdown Replay API ────────────────
    print(f">>> SOURCE 2: Showdown Replay API ({CONFIG['replay_pages']} pages)\n")
    total_fetched = 0
    timeout = aiohttp.ClientTimeout(total=20)
    connector = aiohttp.TCPConnector(limit=CONFIG["max_concurrent_replay_logs"])
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        replay_list = await _fetch_replay_pages(session)
        if replay_list:
            semaphore = asyncio.Semaphore(CONFIG["max_concurrent_replay_logs"])
            tasks = [
                _fetch_and_parse_replay(
                    session,
                    meta,
                    semaphore,
                    CONFIG["request_delay"],
                )
                for meta in replay_list
            ]
            results = await asyncio.gather(*tasks)
            for parsed in results:
                if parsed:
                    engine.ingest_replay(parsed, min_elo=CONFIG["min_elo"])
                    total_fetched += 1

    print(f"\n[Replay API] Total replays fetched and parsed: {total_fetched}")

    # ── RESULTS ───────────────────────────────────────────
    engine.summary()
    dream_duos = engine.get_dream_duos(
        min_occurrences=CONFIG["min_pair_occurrences"],
        top_n=CONFIG["top_n"],
    )

    if dream_duos:
        print(f"Top 20 Dream Duos (Gen 9 OU | Elo >= {CONFIG['min_elo']})")
        print(f"{'Rank':<6} {'Pokemon 1':<22} {'Pokemon 2':<22} {'Count':>7} {'Win%':>8}")
        print("-" * 68)
        for duo in dream_duos[:20]:
            print(
                f"{duo['rank']:<6} {duo['pokemon_1']:<22} {duo['pokemon_2']:<22}"
                f" {duo['co_occurrence_count']:>7} {duo['win_rate_pct']:>7.2f}%"
            )
    else:
        print("[Results] No pairs met the minimum occurrence threshold.")

    # ── EXPORT ────────────────────────────────────────────
    export_csv(dream_duos, CONFIG["output_csv"])
    print("\n[Done] Pipeline complete.")


def main():
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    main()
