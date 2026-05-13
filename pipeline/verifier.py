"""
verifier.py
Cross-checks extracted claims against:
  1. Local RSS news corpus (fast, no network)
  2. Google News RSS search (historical coverage, no API key needed)

Assigns each claim a verdict: CONFIRMED / REFUTED / UNVERIFIED / EXPIRED.

Usage:
    python pipeline/verifier.py                  # verify all pending claims
    python pipeline/verifier.py --handle mkraju  # one journalist
    python pipeline/verifier.py --dry-run        # print verdicts without saving
    python pipeline/verifier.py --no-google      # skip Google News (offline mode)
    python pipeline/verifier.py --recheck        # re-verify UNVERIFIED and EXPIRED claims
"""

import sys
import os
import sqlite3
import argparse
import datetime
import logging
import json
import time
import re

import requests
import feedparser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

VERDICT_CONFIRMED  = "CONFIRMED"
VERDICT_REFUTED    = "REFUTED"
VERDICT_UNVERIFIED = "UNVERIFIED"
VERDICT_EXPIRED    = "EXPIRED"

TIER1_SOURCES = {
    "Reuters World", "Reuters Politics", "AP News Top",
    "BBC World", "BBC US", "Dawn Pakistan", "BBC Urdu",
    "Guardian UK", "BBC UK", "Google News",
}

# Delay between Google News requests to avoid rate limiting
GOOGLE_NEWS_DELAY = 3.0

# (claim_pattern, refutation_pattern) — if both match, the claim is contradicted.
CONTRADICTION_PAIRS = [
    (r"will resign",
     r"den(ies|ied) resignation|refuses to resign|no plans to resign|stays on"),
    (r"arrested",
     r"not arrested|released without charge|acquitted|cleared"),
    (r"fired|dismissed|ousted|removed",
     r"keeps (his|her|their) job|reinstated|not fired|stays as"),
    (r"will (be )?charged|indicted",
     r"no charges|charges dropped|not charged|cleared"),
    (r"confirmed|verified|authenticated",
     r"fake|false|fabricated|denied|hoax"),
    (r"won|victory|elected",
     r"lost|defeated|conceded|election loss"),
    (r"died|dead|passed away",
     r"alive|not dead|recovering|discharged from hospital"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_claims_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    migrate_claims_schema(conn)
    backfill_tweet_created_at(conn)
    return conn


def migrate_claims_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(claims)").fetchall()}
    migrations = {
        "tweet_created_at": "ALTER TABLE claims ADD COLUMN tweet_created_at TEXT",
        "confidence": "ALTER TABLE claims ADD COLUMN confidence REAL DEFAULT 0.5",
    }
    for col, ddl in migrations.items():
        if col not in cols:
            log.info(f"Adding missing claims column: {col}")
            conn.execute(ddl)
    conn.commit()


def backfill_tweet_created_at(conn: sqlite3.Connection) -> None:
    """Populate old claims with tweet timestamps from tweets.db when available."""
    if not os.path.exists(config.TWEETS_DB):
        return
    missing = conn.execute(
        "SELECT COUNT(*) FROM claims WHERE tweet_created_at IS NULL OR tweet_created_at = ''"
    ).fetchone()[0]
    if not missing:
        return

    conn.execute("ATTACH DATABASE ? AS tweets_db", (config.TWEETS_DB,))
    try:
        conn.execute(
            """
            UPDATE claims
            SET tweet_created_at = (
                SELECT t.created_at
                FROM tweets_db.tweets t
                WHERE t.id = claims.tweet_id
            )
            WHERE tweet_created_at IS NULL OR tweet_created_at = ''
            """
        )
        conn.commit()
        log.info(f"Backfilled tweet_created_at for up to {missing} old claims")
    finally:
        conn.execute("DETACH DATABASE tweets_db")


def days_since(dt_str: str) -> int:
    try:
        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00")).replace(tzinfo=None)
        return (datetime.datetime.utcnow() - dt).days
    except Exception:
        return 0


def claim_reference_date(claim: sqlite3.Row) -> str:
    """Prefer the original tweet timestamp; fall back for legacy rows."""
    try:
        tweet_created_at = claim["tweet_created_at"]
    except (IndexError, KeyError):
        tweet_created_at = ""
    return tweet_created_at or claim["extracted_at"]


def build_keyword_query(claim_text: str, entities: list[str]) -> str:
    """Build a search query prioritising entity names then keywords."""
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "will", "be",
        "to", "of", "in", "on", "at", "by", "for", "with", "say",
        "says", "said", "that", "this", "it", "he", "she", "they",
        "have", "has", "had", "not", "but", "and", "or", "its",
    }
    terms = [e.strip() for e in entities[:4] if e.strip()]
    words = [
        w.strip('.,!?"\'')
        for w in claim_text.split()
        if w.lower().strip('.,!?"\'') not in stopwords and len(w) > 3
    ]
    terms.extend(words[:6])
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in terms:
        if t.lower() not in seen:
            seen.add(t.lower())
            unique.append(t)
    return " ".join(unique[:8])


# ---------------------------------------------------------------------------
# 1. Local corpus search (FTS5)
# ---------------------------------------------------------------------------

def search_local_corpus(claim_text: str, entities: list[str],
                        conn: sqlite3.Connection) -> list[dict]:
    query_terms = []
    for e in entities[:4]:
        clean = e.strip().replace('"', "")
        if clean:
            query_terms.append(f'"{clean}"')
    stopwords = {"the","a","an","is","are","was","were","will","be","to","of",
                 "in","on","at","by","for","with","say","says","said","that","this"}
    words = [w.strip('.,!?"\'') for w in claim_text.split()
             if w.lower().strip('.,!?"\'') not in stopwords and len(w) > 3]
    query_terms.extend(words[:6])
    fts_query = " OR ".join(query_terms) if query_terms else claim_text[:50]

    try:
        rows = conn.execute(
            """
            SELECT a.source_name, a.title, a.url, a.published,
                   bm25(articles_fts) AS score
            FROM articles_fts
            JOIN articles a ON articles_fts.rowid = a.rowid
            WHERE articles_fts MATCH ?
            ORDER BY score
            LIMIT 5
            """,
            (fts_query,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug(f"Local FTS failed: {e}")
        return []


# ---------------------------------------------------------------------------
# 2. Google News RSS search (historical, no API key)
# ---------------------------------------------------------------------------

def search_google_news(claim_text: str, entities: list[str],
                       tweet_date: str) -> list[dict]:
    """
    Search Google News RSS for the claim.
    Filters results to only articles published AFTER the tweet date.
    """
    query = build_keyword_query(claim_text, entities)
    if not query.strip():
        return []

    encoded = requests.utils.quote(query)
    url = (
        f"https://news.google.com/rss/search"
        f"?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    )

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SourceRankBot/1.0)"
        })
        if resp.status_code != 200:
            return []

        feed = feedparser.parse(resp.content)
        results = []

        # Parse tweet date for filtering
        try:
            tweet_dt = datetime.datetime.fromisoformat(
                tweet_date.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            tweet_dt = datetime.datetime.utcnow() - datetime.timedelta(days=365)

        for entry in feed.entries[:10]:
            title = getattr(entry, "title", "")
            link  = getattr(entry, "link", "")
            pub   = getattr(entry, "published", "")

            # Parse published date
            try:
                pub_dt = datetime.datetime(*entry.published_parsed[:6])
            except Exception:
                pub_dt = datetime.datetime.utcnow()

            # Only count articles published after the tweet was made
            if pub_dt < tweet_dt:
                continue

            results.append({
                "source_name": "Google News",
                "title": title,
                "url": link,
                "published": pub,
                "pub_dt": pub_dt,
            })

        return results

    except Exception as e:
        log.debug(f"Google News search failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------

def check_contradictions(claim_text: str, articles: list[dict]) -> bool:
    """Return True if any article title contradicts the claim."""
    for claim_pat, refute_pat in CONTRADICTION_PAIRS:
        if not re.search(claim_pat, claim_text, re.IGNORECASE):
            continue
        for article in articles:
            title = article.get("title", "")
            if re.search(refute_pat, title, re.IGNORECASE):
                return True
    return False


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------

def determine_verdict(claim: sqlite3.Row,
                      local_matches: list[dict],
                      google_matches: list[dict]) -> tuple[str, str, str]:
    age_days = days_since(claim_reference_date(claim))
    window   = claim["verification_window"]
    all_matches = local_matches + google_matches

    if not all_matches:
        if age_days > window:
            return VERDICT_EXPIRED, "", ""
        return VERDICT_UNVERIFIED, "", ""

    top    = all_matches[0]
    source = top.get("source_name", "")
    url    = top.get("url", "")

    # Contradiction check — REFUTED if tier-1 source confirms the contradiction,
    # UNVERIFIED if only lower-tier sources carry the refuting story.
    if check_contradictions(claim["claim_text"], all_matches):
        has_tier1 = any(a.get("source_name") in TIER1_SOURCES for a in all_matches)
        if has_tier1:
            return VERDICT_REFUTED, source, url
        return VERDICT_UNVERIFIED, source, url

    # Tier-1 source → CONFIRMED
    if source in TIER1_SOURCES and google_matches:
        return VERDICT_CONFIRMED, source, url

    # Local tier-1 match
    if source in TIER1_SOURCES and local_matches:
        return VERDICT_CONFIRMED, source, url

    # 3+ sources corroborating → CONFIRMED
    if len(all_matches) >= 3:
        return VERDICT_CONFIRMED, source, url

    # Weak single match
    if age_days <= window:
        return VERDICT_UNVERIFIED, source, url

    return VERDICT_EXPIRED, source, url


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def verify_claim(claim: sqlite3.Row, claims_conn: sqlite3.Connection,
                 use_google: bool = True, dry_run: bool = False) -> str:
    entities    = json.loads(claim["entities"] or "[]")
    claim_text  = claim["claim_text"]
    tweet_date  = claim_reference_date(claim)

    # Step 1: local corpus
    local_matches = search_local_corpus(claim_text, entities, claims_conn)

    # Step 2: Google News (if no strong local match)
    google_matches = []
    if use_google and len(local_matches) < 3:
        google_matches = search_google_news(claim_text, entities, tweet_date)
        time.sleep(GOOGLE_NEWS_DELAY)

    verdict, source, url = determine_verdict(claim, local_matches, google_matches)
    verdict_at = datetime.datetime.utcnow().isoformat()

    log.info(
        f"@{claim['handle']:20s}  "
        f"verdict={verdict:12s}  "
        f"local={len(local_matches)}  google={len(google_matches)}  "
        f"source={source[:30] if source else 'none'}"
    )

    if dry_run:
        print(json.dumps({
            "handle":  claim["handle"],
            "claim":   claim_text[:100],
            "verdict": verdict,
            "source":  source,
            "url":     url,
            "local_matches":  len(local_matches),
            "google_matches": len(google_matches),
        }, indent=2))
        return verdict

    claims_conn.execute(
        """
        UPDATE claims
        SET verdict = ?, verdict_source = ?, verdict_url = ?, verdict_at = ?
        WHERE id = ?
        """,
        (verdict, source, url, verdict_at, claim["id"]),
    )
    claims_conn.commit()
    return verdict


def get_pending_claims(handle: str | None, conn: sqlite3.Connection,
                       recheck: bool = False) -> list:
    if recheck:
        query = "SELECT * FROM claims WHERE verdict IN ('PENDING', 'UNVERIFIED', 'EXPIRED')"
    else:
        query = "SELECT * FROM claims WHERE verdict = 'PENDING'"
    params = []
    if handle:
        query += " AND handle = ?"
        params.append(handle.lower())
    return conn.execute(query, params).fetchall()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Verify claims against news corpus + Google News.")
    parser.add_argument("--handle",    help="Verify one journalist only")
    parser.add_argument("--dry-run",   action="store_true")
    parser.add_argument("--no-google", action="store_true", help="Skip Google News (offline mode)")
    parser.add_argument("--recheck",   action="store_true",
                        help="Re-verify UNVERIFIED and EXPIRED claims in addition to PENDING")
    args = parser.parse_args()

    claims_conn = get_claims_db(config.CLAIMS_DB)
    claims      = get_pending_claims(args.handle, claims_conn, recheck=args.recheck)

    use_google = not args.no_google
    log.info(f"Verifying {len(claims)} claims | Google News: {'ON' if use_google else 'OFF'}"
             + (" | recheck=ON" if args.recheck else ""))

    verdicts = {
        VERDICT_CONFIRMED: 0, VERDICT_REFUTED: 0,
        VERDICT_UNVERIFIED: 0, VERDICT_EXPIRED: 0,
    }

    for claim in claims:
        v = verify_claim(claim, claims_conn,
                         use_google=use_google, dry_run=args.dry_run)
        if v in verdicts:
            verdicts[v] += 1

    claims_conn.close()
    log.info(f"Done. {verdicts}")


if __name__ == "__main__":
    main()
