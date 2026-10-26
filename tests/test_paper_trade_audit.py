import datetime as dt
import unittest

from scripts.paper_trade_audit import replay, update_book, public_view, stamp


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


if __name__ == "__main__":
    unittest.main()
