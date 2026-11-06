"""Forward-only comparison: identical technical rules, one pre-registered news gate hypothesis."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts import contextual_news_shadow as study
from scripts import simple_paper_shadow_v2 as base

AT = datetime(2026, 11, 6, 10, 15, 10, tzinfo=timezone.utc)


def candle(minute, o, h, l, c):
    return {"time": (AT.replace(hour=9, minute=0, second=0)-timedelta(minutes=60)
                     +timedelta(minutes=minute)).isoformat().replace("+00:00", "Z"),
            "mid": {"o": o, "h": h, "l": l, "c": c}}


def market(at=AT):
    last = at.replace(minute=0, second=0, microsecond=0)-timedelta(minutes=15)
    bars = [{"time": (last-timedelta(minutes=30)).isoformat(), "mid":
                {"o":1.101, "h":1.1014,"l":1.1006,"c":1.1012}},
            {"time": (last-timedelta(minutes=15)).isoformat(), "mid":
                {"o":1.1012, "h":1.1016,"l":1.1008,"c":1.1015}},
            {"time": last.isoformat(), "mid":
                {"o":1.101,"h":1.1022,"l":1.1005,"c":1.102}}]
    return {"status":"OK","collected_at":base.iso(at-timedelta(seconds=10)),
            "pairs":{"EUR_USD":{"quote":{"time":base.iso(at-timedelta(seconds=6)),
                "tradeable":True,"bid":1.1019,"ask":1.1021,"pip_size":.0001},
                "frames":{"M15":{"completed_at":last.isoformat(),
                    "indicators":{"ema20":1.1011,"ema50":1.10,"atr14":.001},
                    "last_3_completed_candles":bars},
                    "H1":{"completed_at":(at.replace(minute=0,second=0,microsecond=0)-timedelta(hours=1)).isoformat(),
                    "indicators":{"ema20":1.101,"ema50":1.10}}}}}}


def news(at=AT, headline=None, unknown=True):
    info = {"calendar_coverage":"OK","near_high_impact_calendar":[],
            "coverage":{"status":"UNAVAILABLE" if unknown else "OK",
                        "missing_required":[],
                        "currencies_without_supported_official_feed":["USD"] if unknown else []},
            "recent_relevant_headlines":[headline] if headline else []}
    return {"collected_at":base.iso(at),"by_pair":{"EUR_USD":info}}


class ContextualNewsTests(unittest.TestCase):
    def test_generic_third_party_headline_is_not_a_live_clearance(self):
        item = {"source":"Global markets aggregation","currency_source":"GLOBAL",
                "severity":"HIGH","published_at":base.iso(AT),
                "title":"Unreviewed global headline"}
        report=news(headline=item)
        self.assertIsNone(study.contextual_news_gate("EUR_USD",report,AT))
        self.assertIn("MATERIAL_HEADLINE_REVIEW_UNVERIFIED",
                      study.news_uncertainty("EUR_USD",report,AT))
        self.assertEqual(base.news_gate("EUR_USD",report,AT),"PRIMARY_NEWS_SOURCE_MISSING")

    def test_primary_high_and_calendar_blackouts_still_block_paper(self):
        item = {"source":"Fed policy","currency_source":"USD",
                "severity":"HIGH","published_at":base.iso(AT)}
        self.assertEqual(study.contextual_news_gate("EUR_USD",news(headline=item),AT),
                         "CONTEXT_PRIMARY_OFFICIAL_HIGH_IMPACT_REVIEW")
        r=news()
        r["by_pair"]["EUR_USD"]["near_high_impact_calendar"]=[{"currency":"USD"}]
        self.assertEqual(study.contextual_news_gate("EUR_USD",r,AT),
                         "CONTEXT_CALENDAR_UNAVAILABLE_OR_BLACKOUT")
        r["by_pair"]["EUR_USD"]["near_high_impact_calendar"]=[]
        r["by_pair"]["EUR_USD"]["calendar_coverage"]="UNAVAILABLE"
        self.assertEqual(study.contextual_news_gate("EUR_USD",r,AT),
                         "CONTEXT_CALENDAR_UNAVAILABLE_OR_BLACKOUT")

    def test_clone_preserves_base_policy_without_monkeypatch_and_no_orders(self):
        original = base.news_gate
        src=market();report=news()
        candidate=study.candidate_proposals(src,report,AT)
        self.assertIs(base.news_gate,original)
        self.assertTrue(candidate)
        self.assertFalse(base.propose(src,report,AT))
        plan=candidate[0]
        self.assertEqual(plan["policy"],study.VERSION)
        self.assertFalse(plan["broker_order_permitted"])
        self.assertFalse(plan["approved_entry"])
        self.assertEqual(plan["comparison_news_gate"],"PRIMARY_NEWS_SOURCE_MISSING")
        self.assertEqual(plan["entry_condition"],"NEXT_COMPLETED_M15_CLOSE_BEYOND_FROZEN_TRIGGER")

    def test_registration_immutable_and_no_future_fills(self):
        src=market();report=news()
        book=study.update(src,report,fetch=lambda plan,now:[],now=AT)
        self.assertEqual(book["broker_orders_submitted"],0)
        self.assertGreaterEqual(book["metrics"]["plans"],1)
        self.assertEqual(book["metrics"]["scored_exits"],0)
        self.assertTrue(all(r["result"]["status"]=="PENDING_TRIGGER"
                            for r in book["records"].values()))
        later=AT+timedelta(minutes=1)
        with self.assertRaisesRegex(ValueError,"CONTEXT_FROZEN_RULES_CHANGED"):
            study.update(src,report,{**book,"rule_sha256":"altered"},now=later)

    def test_risk_circuit_suspends_after_five_independent_losses(self):
        rows={}
        for index,pair in enumerate(("EUR_USD","GBP_USD","USD_JPY","USD_CAD","EUR_JPY")):
            when=AT+timedelta(minutes=index)
            rows[str(index)]={"plan":{"instrument":pair},
                "result":{"entry_at":base.iso(when),"scored":True,
                          "net_r_stressed_illustrative":-1.1}}
        policy=study.risk_state({"records":rows})
        self.assertEqual(policy["status"],"SUSPENDED_NEW_PAPER_ENTRIES")
        self.assertGreaterEqual(policy["max_drawdown_stressed_r"],5)
        rows.pop("4")
        self.assertEqual(study.risk_state({"records":rows})["status"],"FORWARD_PAPER_ONLY")

    def test_old_stale_source_cannot_create_new_registration(self):
        src=market(AT-timedelta(hours=2))
        book=study.update(src,news(),fetch=lambda plan,now:[],now=AT)
        self.assertEqual(book["metrics"]["plans"],0)
        self.assertEqual(book["source_snapshots"],[])


if __name__ == "__main__":
    unittest.main()
