"""Offline tests for the free headline/event surveillance and hourly macro gating.

Synthetic test data only; never calls news or OANDA APIs.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import news_watch as news
import signal_watch as watch


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def item(headline, code="USD", source="Fed policy", delta=-15):
    return {"source": source, "currency_source": code,
            "published_at": news.iso(NOW + timedelta(minutes=delta)),
            "title": headline, "url": "https://example.org/public-article"}


def signal():
    return {"id": "20261021-EURUSD-01", "instrument": "EUR_USD",
            "direction": "LONG", "created_at": news.iso(NOW - timedelta(hours=2)),
            "expires_at": news.iso(NOW + timedelta(hours=4)),
            "planned_entry": 1.1, "stop": 1.097, "tp1": 1.106}


class NewsTests(unittest.TestCase):
    def test_primary_currency_and_high_severity(self):
        hit = news.classify(item("Federal Reserve interest rate decision: policy changed"))
        self.assertEqual(hit["severity"], "HIGH")
        self.assertIn("USD", hit["currencies"])
        self.assertNotIn("CAD", hit["currencies"])

    def test_geopolitical_global_impact(self):
        hit = news.classify(item("Middle East attack raises global markets oil price",
                                 code="GLOBAL", source="Global markets aggregation"))
        self.assertIn("JPY", hit["currencies"])
        self.assertIn("GBP", hit["currencies"])
        self.assertTrue(hit["global_risk"])

    def test_irrelevant_no_recommendation(self):
        self.assertIsNone(news.classify(item("Office open day invitation", code="CAD", source="BoC press")))

    def test_rss_parser(self):
        raw = ('<rss><channel><item><title>Fed monetary policy rate decision</title>'
               '<pubDate>Wed, 21 Oct 2026 11:45:00 GMT</pubDate>'
               '<link>https://www.federalreserve.gov/test</link></item></channel></rss>')
        parsed = news.parse_rss(raw.encode(), "Fed policy", "USD")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["published_at"], "2026-10-21T11:45:00Z")


    def test_rss1_monetary_policy_publication(self):
        raw = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
               'xmlns="http://purl.org/rss/1.0/" '
               'xmlns:dc="http://purl.org/dc/elements/1.1/">'
               '<item><title>Monetary policy decision by the central bank</title>'
               '<link>https://www.snb.ch/en/test</link>'
               '<dc:date>2026-10-21T11:55:00Z</dc:date></item></rdf:RDF>')
        parsed = news.parse_rss(raw.encode(), "SNB monetary policy", "CHF")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["currency_source"], "CHF")

    def test_calendar_impact_and_timezone(self):
        raw = [{"date": "2026-10-21T08:30:00-04:00", "country": "USD",
                "impact": "High", "title": "Employment report", "forecast": "1%", "previous": "2%"}]
        events = news.calendar_events(json.dumps(raw).encode(), NOW)
        self.assertEqual(events[0]["time"], "2026-10-21T12:30:00Z")
        self.assertTrue(news.event_impact("EUR_USD", events, NOW))
        self.assertFalse(news.event_impact("EUR_GBP", events, NOW))

    def test_no_false_clear_on_coverage_failure(self):
        result = watch.news_check(signal(), {
            "status": "PARTIAL", "collected_at": news.iso(NOW),
            "by_pair": {"EUR_USD": {
                "recent_relevant_headlines": [], "near_high_impact_calendar": [],
                "calendar_coverage": "UNAVAILABLE"}}}, NOW)
        self.assertEqual(result[0], "EVENT_CALENDAR_UNAVAILABLE")

    def test_new_fed_headline_blocks_even_during_partial_outage(self):
        headline = news.classify(item("Fed monetary policy emergency rate decision"))
        headline["id"] = "test-news"
        result = watch.news_check(signal(), {
            "status": "PARTIAL", "collected_at": news.iso(NOW),
            "by_pair": {"EUR_USD": {
                "recent_relevant_headlines": [headline], "near_high_impact_calendar": [],
                "calendar_coverage": "UNAVAILABLE"}}}, NOW)
        self.assertEqual(result[0], "NEW_MATERIAL_HEADLINE_REVIEW")
        self.assertEqual(len(result[1]), 1)

    def test_old_news_predating_research_not_new(self):
        headline = news.classify(item("Fed monetary policy emergency rate decision", delta=-190))
        headline["id"] = "before"
        result = watch.news_check(signal(), {
            "status": "OK", "collected_at": news.iso(NOW),
            "by_pair": {"EUR_USD": {
                "recent_relevant_headlines": [headline], "near_high_impact_calendar": [],
                "calendar_coverage": "OK",
                "coverage": {"status": "OK", "required_sources": ["Fed policy",
                              "ECB press", "Global markets aggregation"]}}}}, NOW)
        self.assertEqual(result[0], "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES")

    def test_price_movement_classified_only_as_observed(self):
        base = datetime(2026, 10, 21, 11, 0, tzinfo=timezone.utc)
        candles = []
        for i, close in enumerate((1.1000, 1.1004, 1.1003, 1.1007, 1.1009)):
            candles.append({"time": news.iso(base + timedelta(minutes=i * 15)),
                            "mid": {"c": close}})
        headline = news.classify(item("Fed monetary policy interest rate decision",
                                     delta=-30))
        result = watch.observed_price_movement({
            "frames": {"M15": {"recent_completed_candles": candles}}
        }, signal(), headline)
        self.assertEqual(result["status"], "OBSERVED_PRICE_MOVEMENT_NOT_CAUSAL")
        self.assertGreater(result["change_pips"], 0)
        self.assertEqual(result["vs_planned_direction"], "SUPPORTS")

    def test_fetch_and_persist_with_synthetic_feeds(self):
        timestamp = "Wed, 21 Oct 2026 11:45:00 GMT"
        xml = ('<rss><channel><item><title>Fed monetary policy rate decision</title>'
               '<pubDate>' + timestamp + '</pubDate>'
               '<link>https://www.federalreserve.gov/test</link></item></channel></rss>').encode()
        weekly = json.dumps([{"date": "2026-10-21T12:30:00Z", "country": "USD",
                              "impact": "High", "title": "CPI release"}]).encode()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(news, "OUT", Path(tmp) / "news_latest.json"), \
                 patch.object(news, "STATE", Path(tmp) / "news_state.json"):
                output = news.collect(fetch=lambda u: weekly if u == news.CALENDAR else xml, now=NOW)
                self.assertEqual(output["status"], "OK")
                self.assertTrue(output["by_pair"]["EUR_USD"]["new_relevant_headlines"])
                self.assertTrue(output["by_pair"]["EUR_USD"]["near_high_impact_calendar"])
                # No new alert on second scan for the same publication.
                repeat = news.collect(fetch=lambda u: weekly if u == news.CALENDAR else xml, now=NOW)
                self.assertFalse(repeat["new_material_headlines"])



    def test_prior_target_touch_invalidates_later_signal(self):
        began = NOW - timedelta(minutes=30)
        candles = [{"time": news.iso(began),
                    "mid": {"h": 1.108, "l": 1.101, "o": 1.102, "c": 1.104}}]
        candles[0]["bid"] = dict(candles[0]["mid"])
        candles[0]["ask"] = {k:v+.0001 for k,v in candles[0]["mid"].items()}
        idea = signal()
        self.assertEqual(watch.setup_already_breached({
            "frames": {"M15": {"recent_completed_candles": candles}}
        }, idea, NOW), "PLAN_TP1_LEVEL_TOUCHED_ON_M15")

    def test_basic_hourly_status_shows_remaining_reward(self):
        idea = signal()
        pair = {"quote": {"bid": 1.101, "ask": 1.1011, "tradeable": True},
                "frames": {"M15": {"completed_at": "2026-10-21T11:30:00Z",
                                    "indicators": {"rsi14": 54, "ema20": 1.1009,
                                                   "adx14": 22, "atr14": 0.0007}},
                           "H1": {"indicators": {"rsi14": 58, "adx14": 30}}}}
        record = watch.basic_technical_status(pair, idea)
        self.assertGreater(record["indicative_distance_to_target_pips"], 0)
        self.assertGreater(record["indicative_remaining_rr_before_new_spread_adjustment"], 1)


    def test_blocked_optional_bls_feed_is_reported_not_hidden(self):
        feeds = {
            "Fed policy": {"status": "OK"},
            "ECB press": {"status": "OK"},
            "Global markets aggregation": {"status": "OK"},
            "BLS inflation": {"status": "UNAVAILABLE"},
        }
        status = news.coverage_for("EUR_USD", feeds, "OK")
        self.assertEqual(status["status"], "LIMITED")
        self.assertIn("BLS inflation", status["other_unavailable"])
        report = {"status": "PARTIAL", "collected_at": news.iso(NOW),
                  "by_pair": {"EUR_USD": {
                      "coverage": status,
                      "recent_relevant_headlines": [],
                      "near_high_impact_calendar": [],
                      "calendar_coverage": "OK"}}}
        self.assertEqual(watch.news_check(signal(), report, NOW)[0],
                         "NO_NEW_NEWS_FLAG_IN_CHECKED_SOURCES_WITH_GAPS")

    def test_missing_essential_source_blocks_watch(self):
        feeds = {"Fed policy": {"status": "UNAVAILABLE"},
                 "ECB press": {"status": "OK"},
                 "Global markets aggregation": {"status": "OK"}}
        status = news.coverage_for("EUR_USD", feeds, "OK")
        self.assertEqual(status["status"], "UNAVAILABLE")
        report = {"status": "PARTIAL", "collected_at": news.iso(NOW),
                  "by_pair": {"EUR_USD": {"coverage": status,
                       "recent_relevant_headlines": [], "near_high_impact_calendar": [],
                       "calendar_coverage": "OK"}}}
        self.assertEqual(watch.news_check(signal(), report, NOW)[0],
                         "NEWS_COVERAGE_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
