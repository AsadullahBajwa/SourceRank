# SourceRank

**A journalist credibility scoring system for X (Twitter)**

SourceRank tracks what journalists claim on X, verifies whether those claims turned out to be true, and computes a transparent credibility score for each journalist — updated weekly.

The goal is simple: when you see a breaking news tweet, you should instantly know how much weight to give it. SourceRank answers that question with data, not gut feeling.

---

## The Problem

X is flooded with journalists, analysts, and "sources" making bold claims — breaking news, predictions, exclusives. There is currently no systematic way to answer:

- Did this journalist's last 10 "BREAKING" tweets actually pan out?
- How often does this person tweet unverified claims and never follow up?
- Is this account a serious journalist or a high-follower spam machine?

Tools like NewsGuard or Media Bias/Fact Check rate *news organizations*, not individual journalists, and they do it manually and slowly. Nothing tracks individual journalist accuracy on X in a retroactive, automated, data-driven way.

SourceRank fills that gap.

---

## How It Works

### 1. Journalist Registry
A curated list of journalists per country and beat. Each journalist has a handle, beat (politics, finance, tech, etc.), country, and follower tier.

### 2. Tweet Scraping
Tweets are scraped and stored locally in a SQLite database. The pipeline pulls historical tweets up to 18 months back, skipping the newest 30 days so claims have time to resolve, and runs incrementally on a weekly schedule.

### 3. Claim Extraction
Each tweet is analyzed to extract structured claims:
- **Claim type** — Breaking news, prediction, exclusive, statistic
- **Claim text** — The specific assertion made
- **Entities** — People, organizations, countries mentioned
- **Verification window** — How long to wait before checking (24h / 7d / 30d)

### 4. Verification Engine
Each extracted claim is cross-referenced against:
- RSS feeds from major news wires (Reuters, AP, BBC)
- Country-specific sources (Dawn, Geo, ARY for Pakistan; NYT, WaPo for US, etc.)
- Google News search within the verification window

Each claim receives a verdict: `CONFIRMED`, `REFUTED`, `UNVERIFIED`, or `EXPIRED`.

### 5. Scoring
Each journalist receives a composite credibility score (0–100) based on:

| Dimension | Description | Weight |
|---|---|---|
| Accuracy Rate | % of claims confirmed true | 40% |
| Prediction Score | Breaking/exclusive claims verified | 25% |
| Correction Behavior | Speed and transparency of corrections | 15% |
| Source Quality | Cites credible sources vs. anonymous | 10% |
| Spam Index | Tweet volume, repetition, deletion patterns | 10% |

### 6. Leaderboard
Results are published as a static website (hosted on GitHub Pages) with:
- Sortable leaderboard by score, country, and beat
- Per-journalist profile with claim history and verdicts
- Score trend over time (weekly snapshots)

---

## Project Structure

```
source-ranker/
├── data/
│   ├── journalists.csv          # curated journalist list
│   ├── rss_sources.csv          # verification news sources per country
│   └── db/
│       ├── tweets.db            # SQLite: all scraped tweets
│       └── claims.db            # SQLite: extracted claims + verdicts
├── scrapers/
│   ├── tweet_scraper.py         # scrapes tweets per journalist
│   └── news_scraper.py          # pulls and stores RSS feed articles
├── pipeline/
│   ├── claim_extractor.py       # extracts structured claims from tweets
│   ├── verifier.py              # cross-checks claims against news corpus
│   └── scorer.py                # computes final credibility scores
├── output/
│   └── site/                    # static HTML leaderboard
│       ├── index.html
│       ├── journalist.html
│       └── data/
│           └── scores.json      # updated weekly, drives the frontend
├── scheduler.py                 # orchestrates the full pipeline
├── config.py                    # paths, weights, API keys (not committed)
├── requirements.txt
└── README.md
```

---

## Roadmap

### Phase 1 - Local MVP (Current)
- [x] Project structure and schema design
- [x] Tweet scraper using Playwright session auth (no X API cost)
- [x] SQLite schema for tweets, claims, verdicts
- [x] Claim extraction pipeline (local LLM via Ollama)
- [x] RSS-based verification engine
- [x] Scoring algorithm with minimum-sample rank eligibility
- [x] Static leaderboard site
- [x] GitHub Actions static validation

### Phase 2 — Country Expansion
Scale the journalist registry to cover top 100 journalists per country:

| Country | Primary verification sources | Status |
|---|---|---|
| United States | AP, Reuters, NYT, WaPo, Politico | Planned |
| United Kingdom | BBC, Guardian, Reuters, Sky News | Planned |
| Pakistan | Dawn, Geo, ARY, Express Tribune | Planned |
| India | The Hindu, NDTV, Indian Express | Planned |
| Nigeria / Kenya | Channels TV, Nation, Daily Nation | Planned |

### Phase 3 — Cross-Country Claim Verification
- Detect when a claim is confirmed in one country's media but denied in another
- Build a Global Credibility Index for cross-border comparison
- Flag coordinated narratives across journalist networks

### Phase 4 — Browser Extension
- Chrome/Firefox extension that overlays the credibility score directly on X
- Shows score, accuracy rate, and pending unverified claims inline on tweets

---

## Tech Stack

| Layer | Tool | Notes |
|---|---|---|
| Tweet scraping | Playwright + saved X session | No X API cost |
| Storage | SQLite | Local, portable |
| Claim extraction | Ollama + qwen2.5:7b | Runs locally, no API cost |
| News verification | RSS parsing + Google News free | No cost |
| Scoring | Python | Custom weighted model |
| Frontend | Static HTML + DataTables.js | No server needed |
| Hosting | GitHub Pages | Free |
| Scheduling | Windows Task Scheduler locally; GitHub Actions validates code/site only | Free |

**Estimated monthly cost to run: $0**

Because GitHub Actions cannot access the local X session or Ollama instance, leaderboard refreshes are generated locally. After a successful local run, commit the updated `output/site/data/scores.json` artifact so GitHub Pages serves the new leaderboard data.

### Local maintenance

```bash
# Check roster balance, active duplicate people, and active handles with no local tweets
python scripts/audit_registry.py
python scripts/audit_registry.py --json

# Check local DB and leaderboard health
python scheduler.py --status
python scheduler.py --status --json
python scheduler.py --from-step extract --through-step score --dry-run

# Run the regression suite
python -m unittest discover -s tests -v

# Refresh RSS feeds; fetch health is stored in claims.db/feed_fetch_log
python scrapers/news_scraper.py
python scrapers/news_scraper.py --tier 1 --limit 5 --dry-run

# Backfill active journalists that still have no local tweets
python scrapers/tweet_scraper.py --only-missing --list-selected
python scrapers/tweet_scraper.py --only-missing
python scrapers/tweet_scraper.py --only-missing --limit 5

# Process scraped tweets in bounded local LLM batches
python pipeline/claim_extractor.py --limit 500

# Verify claims in bounded batches, especially when Google News is enabled
python pipeline/verifier.py --recheck --limit 100

# Refresh the public leaderboard artifact after local pipeline work
python pipeline/scorer.py
```

Scoring writes both the latest public artifact (`output/site/data/scores.json`) and a dated snapshot under `output/site/data/history/` for future trend views.

---

## Scoring Methodology

The credibility score is designed to be:

- **Transparent** - formula and weights are public and auditable
- **Retroactive** - scores are based on what actually happened, not intent
- **Apolitical** - the same logic applies regardless of a journalist's political leaning
- **Minimum-sample aware** - journalists need enough resolved claims before they are ranked

A journalist can improve their score by:
- Making accurate claims that get confirmed
- Issuing timely corrections when wrong
- Citing named, credible sources

A journalist's score drops when:
- Claims expire unverified
- Claims are actively refuted
- Tweets are deleted without public retraction
- High-volume, repetitive posting patterns are detected

---

## Anti-Spam Detection

The spam index penalizes:
- Deleting tweets without issuing a correction
- Posting the same claim multiple times with slight rewording
- Disproportionate ratio of retweets vs. original reporting
- Engagement-bait phrasing ("I'm told...", "Sources say..." with no follow-up)

---

## Contributing

The journalist registry (`data/journalists.csv`) is manually curated. Contributions are welcome:
- Suggest journalists to add via Issues
- Dispute a score via Issues with evidence
- Country maintainers welcome — open an Issue to claim a country beat

---

## Disclaimer

SourceRank scores are analytical estimates based on publicly available data. They are not definitive judgments of a journalist's character or professional standing. The methodology is open and auditable. Disputed scores can be raised as GitHub Issues with supporting evidence.

---

## License

MIT
