"""
filter_arxiv_pk.py
Filters the arXiv 49M Pakistani political tweets dataset to extract tweets
from journalists in journalists.csv, then imports them into tweets.db.

Dataset: https://arxiv.org/abs/2301.06316

Usage:
    python scripts/filter_arxiv_pk.py --input /path/to/dataset.csv
    python scripts/filter_arxiv_pk.py --input /path/to/dataset.jsonl
    python scripts/filter_arxiv_pk.py --input /path/to/dataset.csv --dry-run
    python scripts/filter_arxiv_pk.py --input /path/to/dataset.csv --limit 1000000

If the dataset is tweet-ID-only (not hydrated), the script will detect this
and print instructions for hydrating with twarc2:
    twarc2 hydrate tweet_ids.txt > hydrated.jsonl
"""

import sys
import os
import csv
import json
import sqlite3
import argparse
import logging
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

LOG_INTERVAL = 1_000_000

CREATE_TWEETS_TABLE = """
CREATE TABLE IF NOT EXISTS tweets (
    id              TEXT PRIMARY KEY,
    handle          TEXT NOT NULL,
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    retweet_count   INTEGER DEFAULT 0,
    like_count      INTEGER DEFAULT 0,
    reply_count     INTEGER DEFAULT 0,
    is_retweet      INTEGER DEFAULT 0,
    is_reply        INTEGER DEFAULT 0,
    lang            TEXT,
    url             TEXT,
    scraped_at      TEXT
);
"""


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def load_journalist_handles() -> set[str]:
    """Load lowercase handles for active Pakistan journalists only."""
    handles: set[str] = set()
    with open(config.JOURNALISTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("country", "").strip() == "Pakistan"
                    and row.get("active", "").strip().lower() == "true"):
                handle = row.get("handle", "").strip().lower()
                if handle:
                    handles.add(handle)
    return handles


def get_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(CREATE_TWEETS_TABLE)
    conn.commit()
    return conn


def detect_format(path: str) -> str:
    """Detect CSV or JSONL from extension, with first-byte fallback."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".tsv"):
        return "csv"
    if ext in (".jsonl", ".ndjson"):
        return "jsonl"
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first = f.read(1).strip()
    return "jsonl" if first == "{" else "csv"


# ---------------------------------------------------------------------------
# Row parsers
# ---------------------------------------------------------------------------

def _is_retweet(text: str, row: dict) -> int:
    if text.startswith("RT @"):
        return 1
    rt_field = row.get("retweeted_status") or row.get("is_retweet", "")
    if rt_field and str(rt_field).lower() not in ("", "0", "false", "no", "null", "none"):
        return 1
    return 0


def parse_csv_row(row: dict) -> dict | None:
    """
    Normalise a CSV row into our tweet schema.
    Returns None if the row has no text/handle (ID-only dataset).
    """
    text   = (row.get("full_text") or row.get("text")
              or row.get("tweet_text") or "").strip()
    handle = (row.get("user_screen_name") or row.get("screen_name")
              or row.get("username") or "").strip()
    tid    = str(row.get("tweet_id") or row.get("id_str") or row.get("id") or "").strip()

    if not text or not handle or not tid:
        return None

    is_rt    = _is_retweet(text, row)
    is_reply = 1 if text.startswith("@") and not is_rt else 0

    return {
        "id":            tid,
        "handle":        handle.lower(),
        "text":          text,
        "created_at":    row.get("created_at", "").strip(),
        "retweet_count": _int(row.get("retweet_count")),
        "like_count":    _int(row.get("favorite_count") or row.get("like_count")),
        "reply_count":   _int(row.get("reply_count")),
        "is_retweet":    is_rt,
        "is_reply":      is_reply,
        "lang":          row.get("lang", "").strip(),
        "url":           f"https://x.com/{handle.lower()}/status/{tid}",
    }


def parse_jsonl_row(obj: dict) -> dict | None:
    """
    Normalise a hydrated JSON tweet object into our tweet schema.
    Returns None if the object has no text/handle (ID-only dataset).
    """
    user   = obj.get("user") or {}
    handle = (user.get("screen_name") or obj.get("user_screen_name")
              or obj.get("screen_name") or obj.get("username") or "").strip()
    text   = (obj.get("full_text") or obj.get("text")
              or obj.get("tweet_text") or "").strip()
    tid    = str(obj.get("id_str") or obj.get("tweet_id") or obj.get("id") or "").strip()

    if not text or not handle or not tid:
        return None

    is_rt    = _is_retweet(text, obj)
    is_reply = 1 if text.startswith("@") and not is_rt else 0

    return {
        "id":            tid,
        "handle":        handle.lower(),
        "text":          text,
        "created_at":    obj.get("created_at", ""),
        "retweet_count": _int(obj.get("retweet_count")),
        "like_count":    _int(obj.get("favorite_count") or obj.get("like_count")),
        "reply_count":   _int(obj.get("reply_count")),
        "is_retweet":    is_rt,
        "is_reply":      is_reply,
        "lang":          obj.get("lang", ""),
        "url":           f"https://x.com/{handle.lower()}/status/{tid}",
    }


def _int(val) -> int:
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# DB insert
# ---------------------------------------------------------------------------

def insert_tweet(conn: sqlite3.Connection, tweet: dict, scraped_at: str) -> bool:
    conn.execute(
        """
        INSERT OR IGNORE INTO tweets
            (id, handle, text, created_at, retweet_count, like_count,
             reply_count, is_retweet, is_reply, lang, url, scraped_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tweet["id"], tweet["handle"], tweet["text"], tweet["created_at"],
            tweet["retweet_count"], tweet["like_count"], tweet["reply_count"],
            tweet["is_retweet"], tweet["is_reply"], tweet["lang"],
            tweet["url"], scraped_at,
        ),
    )
    return conn.execute("SELECT changes()").fetchone()[0] == 1


_ID_ONLY_MSG = (
    "Dataset appears to be tweet-ID-only (not hydrated). "
    "Text and user fields are missing. Hydrate the dataset first:\n"
    "    twarc2 hydrate tweet_ids.txt > hydrated.jsonl\n"
    "See: https://twarc-project.readthedocs.io/"
)


# ---------------------------------------------------------------------------
# Streamers
# ---------------------------------------------------------------------------

def stream_csv(path: str, handles: set[str], conn: sqlite3.Connection | None,
               dry_run: bool, limit: int | None) -> tuple[int, int, int]:
    rows_scanned = matches = inserted = 0
    id_only_warned = False
    scraped_at = datetime.datetime.utcnow().isoformat()

    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows_scanned += 1
            if limit and rows_scanned > limit:
                break
            if rows_scanned % LOG_INTERVAL == 0:
                log.info(f"Scanned {rows_scanned:,} rows | matches: {matches:,} | inserted: {inserted:,}")

            tweet = parse_csv_row(row)
            if tweet is None:
                if not id_only_warned:
                    log.warning(_ID_ONLY_MSG)
                    id_only_warned = True
                continue

            if tweet["handle"] not in handles:
                continue

            matches += 1
            if dry_run:
                print(json.dumps(
                    {k: tweet[k] for k in ("id", "handle", "text", "created_at")},
                    ensure_ascii=False,
                ))
                continue

            if insert_tweet(conn, tweet, scraped_at):
                inserted += 1
            if inserted % 500 == 0 and inserted:
                conn.commit()

    if conn:
        conn.commit()
    return rows_scanned, matches, inserted


def stream_jsonl(path: str, handles: set[str], conn: sqlite3.Connection | None,
                 dry_run: bool, limit: int | None) -> tuple[int, int, int]:
    rows_scanned = matches = inserted = 0
    id_only_warned = False
    scraped_at = datetime.datetime.utcnow().isoformat()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows_scanned += 1
            if limit and rows_scanned > limit:
                break
            if rows_scanned % LOG_INTERVAL == 0:
                log.info(f"Scanned {rows_scanned:,} rows | matches: {matches:,} | inserted: {inserted:,}")

            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Bare ID lines: {"id": "1234"} or just an integer/string
            if not isinstance(obj, dict) or len(obj) <= 2:
                if not id_only_warned:
                    log.warning(_ID_ONLY_MSG)
                    id_only_warned = True
                continue

            tweet = parse_jsonl_row(obj)
            if tweet is None:
                if not id_only_warned:
                    log.warning(_ID_ONLY_MSG)
                    id_only_warned = True
                continue

            if tweet["handle"] not in handles:
                continue

            matches += 1
            if dry_run:
                print(json.dumps(
                    {k: tweet[k] for k in ("id", "handle", "text", "created_at")},
                    ensure_ascii=False,
                ))
                continue

            if insert_tweet(conn, tweet, scraped_at):
                inserted += 1
            if inserted % 500 == 0 and inserted:
                conn.commit()

    if conn:
        conn.commit()
    return rows_scanned, matches, inserted


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Filter arXiv 49M Pakistani tweets dataset for journalist handles "
            "and import into tweets.db."
        )
    )
    parser.add_argument("--input",   required=True, help="Path to dataset file (CSV or JSONL)")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without inserting into DB")
    parser.add_argument("--limit",   type=int, default=None, metavar="N",
                        help="Stop after N rows (for testing)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        log.error(f"Input file not found: {args.input}")
        sys.exit(1)

    handles = load_journalist_handles()
    log.info(f"Loaded {len(handles)} active Pakistan journalist handles")

    fmt = detect_format(args.input)
    log.info(f"Detected format: {fmt.upper()} | file: {args.input}")
    if args.limit:
        log.info(f"Limit: {args.limit:,} rows (test mode)")

    conn = None if args.dry_run else get_db(config.TWEETS_DB)

    if fmt == "csv":
        rows_scanned, matches, inserted = stream_csv(
            args.input, handles, conn, args.dry_run, args.limit)
    else:
        rows_scanned, matches, inserted = stream_jsonl(
            args.input, handles, conn, args.dry_run, args.limit)

    if conn:
        conn.close()

    log.info("─" * 50)
    log.info(f"Rows scanned : {rows_scanned:>12,}")
    log.info(f"Matches found: {matches:>12,}")
    if not args.dry_run:
        log.info(f"New inserted : {inserted:>12,}")


if __name__ == "__main__":
    main()
