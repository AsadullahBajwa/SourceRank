"""
news_scraper.py
Fetches and stores articles from RSS feeds for use in claim verification.

Usage:
    python scrapers/news_scraper.py                   # fetch all sources
    python scrapers/news_scraper.py --country US      # one country only
    python scrapers/news_scraper.py --tier 1          # only tier-1 sources
"""

import sys
import os
import csv
import sqlite3
import argparse
import datetime
import logging
import hashlib

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

CREATE_ARTICLES_TABLE = """
CREATE TABLE IF NOT EXISTS articles (
    id          TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    country     TEXT,
    title       TEXT NOT NULL,
    summary     TEXT,
    url         TEXT,
    published   TEXT,
    fetched_at  TEXT NOT NULL
);
"""

CREATE_FTS_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    summary,
    content='articles',
    content_rowid='rowid'
);
"""

CREATE_FTS_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS articles_fts_insert AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, summary) VALUES (new.rowid, new.title, new.summary);
END;
"""

CREATE_FTS_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS articles_fts_delete AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, summary) VALUES('delete', old.rowid, old.title, old.summary);
END;
"""


def get_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(CREATE_ARTICLES_TABLE)
    conn.execute(CREATE_FTS_TABLE)
    conn.execute(CREATE_FTS_TRIGGER_INSERT)
    conn.execute(CREATE_FTS_TRIGGER_DELETE)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def article_id(url: str, title: str) -> str:
    return hashlib.md5(f"{url}{title}".encode()).hexdigest()


def fetch_feed(source: dict, conn: sqlite3.Connection) -> int:
    try:
        import feedparser
    except ImportError:
        log.error("feedparser not installed. Run: pip install feedparser")
        sys.exit(1)

    log.info(f"Fetching {source['name']} ...")
    fetched_at = datetime.datetime.utcnow().isoformat()
    new_count = 0

    try:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            url = getattr(entry, "link", "").strip()
            summary = getattr(entry, "summary", "").strip()
            published = getattr(entry, "published", "") or getattr(entry, "updated", "")

            if not title:
                continue

            row_id = article_id(url, title)

            conn.execute(
                """
                INSERT OR IGNORE INTO articles
                    (id, source_name, country, title, summary, url, published, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, source["name"], source.get("country", ""), title, summary, url, published, fetched_at),
            )
            if conn.execute("SELECT changes()").fetchone()[0]:
                new_count += 1

        conn.commit()
        log.info(f"{source['name']}: {new_count} new articles stored.")

    except Exception as e:
        log.error(f"{source['name']} failed: {e}")

    return new_count


def load_sources(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fetch RSS feeds into the news corpus.")
    parser.add_argument("--country", help="Filter by country code (e.g. US, Pakistan)")
    parser.add_argument("--tier", type=int, help="Filter by source tier (1 = top tier)")
    args = parser.parse_args()

    conn = get_db(config.CLAIMS_DB)
    sources = load_sources(config.RSS_SOURCES_CSV)

    if args.country:
        sources = [s for s in sources if s["country"].lower() == args.country.lower()]
    if args.tier:
        sources = [s for s in sources if int(s.get("tier", 99)) <= args.tier]

    total = 0
    for source in sources:
        total += fetch_feed(source, conn)

    conn.close()
    log.info(f"Done. Total new articles stored: {total}")


if __name__ == "__main__":
    main()
