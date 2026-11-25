import json
import unittest
from datetime import datetime, timedelta, timezone
from metals_contraction_entry_research import analyse, summarize

START=datetime(2026,11,25,tzinfo=timezone.utc)
def iso(minutes):
    return (START+timedelta(minutes=minutes)).isoformat()
def sample(minute,pnl,stop=100,flat=False):
    marks=[] if flat else [dict(trade_id="old",instrument="XAG_USD",side="SHORT",
        unrealized_gbp=pnl,stop_price=stop)]
    return dict(sampled_at_utc=iso(minute),status="COMPLETE",
        xag_open_count=len(marks),position_marks_json=json.dumps(marks))
def entry():
    return dict(broker_trade_id="new",side="SHORT",opened_at_utc=iso(181),
        closed_at_utc=iso(300),status="CLOSED",realized_pl=-2)
def series(stop=100):
    return [sample(i,20-i/18,100 if i<180 else stop) for i in range(0,181,5)]
class ContractionResearch(unittest.TestCase):
    def test_contraction_blocks_and_later_outcome(self):
        row=analyse(series(),{"XAG":[entry()]})[0]
        self.assertEqual(row["C1_1H_IMPROVING"],"BLOCK")
        self.assertEqual(row["C2_3H_IMPROVING"],"BLOCK")
        self.assertEqual(row["C3_SIGNIFICANT_CONTRACTION"],"BLOCK")
        self.assertEqual(row["C4_PROTECTIVE_RELEASE_EXCEPTION"],"BLOCK")
        self.assertEqual(row["actual_gross_realised_gbp"],-2)
        self.assertEqual(summarize([row])[1]["blocked_actual_gross_gbp"],-2)
    def test_protective_short_stop_exception(self):
        row=analyse(series(99),{"XAG":[entry()]})[0]
        self.assertEqual(row["C4_PROTECTIVE_RELEASE_EXCEPTION"],"ALLOW")
    def test_missing_stop_unknown(self):
        row=analyse(series(None),{"XAG":[entry()]})[0]
        self.assertEqual(row["C4_PROTECTIVE_RELEASE_EXCEPTION"],"UNKNOWN")
    def test_future_marks_never_used(self):
        row=analyse(series()+[sample(185,1000,99)],{"XAG":[entry()]})[0]
        self.assertEqual(row["C3_SIGNIFICANT_CONTRACTION"],"BLOCK")
    def test_flat_start(self):
        row=analyse([sample(180,0,flat=True)],{"XAG":[entry()]})[0]
        self.assertEqual(row["C1_1H_IMPROVING"],"ALLOW")
    def test_stale_unknown(self):
        row=analyse([sample(150,20)],{"XAG":[entry()]})[0]
        self.assertEqual(row["C1_1H_IMPROVING"],"UNKNOWN")
if __name__=="__main__":
    unittest.main()
