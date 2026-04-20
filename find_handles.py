"""
find_handles.py
Searches X for each journalist by name, finds the correct handle,
and updates journalists.csv automatically.

Targets journalists where active=false OR where a previous scrape returned 0 tweets.

Usage:
    python find_handles.py               # fix all inactive journalists
    python find_handles.py --all         # re-verify every journalist in the list
    python find_handles.py --name "Sami Ibrahim"  # fix one journalist by name
"""

import os
import sys
import csv
import time
import argparse
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SESSION_PATH = os.path.join(config.DATA_DIR, "session.json")
JOURNALISTS_CSV = config.JOURNALISTS_CSV


# ---------------------------------------------------------------------------
# Search X people tab and extract candidate handles
# ---------------------------------------------------------------------------

def search_journalist(page, name: str, country: str) -> list[dict]:
    """
    Search X people tab for a journalist name.
    Returns list of candidates: [{handle, display_name, bio, followers}]
    """
    query = name.replace(" ", "%20")
    url = f"https://x.com/search?q={query}&f=user"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
    except Exception as e:
        log.warning(f"Search failed for '{name}': {e}")
        return []

    candidates = []
    # Each user result is inside a cell with a link to the profile
    user_cells = page.query_selector_all('[data-testid="UserCell"]')

    for cell in user_cells[:8]:  # check top 8 results
        try:
            # Get handle from profile link
            link = cell.query_selector('a[href^="/"]')
            if not link:
                continue
            href = link.get_attribute("href") or ""
            handle = href.strip("/").split("/")[0]
            if not handle or handle in ("search", "home", "explore"):
                continue

            # Get display name
            name_el = cell.query_selector('[data-testid="UserName"]')
            display_name = name_el.inner_text().strip() if name_el else ""

            # Get bio
            bio_el = cell.query_selector('[data-testid="UserDescription"]')
            bio = bio_el.inner_text().strip() if bio_el else ""

            # Check for verified badge
            verified = bool(cell.query_selector('[data-testid="icon-verified"]'))

            candidates.append({
                "handle": handle,
                "display_name": display_name,
                "bio": bio,
                "verified": verified,
            })
        except Exception:
            continue

    return candidates


def score_candidate(candidate: dict, journalist: dict) -> float:
    """
    Score how likely a candidate is the right journalist.
    Higher = better match.
    """
    score = 0.0
    name_lower = journalist["name"].lower()
    country = journalist.get("country", "").lower()

    display = candidate["display_name"].lower()
    bio = candidate["bio"].lower()
    handle = candidate["handle"].lower()

    # Name match in display name
    name_parts = name_lower.split()
    matched_parts = sum(1 for p in name_parts if p in display)
    score += (matched_parts / len(name_parts)) * 40

    # Name parts in handle
    handle_matches = sum(1 for p in name_parts if p in handle)
    score += handle_matches * 10

    # Country keywords in bio
    country_keywords = {
        "pakistan": ["pakistan", "pak", "islamabad", "karachi", "lahore", "geo", "ary", "dawn", "dunya"],
        "us": ["washington", "dc", "nbc", "cnn", "abc", "msnbc", "npr", "politico"],
        "uk": ["london", "bbc", "guardian", "sky", "itv"],
        "india": ["india", "delhi", "mumbai", "ndtv", "hindu"],
    }
    for kw in country_keywords.get(country, []):
        if kw in bio or kw in handle:
            score += 8
            break

    # Journalist/media keywords in bio
    media_keywords = ["journalist", "anchor", "reporter", "editor", "correspondent",
                      "news", "media", "tv", "channel", "analyst"]
    for kw in media_keywords:
        if kw in bio:
            score += 5
            break

    # Verified badge bonus
    if candidate["verified"]:
        score += 15

    return score


def find_best_handle(page, journalist: dict) -> str | None:
    """Find the best matching X handle for a journalist."""
    name = journalist["name"]
    candidates = search_journalist(page, name, journalist.get("country", ""))

    if not candidates:
        log.warning(f"No candidates found for '{name}'")
        return None

    scored = [(score_candidate(c, journalist), c) for c in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best = scored[0]

    log.info(f"'{name}' → @{best['handle']} (score: {best_score:.0f}, verified: {best['verified']})")
    log.info(f"  Display: {best['display_name']}")
    log.info(f"  Bio: {best['bio'][:100]}")

    # Only accept if score is high enough
    if best_score >= 30:
        return best["handle"]

    log.warning(f"Best match score too low ({best_score:.0f}) — skipping '{name}'")
    return None


# ---------------------------------------------------------------------------
# CSV update
# ---------------------------------------------------------------------------

def load_journalists() -> list[dict]:
    with open(JOURNALISTS_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_journalists(journalists: list[dict]):
    fieldnames = ["handle", "name", "beat", "country", "followers_tier", "verified", "active"]
    with open(JOURNALISTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(journalists)


def zero_tweet_handles() -> set[str]:
    """Return handles that had 0 tweets scraped in their last scrape attempt."""
    import sqlite3
    if not os.path.exists(config.TWEETS_DB):
        return set()
    conn = sqlite3.connect(config.TWEETS_DB)
    rows = conn.execute("""
        SELECT handle FROM scrape_log
        WHERE tweet_count = 0 AND status = 'ok'
        GROUP BY handle
        HAVING MAX(scraped_at) = MAX(scraped_at)
    """).fetchall()
    conn.close()
    return {r[0].lower() for r in rows}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find and fix journalist X handles.")
    parser.add_argument("--all", action="store_true", help="Re-verify all journalists, not just inactive ones")
    parser.add_argument("--name", help="Fix a specific journalist by name")
    args = parser.parse_args()

    if not os.path.exists(SESSION_PATH):
        log.error("No session found. Run: python save_session.py")
        sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    journalists = load_journalists()
    zero_handles = zero_tweet_handles()

    # Decide which journalists to fix
    if args.name:
        targets = [j for j in journalists if j["name"].lower() == args.name.lower()]
    elif args.all:
        targets = journalists
    else:
        # Fix inactive ones + ones with 0 tweets
        targets = [
            j for j in journalists
            if j.get("active", "true").lower() == "false"
            or j["handle"].lower() in zero_handles
        ]

    if not targets:
        log.info("No journalists to fix. Use --all to re-verify everyone.")
        return

    log.info(f"Will search for {len(targets)} journalist(s).")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            storage_state=SESSION_PATH,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        page = context.new_page()

        updated = 0
        for journalist in targets:
            old_handle = journalist["handle"]
            new_handle = find_best_handle(page, journalist)

            if new_handle and new_handle.lower() != old_handle.lower():
                log.info(f"Updating @{old_handle} → @{new_handle} for '{journalist['name']}'")
                # Update in the full journalists list
                for j in journalists:
                    if j["handle"] == old_handle:
                        j["handle"] = new_handle
                        j["active"] = "true"
                        break
                updated += 1
            elif new_handle:
                # Handle was already correct — just re-activate
                for j in journalists:
                    if j["handle"] == old_handle:
                        j["active"] = "true"
                        break
                log.info(f"@{old_handle} confirmed correct for '{journalist['name']}'")
            else:
                log.warning(f"Could not find handle for '{journalist['name']}' — keeping @{old_handle}")

            time.sleep(2)

        browser.close()

    save_journalists(journalists)
    log.info(f"Done. Updated {updated} handle(s). journalists.csv saved.")

    if updated > 0:
        log.info("Now run: python scrapers/tweet_scraper.py --months 6 --no-headless")


if __name__ == "__main__":
    main()
