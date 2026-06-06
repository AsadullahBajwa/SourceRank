"""
SourceRank configuration.
Copy this file to config_local.py and fill in your API keys.
config_local.py is gitignored and never committed.
"""

import os

# --- Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_DIR = os.path.join(DATA_DIR, "db")

TWEETS_DB = os.path.join(DB_DIR, "tweets.db")
CLAIMS_DB = os.path.join(DB_DIR, "claims.db")

JOURNALISTS_CSV = os.path.join(DATA_DIR, "journalists.csv")
RSS_SOURCES_CSV = os.path.join(DATA_DIR, "rss_sources.csv")

OUTPUT_DIR = os.path.join(BASE_DIR, "output", "site", "data")
HISTORY_DIR = os.path.join(OUTPUT_DIR, "history")

# --- Scoring weights ---
SCORE_WEIGHTS = {
    "accuracy_rate": 0.40,
    "prediction_score": 0.25,
    "correction_behavior": 0.15,
    "source_quality": 0.10,
    "spam_index": 0.10,
}

# Minimum evidence needed before a journalist should be ranked on the public
# leaderboard. Scores are still computed for diagnostics, but the site marks
# these rows as not rank-eligible.
MIN_RESOLVED_CLAIMS_FOR_RANKING = 20

# News publishers treated as highest-trust confirmation/refutation sources.
# RSS source names and parsed Google News publisher names both live here so
# verification and source-quality scoring stay aligned.
TIER1_SOURCE_NAMES = {
    "Reuters World", "Reuters Politics", "Reuters",
    "AP News Top", "AP News", "Associated Press",
    "BBC World", "BBC US", "BBC UK", "BBC Urdu", "BBC News",
    "Dawn Pakistan", "Dawn",
    "Guardian UK", "The Guardian",
}

# Verification windows in days by claim type
VERIFICATION_WINDOWS = {
    "breaking": 7,
    "prediction": 30,
    "exclusive": 14,
    "statistic": 30,
    "general": 14,
}

# Scraping window:
# Skip tweets newer than this — too recent to verify claims
SKIP_RECENT_DAYS = 30
QUICK_SKIP_MIN_WORDS = 8  # tweets shorter than this skip LLM entirely
# Go this many months back from the skip cutoff
INITIAL_SCRAPE_MONTHS = 18

# --- X / Twitter ---
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "")

# --- Ollama (local LLM for claim extraction) ---
OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"
OLLAMA_NUM_PARALLEL = 4

# --- Google News ---
GOOGLE_NEWS_LANG = "en"
GOOGLE_NEWS_PERIOD = "7d"

# --- Pipeline schedule ---
# Used by scheduler.py — how often to run each step
SCRAPE_INTERVAL_DAYS = 7
VERIFY_INTERVAL_DAYS = 1
SCORE_INTERVAL_DAYS = 7
