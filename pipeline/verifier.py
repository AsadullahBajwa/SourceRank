"""
verifier.py
Cross-checks extracted claims against the local news corpus (stored RSS articles).
Assigns each claim a verdict: CONFIRMED / REFUTED / UNVERIFIED / EXPIRED.

Usage:
    python pipeline/verifier.py                  # verify all pending claims
    python pipeline/verifier.py --handle mkraju  # one journalist
    python pipeline/verifier.py --dry-run        # print verdicts without saving
"""

import sys
import os
import sqlite3
import argparse
import datetime
import logging
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_REFUTED = "REFUTED"
VERDICT_UNVERIFIED = "UNVERIFIED"
VERDICT_EXPIRED = "EXPIRED"

# Minimum FTS match score to consider a result relevant
MIN_RELEVANCE_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_claims_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def days_since(dt_str: str) -> int:
    try:
        dt = datetime.datetime.fromisoformat(dt_str)
        return (datetime.datetime.utcnow() - dt).days
    except Exception:
        return 0


def build_search_query(claim_text: str, entities: list[str]) -> str:
    """
    Build an FTS5 query from the claim text and entity list.
    Prioritises entity names, falls back to keywords from the claim.
    """
    terms = []

    # Add entity names (quoted for exact match)
    for entity in entities[:5]:
        clean = entity.strip().replace('"', "")
        if clean:
            terms.append(f'"{clean}"')

    # Add significant words from claim (skip stopwords)
    stopwords = {"the", "a", "an", "is", "are", "was", "were", "will", "be",
                 "to", "of", "in", "on", "at", "by", "for", "with", "say",
                 "says", "said", "that", "this", "it", "he", "she", "they"}
    words = [w.strip('.,!?"\'') for w in claim_text.split()
             if w.lower().strip('.,!?"\'') not in stopwords and len(w) > 3]
    terms.extend(words[:8])

    return " OR ".join(terms) if terms else claim_text[:50]


def search_news_corpus(claim_text: str, entities: list[str],
                       conn: sqlite3.Connection, top_n: int = 5) -> list[dict]:
    """
    Full-text search the articles corpus for content matching the claim.
    Returns ranked list of matching articles.
    """
    query = build_search_query(claim_text, entities)
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.source_name, a.country, a.title, a.summary, a.url, a.published,
                   bm25(articles_fts) AS score
            FROM articles_fts
            JOIN articles a ON articles_fts.rowid = a.rowid
            WHERE articles_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (query, top_n),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.debug(f"FTS search failed for query '{query}': {e}")
        return []


def determine_verdict(claim: sqlite3.Row, matches: list[dict]) -> tuple[str, str, str]:
    """
    Given a claim and matching articles, determine the verdict.
    Returns (verdict, source_name, url).
    """
    age_days = days_since(claim["extracted_at"])
    window = claim["verification_window"]

    if not matches:
        if age_days > window:
            return VERDICT_EXPIRED, "", ""
        return VERDICT_UNVERIFIED, "", ""

    # Use the top match
    top = matches[0]
    source = top.get("source_name", "")
    url = top.get("url", "")

    # Tier-1 sources confirming → CONFIRMED
    tier1_sources = {"Reuters World", "Reuters Politics", "AP News Top", "BBC World",
                     "BBC US", "Dawn Pakistan", "BBC Urdu", "Guardian UK", "BBC UK"}
    if source in tier1_sources:
        return VERDICT_CONFIRMED, source, url

    # Multiple sources corroborating → CONFIRMED
    if len(matches) >= 3:
        return VERDICT_CONFIRMED, source, url

    # Single non-tier-1 match within window → UNVERIFIED (needs more evidence)
    if age_days <= window:
        return VERDICT_UNVERIFIED, source, url

    # Past window with weak match → EXPIRED
    return VERDICT_EXPIRED, source, url


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def verify_claim(claim: sqlite3.Row, claims_conn: sqlite3.Connection,
                 dry_run: bool = False) -> str:
    entities = json.loads(claim["entities"] or "[]")
    matches = search_news_corpus(claim["claim_text"], entities, claims_conn)

    verdict, source, url = determine_verdict(claim, matches)
    verdict_at = datetime.datetime.utcnow().isoformat()

    if dry_run:
        print(json.dumps({
            "claim_id": claim["id"],
            "handle": claim["handle"],
            "claim": claim["claim_text"],
            "verdict": verdict,
            "source": source,
            "url": url,
            "matches": len(matches),
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


def get_pending_claims(handle: str | None, conn: sqlite3.Connection) -> list:
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
    parser = argparse.ArgumentParser(description="Verify pending claims against news corpus.")
    parser.add_argument("--handle", help="Verify claims for one journalist only")
    parser.add_argument("--dry-run", action="store_true", help="Print verdicts without saving")
    args = parser.parse_args()

    claims_conn = get_claims_db(config.CLAIMS_DB)
    claims = get_pending_claims(args.handle, claims_conn)

    log.info(f"Verifying {len(claims)} pending claims ...")

    verdicts = {VERDICT_CONFIRMED: 0, VERDICT_REFUTED: 0,
                VERDICT_UNVERIFIED: 0, VERDICT_EXPIRED: 0}

    for claim in claims:
        v = verify_claim(claim, claims_conn, dry_run=args.dry_run)
        if v in verdicts:
            verdicts[v] += 1

    claims_conn.close()
    log.info(f"Done. Results: {verdicts}")


if __name__ == "__main__":
    main()
