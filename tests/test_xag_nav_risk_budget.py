import sys
import types
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from datetime import datetime, timezone
import metals_xag_nav_risk_override as gate

class FakeCursor:
    def __init__(self, results):
        self.results=results
    def fetchall(self):
        return self.results
    def fetchone(self):
        return self.results[0]
class FakeConn:
    def __init__(self, wrong=False, unresolved=0):
        self.wrong, self.unresolved = wrong, unresolved
    def execute(self, sql):
        if "COUNT(*)" in sql:
            return FakeCursor([{"n":self.unresolved}])
        return FakeCursor([{"broker_trade_id":"WRONG" if self.wrong else "trade1"}])
class Core:
    def __init__(self, wrong=False, unresolved=0):
        self.wrong,self.unresolved=wrong,unresolved
    @contextmanager
    def get_conn(self):
        yield FakeConn(self.wrong,self.unresolved)

def risk_stub(stop=50.0,trade_units=-1.0):
    m=types.ModuleType("portfolio_live_risk")
    m._credentials=lambda mode:("test","fake")
    m._api_base=lambda mode:"mock"
    def get_json(base,account,token,endpoint,params=None):
        if endpoint=="summary":
            return {"account":{"currency":"GBP","NAV":"5000"}}
        if endpoint=="openTrades":
            return {"trades":[{"instrument":"XAG_USD","id":"trade1",
                               "currentUnits":str(trade_units)}]}
        if endpoint=="pricing":
            return {"prices":[{"status":"tradeable","closeoutBid":"38.0",
                               "closeoutAsk":"38.1","time":datetime.now(timezone.utc).isoformat()}]}
        raise AssertionError(endpoint)
    m._get_json=get_json
    m._pending_stop_orders=lambda *args:{"1":{}}
    m._verified_stop=lambda trade,pending:stop
    m._factor=lambda *args:0.80
    return m

def sizing_stub(units=1.0):
    m=types.ModuleType("xag_short_live_probe")
    def snap(core):
        return {"ok":True,"blocking_reasons":[],"price_status":"tradeable",
                "price_time":datetime.now(timezone.utc).isoformat(),
                "oanda_account_currency":"GBP","recommended_units":units,
                "minimum_trade_size":1.0,"entry_price":38.0,
                "emergency_stop_price":40.0,"recommended_effective_risk_gbp":1.60,
                "target_risk_gbp":2.0}
    m.snapshot=snap
    return m

class XagNavBudgetTests(unittest.TestCase):
    def test_same_nominal_exposure_as_24_two_pound_trades(self):
        self.assertEqual(gate.CAP_PCT_NAV,0.85)
        self.assertAlmostEqual(5757.84*gate.CAP_PCT_NAV/100,48.94164)
    def test_budget_admits_and_blocks(self):
        self.assertTrue(gate.allowed(30,2,5000))
        self.assertFalse(gate.allowed(41,2,5000))
        for bad in [(float("nan"),2,5000),(0,0,5000),(0,2,-1)]:
            with self.assertRaises(ValueError):
                gate.allowed(*bad)
    def test_tighter_stop_recycles_capacity(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(50.0)}):
            before=gate.snapshot(Core())
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(40.0)}):
            after=gate.snapshot(Core())
        self.assertAlmostEqual(before["used"],(50-38.1)*0.8)
        self.assertAlmostEqual(after["used"],(40-38.1)*0.8)
        self.assertGreater(before["used"],after["used"])
    def test_missing_broker_stop_blocks(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(None)}):
            with self.assertRaisesRegex(ValueError,"attached_stop_missing"):
                gate.snapshot(Core())
    def test_reconciliation_disagreement_blocks(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(50.0)}):
            with self.assertRaisesRegex(ValueError,"reconciliation_mismatch"):
                gate.snapshot(Core(wrong=True))
    def test_outstanding_order_intent_blocks(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(50.0)}):
            with self.assertRaisesRegex(ValueError,"unresolved_broker_entry_intents"):
                gate.snapshot(Core(unresolved=1))
    def test_long_broker_position_blocks(self):
        with patch.dict(sys.modules,{"portfolio_live_risk":risk_stub(50.0,trade_units=1.0)}):
            with self.assertRaisesRegex(ValueError,"unexpected_broker_direction"):
                gate.snapshot(Core())
    def test_candidate_uses_pilot_and_buffer(self):
        state={"bid":38.0,"ask":38.1,"fx":0.8}
        with patch.dict(sys.modules,{"xag_short_live_probe":sizing_stub()}):
            self.assertAlmostEqual(gate.candidate_risk(Core(),state),1.76)
        with patch.dict(sys.modules,{"xag_short_live_probe":sizing_stub(2.0)}):
            with self.assertRaisesRegex(ValueError,"one_unit_pilot"):
                gate.candidate_risk(Core(),state)

if __name__=="__main__":
    unittest.main()
