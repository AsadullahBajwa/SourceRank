# SourceRank Phase Plan

This document tracks the next practical work after the local MVP.

## Phase 2 - Data Coverage

Goal: expand from a small proof-of-concept dataset to enough tweet and claim coverage for meaningful comparisons.

Current blockers:

- Only a minority of active handles have local tweets.
- RSS source depth is uneven across countries.
- Some extracted claims need manual review before score movement is trusted.

Next actions:

1. Run `python scripts/coverage_plan.py --limit 10`.
2. Backfill a small batch with `python scrapers/tweet_scraper.py --only-missing --limit 5`.
3. Process new tweets with `python pipeline/claim_extractor.py --limit 500`.
4. Audit unresolved claims with `python scripts/claim_review.py --limit 25`.
5. Re-run verification with `python pipeline/verifier.py --recheck --limit 100`.
6. Refresh RSS source depth with `python scripts/source_coverage.py`.

## Phase 3 - Product Surface

Goal: make the static site useful as a public-facing portfolio artifact.

Completed:

- Leaderboard
- Journalist profile page
- Score trends page
- Coverage page
- Methodology page
- Dispute workflow page

Next actions:

1. Add a compact methodology diagram to the site itself.
2. Add better empty states for journalists with no claims.
3. Add direct links from dispute workflow to GitHub Issues.

## Phase 4 - Browser Extension

Goal: overlay credibility signals directly on X.

Completed:

- Manifest V3 scaffold
- Configurable SourceRank site URL
- Score data fetch
- Inline score badges
- Badge click-through to profile pages
- Hover/focus details

Next actions:

1. Add verdict totals to hover cards.
2. Add a browser-action popup with current configuration and data freshness.
3. Add signed build instructions for Chrome and Firefox.
4. Add tests for pure content-script helpers if the extension grows further.

## Quality Bar

Each phase change should include one of:

- A regression test.
- A static validation script.
- A README or docs update.
- A generated public artifact update when the site depends on it.

Current CI runs:

- Python compilation across core scripts.
- `python -m unittest discover -s tests -v`.
- `python scripts/site_check.py` for static site pages and JSON artifacts.
- Local `.html` links between static site pages.
