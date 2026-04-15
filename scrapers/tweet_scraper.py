"""
tweet_scraper.py
Scrapes tweets for each journalist in the registry and stores them in SQLite.
Uses snscrape — no API key required.

Usage:
    python scrapers/tweet_scraper.py                  # scrape all journalists
    python scrapers/tweet_scraper.py --handle mkraju  # scrape one journalist
    python scrapers/tweet_scraper.py --months 3       # override lookback window
"""

import sys
import os
import csv
import sqlite3
import argparse
import datetime
import logging

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
    scraped_at      TEXT NOT NULL
);
"""

CREATE_SCRAPE_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS scrape_log (
    handle          TEXT NOT NULL,
    scraped_at      TEXT NOT NULL,
    tweet_count     INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'ok',
    error           TEXT
);
"""


def get_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(CREATE_TWEETS_TABLE)
    conn.execute(CREATE_SCRAPE_LOG_TABLE)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def scrape_journalist(handle: str, months: int, conn: sqlite3.Connection) -> int:
    """
    Scrape tweets for a single journalist handle going back `months` months.
    Returns the number of new tweets inserted.
    """
    try:
        import snscrape.modules.twitter as sntwitter
    except ImportError:
        log.error("snscrape not installed. Run: pip install snscrape")
        sys.exit(1)

    since = (datetime.datetime.utcnow() - datetime.timedelta(days=months * 30)).strftime("%Y-%m-%d")
    query = f"from:{handle} since:{since} -filter:retweets"

    log.info(f"Scraping @{handle} since {since} ...")

    new_count = 0
    scraped_at = datetime.datetime.utcnow().isoformat()

    try:
        for tweet in sntwitter.TwitterSearchScraper(query).get_items():
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tweets
                        (id, handle, text, created_at, retweet_count, like_count,
                         reply_count, is_retweet, is_reply, lang, url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(tweet.id),
                        handle.lower(),
                        tweet.rawContent,
                        tweet.date.isoformat(),
                        tweet.retweetCount or 0,
                        tweet.likeCount or 0,
                        tweet.replyCount or 0,
                        1 if tweet.retweetedTweet else 0,
                        1 if tweet.inReplyToTweetId else 0,
                        tweet.lang or "",
                        tweet.url or "",
                        scraped_at,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    new_count += 1
            except Exception as e:
                log.warning(f"Skipping tweet {tweet.id}: {e}")

        conn.commit()
        conn.execute(
            "INSERT INTO scrape_log (handle, scraped_at, tweet_count, status) VALUES (?, ?, ?, ?)",
            (handle.lower(), scraped_at, new_count, "ok"),
        )
        conn.commit()
        log.info(f"@{handle}: {new_count} new tweets stored.")

    except Exception as e:
        conn.execute(
            "INSERT INTO scrape_log (handle, scraped_at, tweet_count, status, error) VALUES (?, ?, ?, ?, ?)",
            (handle.lower(), scraped_at, 0, "error", str(e)),
        )
        conn.commit()
        log.error(f"@{handle} failed: {e}")

    return new_count


def load_journalists(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape tweets for journalist registry.")
    parser.add_argument("--handle", help="Scrape a single journalist by handle")
    parser.add_argument("--months", type=int, default=config.INITIAL_SCRAPE_MONTHS,
                        help="Months of history to fetch (default: from config)")
    args = parser.parse_args()

    conn = get_db(config.TWEETS_DB)
    journalists = load_journalists(config.JOURNALISTS_CSV)

    if args.handle:
        journalists = [j for j in journalists if j["handle"].lower() == args.handle.lower()]
        if not journalists:
            log.error(f"Handle '{args.handle}' not found in {config.JOURNALISTS_CSV}")
            sys.exit(1)

    total = 0
    for j in journalists:
        if j.get("active", "true").lower() != "true":
            continue
        total += scrape_journalist(j["handle"], args.months, conn)

    conn.close()
    log.info(f"Done. Total new tweets stored: {total}")


if __name__ == "__main__":
    main()
