import sys
import types
import unittest
from contextlib import contextmanager
from unittest.mock import patch
import metals_xau_nav_risk_override as gate

class Conn:
    def execute(self, sql):
        return self
    def fetchall(self):
        return [{"broker_trade_id":"101"}]

class Core:
    @contextmanager
    def get_conn(self):
        yield Conn()
    def _metals_xau_side_sizing_preview(self, side):
        return {"executable":True, "blocking_reasons":[], "units":0.1,
                "minimum_trade_size":0.1, "entry_price":4000,
                "stop_price":3950 if side=="long" else 4050}

def broker_stub(stop=4010):
    m=types.ModuleType("portfolio_live_risk")
    m._credentials=lambda mode:("account","token")
    m._api_base=lambda mode:"https://broker.example"
    def get_json(base, acct, token, endpoint, params=None):
        if endpoint=="summary":
            return {"account":{"currency":"GBP","NAV":"5000"}}
        if endpoint=="openTrades":
            return {"trades":[{"instrument":"XAU_USD","id":"101",
                               "currentUnits":"-0.1","price":"4000"}]}
        if endpoint=="pricing":
            return {"prices":[{"closeoutBid":"3998","closeoutAsk":"4000",
                              "time":"__DYNAMIC__"}]}
        raise AssertionError(endpoint)
    from datetime import datetime, timezone
    def live_get(*a, **kw):
        response=get_json(*a, **kw)
        if a[3]=="pricing":
            response["prices"][0]["time"]=datetime.now(timezone.utc).isoformat()
        return response
    m._get_json=live_get
    m._pending_stop_orders=lambda *args:{"order":{}}
    m._verified_stop=lambda trade, pending:stop
    m._factor=lambda *args:0.8
    return m

class XauBudgetTests(unittest.TestCase):
    def test_nav_percent_and_recycling(self):
        self.assertEqual(gate.CAP_PCT_NAV,2.4)
        self.assertFalse(gate.admission(118,5,5000))
        self.assertTrue(gate.admission(114,5,5000))
    def test_invalid_values_never_admit(self):
        for values in [(float("nan"),1,5000),(0,-1,5000),(0,1,0)]:
            with self.assertRaises(ValueError):
                gate.admission(*values)
    def test_stop_tightening_frees_capacity(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":broker_stub(4030)}):
            before=gate.broker_snapshot(Core())
        with patch.dict(sys.modules,{"portfolio_live_risk":broker_stub(4010)}):
            after=gate.broker_snapshot(Core())
        self.assertAlmostEqual(before["used"],2.4)
        self.assertAlmostEqual(after["used"],0.8)
        self.assertLess(after["used"],before["used"])
    def test_missing_stop_blocks(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":broker_stub(None)}):
            with self.assertRaisesRegex(ValueError,"missing_verified_broker_stop"):
                gate.broker_snapshot(Core())
    def test_new_short_preview_includes_buffer(self):
        s={"ask":4000,"bid":3998,"fx":0.8}
        self.assertAlmostEqual(gate.next_order_risk(Core(),"short",s),4.4)
    def test_fails_closed_if_local_links_mismatch(self):
        class WrongCore(Core):
            @contextmanager
            def get_conn(self):
                class WrongConn(Conn):
                    def fetchall(self):
                        return [{"broker_trade_id":"999"}]
                yield WrongConn()
        with patch.dict(sys.modules,{"portfolio_live_risk":broker_stub(4010)}):
            with self.assertRaisesRegex(ValueError,"links_disagree"):
                gate.broker_snapshot(WrongCore())

if __name__=="__main__":
    unittest.main()
