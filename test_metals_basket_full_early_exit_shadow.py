"""Offline, no broker connection, acceptance tests for basket-full MFE25 shadow."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import metals_basket_full_early_exit_shadow as shadow


class Core:
    def __init__(self, path):
        self.path=path
    def get_conn(self):
        c=sqlite3.connect(self.path)
        c.row_factory=sqlite3.Row
        return c
    def broker_write(self, *args, **kwargs):
        raise AssertionError("Shadow attempted broker write")


def setup(core):
    with core.get_conn() as c:
        c.execute("""CREATE TABLE metals_forward_equity_samples
            (sampled_at_utc TEXT PRIMARY KEY,status TEXT,position_marks_json TEXT)""")
        for table in ("metals_xau_live_trade_links","metals_xag_live_trade_links"):
            c.execute(f"""CREATE TABLE {table} (
                broker_trade_id TEXT,status TEXT,manager_last_review_candles INTEGER,
                closed_at_utc TEXT,realized_pl DOUBLE PRECISION)""")
        c.commit()


def source(core,t,marks,status="COMPLETE",asset="XAG",trade_id="1",
           manager_candles=3,closed=False,gross=4.0):
    with core.get_conn() as c:
        c.execute("INSERT INTO metals_forward_equity_samples VALUES (?,?,?)",
                  (t,status,json.dumps(marks)))
        table=("metals_xag_live_trade_links" if asset=="XAG"
               else "metals_xau_live_trade_links")
        c.execute(f"DELETE FROM {table} WHERE broker_trade_id=?", (trade_id,))
        c.execute(f"""INSERT INTO {table}
            (broker_trade_id,status,manager_last_review_candles,closed_at_utc,realized_pl)
            VALUES (?,?,?,?,?)""",
            (trade_id,"CLOSED" if closed else "OPEN",
             manager_candles,t if closed else None,gross if closed else None))
        c.commit()


def mark(r,when="2026-11-24T17:01:00+00:00",asset="XAG",side="SHORT",trade_id="1"):
    return {"instrument":"XAG_USD" if asset=="XAG" else "XAU_USD",
            "side":side,"trade_id":trade_id,"opened_at_utc":when,
            "current_r":r,"unrealized_gbp":2*r}


def budget(*args):
    return {"status":"VERIFIED","reason":None,"used":52.,"cap":49.,
            "nav":5800.,"open":3,"full":True}


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.core=Core(str(Path(self.tmp.name)/"research.sqlite"))
        setup(self.core)
        self.patch=patch.object(shadow,"_budget",side_effect=budget)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def capture(self,t,marks,**kw):
        source(self.core,t,marks,**kw)
        return shadow.capture(self.core,t)

    def test_forward_1r_trigger_and_75pct_mfe_exit(self):
        self.capture("2026-11-24T17:00:00+00:00",[],trade_id="0")
        self.capture("2026-11-24T17:05:00+00:00",[mark(1.2)])
        self.capture("2026-11-24T17:10:00+00:00",[mark(.8)])
        items=shadow.export_rows(self.core)["trades"]
        by={x["policy"]:x for x in items}
        self.assertEqual(by["MFE_1R"]["last_status"],"EARLY_EXIT_SHADOW")
        self.assertEqual(by["MFE_1R"]["early_exit_observed_r"],.8)
        self.assertIsNone(by["MFE_2R"]["armed_at_utc"])
        self.assertEqual(by["MFE_1R_12H"]["last_status"],"OBSERVING")
        self.assertEqual(by["ANY_POSITIVE"]["last_status"],"EARLY_EXIT_SHADOW")
        self.assertEqual(shadow.status(self.core)["execution_authority"],False)

    def test_full_budget_required_and_does_not_arm_just_because_reversal_frees_risk(self):
        self.capture("2026-11-24T17:00:00+00:00",[],trade_id="0")
        with patch.object(shadow,"_budget",return_value=dict(budget(),full=False,used=38.)):
            self.capture("2026-11-24T17:05:00+00:00",[mark(1.5)])
        with patch.object(shadow,"_budget",return_value=dict(budget(),full=False,used=38.)):
            self.capture("2026-11-24T17:10:00+00:00",[mark(.2)])
        by={x["policy"]:x for x in shadow.export_rows(self.core)["trades"]}
        self.assertTrue(all(x["armed_at_utc"] is None for x in by.values()))

    def test_legacy_positions_and_mature_positions_are_never_armed(self):
        self.capture("2026-11-24T17:00:00+00:00",[mark(1.3,
                     when="2026-11-23T17:00:00+00:00")])
        self.capture("2026-11-24T17:05:00+00:00",[mark(1.4,
                     when="2026-11-23T17:00:00+00:00")])
        self.assertEqual(shadow.export_rows(self.core)["trades"],[])
        self.capture("2026-11-24T17:10:00+00:00",[mark(1.5,
                     when="2026-11-24T17:07:00+00:00")],manager_candles=48)
        self.assertEqual(shadow.export_rows(self.core)["trades"],[])

    def test_broker_risk_unverified_blocks_all_shadow_activation(self):
        self.capture("2026-11-24T17:00:00+00:00",[],trade_id="0")
        with patch.object(shadow,"_budget",return_value={
            "status":"UNVERIFIED","reason":"broker_stop_unavailable","used":None,
            "cap":None,"nav":None,"open":None,"full":None}):
            self.capture("2026-11-24T17:05:00+00:00",[mark(1.5)])
        self.assertEqual(shadow.export_rows(self.core)["trades"],[])
        samples=shadow.export_rows(self.core)["budget"]
        self.assertEqual(samples[-1]["status"],"INCOMPLETE")
        self.assertEqual(samples[-1]["budget_full"],None)

    def test_actual_close_is_only_observed_and_never_generated(self):
        self.capture("2026-11-24T17:00:00+00:00",[],trade_id="0")
        self.capture("2026-11-24T17:05:00+00:00",[mark(1.2)])
        self.capture("2026-11-24T17:10:00+00:00",[mark(.8)])
        self.capture("2026-11-24T17:15:00+00:00",[],closed=True,gross=-1.0)
        rows=shadow.export_rows(self.core)["trades"]
        self.assertTrue(all(x["actual_gross_realised_gbp"]==-1.0 for x in rows))
        self.assertTrue(all(x["execution_authority"]==0 for x in rows))
        self.assertEqual(len(shadow.export_rows(self.core)["budget"]),8)

    def test_xau_long_and_incomplete_source_do_not_produce_exit_signals(self):
        self.capture("2026-11-24T17:00:00+00:00",[],asset="XAU",trade_id="0")
        self.capture("2026-11-24T17:05:00+00:00",
                     [mark(1.5,asset="XAU",side="LONG")],asset="XAU")
        self.assertEqual(shadow.export_rows(self.core)["trades"],[])
        self.capture("2026-11-24T17:10:00+00:00",
                     [mark(1.5,asset="XAU")],asset="XAU",status="INCOMPLETE")
        self.assertEqual(shadow.export_rows(self.core)["trades"],[])


if __name__=="__main__":
    unittest.main()
