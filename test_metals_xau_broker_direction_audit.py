import unittest
from metals_xau_broker_direction_audit import reconcile
from datetime import datetime, timezone

class TestAudit(unittest.TestCase):
    def test_mixed_close_and_financing(self):
        now=datetime(2026,11,21,tzinfo=timezone.utc)
        trades=[dict(id="1",instrument="XAU_USD",initialUnits="2",realizedPL="10"),dict(id="2",instrument="XAU_USD",initialUnits="-2",realizedPL="-3")]
        def tx(id,**kw):return dict(id=str(id),time="2026-11-20T00:00:00Z",**kw)
        events=[tx(1,type="ORDER_FILL",instrument="XAU_USD",pl="0",tradeOpened={"tradeID":"1"}),tx(2,type="ORDER_FILL",instrument="XAU_USD",pl="0",tradeOpened={"tradeID":"2"}),tx(3,type="ORDER_FILL",instrument="XAU_USD",pl="7",tradesClosed=[{"tradeID":"1","realizedPL":"10"},{"tradeID":"2","realizedPL":"-3"}]),tx(4,type="DAILY_FINANCING",positionFinancings=[{"instrument":"XAU_USD","financing":"-1","openTradeFinancings":[{"tradeID":"1","financing":"-0.8"},{"tradeID":"2","financing":"-0.2"}]}])]
        r=reconcile(trades,events,now)
        self.assertTrue(r["ok"],r)
        self.assertEqual(r["directions"]["long"]["all_time"],9.2)
        self.assertEqual(r["directions"]["short"]["all_time"],-3.2)
        events[2]["commission"]="1"
        r=reconcile(trades,events,now)
        self.assertFalse(r["ok"])
        self.assertEqual(r["directions"],{})
        self.assertIn("mixed_direction_fee_unallocated",r["issues"])
        self.assertFalse(reconcile(trades,events[:2],now)["ok"])
