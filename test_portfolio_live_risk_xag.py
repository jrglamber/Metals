"""Read-only Metals XAU/XAG stop-floor coverage and broker-stop safety tests."""
import unittest
from unittest.mock import patch
import portfolio_live_risk as risk

BASE="https://api-fxtrade.oanda.com"

def mock_broker(base,account,token,path,params=None):
    if path=="summary":
        return {"account":{"currency":"GBP"}}
    if path=="openTrades":
        return {"trades":[
            {"id":"a1","instrument":"XAU_USD","currentUnits":"1","price":"4000.0","stopLossOrderID":"o1"},
            {"id":"s1","instrument":"XAG_USD","currentUnits":"-1","price":"30.0","stopLossOrderID":"o2"},
        ]}
    if path=="pendingOrders":
        return {"orders":[
            {"id":"o1","tradeID":"a1","type":"STOP_LOSS","state":"PENDING","price":"3990.0"},
            {"id":"o2","tradeID":"s1","type":"STOP_LOSS","state":"PENDING","price":"32.0"},
        ]}
    if path=="pricing":
        return {"prices":[{"closeoutBid":"0.75","closeoutAsk":"0.75"}]}
    raise AssertionError("Unexpected OANDA endpoint: "+str(path))

class MetalsXagRiskTests(unittest.TestCase):
    def calculate(self, override=None):
        def reader(base,account,token,path,params=None):
            if override:
                result=override(path)
                if result is not None: return result
            return mock_broker(base,account,token,path,params)
        with patch.object(risk,"_credentials",return_value=("test-live-account","token")),\
             patch.object(risk,"_api_base",return_value=BASE),\
             patch.object(risk,"_get_json",side_effect=reader):
            return risk._calculate("metals")

    def test_both_live_metals_assets_count_and_quantify(self):
        d=self.calculate()
        self.assertTrue(d["ok"])
        self.assertEqual((d["open_trade_count"],d["quantified_trade_count"]),(2,2))
        self.assertEqual(d["unquantified_trade_count"],0)
        self.assertEqual(set(d["broker_trade_ids"]),{"a1","s1"})
        self.assertEqual(d["by_instrument"]["XAG_USD"]["short_trade_count"],1)
        self.assertAlmostEqual(d["capital_at_risk_gbp"],9.0,places=2)
        self.assertEqual(d["locked_profit_floor_gbp"],0.0)
        self.assertFalse(d["execution_authority"])

    def test_missing_pending_xag_stop_remains_unquantified(self):
        d=self.calculate(lambda path: {"orders":[
            {"id":"o1","tradeID":"a1","type":"STOP_LOSS","state":"PENDING","price":"3990.0"}]
        } if path=="pendingOrders" else None)
        self.assertEqual((d["open_trade_count"],d["quantified_trade_count"]),(2,1))
        self.assertEqual(d["without_stop_trade_count"],1)
        self.assertEqual(d["by_instrument"]["XAG_USD"]["quantified_trade_count"],0)

    def test_wrong_trade_id_or_cancelled_order_is_never_a_valid_stop(self):
        for wrong in (
            {"id":"o2","tradeID":"another-trade","type":"STOP_LOSS","state":"PENDING","price":"32"},
            {"id":"o2","tradeID":"s1","type":"STOP_LOSS","state":"CANCELLED","price":"32"},
            {"id":"o2","tradeID":"s1","type":"LIMIT","state":"PENDING","price":"32"},
        ):
            with self.subTest(order=wrong):
                d=self.calculate(lambda path: {"orders":[
                    {"id":"o1","tradeID":"a1","type":"STOP_LOSS","state":"PENDING","price":"3990"},
                    wrong
                ]} if path=="pendingOrders" else None)
                self.assertEqual(d["unquantified_trade_count"],1)

    def test_failure_fetching_pending_orders_fails_closed(self):
        def broken(path):
            if path=="pendingOrders": raise RuntimeError("OANDA timeout")
        d=self.calculate(broken)
        self.assertFalse(d["ok"])
        self.assertIsNone(d["capital_at_risk_gbp"])
        self.assertIn("pending stop orders",d["error"])

    def test_metals_scope_and_other_assets_unchanged(self):
        self.assertTrue(risk._allowed("metals","XAU_USD"))
        self.assertTrue(risk._allowed("metals","XAG_USD"))
        self.assertFalse(risk._allowed("metals","NAS100_USD"))
        self.assertTrue(risk._allowed("indices","NAS100_USD"))

if __name__=="__main__":
    unittest.main()
