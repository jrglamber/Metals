import unittest
from metals_intrahour_exit_override import _safe_stop_write_failure_detail


class XauStopDiagnosticTests(unittest.TestCase):
    def test_oanda_nested_rejection_is_visible_without_account_fields(self):
        response = {
            "ok": False, "status": "FAILED", "response": {
                "status_code": 400, "url_path": "/v3/accounts/PRIVATE/trades/123/orders",
                "data": {"errorMessage": "Invalid stop price", "stopLossOrderRejectTransaction": {
                    "rejectReason": "STOP_LOSS_ORDER_PRICE_INVALID", "accountID": "PRIVATE",
                    "clientExtensions": {"secret": "PRIVATE"}
                }}
            }}
        details = _safe_stop_write_failure_detail(response)
        self.assertEqual(details["write.response.status_code"], "400")
        self.assertEqual(details["write.response.data.errorMessage"], "Invalid stop price")
        self.assertEqual(details["write.response.data.stopLossOrderRejectTransaction.rejectReason"], "STOP_LOSS_ORDER_PRICE_INVALID")
        self.assertNotIn("PRIVATE", str(details))

    def test_broker_failure_status_is_kept(self):
        self.assertEqual(_safe_stop_write_failure_detail({"status": "FAILED", "reason": "broker_rejected"}),
                         {"write.status": "FAILED", "write.reason": "broker_rejected"})


if __name__ == "__main__":
    unittest.main()
