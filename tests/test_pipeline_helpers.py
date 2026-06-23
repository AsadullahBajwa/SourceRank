import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import config
from find_handles import zero_tweet_handles
from pipeline.claim_extractor import normalize_claim_type, normalize_confidence, quick_skip
from pipeline.scorer import compute_source_quality, public_claim_record, score_journalist
from pipeline.verifier import (
    VERDICT_REFUTED,
    determine_verdict,
    filter_relevant_articles,
    find_contradiction,
    parse_google_news_title,
)
from scrapers.news_scraper import select_sources
from scrapers.tweet_scraper import select_journalists
from scheduler import _history_status, select_steps
from scripts.audit_registry import build_report
from scripts.claim_review import review_candidates
from scripts.coverage_plan import missing_active_handles
from scripts.extension_check import missing_extension_files, validate_manifest
from scripts.source_coverage import build_source_report
from scripts.site_check import missing_site_files, validate_local_links
from time_utils import parse_utc


class TimeUtilsTests(unittest.TestCase):
    def test_parse_utc_normalizes_offsets(self):
        self.assertEqual(
            parse_utc("2026-05-16T05:00:00+02:00"),
            datetime.datetime(2026, 5, 16, 3, 0, 0),
        )


class ClaimExtractorTests(unittest.TestCase):
    def test_quick_skip_rejects_short_non_claim(self):
        self.assertTrue(quick_skip("Good morning everyone"))

    def test_quick_skip_keeps_statistical_claims(self):
        self.assertFalse(
            quick_skip("Official data shows inflation rose 5% last month in Pakistan")
        )

    def test_normalize_claim_type_defaults_unknown_values(self):
        self.assertEqual(normalize_claim_type("Prediction"), "prediction")
        self.assertEqual(normalize_claim_type("rumor"), "general")

    def test_normalize_confidence_clamps_bad_values(self):
        self.assertEqual(normalize_confidence("0.8"), 0.8)
        self.assertEqual(normalize_confidence(9), 1.0)
        self.assertEqual(normalize_confidence(None), 0.5)


class ScorerTests(unittest.TestCase):
    def test_source_quality_uses_configured_tier1_names(self):
        claims = [
            {"verdict": "CONFIRMED", "verdict_source": "Reuters"},
            {"verdict": "CONFIRMED", "verdict_source": "Blog"},
        ]
        self.assertEqual(compute_source_quality(claims), 0.5)

    def test_score_journalist_accepts_min_resolved_override(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE tweets (
                handle TEXT,
                created_at TEXT,
                is_retweet INTEGER
            )
        """)
        score = score_journalist(
            {"handle": "alpha", "name": "Alpha"},
            [{"verdict": "CONFIRMED", "claim_type": "general", "verdict_source": "Reuters"}],
            conn,
            min_resolved_claims=1,
        )
        conn.close()
        self.assertTrue(score["eligible"])
        self.assertEqual(score["min_resolved_claims"], 1)

    def test_public_claim_record_limits_fields_for_site_export(self):
        record = public_claim_record({
            "id": "c1",
            "tweet_id": "t1",
            "handle": "alpha",
            "claim_text": "A checkable claim",
            "claim_type": "prediction",
            "tweet_created_at": "2026-06-01T00:00:00",
            "verdict": "CONFIRMED",
            "verdict_source": "Reuters",
            "verdict_url": "https://example.test",
            "confidence": 0.8,
            "internal_note": "not exported",
        })
        self.assertEqual(record["handle"], "alpha")
        self.assertNotIn("internal_note", record)


class SchedulerTests(unittest.TestCase):
    def test_select_steps_can_choose_pipeline_range(self):
        self.assertEqual(
            select_steps(from_step="extract", through_step="score"),
            ["extract", "verify", "score"],
        )

    def test_select_steps_rejects_reversed_range(self):
        with self.assertRaises(ValueError):
            select_steps(from_step="verify", through_step="tweets")

    def test_history_status_reads_snapshot_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "scores_2026-06-01.json"), "w").close()
            with open(os.path.join(tmpdir, "index.json"), "w", encoding="utf-8") as f:
                f.write('{"latest": "scores_2026-06-01.json"}')

            status = _history_status(tmpdir)

        self.assertEqual(status["history_snapshots"], 1)
        self.assertEqual(status["latest_snapshot"], "scores_2026-06-01.json")


class VerifierTests(unittest.TestCase):
    def test_parse_google_news_title_extracts_publisher(self):
        title, source = parse_google_news_title("Minister denies resignation - Reuters")
        self.assertEqual(title, "Minister denies resignation")
        self.assertEqual(source, "Reuters")

    def test_google_news_aggregator_is_not_tier1_source(self):
        claim = {
            "claim_text": "The minister will resign",
            "verification_window": 7,
            "tweet_created_at": "2026-05-15T00:00:00",
            "extracted_at": "2026-05-15T00:00:00",
        }
        verdict, source, url = determine_verdict(
            claim,
            [],
            [{
                "source_name": "Google News",
                "title": "Minister expected to resign",
                "summary": "",
                "url": "https://example.test/aggregate",
            }],
        )
        self.assertNotEqual(verdict, "CONFIRMED")
        self.assertEqual(source, "Google News")
        self.assertEqual(url, "https://example.test/aggregate")

    def test_parsed_reuters_google_result_is_tier1(self):
        claim = {
            "claim_text": "The minister will resign",
            "verification_window": 7,
            "tweet_created_at": "2026-06-01T00:00:00",
            "extracted_at": "2026-06-01T00:00:00",
        }
        verdict, source, url = determine_verdict(
            claim,
            [],
            [{
                "source_name": "Reuters",
                "title": "Minister expected to resign",
                "summary": "",
                "url": "https://example.test/reuters",
            }],
        )
        self.assertEqual(verdict, "CONFIRMED")
        self.assertEqual(source, "Reuters")
        self.assertEqual(url, "https://example.test/reuters")

    def test_find_contradiction_uses_summary_text(self):
        contradiction = find_contradiction(
            "The minister will resign",
            [{
                "source_name": "Reuters World",
                "title": "Minister addresses rumors",
                "summary": "The minister denies resignation and says he stays on.",
                "url": "https://example.test/refute",
            }],
        )
        self.assertIsNotNone(contradiction)

    def test_filter_relevant_articles_requires_claim_overlap(self):
        matches = filter_relevant_articles(
            "The cabinet minister will resign next week",
            [
                {"title": "Minister denies resignation rumors", "summary": ""},
                {"title": "Sports schedule announced", "summary": "Local teams prepare for playoffs"},
            ],
        )
        self.assertEqual(len(matches), 1)
        self.assertIn("resignation", matches[0]["title"])

    def test_refuted_verdict_uses_refuting_article_source(self):
        claim = {
            "claim_text": "The minister will resign",
            "verification_window": 7,
            "tweet_created_at": "2026-05-15T00:00:00",
            "extracted_at": "2026-05-15T00:00:00",
        }
        verdict, source, url = determine_verdict(
            claim,
            [{
                "source_name": "The Hill",
                "title": "Routine coverage",
                "summary": "",
                "url": "https://example.test/coverage",
            }],
            [{
                "source_name": "Reuters World",
                "title": "Minister denies resignation",
                "summary": "",
                "url": "https://example.test/refute",
            }],
        )
        self.assertEqual(verdict, VERDICT_REFUTED)
        self.assertEqual(source, "Reuters World")
        self.assertEqual(url, "https://example.test/refute")


class FindHandlesTests(unittest.TestCase):
    def test_zero_tweet_handles_uses_latest_scrape_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tweets.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE scrape_log (
                    handle TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    tweet_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'ok',
                    error TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO scrape_log (handle, scraped_at, tweet_count, status) VALUES (?, ?, ?, ?)",
                [
                    ("healthy", "2026-05-01T00:00:00", 0, "ok"),
                    ("healthy", "2026-05-02T00:00:00", 5, "ok"),
                    ("broken", "2026-05-01T00:00:00", 8, "ok"),
                    ("broken", "2026-05-02T00:00:00", 0, "ok"),
                ],
            )
            conn.commit()
            conn.close()

            with mock.patch.object(config, "TWEETS_DB", db_path):
                self.assertEqual(zero_tweet_handles(), {"broken"})


class RegistryAuditTests(unittest.TestCase):
    def test_build_report_finds_duplicate_names_and_coverage_gaps(self):
        rows = [
            {"handle": "alpha", "name": "Alex Example", "country": "US", "active": "true"},
            {"handle": "alpha2", "name": "Alex Example", "country": "US", "active": "false"},
            {"handle": "beta", "name": "Beta Example", "country": "Pakistan", "active": "true"},
        ]
        report = build_report(rows, {"alpha": 3})
        self.assertEqual(report["active"], 2)
        self.assertEqual(report["active_with_tweets"], 1)
        self.assertEqual(report["active_coverage_pct"], 50.0)
        self.assertIn("Alex Example", report["duplicate_names"])
        self.assertNotIn("Alex Example", report["active_duplicate_names"])
        self.assertEqual(report["active_without_tweets_by_country"], {"Pakistan": 1})
        self.assertEqual(report["active_without_tweets"], ["beta"])

    def test_missing_active_handles_can_filter_country_and_limit(self):
        rows = [
            {"handle": "alpha", "country": "US", "active": "true"},
            {"handle": "beta", "country": "Pakistan", "active": "true"},
            {"handle": "gamma", "country": "Pakistan", "active": "true"},
        ]
        handles = missing_active_handles(
            rows,
            {"gamma": 2},
            country="Pakistan",
            limit=1,
        )
        self.assertEqual(handles, ["beta"])


class ClaimReviewTests(unittest.TestCase):
    def test_review_candidates_prioritizes_low_confidence_claims(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE claims (
                id TEXT,
                handle TEXT,
                claim_text TEXT,
                claim_type TEXT,
                verdict TEXT,
                confidence REAL,
                verdict_source TEXT,
                verdict_url TEXT,
                tweet_created_at TEXT
            )
        """)
        conn.executemany(
            """
            INSERT INTO claims
                (id, handle, claim_text, claim_type, verdict, confidence, tweet_created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("c1", "alpha", "Weak claim", "general", "UNVERIFIED", 0.2, "2026-06-01"),
                ("c2", "beta", "Strong claim", "general", "CONFIRMED", 0.9, "2026-06-02"),
            ],
        )
        rows = review_candidates(conn, ["UNVERIFIED"], limit=10)
        conn.close()
        self.assertEqual([row["id"] for row in rows], ["c1"])


class TweetScraperTests(unittest.TestCase):
    def test_select_journalists_can_target_missing_active_handles(self):
        rows = [
            {"handle": "alpha", "active": "true"},
            {"handle": "beta", "active": "true"},
            {"handle": "gamma", "active": "false"},
        ]
        selected = select_journalists(
            rows,
            only_missing=True,
            tweet_counts={"alpha": 4},
            limit=1,
        )
        self.assertEqual([row["handle"] for row in selected], ["beta"])

    def test_select_journalists_applies_limit_after_filtering(self):
        rows = [
            {"handle": "alpha", "active": "true"},
            {"handle": "beta", "active": "true"},
            {"handle": "gamma", "active": "true"},
        ]
        selected = select_journalists(rows, limit=2)
        self.assertEqual([row["handle"] for row in selected], ["alpha", "beta"])


class NewsScraperTests(unittest.TestCase):
    def test_select_sources_filters_country_tier_and_limit(self):
        sources = [
            {"name": "Reuters", "country": "global", "tier": "1"},
            {"name": "Dawn", "country": "Pakistan", "tier": "1"},
            {"name": "Local", "country": "Pakistan", "tier": "2"},
        ]
        selected = select_sources(sources, country="Pakistan", tier=1, limit=1)
        self.assertEqual([source["name"] for source in selected], ["Dawn"])

    def test_build_source_report_counts_tiers_and_countries(self):
        report = build_source_report([
            {"country": "global", "language": "en", "tier": "1"},
            {"country": "Pakistan", "language": "en", "tier": "1"},
            {"country": "Pakistan", "language": "ur", "tier": "2"},
        ])
        self.assertEqual(report["total_sources"], 3)
        self.assertEqual(report["tier1_by_country"], {"Pakistan": 1, "global": 1})


class SiteCheckTests(unittest.TestCase):
    def test_missing_site_files_reports_required_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            site_dir = os.path.join(tmpdir, "site")
            data_dir = os.path.join(site_dir, "data")
            os.makedirs(data_dir)
            missing = missing_site_files(site_dir, data_dir)
        self.assertTrue(any(path.endswith("index.html") for path in missing))
        self.assertTrue(any(path.endswith("scores.json") for path in missing))

    def test_validate_local_links_reports_missing_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for name in ["index.html", "journalist.html", "trends.html", "coverage.html", "methodology.html", "dispute.html"]:
                with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                    f.write('<a href="missing.html">Missing</a>' if name == "index.html" else "")
            errors = validate_local_links(tmpdir)
        self.assertEqual(errors, ["index.html links to missing local page: missing.html"])


class ExtensionCheckTests(unittest.TestCase):
    def test_missing_extension_files_reports_required_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "manifest.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            missing = missing_extension_files(tmpdir)
        self.assertTrue(any(path.endswith("content.js") for path in missing))
        self.assertTrue(any(path.endswith("popup.html") for path in missing))

    def test_validate_manifest_accepts_current_manifest_shape(self):
        manifest = {
            "manifest_version": 3,
            "name": "SourceRank Overlay",
            "version": "0.1.0",
            "permissions": ["storage"],
            "content_scripts": [{"matches": ["https://x.com/*"], "js": ["content.js"]}],
            "action": {"default_popup": "popup.html"},
            "options_ui": {"page": "options.html"},
        }
        self.assertEqual(validate_manifest(manifest), [])

    def test_validate_manifest_rejects_missing_storage_permission(self):
        manifest = {
            "manifest_version": 3,
            "name": "SourceRank Overlay",
            "version": "0.1.0",
            "permissions": [],
            "content_scripts": [{"matches": ["https://x.com/*"], "js": ["content.js"]}],
            "action": {"default_popup": "popup.html"},
            "options_ui": {"page": "options.html"},
        }
        errors = validate_manifest(manifest)
        self.assertIn("manifest.json permissions must include storage", errors)


if __name__ == "__main__":
    unittest.main()
