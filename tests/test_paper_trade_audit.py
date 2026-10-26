import datetime as dt
import unittest

from paper_trade_audit import replay, update_book, public_view, stamp, directional_pct, ranked_priority_watches


def bar(time, op, high, low, close, spread=.0002):
    def side(delta):
        return {"o": op+delta, "h": high+delta, "l": low+delta, "c": close+delta}
    return {"t": stamp(time), "bid": side(-spread/2),
            "ask": side(spread/2),
            "mid": {"o": op, "h": high, "l": low, "c": close}}


def plan():
    return {"id": "T001", "instrument": "EUR_USD", "direction": "SHORT",
            "created_at": "2026-10-23T09:58:00Z",
            "expires_at": "2026-10-23T12:30:00Z",
            "planned_entry": 1.1, "stop": 1.105, "tp1": 1.09,
            "alert_b": 1.1005, "max_oanda_spread_pips": 2.5}


def first_two():
    return [bar("2026-10-23T10:00:00Z", 1.1008, 1.1012, 1.0995, 1.0997),
            bar("2026-10-23T10:15:00Z", 1.0998, 1.1006, 1.0992, 1.1002)]


class PaperAuditTests(unittest.TestCase):
    def test_percent_is_underlying_price_movement_not_account_return(self):
        p = {"instrument":"GBP_JPY","direction":"SHORT","planned_entry":209.78}
        self.assertEqual(directional_pct(p,209.20),0.27648)
        self.assertEqual(directional_pct(p,210.12),-0.16207)

    def test_public_research_uses_percentages_not_r_multiples(self):
        p=dict(plan())
        book={"candidates":{"T001":{"plan":p,"result":{
            "status":"WIN_TP1","scored":True,"net_r":2.0,
            "exit":1.09,"mfe_r":2.2,"mae_r":-0.4,"route":"B"}}}}
        public=public_view(book)
        self.assertEqual(public["candidates"][0]["move_pct"],0.90909)
        self.assertNotIn("net_r",public["candidates"][0])
        self.assertNotIn("mfe_r",public["candidates"][0])
        self.assertNotIn("total_r",public["strategy"]["M15_REJECTION / ROUTE_B"])

    def test_hourly_tracking_without_verified_entry_shows_hypothetical_move(self):
        p=plan()
        time=stamp("2026-10-23T10:30:00Z")
        book={"candidates":{},"warnings":[]}
        market={"pairs":{"EUR_USD":{"quote":{
            "time":"2026-10-23T10:29:00Z","bid":1.0988,"ask":1.099,
            "tradeable":True}}}}
        update_book(book,{"candidates":[p]},{"signals":[]},time,
                    lambda *_:first_two()[:1],market=market)
        rec=book["candidates"]["T001"]
        self.assertEqual(rec["result"]["status"],"TRIGGERED_UNFILLED")
        self.assertEqual(rec["tracking"]["state"],"NO_VERIFIED_ENTRY")
        self.assertEqual(rec["tracking"]["move_pct"],0.09091)
        self.assertEqual(len(public_view(book)["candidates"][0]["tracking"]["history"]),1)

    def test_stale_quote_never_claimed_current(self):
        p=plan()
        time=stamp("2026-10-23T10:30:00Z")
        book={"candidates":{}}
        market={"pairs":{"EUR_USD":{"quote":{
            "time":"2026-10-23T09:30:00Z","bid":1.0988,"ask":1.099,
            "tradeable":True}}}}
        update_book(book,{"candidates":[p]},{"signals":[]},time,
                    lambda *_:first_two()[:1],market=market)
        self.assertEqual(book["candidates"]["T001"]["tracking"]["state"],"QUOTE_STALE")
        self.assertIsNone(public_view(book)["candidates"][0]["move_pct"])

    def test_expired_and_untriggered_candidate_still_gets_hourly_percentage(self):
        p = plan()
        book = {"candidates": {"T001": {
            "plan": p, "result": {"status": "UNTRIGGERED", "scored": False},
            "events": []}}, "warnings": []}
        now = stamp("2026-10-26T10:30:00Z")
        market = {"pairs": {"EUR_USD": {"quote": {
            "time": "2026-10-26T10:29:00Z", "bid": 1.0958,
            "ask": 1.096, "tradeable": True}}}}
        update_book(book, {"candidates":[]}, {"signals":[]}, now, market=market)
        public = public_view(book)["candidates"][0]
        self.assertEqual(public["status"], "UNTRIGGERED")
        self.assertEqual(public["tracking"]["state"], "HISTORICAL_NO_VERIFIED_ENTRY")
        self.assertEqual(public["tracking"]["reference_kind"], "ORIGINAL_PLANNED_ENTRY")
        self.assertEqual(public["move_pct"], 0.36364)
        self.assertEqual(len(public["tracking"]["history"]), 1)
        self.assertFalse(public["scored"])

    def test_incomplete_shortlist_with_zone_is_monitored_without_fake_trade(self):
        p = dict(plan(), id="ZONE", entry_low=1.100, entry_high=1.102)
        del p["planned_entry"]
        del p["expires_at"]
        book = {"candidates": {}}
        now = stamp("2026-10-26T10:30:00Z")
        market = {"pairs": {"EUR_USD": {"quote": {
            "time": "2026-10-26T10:29:00Z", "bid": 1.098,
            "ask": 1.0982, "tradeable": True}}}}
        update_book(book, {"candidates": [p]}, {"signals":[]}, now, market=market)
        row = public_view(book)["candidates"][0]
        self.assertEqual(row["status"], "UNTESTABLE")
        self.assertEqual(row["tracking"]["reference_kind"], "ORIGINAL_ZONE_MIDPOINT")
        self.assertEqual(row["tracking"]["reference_price"], 1.101)
        self.assertEqual(row["move_pct"], 0.25431)
        self.assertFalse(row["scored"])
        self.assertEqual(len(row["tracking"]["history"]), 1)

    def test_shortlist_without_any_original_price_starts_forward_only_baseline(self):
        p = dict(plan(), id="UNPRICED")
        for key in ("planned_entry", "stop", "tp1", "expires_at"):
            del p[key]
        book = {"candidates": {}}
        first = {"pairs": {"EUR_USD": {"quote": {
            "time": "2026-10-26T09:59:00Z", "bid": 1.098,
            "ask": 1.0982, "tradeable": True}}}}
        update_book(book, {"candidates": [p]}, {"signals":[]},
                    stamp("2026-10-26T10:00:00Z"), market=first)
        row = public_view(book)["candidates"][0]
        self.assertEqual(row["tracking"]["reference_kind"], "FIRST_OBSERVED_QUOTE")
        self.assertEqual(row["tracking"]["reference_at"], "2026-10-26T09:59:00Z")
        self.assertEqual(row["move_pct"], 0)
        second = {"pairs": {"EUR_USD": {"quote": {
            "time": "2026-10-26T10:59:00Z", "bid": 1.095,
            "ask": 1.0952, "tradeable": True}}}}
        update_book(book, {"candidates":[p]}, {"signals":[]},
                    stamp("2026-10-26T11:00:00Z"), market=second)
        row = public_view(book)["candidates"][0]
        self.assertEqual(row["move_pct"], 0.27317)
        self.assertEqual(row["tracking"]["reference_price"], 1.0982)
        self.assertEqual(len(row["tracking"]["history"]), 2)
        self.assertEqual(row["status"], "UNTESTABLE")

    def test_completed_trade_keeps_frozen_exit_even_as_watch_price_moves(self):
        p = plan()
        book = {"candidates": {"T001": {
            "plan": p, "result": {"status": "WIN_TP1", "scored": True,
                                  "net_r": 2.0, "exit": 1.09},
            "events": []}}}
        market = {"pairs": {"EUR_USD": {"quote": {
            "time": "2026-10-26T10:29:00Z", "bid": 1.085,
            "ask": 1.086, "tradeable": True}}}}
        update_book(book, {"candidates":[]}, {"signals":[]},
                    stamp("2026-10-26T10:30:00Z"), market=market)
        row = public_view(book)["candidates"][0]
        self.assertEqual(row["move_pct"], 0.90909)
        self.assertEqual(row["tracking"]["move_pct"], 1.27273)
        self.assertEqual(row["tracking"]["state"], "PAPER_CLOSED")
        self.assertEqual(len(row["tracking"]["history"]), 1)

    def test_win_and_loss_are_prospective(self):
        wins = first_two() + [bar("2026-10-23T10:30:00Z", 1.098,
                                   1.101, 1.088, 1.09)]
        lose = first_two() + [bar("2026-10-23T10:30:00Z", 1.101,
                                   1.106, 1.098, 1.105)]
        r = replay(plan(), wins, stamp("2026-10-23T10:45:00Z"))
        self.assertEqual(r["status"], "WIN_TP1")
        self.assertTrue(r["scored"])
        self.assertEqual(r["net_r"], 2.0)
        r2 = replay(plan(), lose, stamp("2026-10-23T10:45:00Z"))
        self.assertEqual(r2["status"], "LOSS_STOP")
        self.assertEqual(r2["net_r"], -1)

    def test_same_bar_stop_and_target_never_mark_win(self):
        both = first_two() + [bar("2026-10-23T10:30:00Z", 1.1,
                                   1.106, 1.088, 1.1)]
        r = replay(plan(), both, stamp("2026-10-23T10:45:00Z"))
        self.assertEqual(r["status"], "AMBIGUOUS_EXIT_BAR")
        self.assertFalse(r["scored"])

    def test_target_in_entry_bar_is_unscorable(self):
        data = first_two()[:1] + [bar("2026-10-23T10:15:00Z", 1.1002,
                                       1.1008, 1.088, 1.091)]
        r = replay(plan(), data, stamp("2026-10-23T10:30:00Z"))
        self.assertEqual(r["status"], "AMBIGUOUS_ENTRY_BAR")

    def test_never_fill_before_trigger(self):
        data = [bar("2026-10-23T10:00:00Z", 1.1, 1.101, 1.098, 1.1008)]
        r = replay(plan(), data, stamp("2026-10-23T13:00:00Z"))
        self.assertEqual(r["status"], "UNTRIGGERED")
        self.assertFalse(r["scored"])

    def test_creation_candle_not_reused(self):
        p = plan()
        p["created_at"] = "2026-10-23T10:04:00Z"
        data = first_two()
        r = replay(p, data, stamp("2026-10-23T11:00:00Z"))
        self.assertEqual(r["status"], "PENDING_TRIGGER")
        self.assertFalse(r["scored"])

    def test_data_gap_not_falsely_expired(self):
        data = [bar("2026-10-23T10:45:00Z", 1.1, 1.101, 1.099, 1.1)]
        r = replay(plan(), data, stamp("2026-10-23T13:00:00Z"))
        self.assertEqual(r["status"], "DATA_GAP")

    def test_incomplete_friday_candidate_unscored(self):
        p = dict(plan())
        del p["expires_at"]
        r = replay(p, [], stamp("2026-10-23T13:00:00Z"))
        self.assertEqual(r["status"], "UNTESTABLE")

    def test_stop_before_confirmation_cancels_plan(self):
        data = [bar("2026-10-23T10:00:00Z", 1.103, 1.106, 1.099, 1.0997)]
        r = replay(plan(), data, stamp("2026-10-23T10:15:00Z"))
        self.assertEqual(r["status"], "INVALIDATED_PRE_ENTRY")
        self.assertFalse(r["scored"])

    def test_blackout_skips_confirmation(self):
        p = plan()
        p["event_blackouts_utc"] = [
            {"start":"2026-10-23T10:00:00Z","end":"2026-10-23T10:30:00Z"}]
        r = replay(p, first_two(), stamp("2026-10-23T10:30:00Z"))
        self.assertEqual(r["status"], "PENDING_TRIGGER")

    def test_no_premature_time_exit(self):
        r = replay(plan(), first_two(), stamp("2026-10-23T10:30:00Z"))
        self.assertEqual(r["status"], "OPEN")

    def test_time_exit_after_96_completed_trading_bars(self):
        base = stamp("2026-10-23T10:30:00Z")
        data = first_two()+[
            bar((base+dt.timedelta(minutes=15*i)).isoformat(),
                1.099,1.101,1.096,1.098) for i in range(96)]
        r = replay(plan(), data, stamp("2026-10-24T10:30:00Z"))
        self.assertEqual(r["status"], "TIME_EXIT")
        self.assertTrue(r["scored"])

    def test_short_friday_intraday_gap_is_not_ignored(self):
        data=first_two()+[bar("2026-10-23T14:30:00Z",1.099,1.101,1.096,1.098)]
        r=replay(plan(),data,stamp("2026-10-23T14:45:00Z"))
        self.assertEqual(r["status"],"DATA_GAP")

    def test_even_one_missing_m15_bar_is_a_data_gap(self):
        data=first_two()+[bar("2026-10-23T10:45:00Z",1.099,1.101,1.096,1.098)]
        r=replay(plan(),data,stamp("2026-10-23T11:00:00Z"))
        self.assertEqual(r["status"],"DATA_GAP")

    def test_triggered_unfilled_is_not_frozen_before_expiry(self):
        p = plan()
        book = {"candidates": {}, "warnings": []}
        waiting = first_two()[:1]
        update_book(book, {"candidates":[p]}, {"signals":[]},
                    stamp("2026-10-23T10:15:00Z"), lambda *_: waiting)
        self.assertEqual(book["candidates"]["T001"]["result"]["status"], "TRIGGERED_UNFILLED")
        update_book(book, {"candidates":[p]}, {"signals":[]},
                    stamp("2026-10-23T10:45:00Z"),
                    lambda *_: first_two()+[bar("2026-10-23T10:30:00Z",
                                                1.098,1.101,1.088,1.09)])
        self.assertEqual(book["candidates"]["T001"]["result"]["status"], "WIN_TP1")

    def test_immutable_plan_and_scoring(self):
        p = plan()
        book = {"candidates": {}, "warnings": []}
        now = stamp("2026-10-23T10:45:00Z")
        bars = first_two()+[bar("2026-10-23T10:30:00Z",1.098,1.101,1.088,1.09)]
        fetch = lambda *_: bars
        update_book(book, {"candidates":[p]}, {"signals":[]}, now, fetch)
        self.assertEqual(book["candidates"]["T001"]["result"]["status"], "WIN_TP1")
        changed = dict(p, stop=1.104)
        update_book(book, {"candidates":[changed]}, {"signals":[]}, now, fetch)
        self.assertEqual(book["candidates"]["T001"]["plan"]["stop"], 1.105)
        self.assertIn("IMMUTABLE_PLAN_CONFLICT:T001",book["warnings"])
        totals = public_view(book)["totals"]
        self.assertEqual((totals["scored"],totals["wins"]), (1,1))

    def test_evidence_is_private_and_legacy_win_can_be_audited(self):
        p = plan()
        now = stamp("2026-10-23T10:45:00Z")
        bars = first_two()+[bar("2026-10-23T10:30:00Z",1.098,1.101,1.088,1.09)]
        book = {"candidates":{"T001":{"plan":p,"first_recorded_at":"2026-10-23T10:45:00Z",
                   "result":{"status":"WIN_TP1","net_r":2.0,"scored":True},
                   "events":[]}},"warnings":[]}
        update_book(book, {"candidates":[]}, {"signals":[]}, now, lambda *_: bars)
        self.assertIn("confirmation",book["candidates"]["T001"]["result"]["evidence"])
        self.assertIn("entry",book["candidates"]["T001"]["result"]["evidence"])
        self.assertIn("exit",book["candidates"]["T001"]["result"]["evidence"])
        exported=public_view(book)
        self.assertNotIn("evidence",exported["candidates"][0])
        self.assertEqual(exported["totals"]["wins"],1)

    def test_same_economic_terms_from_shortlist_and_signal_do_not_conflict(self):
        p = plan()
        with_notes = dict(p, source="SCHEDULED_MAIN_ASSESSMENT",
                          technical_context={"m15_adx":24.5})
        book = {"candidates": {}, "warnings":[]}
        update_book(book, {"candidates":[with_notes]},
                    {"signals":[p]}, stamp("2026-10-23T10:15:00Z"),
                    lambda *_: first_two()[:1])
        self.assertNotIn("IMMUTABLE_PLAN_CONFLICT:T001",book["warnings"])
        self.assertEqual(book["candidates"]["T001"]["plan"]["source"],
                         "SCHEDULED_MAIN_ASSESSMENT")

    def test_only_matching_original_source_can_restore_omitted_zone(self):
        p = plan()
        p["source"] = "FRIDAY_CONVERSATION_SHORTLIST"
        p["original_note"] = "Original 23 Oct watch"
        old = dict(p)
        full = dict(p, entry_low=1.0990, entry_high=1.1010)
        book = {"candidates": {"T001": {
            "plan": old, "result":{"status":"UNTRIGGERED","scored":False},
            "events":[]}}, "warnings":[]}
        update_book(book, {"candidates":[full]}, {"signals":[]},
                    stamp("2026-10-26T09:00:00Z"),lambda *_:[])
        self.assertEqual(book["candidates"]["T001"]["plan"]["entry_low"],1.0990)
        self.assertEqual(book["candidates"]["T001"]["plan"]["stop"],1.105)
        self.assertTrue(any(x["to"]=="ORIGINAL_SOURCE_ZONE_METADATA_RESTORED"
                            for x in book["candidates"]["T001"]["events"]))

    def test_breakout_then_later_retest_route_a(self):
        p = plan()
        del p["alert_b"]
        p["alert_a"] = 1.1
        p["strategy"] = "M15_BREAKOUT_RETEST"
        candles = [
            bar("2026-10-23T10:00:00Z",1.1009,1.1016,1.1002,1.1008),
            bar("2026-10-23T10:15:00Z",1.1007,1.1008,1.0992,1.0995),
            bar("2026-10-23T10:30:00Z",1.0996,1.1002,1.0989,1.0994),
            bar("2026-10-23T10:45:00Z",1.0995,1.1005,1.0990,1.1001),
            bar("2026-10-23T11:00:00Z",1.0988,1.1000,1.0880,1.0900),
        ]
        r=replay(p,candles,stamp("2026-10-23T11:15:00Z"))
        self.assertEqual(r["status"],"WIN_TP1")
        self.assertEqual(r["route"],"A")

    def test_learning_bias_requires_diverse_forward_sample(self):
        base = stamp("2026-10-01T09:00:00Z")
        book = {"candidates": {}}
        for i in range(30):
            p = dict(plan(), id="S"+str(i), instrument=["EUR_USD","GBP_USD","AUD_USD"][i%3],
                     created_at=(base+dt.timedelta(days=i//3)).isoformat())
            book["candidates"][p["id"]] = {"plan": p, "result": {
                "status": "WIN_TP1", "scored": True, "net_r": 2.0, "route": "B"}}
        score = public_view(book)["strategy"]["M15_REJECTION / ROUTE_B"]
        self.assertEqual(score["paper_priority_bias"], 1)
        self.assertEqual(score["distinct_days"], 10)
        book["candidates"].pop("S29")
        score = public_view(book)["strategy"]["M15_REJECTION / ROUTE_B"]
        self.assertEqual(score["paper_priority_bias"], 0)


    def test_ranked_research_is_captured_and_monitored_without_fake_paper_wins(self):
        now = stamp("2026-10-26T10:00:00Z")
        market = {"collected_at": "2026-10-26T10:00:00Z",
                  "pairs": {"GBP_USD": {"quote": {"time":"2026-10-26T09:59:45Z",
                    "bid":1.1000, "ask":1.1002, "tradeable":True}}}}
        summary = {"collected_at": market["collected_at"],
                   "ranked_review_candidates": [
                       {"instrument":"GBP_USD", "potential_direction_not_approved":"SHORT",
                        "research_priority":68.5}]}
        book = {"candidates": {}, "warnings": []}
        seeds = ranked_priority_watches(summary, market, book, now)
        self.assertEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["source"], "AUTOMATED_RESEARCH_PRIORITY")
        self.assertNotIn("planned_entry", seeds[0])
        update_book(book, {"candidates":seeds}, {"signals":[]}, now, market=market)
        row = public_view(book)["candidates"][0]
        self.assertEqual(row["status"], "UNTESTABLE")
        self.assertFalse(row["scored"])
        self.assertEqual(row["move_pct"], 0)
        self.assertEqual(row["tracking"]["reference_kind"], "FIRST_OBSERVED_QUOTE")
        # A later ranking creates a distinct unfilled research observation;
        # the original baseline remains frozen and neither counts as a trade.
        later = stamp("2026-10-26T11:00:00Z")
        market["collected_at"] = "2026-10-26T11:00:00Z"
        market["pairs"]["GBP_USD"]["quote"].update(
            time="2026-10-26T10:59:45Z", bid=1.0990, ask=1.0992)
        summary["collected_at"] = market["collected_at"]
        second = ranked_priority_watches(summary, market, book, later)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(second[0]["id"], seeds[0]["id"])
        update_book(book, {"candidates":second}, {"signals":[]}, later, market=market)
        final = public_view(book)
        self.assertEqual(final["totals"]["candidates"], 2)
        self.assertEqual(final["totals"]["scored"], 0)
        self.assertEqual(final["totals"]["wins"], 0)
        original = book["candidates"][seeds[0]["id"]]
        newest = book["candidates"][second[0]["id"]]
        self.assertEqual(original["tracking"]["reference_price"], 1.1002)
        self.assertGreater(original["tracking"]["move_pct"], 0)
        self.assertEqual(len(original["tracking"]["history"]), 2)
        self.assertEqual(newest["tracking"]["reference_price"], 1.0992)
        self.assertFalse(newest["result"]["scored"])

    def test_priority_research_rejects_stale_and_mismatched_snapshots(self):
        now = stamp("2026-10-26T10:00:00Z")
        market = {"collected_at":"2026-10-26T09:00:00Z",
                  "pairs":{"EUR_USD":{"quote":{"time":"2026-10-26T09:59:00Z",
                   "bid":1.1,"ask":1.1002,"tradeable":True}}}}
        summary = {"collected_at":"2026-10-26T10:00:00Z",
                   "ranked_review_candidates":[{"instrument":"EUR_USD",
                     "potential_direction_not_approved":"LONG"}]}
        self.assertEqual(ranked_priority_watches(summary,market,{},now),[])
        market["collected_at"]=summary["collected_at"]
        market["pairs"]["EUR_USD"]["quote"]["time"]="2026-10-26T08:00:00Z"
        self.assertEqual(ranked_priority_watches(summary,market,{},now),[])


if __name__ == "__main__":
    unittest.main()
