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
    python scrapers/tweet_scraper.py --only-missing    # scrape active handles with no local tweets
    python scrapers/tweet_scraper.py --only-missing --limit 5
    python scrapers/tweet_scraper.py --rescrape-complete  # ignore completed window log
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
from time_utils import parse_utc, utc_now, utc_now_iso

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

CREATE_SCRAPE_WINDOWS_TABLE = """
CREATE TABLE IF NOT EXISTS scrape_windows (
    handle       TEXT NOT NULL,
    since_date   TEXT NOT NULL,
    until_date   TEXT NOT NULL,
    scraped_at   TEXT NOT NULL,
    tweet_count  INTEGER DEFAULT 0,
    status       TEXT DEFAULT 'ok',
    error        TEXT,
    PRIMARY KEY (handle, since_date, until_date)
);
"""


def get_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(CREATE_TWEETS_TABLE)
    conn.execute(CREATE_SCRAPE_LOG_TABLE)
    conn.execute(CREATE_SCRAPE_WINDOWS_TABLE)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# X login
# ---------------------------------------------------------------------------

def fill_input(page, selectors: list[str], value: str, timeout: int = 15000) -> bool:
    """Try multiple selectors until one works. Uses type() to simulate real keystrokes."""
    for selector in selectors:
        try:
            el = page.wait_for_selector(selector, timeout=timeout)
            if el:
                el.click()
                time.sleep(0.5)
                el.triple_click()           # select any existing text
                el.press("Backspace")       # clear it
                page.keyboard.type(value, delay=50)   # type char by char
                return True
        except Exception:
            continue
    return False


def dismiss_cookies(page):
    """Dismiss cookie banner by trying all known button texts."""
    for text in ["Accept all cookies", "Accept all", "Allow all cookies", "Agree"]:
        try:
            btn = page.wait_for_selector(f'button:has-text("{text}")', timeout=3000)
            if btn:
                btn.click()
                log.info(f"Cookie banner dismissed: '{text}'")
                time.sleep(2)
                return
        except Exception:
            continue


def login_to_x(page, username: str, password: str) -> bool:
    log.info("Logging into X ...")
    try:
        # Go to homepage first to accept cookies before login flow
        page.goto("https://x.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        dismiss_cookies(page)

        # Now go to login
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)
        dismiss_cookies(page)

        # Step 2: Click the input field and type username via keyboard
        try:
            page.wait_for_selector("input", timeout=15000)
            page.evaluate("document.querySelector('input').click()")
            time.sleep(0.5)
            page.keyboard.type(username, delay=80)
            log.info("Username typed.")
        except Exception as e:
            log.error(f"Could not type username: {e}")
            page.screenshot(path="debug_login.png")
            return False

        time.sleep(1)

        # Click Next via JS to avoid focus issues
        try:
            page.evaluate("""
                const btns = Array.from(document.querySelectorAll('button'));
                const next = btns.find(b => b.textContent.trim() === 'Next');
                if (next) next.click();
            """)
        except Exception:
            page.keyboard.press("Enter")
        time.sleep(3)

        # Step 3: Handle unusual activity check
        try:
            page.wait_for_selector('input[data-testid="ocfEnterTextTextInput"]', timeout=4000)
            log.info("Unusual activity check — re-entering username.")
            page.evaluate("document.querySelector('input[data-testid=\"ocfEnterTextTextInput\"]').click()")
            page.keyboard.type(username, delay=80)
            page.keyboard.press("Enter")
            time.sleep(3)
        except Exception:
            pass

        # Step 4: Type password via keyboard
        try:
            page.wait_for_selector('input[type="password"]', timeout=15000)
            page.evaluate("document.querySelector('input[type=\"password\"]').click()")
            time.sleep(0.5)
            page.keyboard.type(password, delay=80)
            log.info("Password typed.")
        except Exception as e:
            log.error(f"Could not type password: {e}")
            page.screenshot(path="debug_password.png")
            return False

        time.sleep(1)

        # Click Login via JS
        try:
            page.evaluate("""
                const btns = Array.from(document.querySelectorAll('button'));
                const login = btns.find(b => b.textContent.trim() === 'Log in');
                if (login) login.click();
            """)
        except Exception:
            page.keyboard.press("Enter")
        time.sleep(5)

        # Verify login
        if "home" in page.url or page.query_selector('[data-testid="SideNav_AccountSwitcher_Button"]'):
            log.info("Login successful.")
            return True

        page.screenshot(path="debug_after_login.png")
        log.error("Login may have failed — screenshot saved to debug_after_login.png")
        return False

    except Exception as e:
        log.error(f"Login error: {e}")
        try:
            page.screenshot(path="debug_error.png")
        except Exception:
            pass
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
    return utc_now_iso()


def extract_tweet_id_from_url(url: str) -> str | None:
    match = re.search(r"/status/(\d+)", url or "")
    return match.group(1) if match else None


def cutoff_reached(tweet_date_str: str, cutoff: datetime.datetime) -> bool:
    try:
        dt = parse_utc(tweet_date_str)
        return dt < cutoff
    except Exception:
        return False


def scrape_month(page, handle: str, since: str, until: str,
                  conn: sqlite3.Connection, seen_ids: set) -> tuple[int, str, str]:
    """Scrape one month window for a journalist using X search."""
    search_url = (
        f"https://x.com/search?q=from%3A{handle}%20since%3A{since}%20until%3A{until}"
        f"&src=typed_query&f=live"
    )
    for attempt in range(3):
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(4)
            # Detect rate limit / error page
            if page.query_selector('text="Something went wrong"') or \
               page.query_selector('[data-testid="error-detail"]'):
                wait = 30 + attempt * 30
                log.warning(f"@{handle} [{since}]: X rate limit hit — waiting {wait}s ...")
                time.sleep(wait)
                continue
            break
        except Exception as e:
            log.warning(f"@{handle} [{since}->{until}]: failed - {e}")
            return 0, "error", str(e)
    else:
        error = "gave up after 3 attempts"
        log.error(f"@{handle} [{since}->{until}]: {error}")
        return 0, "error", error

    scraped_at = utc_now_iso()
    new_count = 0
    no_new_streak = 0
    scroll_attempts = 0
    max_scrolls = 100

    # Get current scroll height to do incremental scrolling
    current_y = 0

    while scroll_attempts < max_scrolls:
        # Extract all currently visible tweets before scrolling
        tweet_articles = page.query_selector_all('article[data-testid="tweet"]')

        for article in tweet_articles:
            try:
                link_el = article.query_selector('a[href*="/status/"]')
                tweet_url = link_el.get_attribute("href") if link_el else ""
                tweet_id = extract_tweet_id_from_url(tweet_url)
                if not tweet_id or tweet_id in seen_ids:
                    continue
                seen_ids.add(tweet_id)

                social_ctx = article.query_selector('[data-testid="socialContext"]')
                ctx_text = social_ctx.inner_text().lower() if social_ctx else ""
                is_pinned = "pinned" in ctx_text
                is_retweet = 1 if (social_ctx and not is_pinned) else 0

                if is_pinned:
                    continue

                text_el = article.query_selector('[data-testid="tweetText"]')
                text = text_el.inner_text() if text_el else ""
                if not text.strip():
                    continue

                time_el = article.query_selector("time")
                created_at = parse_tweet_date(time_el)

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
                        tweet_id, handle.lower(), text, created_at,
                        retweet_count, like_count, reply_count,
                        is_retweet, is_reply, "", full_url, scraped_at,
                    ),
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    new_count += 1

            except Exception as e:
                log.debug(f"Tweet parse error: {e}")
                continue

        conn.commit()

        prev_seen = len(seen_ids)

        # Scroll in small increments (600px) so tweets have time to render
        # before they leave the viewport and get removed from DOM
        current_y += 600
        page.evaluate(f"window.scrollTo(0, {current_y})")
        time.sleep(3.5)   # wait for lazy-loaded tweets to render

        # Every 5 scrolls do a full-height jump to keep momentum
        if scroll_attempts % 5 == 0:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(4)

        scroll_attempts += 1

        if len(seen_ids) == prev_seen:
            no_new_streak += 1
        else:
            no_new_streak = 0
        if no_new_streak >= 10:
            break

    return new_count, "ok", ""


def window_completed(conn: sqlite3.Connection, handle: str, since: str, until: str) -> bool:
    row = conn.execute(
        """
        SELECT status FROM scrape_windows
        WHERE handle = ? AND since_date = ? AND until_date = ?
        """,
        (handle.lower(), since, until),
    ).fetchone()
    return bool(row and row[0] == "ok")


def mark_window(conn: sqlite3.Connection, handle: str, since: str, until: str,
                tweet_count: int, status: str, error: str = "") -> None:
    conn.execute(
        """
        INSERT INTO scrape_windows
            (handle, since_date, until_date, scraped_at, tweet_count, status, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(handle, since_date, until_date) DO UPDATE SET
            scraped_at = excluded.scraped_at,
            tweet_count = excluded.tweet_count,
            status = excluded.status,
            error = excluded.error
        """,
        (
            handle.lower(), since, until, utc_now_iso(),
            tweet_count, status, error,
        ),
    )
    conn.commit()


def scrape_profile(page, handle: str, months: int, conn: sqlite3.Connection,
                   rescrape_complete: bool = False) -> int:
    """Scrape a journalist month by month to maximise tweet coverage."""
    now = utc_now()
    upper_cutoff = now - datetime.timedelta(days=config.SKIP_RECENT_DAYS)
    lower_cutoff = now - datetime.timedelta(days=months * 30)

    log.info(f"Scraping @{handle} | window: {lower_cutoff.date()} → {upper_cutoff.date()}")

    # Build list of monthly windows from oldest to newest
    windows = []
    cursor = lower_cutoff
    while cursor < upper_cutoff:
        next_cursor = min(cursor + datetime.timedelta(days=30), upper_cutoff)
        windows.append((cursor.strftime("%Y-%m-%d"), next_cursor.strftime("%Y-%m-%d")))
        cursor = next_cursor

    seen_ids: set[str] = set()
    total_new = 0
    scraped_at = utc_now_iso()

    for since, until in windows:
        if not rescrape_complete and window_completed(conn, handle, since, until):
            log.info(f"  @{handle}: month {since} -> {until} already complete, skipping")
            continue
        log.info(f"  @{handle}: month {since} -> {until}")
        new, status, error = scrape_month(page, handle, since, until, conn, seen_ids)
        mark_window(conn, handle, since, until, new, status, error)
        total_new += new
        time.sleep(8 + (len(windows) % 3) * 2)  # 8-12s between months to avoid rate limiting

    log.info(f"@{handle}: {total_new} new tweets stored across {len(windows)} months.")
    conn.execute(
        "INSERT INTO scrape_log (handle, scraped_at, tweet_count, status) VALUES (?, ?, ?, ?)",
        (handle.lower(), scraped_at, total_new, "ok"),
    )
    conn.commit()
    return total_new


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def load_journalists(csv_path: str) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def select_journalists(journalists: list[dict], handle: str | None = None,
                       only_missing: bool = False,
                       tweet_counts: dict[str, int] | None = None,
                       limit: int | None = None) -> list[dict]:
    selected = list(journalists)
    if handle:
        selected = [j for j in selected if j["handle"].lower() == handle.lower()]

    if only_missing:
        counts = tweet_counts or {}
        selected = [
            j for j in selected
            if j.get("active", "true").lower() == "true"
            and counts.get(j["handle"].lower(), 0) == 0
        ]

    if limit is not None and limit > 0:
        selected = selected[:limit]

    return selected


def load_credentials() -> tuple[str, str]:
    try:
        from dotenv import load_dotenv
        # Windows sometimes saves .env as UTF-16 — try both encodings
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        try:
            load_dotenv(dotenv_path=env_path, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(dotenv_path=env_path, encoding="utf-16")
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
    parser.add_argument("--only-missing", action="store_true",
                        help="Only scrape active journalists with no local tweets yet")
    parser.add_argument("--limit", type=int,
                        help="Maximum number of selected journalists to scrape this run")
    parser.add_argument("--rescrape-complete", action="store_true",
                        help="Re-run windows already marked complete")
    parser.add_argument("--no-headless", action="store_true",
                        help="Show the browser window (useful for debugging)")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Playwright not installed.\nRun: pip install playwright && playwright install chromium")
        sys.exit(1)

    session_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "session.json")
    if not os.path.exists(session_path):
        log.error(
            "No saved session found.\n"
            "Run this first to log in manually:\n"
            "  python save_session.py"
        )
        sys.exit(1)

    journalists = load_journalists(config.JOURNALISTS_CSV)

    journalists = select_journalists(journalists, handle=args.handle)
    if args.handle and not journalists:
        log.error(f"Handle '{args.handle}' not found in journalist registry.")
        sys.exit(1)

    if args.only_missing:
        from scripts.audit_registry import load_tweet_counts
        journalists = select_journalists(
            journalists,
            only_missing=True,
            tweet_counts=load_tweet_counts(),
            limit=args.limit,
        )
        log.info(f"Targeting {len(journalists)} active handles with no local tweets.")
    elif args.limit:
        journalists = select_journalists(journalists, limit=args.limit)
        log.info(f"Limiting run to {len(journalists)} selected journalist(s).")

    conn = get_db(config.TWEETS_DB)
    total = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.no_headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            storage_state=session_path,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = context.new_page()
        log.info("Session loaded — skipping login.")

        for j in journalists:
            if j.get("active", "true").lower() != "true":
                continue
            try:
                total += scrape_profile(
                    page, j["handle"], args.months, conn,
                    rescrape_complete=args.rescrape_complete,
                )
            except Exception as e:
                log.error(f"@{j['handle']} failed: {e}")
                continue

        browser.close()

    conn.close()
    log.info(f"Done. Total new tweets stored: {total}")


if __name__ == "__main__":
    main()
