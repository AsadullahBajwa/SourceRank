"""
claim_extractor.py
Analyzes stored tweets and extracts structured claims using a local LLM (Ollama).
Falls back to rule-based extraction if Ollama is unavailable.

Usage:
    python pipeline/claim_extractor.py                  # process all unprocessed tweets
    python pipeline/claim_extractor.py --handle mkraju  # one journalist
    python pipeline/claim_extractor.py --dry-run        # print output, don't save
"""

import sys
import os
import sqlite3
import argparse
import datetime
import logging
import json
import re
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database setup
# ---------------------------------------------------------------------------

CREATE_CLAIMS_TABLE = """
CREATE TABLE IF NOT EXISTS claims (
    id                  TEXT PRIMARY KEY,
    tweet_id            TEXT NOT NULL,
    handle              TEXT NOT NULL,
    claim_text          TEXT NOT NULL,
    claim_type          TEXT NOT NULL,
    entities            TEXT,
    verification_window INTEGER NOT NULL,
    extracted_at        TEXT NOT NULL,
    verdict             TEXT DEFAULT 'PENDING',
    verdict_source      TEXT,
    verdict_url         TEXT,
    verdict_at          TEXT
);
"""

PROCESSED_FLAG_TABLE = """
CREATE TABLE IF NOT EXISTS processed_tweets (
    tweet_id    TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);
"""

# Signals that suggest a tweet contains a checkable claim
CLAIM_SIGNALS = [
    r"\bBREAKING\b",
    r"\bEXCLUSIVE\b",
    r"\bSources? say\b",
    r"\bI(?:'m| am) told\b",
    r"\bI(?:'ve| have) learned\b",
    r"\bConfirmed\b",
    r"\bwill resign\b",
    r"\bwill be (fired|arrested|charged|indicted|ousted)\b",
    r"\bexpected to\b",
    r"\bset to\b",
    r"\bplanning to\b",
    r"\baccording to\b",
    r"\bhas been (arrested|charged|indicted|fired|removed)\b",
    r"\bFirst to report\b",
    r"\bJust in\b",
]

CLAIM_TYPE_MAP = {
    "breaking": [r"\bBREAKING\b", r"\bJust in\b"],
    "exclusive": [r"\bEXCLUSIVE\b", r"\bFirst to report\b", r"\bI(?:'m| am) told\b", r"\bI(?:'ve| have) learned\b"],
    "prediction": [r"\bwill\b", r"\bexpected to\b", r"\bset to\b", r"\bplanning to\b"],
    "statistic": [r"\b\d+%\b", r"\bmillion\b", r"\bbillion\b"],
    "general": [],
}


def has_claim_signal(text: str) -> bool:
    for pattern in CLAIM_SIGNALS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def infer_claim_type(text: str) -> str:
    for claim_type, patterns in CLAIM_TYPE_MAP.items():
        if claim_type == "general":
            continue
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return claim_type
    return "general"


# ---------------------------------------------------------------------------
# LLM extraction (Ollama)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You are a fact-checking assistant. Analyze the tweet below and extract any checkable factual claim.

Tweet: "{tweet}"

Respond ONLY with a JSON object in this exact format (no markdown, no extra text):
{{
  "has_claim": true or false,
  "claim_text": "the specific checkable claim, or empty string if none",
  "claim_type": "breaking | exclusive | prediction | statistic | general",
  "entities": ["list", "of", "named", "entities"],
  "confidence": 0.0 to 1.0
}}

Rules:
- Only extract SPECIFIC, VERIFIABLE claims (events, facts, numbers, named actions)
- Opinions, commentary, and vague statements are NOT claims
- "Sources say X will happen" → claim_text = "X will happen"
- If no checkable claim, set has_claim to false"""


def extract_with_ollama(tweet_text: str) -> dict | None:
    try:
        import requests
        prompt = EXTRACTION_PROMPT.format(tweet=tweet_text.replace('"', "'"))
        response = requests.post(
            f"{config.OLLAMA_HOST}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0, "num_predict": 150},
            },
            timeout=90,
        )
        if response.status_code == 200:
            raw = response.json().get("response", "").strip()
            # Strip markdown code fences if model adds them
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        log.debug(f"Ollama extraction failed: {e}")
    return None


def extract_fallback(tweet_text: str) -> dict:
    """Simple rule-based fallback when Ollama is not available."""
    has_claim = has_claim_signal(tweet_text)
    claim_type = infer_claim_type(tweet_text) if has_claim else "general"
    return {
        "has_claim": has_claim,
        "claim_text": tweet_text[:280] if has_claim else "",
        "claim_type": claim_type,
        "entities": [],
        "confidence": 0.5,
    }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def process_tweet(tweet: sqlite3.Row, conn: sqlite3.Connection, dry_run: bool = False) -> bool:
    tweet_id, handle, text = tweet["id"], tweet["handle"], tweet["text"]

    # Try LLM first, fall back to rules
    result = extract_with_ollama(text) or extract_fallback(text)

    if not result.get("has_claim") or not result.get("claim_text", "").strip():
        if not dry_run:
            mark_processed(tweet_id, conn)
        return False

    claim_type = result.get("claim_type", "general")
    window = config.VERIFICATION_WINDOWS.get(claim_type, config.VERIFICATION_WINDOWS["general"])
    claim_id = f"{tweet_id}_{claim_type[:3]}"
    extracted_at = datetime.datetime.utcnow().isoformat()

    if dry_run:
        print(json.dumps({
            "tweet_id": tweet_id,
            "handle": handle,
            "claim": result["claim_text"],
            "type": claim_type,
            "window_days": window,
            "entities": result.get("entities", []),
        }, indent=2))
        return True

    conn.execute(
        """
        INSERT OR IGNORE INTO claims
            (id, tweet_id, handle, claim_text, claim_type, entities,
             verification_window, extracted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            tweet_id,
            handle,
            result["claim_text"],
            claim_type,
            json.dumps(result.get("entities", [])),
            window,
            extracted_at,
        ),
    )
    mark_processed(tweet_id, conn)
    conn.commit()
    return True


def mark_processed(tweet_id: str, conn: sqlite3.Connection):
    conn.execute(
        "INSERT OR IGNORE INTO processed_tweets (tweet_id, processed_at) VALUES (?, ?)",
        (tweet_id, datetime.datetime.utcnow().isoformat()),
    )


def get_unprocessed_tweets(handle: str | None, tweets_conn: sqlite3.Connection,
                           claims_conn: sqlite3.Connection) -> list:
    processed_ids = {
        row[0] for row in claims_conn.execute("SELECT tweet_id FROM processed_tweets").fetchall()
    }
    tweets_conn.row_factory = sqlite3.Row
    query = "SELECT id, handle, text FROM tweets WHERE is_retweet = 0"
    params = []
    if handle:
        query += " AND handle = ?"
        params.append(handle.lower())
    rows = tweets_conn.execute(query, params).fetchall()
    return [r for r in rows if r["id"] not in processed_ids]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Extract claims from scraped tweets.")
    parser.add_argument("--handle", help="Process tweets for one journalist only")
    parser.add_argument("--dry-run", action="store_true", help="Print claims without saving")
    args = parser.parse_args()

    tweets_conn = sqlite3.connect(config.TWEETS_DB)
    claims_conn = get_db(config.CLAIMS_DB)

    tweets = get_unprocessed_tweets(args.handle, tweets_conn, claims_conn)
    log.info(f"Processing {len(tweets)} unprocessed tweets ...")

    extracted = 0
    for i, tweet in enumerate(tweets, 1):
        if i % 10 == 0 or i == 1:
            log.info(f"Progress: {i}/{len(tweets)} tweets | claims found: {extracted}")
        if process_tweet(tweet, claims_conn, dry_run=args.dry_run):
            extracted += 1

    tweets_conn.close()
    claims_conn.close()
    log.info(f"Done. Claims extracted: {extracted} / {len(tweets)} tweets.")


def get_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(CREATE_CLAIMS_TABLE)
    conn.execute(PROCESSED_FLAG_TABLE)
    conn.commit()
    return conn


if __name__ == "__main__":
    main()
