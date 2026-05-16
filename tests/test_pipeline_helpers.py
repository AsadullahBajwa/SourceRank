import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import config
from find_handles import zero_tweet_handles
from pipeline.claim_extractor import quick_skip
from pipeline.verifier import (
    VERDICT_REFUTED,
    determine_verdict,
    find_contradiction,
)
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


class VerifierTests(unittest.TestCase):
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
        self.assertIn("Alex Example", report["duplicate_names"])
        self.assertEqual(report["active_without_tweets"], ["beta"])


if __name__ == "__main__":
    unittest.main()
