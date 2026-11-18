import datetime
import io
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import config
from find_handles import zero_tweet_handles
from pipeline.claim_extractor import normalize_claim_type, normalize_confidence, quick_skip
from pipeline.scorer import (
    compute_accuracy_rate,
    compute_correction_score,
    compute_prediction_score,
    compute_source_quality,
    compute_spam_index,
    public_claim_record,
    score_journalist,
    score_to_grade,
)
from pipeline.verifier import (
    VERDICT_REFUTED,
    backfill_tweet_created_at,
    claim_reference_date,
    determine_verdict,
    filter_relevant_articles,
    find_contradiction,
    get_pending_claims,
    migrate_claims_schema,
    parse_google_news_title,
)
from scrapers.news_scraper import get_db as get_news_db, migrate_articles_schema, select_sources
from scrapers.tweet_scraper import select_journalists
from scheduler import (
    _backfill_status,
    _feed_health_status,
    _history_status,
    _source_gap_status,
    print_preflight,
    run_preflight_checks,
    run_step,
    select_steps,
)
from scripts.audit_registry import build_report, normalized_handle, row_is_active
from scripts.claim_review import review_candidates, write_csv
from scripts.config_check import validate_config
from scripts.coverage_plan import (
    backfill_sort_key,
    build_coverage_plan,
    missing_active_handles,
    missing_active_rows,
    write_csv as write_coverage_csv,
)
from scripts.extension_check import missing_extension_files, validate_manifest
from scripts.feed_health import (
    build_feed_health_report,
    filter_problems,
    has_feed_problems,
    latest_logs_by_source,
    load_feed_logs,
)
from scripts.journalists_check import validate_journalist_rows
from scripts.pipeline_health import build_health_report, count_csv_rows, latest_snapshot, sqlite_count
from scripts.rss_check import validate_source_rows
from scripts.snapshot_check import validate_history, validate_scores_payload
from scripts.source_balance_check import validate_source_balance
from scripts.source_coverage import build_source_report, filter_sources
from scripts.source_gap_plan import (
    build_gap_plan,
    filter_gaps_by_priority,
    limited_gaps,
    print_gap_plan,
    source_gap_hint,
    source_counts_by_country_category,
    write_csv as write_gap_csv,
)
from scripts.site_check import (
    missing_site_files,
    validate_local_links,
    validate_methodology_weights,
    validate_site,
)
from scripts.source_taxonomy import is_valid_category, normalize_category
from time_utils import parse_utc, utc_now


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
    @staticmethod
    def _claim(verdict, claim_type="general"):
        return {"verdict": verdict, "claim_type": claim_type, "verdict_source": ""}

    def test_accuracy_rate_ignores_unresolved_claims(self):
        claims = [
            self._claim("CONFIRMED"),
            self._claim("CONFIRMED"),
            self._claim("CONFIRMED"),
            self._claim("REFUTED"),
            self._claim("UNVERIFIED"),
            self._claim("EXPIRED"),
        ]
        self.assertEqual(compute_accuracy_rate(claims), 0.75)

    def test_accuracy_rate_is_neutral_without_resolved_claims(self):
        self.assertEqual(compute_accuracy_rate([self._claim("PENDING")]), 0.5)
        self.assertEqual(compute_accuracy_rate([]), 0.5)

    def test_prediction_score_only_uses_high_value_claims(self):
        claims = [
            self._claim("CONFIRMED", "breaking"),
            self._claim("REFUTED", "exclusive"),
            self._claim("CONFIRMED", "general"),
            self._claim("CONFIRMED", "statistic"),
        ]
        self.assertEqual(compute_prediction_score(claims), 0.5)

    def test_prediction_score_falls_back_to_accuracy_without_high_value_claims(self):
        claims = [
            self._claim("CONFIRMED"),
            self._claim("CONFIRMED"),
            self._claim("CONFIRMED"),
            self._claim("REFUTED"),
        ]
        self.assertEqual(compute_prediction_score(claims), compute_accuracy_rate(claims))

    def test_correction_score_double_weights_refutations(self):
        self.assertEqual(compute_correction_score([self._claim("CONFIRMED")]), 1.0)
        claims = [self._claim("REFUTED")] + [self._claim("CONFIRMED")] * 9
        self.assertAlmostEqual(compute_correction_score(claims), 0.8)

    def test_correction_score_never_goes_negative(self):
        claims = [self._claim("REFUTED")] * 3 + [self._claim("CONFIRMED")]
        self.assertEqual(compute_correction_score(claims), 0.0)

    def test_score_to_grade_boundaries(self):
        cases = [
            (100, "A+"), (90, "A+"), (89.9, "A"), (85, "A"), (80, "A-"),
            (75, "B+"), (70, "B"), (65, "B-"), (60, "C+"), (55, "C"),
            (50, "C-"), (49.9, "D"), (40, "D"), (39.9, "F"), (0, "F"),
        ]
        for score, grade in cases:
            with self.subTest(score=score):
                self.assertEqual(score_to_grade(score), grade)

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

    @staticmethod
    def _tweets_db(rows):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE tweets (handle TEXT, created_at TEXT, is_retweet INTEGER)")
        conn.executemany("INSERT INTO tweets VALUES (?, ?, ?)", rows)
        return conn

    def test_spam_index_volume_tiers(self):
        recent = (utc_now() - datetime.timedelta(days=1)).isoformat()
        # Thresholds are on the 30-day daily average: <=5, <=10, <=20, above.
        cases = [(0, 1.0), (150, 1.0), (151, 0.8), (300, 0.8),
                 (301, 0.6), (600, 0.6), (601, 0.4)]
        for count, expected in cases:
            with self.subTest(tweets=count):
                conn = self._tweets_db([("alpha", recent, 0)] * count)
                self.assertEqual(compute_spam_index("Alpha", conn), expected)
                conn.close()

    def test_spam_index_ignores_retweets_and_old_tweets(self):
        recent = (utc_now() - datetime.timedelta(days=1)).isoformat()
        old = (utc_now() - datetime.timedelta(days=45)).isoformat()
        rows = (
            [("alpha", recent, 0)] * 100
            + [("alpha", recent, 1)] * 500
            + [("alpha", old, 0)] * 500
            + [("beta", recent, 0)] * 500
        )
        conn = self._tweets_db(rows)
        self.assertEqual(compute_spam_index("alpha", conn), 1.0)
        conn.close()

    def test_score_journalist_verdict_counts_cover_every_claim(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE tweets (handle TEXT, created_at TEXT, is_retweet INTEGER)")
        verdicts = ["CONFIRMED", "REFUTED", "PENDING", "UNVERIFIED", "UNVERIFIED", "EXPIRED"]
        claims = [
            {"verdict": verdict, "claim_type": "general", "verdict_source": ""}
            for verdict in verdicts
        ]
        score = score_journalist({"handle": "alpha"}, claims, conn)
        conn.close()
        self.assertEqual(score["unverified"], 2)
        counted = sum(
            score[key] for key in ("confirmed", "refuted", "pending", "unverified", "expired")
        )
        self.assertEqual(counted, score["total_claims"])

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

    def test_run_preflight_checks_collects_named_errors(self):
        report = run_preflight_checks([
            ("passing", lambda: []),
            ("failing", lambda: ["broken artifact"]),
        ])
        self.assertEqual(report, {
            "passing": [],
            "failing": ["broken artifact"],
        })

    def test_source_gap_status_limits_top_gaps(self):
        rows = [{"country": "Pakistan", "category": "newspaper"}]
        status = _source_gap_status(rows)
        self.assertGreaterEqual(status["source_gap_count"], 1)
        self.assertLessEqual(len(status["top_source_gaps"]), 5)

    def test_feed_health_status_limits_top_problems(self):
        rows = [{"name": f"Source {index}"} for index in range(7)]
        with tempfile.TemporaryDirectory() as tmpdir, \
                mock.patch.object(config, "CLAIMS_DB", os.path.join(tmpdir, "claims.db")):
            status = _feed_health_status(rows)
        self.assertEqual(status["feed_problem_sources"], 7)
        self.assertEqual(status["feed_missing_sources"], 7)
        self.assertEqual(status["feed_stale_sources"], 0)
        self.assertEqual(len(status["top_feed_problems"]), 5)

    def test_backfill_status_prioritizes_top_missing_handles(self):
        rows = [
            {"handle": "low", "followers_tier": "low", "active": "true"},
            {"handle": "high", "followers_tier": "high", "active": "true"},
            {"handle": "covered", "followers_tier": "high", "active": "true"},
        ]
        status = _backfill_status(rows, {"covered": 2})
        self.assertEqual(status["backfill_needed"], 2)
        self.assertEqual(status["top_backfill_handles"], ["high", "low"])

    def test_run_step_dry_run_does_not_spawn_process(self):
        with mock.patch("scheduler.subprocess.run") as run:
            self.assertTrue(run_step("news", ["python", "news_scraper.py"], dry_run=True))
        run.assert_not_called()

    def test_run_step_reports_subprocess_failure(self):
        completed = SimpleNamespace(returncode=2)
        with mock.patch("scheduler.subprocess.run", return_value=completed):
            self.assertFalse(run_step("news", ["python", "news_scraper.py"]))

    def test_print_preflight_json_returns_pass_status(self):
        with mock.patch("scheduler.run_preflight_checks", return_value={"ok": [], "bad": ["broken"]}):
            stream = io.StringIO()
            with mock.patch("sys.stdout", stream):
                passed = print_preflight(as_json=True)
        payload = json.loads(stream.getvalue())
        self.assertFalse(passed)
        self.assertFalse(payload["passed"])
        self.assertEqual(payload["checks"]["bad"], ["broken"])


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

    def test_find_contradiction_ignores_keywords_inside_other_words(self):
        cases = [
            ("Budget deadline set for Friday", "Hostages found alive after raid"),
            ("Analysts wonder about the vote count", "Rival lost the seat"),
            ("Squad selected for the cup final", "Coach admits side lost focus"),
        ]
        for claim, headline in cases:
            with self.subTest(claim=claim):
                self.assertIsNone(find_contradiction(
                    claim,
                    [{"source_name": "Reuters", "title": headline, "summary": ""}],
                ))

    def test_find_contradiction_skips_claims_that_state_the_refutation(self):
        contradiction = find_contradiction(
            "Khan was not arrested, police say",
            [{"source_name": "Reuters", "title": "Police confirm Khan not arrested", "summary": ""}],
        )
        self.assertIsNone(contradiction)

    def test_find_contradiction_still_matches_whole_words(self):
        contradiction = find_contradiction(
            "The party won the by-election",
            [{"source_name": "Reuters", "title": "Rival conceded defeat", "summary": ""}],
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

    def test_tier1_confirmation_does_not_depend_on_match_order(self):
        claim = {
            "claim_text": "The minister will resign",
            "verification_window": 7,
            "tweet_created_at": "2026-06-01T00:00:00",
            "extracted_at": "2026-06-01T00:00:00",
        }
        verdict, source, url = determine_verdict(
            claim,
            [{
                "source_name": "Local Blog",
                "title": "Minister resignation rumors continue",
                "summary": "",
                "url": "https://example.test/blog",
            }],
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

    def test_tier1_refutation_is_preferred_over_weak_refuting_match(self):
        claim = {
            "claim_text": "The minister will resign",
            "verification_window": 7,
            "tweet_created_at": "2026-06-01T00:00:00",
            "extracted_at": "2026-06-01T00:00:00",
        }
        verdict, source, url = determine_verdict(
            claim,
            [{
                "source_name": "Local Blog",
                "title": "Minister denies resignation",
                "summary": "",
                "url": "https://example.test/blog-denial",
            }],
            [{
                "source_name": "Reuters World",
                "title": "Minister denies resignation",
                "summary": "The minister says he has no plans to resign.",
                "url": "https://example.test/reuters-denial",
            }],
        )
        self.assertEqual(verdict, VERDICT_REFUTED)
        self.assertEqual(source, "Reuters World")
        self.assertEqual(url, "https://example.test/reuters-denial")

    def test_migrate_claims_schema_adds_legacy_columns(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE claims (
                id TEXT PRIMARY KEY,
                tweet_id TEXT,
                claim_text TEXT
            )
        """)
        migrate_claims_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(claims)")}
        conn.close()
        self.assertIn("tweet_created_at", columns)
        self.assertIn("confidence", columns)

    def test_backfill_tweet_created_at_reads_tweets_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tweets_db = os.path.join(tmpdir, "tweets.db")
            tweets_conn = sqlite3.connect(tweets_db)
            tweets_conn.execute("CREATE TABLE tweets (id TEXT, created_at TEXT)")
            tweets_conn.execute(
                "INSERT INTO tweets (id, created_at) VALUES (?, ?)",
                ("t1", "2026-06-01T12:00:00"),
            )
            tweets_conn.commit()
            tweets_conn.close()

            claims_conn = sqlite3.connect(":memory:")
            claims_conn.execute("""
                CREATE TABLE claims (
                    id TEXT,
                    tweet_id TEXT,
                    tweet_created_at TEXT
                )
            """)
            claims_conn.execute(
                "INSERT INTO claims (id, tweet_id, tweet_created_at) VALUES (?, ?, ?)",
                ("c1", "t1", ""),
            )
            with mock.patch.object(config, "TWEETS_DB", tweets_db):
                backfill_tweet_created_at(claims_conn)
            created_at = claims_conn.execute(
                "SELECT tweet_created_at FROM claims WHERE id = 'c1'"
            ).fetchone()[0]
            claims_conn.close()

        self.assertEqual(created_at, "2026-06-01T12:00:00")

    def test_claim_reference_date_prefers_tweet_timestamp(self):
        self.assertEqual(
            claim_reference_date({
                "tweet_created_at": "2026-06-01T12:00:00",
                "extracted_at": "2026-06-03T12:00:00",
            }),
            "2026-06-01T12:00:00",
        )

    def test_get_pending_claims_recheck_includes_unverified_and_expired(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE claims (
                id TEXT,
                handle TEXT,
                verdict TEXT
            )
        """)
        conn.executemany(
            "INSERT INTO claims (id, handle, verdict) VALUES (?, ?, ?)",
            [
                ("c1", "alpha", "PENDING"),
                ("c2", "alpha", "UNVERIFIED"),
                ("c3", "alpha", "EXPIRED"),
                ("c4", "alpha", "CONFIRMED"),
                ("c5", "beta", "PENDING"),
            ],
        )
        pending_only = get_pending_claims("alpha", conn)
        recheck = get_pending_claims("alpha", conn, recheck=True)
        conn.close()
        self.assertEqual([row["id"] for row in pending_only], ["c1"])
        self.assertEqual([row["id"] for row in recheck], ["c1", "c2", "c3"])


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
            {
                "handle": "alpha",
                "name": "Alex Example",
                "beat": "Politics",
                "country": "US",
                "followers_tier": "high",
                "active": "true",
            },
            {
                "handle": "alpha2",
                "name": "Alex Example",
                "beat": "Politics",
                "country": "US",
                "followers_tier": "medium",
                "active": "false",
            },
            {
                "handle": "beta",
                "name": "Beta Example",
                "beat": "Politics",
                "country": "Pakistan",
                "followers_tier": "medium",
                "active": "true",
            },
        ]
        report = build_report(rows, {"alpha": 3})
        self.assertEqual(report["active"], 2)
        self.assertEqual(report["active_with_tweets"], 1)
        self.assertEqual(report["active_coverage_pct"], 50.0)
        self.assertEqual(report["active_by_beat"], {"Politics": 2})
        self.assertEqual(report["inactive_by_beat"], {"Politics": 1})
        self.assertEqual(report["active_by_follower_tier"], {"high": 1, "medium": 1})
        self.assertEqual(report["inactive_by_follower_tier"], {"medium": 1})
        self.assertIn("Alex Example", report["duplicate_names"])
        self.assertNotIn("Alex Example", report["active_duplicate_names"])
        self.assertEqual(report["active_without_tweets_by_country"], {"Pakistan": 1})
        self.assertEqual(report["active_without_tweets_by_beat"], {"Politics": 1})
        self.assertEqual(report["active_without_tweets_by_follower_tier"], {"medium": 1})
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

    def test_build_coverage_plan_returns_json_ready_payload(self):
        rows = [
            {"handle": "alpha", "country": "US", "beat": "Politics", "active": "true"},
            {"handle": "beta", "country": "Pakistan", "beat": "Politics", "active": "true"},
        ]
        plan = build_coverage_plan(rows, {"alpha": 2}, country="Pakistan", beat="Politics")
        self.assertEqual(plan["country"], "Pakistan")
        self.assertEqual(plan["beat"], "Politics")
        self.assertEqual(plan["missing_count"], 1)
        self.assertEqual(plan["handles"], ["beta"])

    def test_missing_active_handles_can_filter_by_beat(self):
        rows = [
            {"handle": "alpha", "beat": "Politics", "active": "true"},
            {"handle": "beta", "beat": "Finance", "active": "true"},
            {"handle": "gamma", "beat": "Politics", "active": "false"},
        ]
        handles = missing_active_handles(rows, {}, beat=" politics ")
        self.assertEqual(handles, ["alpha"])

    def test_missing_active_rows_and_csv_keep_metadata(self):
        rows = [
            {
                "handle": "beta",
                "name": "Beta Example",
                "country": "Pakistan",
                "beat": "Politics",
                "followers_tier": "medium",
                "active": "true",
            },
        ]
        missing = missing_active_rows(rows, {})
        stream = io.StringIO()
        write_coverage_csv(missing, stream)
        output = stream.getvalue()
        self.assertEqual(missing[0]["name"], "Beta Example")
        self.assertTrue(output.startswith("handle,name,country,beat,followers_tier"))
        self.assertIn("Beta Example", output)

    def test_backfill_plan_can_prioritize_high_follower_rows(self):
        rows = [
            {"handle": "zeta", "country": "US", "followers_tier": "low", "active": "true"},
            {"handle": "alpha", "country": "US", "followers_tier": "high", "active": "true"},
            {"handle": "beta", "country": "US", "followers_tier": "medium", "active": "true"},
        ]
        missing = missing_active_rows(rows, {}, priority="tier")
        self.assertEqual([row["handle"] for row in missing], ["alpha", "beta", "zeta"])
        self.assertLess(backfill_sort_key(rows[1], priority="tier"), backfill_sort_key(rows[0], priority="tier"))

    def test_registry_helpers_normalize_whitespace_and_case(self):
        row = {"handle": " Alpha ", "active": " TRUE "}
        self.assertTrue(row_is_active(row))
        self.assertEqual(normalized_handle(row), "alpha")

    def test_build_report_uses_normalized_active_flags_and_handles(self):
        rows = [
            {"handle": " Alpha ", "name": "Alpha Example", "country": "US", "active": " TRUE "},
            {"handle": "Beta", "name": "Beta Example", "country": "Pakistan", "active": " false "},
        ]
        report = build_report(rows, {"alpha": 4})
        self.assertEqual(report["active"], 1)
        self.assertEqual(report["inactive"], 1)
        self.assertEqual(report["active_with_tweets"], 1)

    def test_missing_active_handles_uses_normalized_handles(self):
        rows = [
            {"handle": " Alpha ", "country": "US", "active": " TRUE "},
            {"handle": "Beta", "country": "US", "active": " true "},
        ]
        handles = missing_active_handles(rows, {"alpha": 2}, country=" us ")
        self.assertEqual(handles, ["Beta"])


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

    def test_write_csv_preserves_claim_text_and_header(self):
        stream = io.StringIO()
        write_csv(
            [{
                "id": "c1",
                "handle": "alpha",
                "claim_text": "Inflation rose, according to the report",
                "claim_type": "statistic",
                "verdict": "UNVERIFIED",
                "confidence": 0.4,
                "verdict_source": "",
                "verdict_url": "",
                "tweet_created_at": "2026-06-01",
            }],
            stream,
        )
        output = stream.getvalue()
        self.assertTrue(output.startswith("id,handle,claim_text"))
        self.assertIn('"Inflation rose, according to the report"', output)


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
            {"name": "Reuters", "country": "global", "tier": "1", "category": "wire"},
            {"name": "Dawn", "country": "Pakistan", "tier": "1", "category": "newspaper"},
            {"name": "Local", "country": "Pakistan", "tier": "2", "category": "tv"},
        ]
        selected = select_sources(sources, country="Pakistan", tier=1, limit=1)
        self.assertEqual([source["name"] for source in selected], ["Dawn"])

    def test_select_sources_filters_by_normalized_category(self):
        sources = [
            {"name": "Reuters", "country": "global", "tier": "1", "category": "wire"},
            {"name": "BBC", "country": "global", "tier": "1", "category": "public_broadcaster"},
            {"name": "Dawn", "country": "Pakistan", "tier": "1", "category": "newspaper"},
        ]
        selected = select_sources(sources, category="Public Broadcaster")
        self.assertEqual([source["name"] for source in selected], ["BBC"])

    def test_news_database_includes_source_category_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "claims.db")
            conn = get_news_db(db_path)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
            conn.close()
        self.assertIn("source_category", columns)

    def test_news_database_migrates_legacy_articles_table(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE articles (
                id TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                country TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                published TEXT,
                fetched_at TEXT NOT NULL
            )
        """)
        migrate_articles_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        conn.close()
        self.assertIn("source_category", columns)

    def test_source_taxonomy_normalizes_category_names(self):
        self.assertEqual(normalize_category("Public Broadcaster"), "public_broadcaster")
        self.assertEqual(normalize_category("digital-native"), "digital_native")
        self.assertTrue(is_valid_category("Nonprofit Investigative"))
        self.assertFalse(is_valid_category("influencer"))

    def test_build_source_report_counts_tiers_and_countries(self):
        report = build_source_report([
            {"country": "global", "language": "en", "tier": "1", "category": "wire"},
            {"country": "Pakistan", "language": "en", "tier": "1", "category": "newspaper"},
            {"country": "Pakistan", "language": "ur", "tier": "2", "category": "tv"},
        ])
        self.assertEqual(report["total_sources"], 3)
        self.assertEqual(report["tier1_by_country"], {"Pakistan": 1, "global": 1})
        self.assertEqual(report["tier1_missing_countries"], ["US"])
        self.assertEqual(report["languages_by_country"]["Pakistan"], ["en", "ur"])
        self.assertEqual(report["by_category"], {"newspaper": 1, "tv": 1, "wire": 1})
        self.assertEqual(report["categories_by_country"]["Pakistan"], ["newspaper", "tv"])
        self.assertEqual(
            report["category_matrix"],
            {
                "Pakistan": {"newspaper": 1, "tv": 1},
                "global": {"wire": 1},
            },
        )

    def test_build_source_report_normalizes_blank_values(self):
        report = build_source_report([
            {"country": " Pakistan ", "language": " en ", "tier": " 1 ", "category": " Public Broadcaster "},
            {"country": "", "language": "", "tier": "", "category": ""},
        ])
        self.assertEqual(report["by_country"], {"Pakistan": 1, "unknown": 1})
        self.assertEqual(report["by_language"], {"en": 1, "unknown": 1})
        self.assertEqual(report["by_tier"], {"1": 1, "unknown": 1})
        self.assertEqual(report["by_category"], {"public_broadcaster": 1, "unknown": 1})

    def test_filter_sources_filters_by_normalized_category(self):
        rows = [
            {"name": "Reuters", "category": "wire"},
            {"name": "BBC", "category": "Public Broadcaster"},
            {"name": "Dawn", "category": "newspaper"},
        ]
        self.assertEqual(
            [row["name"] for row in filter_sources(rows, category="public-broadcaster")],
            ["BBC"],
        )

    def test_filter_sources_can_filter_by_country_and_category(self):
        rows = [
            {"name": "Dawn", "country": "Pakistan", "category": "newspaper"},
            {"name": "NYT", "country": "US", "category": "newspaper"},
            {"name": "BBC", "country": "global", "category": "public_broadcaster"},
        ]
        self.assertEqual(
            [row["name"] for row in filter_sources(rows, country=" pakistan ", category="Newspaper")],
            ["Dawn"],
        )


class RssCheckTests(unittest.TestCase):
    def test_validate_source_rows_accepts_clean_rows(self):
        rows = [{
            "name": "Reuters World",
            "url": "https://news.google.com/rss/search?q=site:reuters.com",
            "country": "global",
            "language": "en",
            "tier": "1",
            "category": "wire",
        }]
        self.assertEqual(validate_source_rows(rows), [])

    def test_validate_source_rows_flags_registry_problems(self):
        rows = [
            {
                "name": "Reuters",
                "url": "https://feeds.reuters.com/reuters/worldNews",
                "country": "global",
                "language": "en",
                "tier": "1",
                "category": "wire",
            },
            {
                "name": "Reuters",
                "url": "not-a-url",
                "country": "global",
                "language": "en",
                "tier": "9",
                "category": "blog",
            },
        ]
        errors = validate_source_rows(rows)
        self.assertTrue(any("dead RSS host" in error for error in errors))
        self.assertTrue(any("duplicates source name" in error for error in errors))
        self.assertTrue(any("invalid URL" in error for error in errors))
        self.assertTrue(any("invalid tier" in error for error in errors))
        self.assertTrue(any("invalid category" in error for error in errors))

    def test_validate_source_balance_accepts_diverse_registry(self):
        rows = [
            {"country": "global", "language": "en", "tier": "1", "category": "wire"},
            {"country": "US", "language": "en", "tier": "1", "category": "newspaper"},
            {"country": "Pakistan", "language": "en", "tier": "1", "category": "tv"},
            {"country": "US", "language": "en", "tier": "2", "category": "public_broadcaster"},
            {"country": "Pakistan", "language": "ur", "tier": "2", "category": "radio"},
        ]
        self.assertEqual(validate_source_balance(rows), [])

    def test_validate_source_balance_flags_narrow_registry(self):
        rows = [
            {"country": "global", "language": "en", "tier": "1", "category": "wire"},
            {"country": "global", "language": "en", "tier": "2", "category": "wire"},
        ]
        errors = validate_source_balance(rows)
        self.assertTrue(any("at least 5 source categories" in error for error in errors))
        self.assertTrue(any("missing core source categories" in error for error in errors))
        self.assertTrue(any("missing tier-1 sources" in error for error in errors))

    def test_source_counts_by_country_category_normalizes_categories(self):
        rows = [
            {"country": "Pakistan", "category": "Public Broadcaster"},
            {"country": "Pakistan", "category": "public-broadcaster"},
            {"country": "", "category": ""},
        ]
        counts = source_counts_by_country_category(rows)
        self.assertEqual(counts[("Pakistan", "public_broadcaster")], 2)
        self.assertEqual(counts[("unknown", "unknown")], 1)

    def test_build_gap_plan_prioritizes_empty_required_categories(self):
        rows = [
            {"country": "Pakistan", "category": "newspaper"},
            {"country": "Pakistan", "category": "newspaper"},
            {"country": "US", "category": "digital_native"},
        ]
        gaps = build_gap_plan(
            rows,
            targets={
                "Pakistan": {"newspaper": 3, "tv": 1},
                "US": {"digital_native": 2},
            },
        )
        self.assertEqual(gaps[0]["country"], "Pakistan")
        self.assertEqual(gaps[0]["category"], "tv")
        self.assertEqual(gaps[0]["priority"], "high")
        self.assertEqual(gaps[0]["missing"], 1)
        self.assertIn("Pakistan", gaps[0]["search_query"])
        self.assertIn("RSS source", gaps[0]["registry_note"])

    def test_source_gap_hint_uses_international_search_for_global_sources(self):
        hint = source_gap_hint("global", "wire")
        self.assertIn("international", hint["search_query"])
        self.assertIn("Wire service", hint["registry_note"])

    def test_build_gap_plan_filters_country(self):
        rows = [{"country": "Pakistan", "category": "newspaper"}]
        gaps = build_gap_plan(
            rows,
            targets={
                "Pakistan": {"newspaper": 2},
                "US": {"newspaper": 2},
            },
            country="pakistan",
        )
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["country"], "Pakistan")

    def test_limited_gaps_keeps_requested_prefix(self):
        gaps = [{"country": "A"}, {"country": "B"}]
        self.assertEqual(limited_gaps(gaps, limit=1), [{"country": "A"}])
        self.assertEqual(limited_gaps(gaps, limit=0), gaps)

    def test_filter_gaps_by_priority_matches_normalized_priority(self):
        gaps = [{"priority": "high"}, {"priority": "medium"}]
        self.assertEqual(filter_gaps_by_priority(gaps, " HIGH "), [{"priority": "high"}])
        self.assertEqual(filter_gaps_by_priority(gaps, None), gaps)

    def test_print_gap_plan_handles_empty_plan(self):
        stream = io.StringIO()
        with mock.patch("sys.stdout", stream):
            print_gap_plan([])
        self.assertEqual(stream.getvalue().strip(), "No RSS source category gaps found.")

    def test_write_gap_csv_preserves_recommendation_fields(self):
        stream = io.StringIO()
        write_gap_csv(
            [{
                "country": "Pakistan",
                "category": "digital_native",
                "category_label": "Digital-native outlet",
                "current": 0,
                "target": 1,
                "missing": 1,
                "priority": "high",
                "search_query": "Pakistan digital news outlet RSS politics",
                "registry_note": "Add a digital-native source, then validate RSS.",
            }],
            stream,
        )
        output = stream.getvalue()
        self.assertTrue(output.startswith("country,category,category_label"))
        self.assertIn('"Add a digital-native source, then validate RSS."', output)


class JournalistsCheckTests(unittest.TestCase):
    def test_validate_journalist_rows_accepts_balanced_registry(self):
        rows = [
            {
                "handle": f"pk{index}",
                "name": f"Pakistan Journalist {index}",
                "beat": "Politics",
                "country": "Pakistan",
                "followers_tier": "medium",
                "verified": "true",
                "active": "true",
            }
            for index in range(50)
        ]
        rows.extend(
            {
                "handle": f"us{index}",
                "name": f"US Journalist {index}",
                "beat": "Politics",
                "country": "US",
                "followers_tier": "high",
                "verified": "false",
                "active": "true",
            }
            for index in range(50)
        )
        self.assertEqual(validate_journalist_rows(rows), [])

    def test_validate_journalist_rows_flags_duplicate_and_invalid_values(self):
        row = {
            "handle": "bad-handle",
            "name": "Example",
            "beat": "Politics",
            "country": "Canada",
            "followers_tier": "huge",
            "verified": "yes",
            "active": "sometimes",
        }
        duplicate = dict(row, handle="BAD-HANDLE")
        errors = validate_journalist_rows([row, duplicate], minimum_total=2)
        self.assertTrue(any("invalid X handle" in error for error in errors))
        self.assertTrue(any("duplicates handle" in error for error in errors))
        self.assertTrue(any("unsupported country" in error for error in errors))
        self.assertTrue(any("invalid followers_tier" in error for error in errors))
        self.assertTrue(any("invalid verified" in error for error in errors))

    def test_validate_journalist_rows_warns_on_country_imbalance(self):
        rows = [
            {
                "handle": f"pk{index}",
                "name": f"Pakistan Journalist {index}",
                "beat": "Politics",
                "country": "Pakistan",
                "followers_tier": "medium",
                "verified": "true",
                "active": "true",
            }
            for index in range(30)
        ]
        rows.extend(
            {
                "handle": f"us{index}",
                "name": f"US Journalist {index}",
                "beat": "Politics",
                "country": "US",
                "followers_tier": "high",
                "verified": "false",
                "active": "true",
            }
            for index in range(10)
        )
        errors = validate_journalist_rows(rows, minimum_total=40)
        self.assertEqual(len(errors), 1)
        self.assertIn("country balance differs", errors[0])


class ConfigCheckTests(unittest.TestCase):
    def valid_settings(self, base_dir: str) -> SimpleNamespace:
        return SimpleNamespace(
            SCORE_WEIGHTS={
                "accuracy_rate": 0.4,
                "prediction_score": 0.25,
                "correction_behavior": 0.15,
                "source_quality": 0.1,
                "spam_index": 0.1,
            },
            VERIFICATION_WINDOWS={
                "breaking": 7,
                "prediction": 30,
                "exclusive": 14,
                "statistic": 30,
                "general": 14,
            },
            INITIAL_SCRAPE_MONTHS=18,
            SKIP_RECENT_DAYS=30,
            QUICK_SKIP_MIN_WORDS=8,
            MIN_RESOLVED_CLAIMS_FOR_RANKING=20,
            OLLAMA_HOST="http://localhost:11434",
            OLLAMA_MODEL="qwen2.5:7b",
            OLLAMA_NUM_PARALLEL=4,
            BASE_DIR=base_dir,
            DATA_DIR=os.path.join(base_dir, "data"),
            DB_DIR=os.path.join(base_dir, "data", "db"),
            OUTPUT_DIR=os.path.join(base_dir, "output"),
            HISTORY_DIR=os.path.join(base_dir, "output", "history"),
        )

    def test_validate_config_accepts_current_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(validate_config(self.valid_settings(tmpdir)), [])

    def test_validate_config_flags_unsafe_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = self.valid_settings(tmpdir)
            settings.SCORE_WEIGHTS["accuracy_rate"] = 0.9
            settings.OLLAMA_HOST = "localhost:11434"
            settings.OLLAMA_NUM_PARALLEL = 0
            settings.HISTORY_DIR = os.path.dirname(tmpdir)
            errors = validate_config(settings)
        self.assertTrue(any("sum to 1.0" in error for error in errors))
        self.assertTrue(any("OLLAMA_HOST" in error for error in errors))
        self.assertTrue(any("OLLAMA_NUM_PARALLEL" in error for error in errors))
        self.assertTrue(any("HISTORY_DIR" in error for error in errors))

    def test_validate_config_flags_missing_expected_keys(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = self.valid_settings(tmpdir)
            settings.SCORE_WEIGHTS.pop("spam_index")
            settings.VERIFICATION_WINDOWS.pop("breaking")
            errors = validate_config(settings)
        self.assertTrue(any("SCORE_WEIGHTS keys" in error for error in errors))
        self.assertTrue(any("VERIFICATION_WINDOWS keys" in error for error in errors))


class SnapshotCheckTests(unittest.TestCase):
    def valid_payload(self) -> dict:
        return {
            "generated_at": "2026-06-26T10:00:00",
            "journalists": [
                {
                    "handle": "alpha",
                    "score": 90.0,
                    "eligible": True,
                    "rank_status": "ranked",
                    "total_claims": 5,
                    "confirmed": 3,
                    "refuted": 1,
                    "pending": 1,
                    "expired": 0,
                    "resolved_claims": 4,
                }
            ],
        }

    def test_validate_scores_payload_accepts_consistent_counts(self):
        self.assertEqual(validate_scores_payload(self.valid_payload()), [])

    def test_validate_scores_payload_flags_duplicate_and_bad_resolved_count(self):
        payload = self.valid_payload()
        duplicate = dict(payload["journalists"][0])
        duplicate["resolved_claims"] = 2
        payload["journalists"].append(duplicate)
        errors = validate_scores_payload(payload)
        self.assertTrue(any("duplicates handle" in error for error in errors))
        self.assertTrue(any("confirmed + refuted" in error for error in errors))

    def test_validate_scores_payload_flags_unsorted_ranked_rows(self):
        payload = self.valid_payload()
        payload["journalists"].append(
            dict(payload["journalists"][0], handle="beta", score=95.0)
        )
        errors = validate_scores_payload(payload)
        self.assertEqual(errors, ["scores.json eligible journalists must be sorted by score descending"])

    def test_validate_scores_payload_requires_boolean_eligible(self):
        payload = self.valid_payload()
        payload["journalists"][0]["eligible"] = "yes"
        errors = validate_scores_payload(payload)
        self.assertEqual(errors, ["scores.json journalist 1 eligible must be a boolean"])

    def test_validate_scores_payload_allows_unpublished_unverified_count(self):
        payload = self.valid_payload()
        payload["journalists"][0]["total_claims"] = 8
        self.assertEqual(validate_scores_payload(payload), [])

    def test_validate_scores_payload_flags_verdict_counts_above_total(self):
        payload = self.valid_payload()
        payload["journalists"][0]["total_claims"] = 3
        errors = validate_scores_payload(payload)
        self.assertEqual(errors, ["scores.json journalist 1 published verdict counts cannot exceed total_claims"])

    def test_validate_history_requires_latest_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            history_dir = os.path.join(tmpdir, "history")
            os.makedirs(history_dir)
            with open(os.path.join(history_dir, "index.json"), "w", encoding="utf-8") as f:
                f.write(
                    '{"latest":"scores_2026-06-26.json",'
                    '"snapshots":["scores_2026-06-26.json"]}'
                )
            errors = validate_history(tmpdir)
        self.assertEqual(errors, ["Missing history snapshot: scores_2026-06-26.json"])


class PipelineHealthTests(unittest.TestCase):
    def test_count_csv_rows_counts_data_rows_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "rows.csv")
            with open(path, "w", encoding="utf-8") as f:
                f.write("name,value\nalpha,1\nbeta,2\n")
            self.assertEqual(count_csv_rows(path), 2)

    def test_sqlite_count_handles_missing_database(self):
        self.assertIsNone(sqlite_count(os.path.join("missing", "db.sqlite"), "tweets"))

    def test_latest_snapshot_reads_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "index.json"), "w", encoding="utf-8") as f:
                f.write('{"latest": "scores_2026-06-16.json"}')
            self.assertEqual(latest_snapshot(tmpdir), "scores_2026-06-16.json")

    def test_build_health_report_includes_source_gap_counts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journalists_path = os.path.join(tmpdir, "journalists.csv")
            rss_path = os.path.join(tmpdir, "rss_sources.csv")
            with open(journalists_path, "w", encoding="utf-8") as f:
                f.write("handle,name,beat,country,followers_tier,verified,active\n")
                f.write("alpha,Alpha,Politics,US,medium,true,true\n")
            with open(rss_path, "w", encoding="utf-8") as f:
                f.write("name,url,country,language,tier,category\n")
                f.write("Reuters,https://example.test,global,en,1,wire\n")
            with mock.patch.object(config, "JOURNALISTS_CSV", journalists_path), \
                    mock.patch.object(config, "RSS_SOURCES_CSV", rss_path), \
                    mock.patch.object(config, "TWEETS_DB", os.path.join(tmpdir, "tweets.db")), \
                    mock.patch.object(config, "CLAIMS_DB", os.path.join(tmpdir, "claims.db")), \
                    mock.patch.object(config, "HISTORY_DIR", os.path.join(tmpdir, "history")):
                report = build_health_report()
        self.assertEqual(report["active_journalists"], 1)
        self.assertGreater(report["source_gap_count"], 0)
        self.assertGreater(report["high_priority_source_gaps"], 0)
        self.assertEqual(report["feed_problem_sources"], 1)
        self.assertEqual(report["feed_missing_sources"], 1)
        self.assertEqual(report["feed_stale_sources"], 0)


class FeedHealthTests(unittest.TestCase):
    def test_latest_logs_by_source_keeps_newest_fetch(self):
        latest = latest_logs_by_source([
            {"source_name": "Reuters", "fetched_at": "2026-06-01T00:00:00", "status": "error"},
            {"source_name": "Reuters", "fetched_at": "2026-06-02T00:00:00", "status": "ok"},
        ])
        self.assertEqual(latest["Reuters"]["status"], "ok")

    def test_build_feed_health_report_flags_missing_and_stale_sources(self):
        report = build_feed_health_report(
            [
                {"name": "Reuters", "country": "global", "category": "wire"},
                {"name": "Dawn", "country": "Pakistan", "category": "newspaper"},
                {"name": "BBC", "country": "global", "category": "public_broadcaster"},
            ],
            [
                {
                    "source_name": "Reuters",
                    "fetched_at": "2026-06-01T00:00:00",
                    "status": "ok",
                    "entry_count": 10,
                    "new_count": 2,
                    "error": "",
                },
                {
                    "source_name": "Dawn",
                    "fetched_at": "2026-06-02T11:00:00",
                    "status": "warning",
                    "entry_count": 5,
                    "new_count": 1,
                    "error": "parse warning",
                },
            ],
            stale_after_hours=24,
            now=datetime.datetime(2026, 6, 2, 12, 0, 0),
        )
        self.assertEqual(report["total_sources"], 3)
        self.assertEqual(report["fetched_sources"], 2)
        self.assertEqual(report["status_counts"]["missing"], 1)
        self.assertEqual(report["status_counts"]["stale"], 1)
        self.assertEqual([problem["source_name"] for problem in report["problems"]], ["BBC", "Dawn", "Reuters"])

    def test_load_feed_logs_handles_missing_database(self):
        self.assertEqual(load_feed_logs(os.path.join("missing", "claims.db")), [])

    def test_filter_problems_can_select_stale_or_limit_rows(self):
        problems = [
            {"source_name": "Reuters", "status": "ok", "is_stale": True},
            {"source_name": "BBC", "status": "missing", "is_stale": False},
            {"source_name": "Dawn", "status": "warning", "is_stale": False},
        ]
        self.assertEqual(
            [problem["source_name"] for problem in filter_problems(problems, status="stale")],
            ["Reuters"],
        )
        self.assertEqual(
            [problem["source_name"] for problem in filter_problems(problems, status="missing")],
            ["BBC"],
        )
        self.assertEqual(filter_problems(problems, limit=1), [problems[0]])

    def test_filter_problems_can_select_country_and_category(self):
        problems = [
            {"source_name": "Dawn", "country": "Pakistan", "category": "newspaper", "status": "missing"},
            {"source_name": "Naya Daur", "country": "Pakistan", "category": "digital_native", "status": "missing"},
            {"source_name": "NYT", "country": "US", "category": "newspaper", "status": "missing"},
        ]
        selected = filter_problems(
            problems,
            country="pakistan",
            category="Digital Native",
        )
        self.assertEqual([problem["source_name"] for problem in selected], ["Naya Daur"])

    def test_has_feed_problems_uses_full_report_count(self):
        self.assertFalse(has_feed_problems({"problem_count": 0}))
        self.assertTrue(has_feed_problems({"problem_count": 2, "problems": []}))


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

    def test_validate_site_accepts_required_files_and_query_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            site_dir = os.path.join(tmpdir, "site")
            data_dir = os.path.join(site_dir, "data")
            history_dir = os.path.join(data_dir, "history")
            os.makedirs(history_dir)
            for name in ["index.html", "journalist.html", "trends.html", "coverage.html", "methodology.html", "dispute.html"]:
                with open(os.path.join(site_dir, name), "w", encoding="utf-8") as f:
                    f.write('<a href="journalist.html?handle=alpha">Alpha</a>' if name == "index.html" else "")
            with open(os.path.join(data_dir, "scores.json"), "w", encoding="utf-8") as f:
                f.write('{"journalists":[]}')
            with open(os.path.join(data_dir, "claims.json"), "w", encoding="utf-8") as f:
                f.write('{"claims":[]}')
            with open(os.path.join(history_dir, "index.json"), "w", encoding="utf-8") as f:
                f.write('{"snapshots":[]}')
            self.assertEqual(validate_site(site_dir, data_dir), [])


class ExtensionCheckTests(unittest.TestCase):
    def _methodology_errors(self, rows, weights):
        with tempfile.TemporaryDirectory() as site_dir:
            with open(os.path.join(site_dir, "methodology.html"), "w", encoding="utf-8") as f:
                f.write("<table>" + "".join(
                    f"<tr><td>{label}</td><td>{percent}%</td><td>...</td></tr>"
                    for label, percent in rows
                ) + "</table>")
            return validate_methodology_weights(site_dir, weights)

    def test_methodology_weights_match_config(self):
        errors = self._methodology_errors(
            [("Accuracy Rate", "40"), ("Spam Index", "10")],
            {"accuracy_rate": 0.4, "spam_index": 0.1},
        )
        self.assertEqual(errors, [])

    def test_methodology_weights_report_drift(self):
        errors = self._methodology_errors(
            [("Accuracy Rate", "40"), ("Legacy Metric", "5")],
            {"accuracy_rate": 0.35, "spam_index": 0.1},
        )
        self.assertEqual(errors, [
            "methodology.html lists accuracy_rate at 40% but config uses 35%",
            "methodology.html is missing the spam_index weight (10%)",
            "methodology.html lists legacy_metric, which is not in config.SCORE_WEIGHTS",
        ])

    def test_published_methodology_matches_config(self):
        site_dir = os.path.join(config.BASE_DIR, "output", "site")
        self.assertEqual(validate_methodology_weights(site_dir, config.SCORE_WEIGHTS), [])

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
