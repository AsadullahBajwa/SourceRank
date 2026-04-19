"""
tweet_scraper.py
Scrapes tweets for each journalist using Playwright (headless browser).
No API key required. Logs into X with your credentials stored in .env

Setup:
    pip install playwright python-dotenv
    playwright install chromium

    Create a .env file in the project root:
        X_USERNAME=your_username
        X_PASSWORD=your_password

Usage:
    python scrapers/tweet_scraper.py                   # scrape all journalists
    python scrapers/tweet_scraper.py --handle mkraju   # scrape one journalist
    python scrapers/tweet_scraper.py --months 3        # override lookback window
    python scrapers/tweet_scraper.py --no-headless     # show browser window
"""

import sys
import os
import csv
import sqlite3
import argparse
import datetime
import logging
import time
import re

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
    handle      TEXT NOT NULL,
    scraped_at  TEXT NOT NULL,
    tweet_count INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'ok',
    error       TEXT
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
# X login
# ---------------------------------------------------------------------------

def login_to_x(page, username: str, password: str) -> bool:
    log.info("Logging into X ...")
    try:
        page.goto("https://x.com/i/flow/login", wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Enter username
        page.get_by_label("Phone, email, or username").fill(username)
        page.keyboard.press("Enter")
        time.sleep(2)

        # Handle "enter your phone/username" verification step if it appears
        unusual = page.query_selector('input[data-testid="ocfEnterTextTextInput"]')
        if unusual:
            unusual.fill(username)
            page.keyboard.press("Enter")
            time.sleep(2)

        # Enter password
        page.get_by_label("Password", exact=True).fill(password)
        page.keyboard.press("Enter")
        time.sleep(4)

        # Verify login succeeded
        if "home" in page.url or page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]'):
            log.info("Login successful.")
            return True

        log.error("Login may have failed — check credentials in .env")
        return False

    except Exception as e:
        log.error(f"Login error: {e}")
        return False


# ---------------------------------------------------------------------------
# Tweet scraping
# ---------------------------------------------------------------------------

def parse_count(text: str) -> int:
    """Parse '1.2K', '45M', '300' style counts into integers."""
    if not text:
        return 0
    text = text.strip().replace(",", "")
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(text)
    except ValueError:
        return 0


def parse_tweet_date(time_el) -> str:
    """Extract ISO datetime from a <time> element's datetime attribute."""
    try:
        dt_str = time_el.get_attribute("datetime")
        if dt_str:
            return dt_str
    except Exception:
        pass
    return datetime.datetime.utcnow().isoformat()


def extract_tweet_id_from_url(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", url or "")
    return match.group(1) if match else None


def cutoff_reached(tweet_date_str: str, cutoff: datetime.datetime) -> bool:
    try:
        dt = datetime.datetime.fromisoformat(tweet_date_str.replace("Z", "+00:00"))
        dt = dt.replace(tzinfo=None)
        return dt < cutoff
    except Exception:
        return False


def scrape_profile(page, handle: str, months: int, conn: sqlite3.Connection) -> int:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=months * 30)
    url = f"https://x.com/{handle}"
    log.info(f"Scraping @{handle} (cutoff: {cutoff.date()}) ...")

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
    except Exception as e:
        log.error(f"@{handle}: failed to load profile — {e}")
        return 0

    scraped_at = datetime.datetime.utcnow().isoformat()
    new_count = 0
    seen_ids: set[str] = set()
    no_new_streak = 0
    scroll_attempts = 0
    max_scrolls = 300  # safety cap

    while scroll_attempts < max_scrolls:
        tweet_articles = page.query_selector_all('article[data-testid="tweet"]')

        stop = False
        for article in tweet_articles:
            try:
                # Get tweet URL / ID
                link_el = article.query_selector('a[href*="/status/"]')
                tweet_url = link_el.get_attribute("href") if link_el else ""
                tweet_id = extract_tweet_id_from_url(tweet_url)
                if not tweet_id or tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                # Skip retweets
                is_retweet = 1 if article.query_selector('[data-testid="socialContext"]') else 0

                # Get text
                text_el = article.query_selector('[data-testid="tweetText"]')
                text = text_el.inner_text() if text_el else ""
                if not text.strip():
                    continue

                # Get date
                time_el = article.query_selector("time")
                created_at = parse_tweet_date(time_el)

                # Check cutoff
                if cutoff_reached(created_at, cutoff):
                    stop = True
                    break

                # Get stats
                reply_el = article.query_selector('[data-testid="reply"] span')
                rt_el = article.query_selector('[data-testid="retweet"] span')
                like_el = article.query_selector('[data-testid="like"] span')

                reply_count = parse_count(reply_el.inner_text() if reply_el else "")
                retweet_count = parse_count(rt_el.inner_text() if rt_el else "")
                like_count = parse_count(like_el.inner_text() if like_el else "")

                is_reply = 1 if text.startswith("@") else 0
                full_url = f"https://x.com/{handle}/status/{tweet_id}"

                conn.execute(
                    """
                    INSERT OR IGNORE INTO tweets
                        (id, handle, text, created_at, retweet_count, like_count,
                         reply_count, is_retweet, is_reply, lang, url, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tweet_id,
                        handle.lower(),
                        text,
                        created_at,
                        retweet_count,
                        like_count,
                        reply_count,
                        is_retweet,
                        is_reply,
                        "",
                        full_url,
                        scraped_at,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    new_count += 1
                    no_new_streak = 0

            except Exception as e:
                log.debug(f"Tweet parse error: {e}")
                continue

        conn.commit()

        if stop:
            log.info(f"@{handle}: reached cutoff date.")
            break

        # Scroll down
        prev_count = len(seen_ids)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2.5)
        scroll_attempts += 1

        if len(seen_ids) == prev_count:
            no_new_streak += 1
        if no_new_streak >= 5:
            log.info(f"@{handle}: no new tweets after 5 scrolls — profile end reached.")
            break

    log.info(f"@{handle}: {new_count} new tweets stored ({len(seen_ids)} seen total).")

    conn.execute(
        "INSERT INTO scrape_log (handle, scraped_at, tweet_count, status) VALUES (?, ?, ?, ?)",
        (handle.lower(), scraped_at, new_count, "ok"),
    )
    conn.commit()
    return new_count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_journalists(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_credentials() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    username = os.environ.get("X_USERNAME", "")
    password = os.environ.get("X_PASSWORD", "")

    if not username or not password:
        log.error(
            "X credentials not found.\n"
            "Create a .env file in the project root with:\n"
            "  X_USERNAME=your_username\n"
            "  X_PASSWORD=your_password"
        )
        sys.exit(1)

    return username, password


def main():
    parser = argparse.ArgumentParser(description="Scrape tweets using Playwright.")
    parser.add_argument("--handle", help="Scrape a single journalist by handle")
    parser.add_argument("--months", type=int, default=config.INITIAL_SCRAPE_MONTHS)
    parser.add_argument("--no-headless", action="store_true",
                        help="Show the browser window (useful for debugging)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed.\nRun: pip install playwright && playwright install chromium")
        sys.exit(1)

    username, password = load_credentials()
    journalists = load_journalists(config.JOURNALISTS_CSV)

    if args.handle:
        journalists = [j for j in journalists if j["handle"].lower() == args.handle.lower()]
        if not journalists:
            log.error(f"Handle '{args.handle}' not found in journalist registry.")
            sys.exit(1)

    conn = get_db(config.TWEETS_DB)
    total = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.no_headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        if not login_to_x(page, username, password):
            browser.close()
            conn.close()
            sys.exit(1)

        for j in journalists:
            if j.get("active", "true").lower() != "true":
                continue
            try:
                total += scrape_profile(page, j["handle"], args.months, conn)
            except Exception as e:
                log.error(f"@{j['handle']} failed: {e}")
                continue

        browser.close()

    conn.close()
    log.info(f"Done. Total new tweets stored: {total}")


if __name__ == "__main__":
    main()
