"""News risk scope regression; no live feeds, no retroactive approvals."""
import unittest
from datetime import datetime, timezone
from scripts import news_watch
from scripts.signal_admission import news_gate

NOW = datetime(2026,11,4,10,28,tzinfo=timezone.utc)


def headline(title):
    return {"title":title, "source":"Global markets aggregation",
            "currency_source":"GLOBAL","published_at":"2026-11-04T09:01:00Z",
            "url":"https://example.invalid/feed/item"}


class ScopedGlobalNewsTests(unittest.TestCase):
    def setUp(self):
        # Synthetic universe: real production refresh draws from OANDA instead.
        self.previous = (news_watch.PAIR_CURRENCIES,news_watch.CURRENCIES)
        news_watch.PAIR_CURRENCIES={
            "EUR_USD":("EUR","USD"),"USD_SEK":("USD","SEK"),"EUR_SEK":("EUR","SEK")}
        news_watch.CURRENCIES=frozenset(["EUR","USD","SEK"])

    def tearDown(self):
        news_watch.PAIR_CURRENCIES,news_watch.CURRENCIES=self.previous

    def test_current_sweden_policy_is_not_a_new_global_war_shock(self):
        hit=news_watch.classify(headline(
            "Sweden’s Central Bank Latest to Lift Rates in Response to War - WSJ"))
        self.assertEqual(hit["currencies"],["SEK"])
        self.assertFalse(hit["global_risk"])
        self.assertEqual(hit["severity"],"HIGH")
        self.assertTrue(news_watch.requires_headline_review(hit))
        self.assertEqual(news_watch.relevance("EUR_USD",[hit]),[])
        self.assertEqual(news_watch.relevance("EUR_SEK",[hit]),[hit])

    def test_genuine_war_escalation_retains_global_reassessment(self):
        hit=news_watch.classify(headline("Iran war escalates after new missile attacks disrupt global markets"))
        self.assertTrue(hit["global_risk"])
        self.assertIn("USD",hit["currencies"])
        self.assertIn("SEK",hit["currencies"])
        self.assertIn("EUR",hit["currencies"])
        self.assertEqual(hit["severity"],"HIGH")

    def test_local_policy_article_does_not_override_missing_news_coverage(self):
        hit=news_watch.classify(headline(
            "Sweden’s Central Bank Latest to Lift Rates in Response to War - WSJ"))
        base={"collected_at":"2026-11-04T10:27:00Z","by_pair":{}}
        good={"calendar_coverage":"OK","near_high_impact_calendar":[],
              "coverage":{"status":"LIMITED","missing_required":[],
                          "currencies_without_supported_official_feed":[]},
              "recent_relevant_headlines":[]}
        base["by_pair"]["EUR_USD"]=dict(good)
        base["by_pair"]["EUR_SEK"]=dict(good,recent_relevant_headlines=[hit],
                                        coverage={**good["coverage"],
                                            "currencies_without_supported_official_feed":["SEK"]})
        self.assertIsNone(news_gate("EUR_USD",base,NOW))
        self.assertEqual(news_gate("EUR_SEK",base,NOW),"PRIMARY_NEWS_SOURCE_MISSING")
        base["by_pair"]["EUR_USD"]["recent_relevant_headlines"]=[
            news_watch.classify(headline("Iran war escalates after missile attack"))]
        self.assertEqual(news_gate("EUR_USD",base,NOW),"RECENT_MATERIAL_HEADLINE_REVIEW")

    def test_other_currency_local_policy_remains_scoped(self):
        hit=news_watch.classify(headline("Bank of England raises rates as war continues"))
        self.assertEqual(hit["currencies"],["GBP"])
        self.assertFalse(hit["global_risk"])
        self.assertTrue(news_watch.requires_headline_review(hit))


if __name__ == "__main__":
    unittest.main()
