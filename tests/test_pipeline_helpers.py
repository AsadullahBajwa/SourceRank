import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import config
from find_handles import zero_tweet_handles
from pipeline.claim_extractor import normalize_claim_type, normalize_confidence, quick_skip
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


if __name__ == "__main__":
    unittest.main()
