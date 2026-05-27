import os


SMOGON_BASE = "https://www.smogon.com/stats"
GEN9_START = "2022-11"

DEFAULT_ELO_TIERS = [0, 1500, 1630, 1695, 1760, 1825]

DEFAULT_FORMATS = [
    "gen9ou",
    "gen9ubers",
    "gen9uu",
    "gen9nu",
    "gen9pu",
    "gen9zu",
    "gen9lc",
    "gen9doublesou",
    "gen9monotype",
    "gen9nationaldex",
    "gen9nationaldexubers",
    "gen9nationaldexuu",
    "gen9nationaldexmonotype",
    "gen9nationaldexdoubles",
    "gen9anythinggoes",
    "gen9vgc2026regi",
    "gen9vgc2026regf",
    "gen9balancedhackmons",
    "gen9mixandmega",
    "gen9stabmons",
    "gen9godlygift",
    "gen9almostanyability",
    "gen9crossevolution",
    "gen9sharedpower",
    "gen9staaabmons",
    "gen9legendszaou",
    "gen9championsou",
    "gen9champoinsou",
    "gen9doublesubers",
    "gen9doublesuu",
    "gen9ru",
    "gen9cap",
    "gen9alphabetcup",
    "gen9pokebilitiesaaa",
    "gen9bssregi",
    "gen9championsbssregma",
    "gen9championsvgc2026regma",
    "gen9metronomebattle",
    "gen9monocolor",
    "gen9linked",
    "gen91v1",
    "gen92v2doubles",
    "gen94v4doublesuu",
    "gen9nationaldexru",
]

REPLAY_SEARCH_URL = "https://replay.pokemonshowdown.com/search.json"
REPLAY_BASE = "https://replay.pokemonshowdown.com"
MAX_CONCURRENT_LOGS = 12
REPLAY_PAGES = 10
MIN_ELO_REPLAY = 1500

BATCH_SIZE = 500
FLUSH_THRESHOLD = 10_000

PROJECT_ID = os.environ.get("PROJECT_ID", "")
REGION = os.environ.get("REGION", "us-central1")
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
BQ_DATASET = os.environ.get("BQ_DATASET", "smogon_etl")
RUN_ID = os.environ.get("RUN_ID", "manual_uploads")
