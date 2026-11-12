"""Unit/regression coverage for LIVE XAG Short ownership and practice-independent router."""
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, Mock

import xag_short_live_override as live
import metals_xag_live_independent_override as independent


class Core:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE metals_xag_live_trade_links(
            id INTEGER PRIMARY KEY,raw_signal_id INTEGER,broker_trade_id TEXT,
            instrument TEXT,side TEXT,created_at_utc TEXT,updated_at_utc TEXT,
            status TEXT,closed_at_utc TEXT,close_reason TEXT,realized_pl REAL,
            current_stop_price REAL,entry_price REAL,broker_open_time_utc TEXT,
            manager_last_review_signal_id INTEGER,last_reconciled_at_utc TEXT
        );
        CREATE TABLE metals_xag_live_execution_audit (
            id INTEGER PRIMARY KEY,created_at_utc TEXT,raw_signal_id INTEGER,
            action TEXT,status TEXT,reason TEXT,link_id INTEGER,
            broker_trade_id TEXT,raw_json TEXT
        );
        CREATE TABLE metals_xag_live_entry_intents (
            raw_signal_id INTEGER PRIMARY KEY,created_at_utc TEXT,updated_at_utc TEXT,
            status TEXT,broker_trade_id TEXT,reason TEXT
        );
        CREATE TABLE raw_signals(
            id INTEGER PRIMARY KEY,pair TEXT,received_at_utc TEXT,timestamp_readable TEXT
        );
        """)
    @contextmanager
    def get_conn(self):
        yield self.db
    @staticmethod
    def safe_float(x):
        try:
            return None if x is None else float(x)
        except (TypeError, ValueError):
            return None
    @staticmethod
    def _metals_demo_asset(asset):
        return str(asset or "").upper()
    @staticmethod
    def _metals_demo_side(side):
        return str(side or "").lower()
    @staticmethod
    def now_utc_iso():
        return datetime.now(timezone.utc).isoformat()
    @staticmethod
    def is_oanda_timeout_or_unknown_response(x):
        return False


def insert_link(core, trade_id, status="CLOSED", side="short"):
    core.db.execute(
        "INSERT INTO metals_xag_live_trade_links "
        "(id,raw_signal_id,broker_trade_id,instrument,side,status,"
        "created_at_utc,closed_at_utc,close_reason,realized_pl) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (int(trade_id), int(trade_id), str(trade_id),"XAG_USD",side,status,
         "2026-11-05T20:00:00Z","2026-11-10T11:00:00Z","LEGACY_CLOSE",1.5),
    )
    core.db.commit()


def open_trade(trade_id, price=39.0, units=-1, stop=41.0):
    return {
        "id":str(trade_id),"state":"OPEN","instrument":"XAG_USD",
        "currentUnits":str(units),"price":str(price),
        "stopLossOrder":{"price":str(stop),"state":"PENDING","tradeID":str(trade_id)},
        "openTime":"2026-11-05T20:00:00Z","realizedPL":"0.0"
    }


class BrokerReconcileTests(unittest.TestCase):
    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)
    def stub(self, broker, detail_by_id):
        def request(_core, path, method="GET", body=None):
            bid=path.split("/")[-1]
            trade=detail_by_id.get(bid)
            return ({"ok":True,"data":{"trade":trade}} if trade is not None
                    else {"ok":False,"error":"not_found"})
        return patch.multiple(live, _open_trades=lambda core:{"ok":True,"xag":broker},
                              _request=request)
    def test_reopen_two_historical_closed_records_with_protective_stops(self):
        insert_link(self.core,123)
        insert_link(self.core,124)
        trades=[open_trade(123),open_trade(124)]
        with self.stub(trades,{"123":trades[0],"124":trades[1]}):
            result=live._reconcile(self.core)
        self.assertTrue(result["ok"])
        self.assertTrue(result["exact_live_ownership"])
        self.assertEqual(result["recovered_historical_live_count"],2)
        self.assertEqual(result["unlinked_broker_xag_count"],0)
        rows=self.core.db.execute("SELECT status,closed_at_utc,close_reason,current_stop_price "
                                  "FROM metals_xag_live_trade_links").fetchall()
        self.assertTrue(all(r["status"]=="OPEN" and r["closed_at_utc"] is None
                            and r["close_reason"] is None and r["current_stop_price"]==41 for r in rows))
        self.assertEqual(self.core.db.execute(
            "SELECT COUNT(*) FROM metals_xag_live_execution_audit WHERE status='RECOVERED_BROKER_OPEN'"
        ).fetchone()[0],2)
    def test_recovery_refuses_missing_stop_and_leaves_closed_record(self):
        insert_link(self.core,123)
        bad=open_trade(123)
        bad["stopLossOrder"]={}
        with self.stub([bad],{"123":bad}):
            out=live._reconcile(self.core)
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"],"historical_open_broker_stop_unverified")
        self.assertEqual(self.core.db.execute(
            "SELECT status FROM metals_xag_live_trade_links").fetchone()[0],"CLOSED")
    def test_recovery_refuses_wrong_direction(self):
        insert_link(self.core,123)
        bad=open_trade(123,units=1)
        with self.stub([bad],{"123":bad}):
            out=live._reconcile(self.core)
        self.assertFalse(out["ok"])
    def test_unknown_live_trade_is_never_adopted(self):
        trade=open_trade(999)
        with self.stub([trade],{"999":trade}):
            result=live._reconcile(self.core)
        self.assertTrue(result["ok"])
        self.assertFalse(result["exact_live_ownership"])
        self.assertEqual(result["unlinked_broker_xag_count"],1)


class IndependentRouteTests(unittest.TestCase):
    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)
        self.old_calls=[]
        self.core.execute_metals_demo_candidate=lambda *args,**kw: self.old_calls.append((args,kw)) or {"ok":True}
        self.core._raw_signal_json=lambda x:{}
        now=datetime.now(timezone.utc).isoformat()
        self.core.db.execute("INSERT INTO raw_signals(id,pair,received_at_utc,timestamp_readable) VALUES(?,?,?,?)",
                             (5,"XAGUSD",now,"2026-11-12 14:00"))
        self.core.db.execute("INSERT INTO raw_signals(id,pair,received_at_utc,timestamp_readable) VALUES(?,?,?,?)",
                             (6,"XAUUSD",now,"2026-11-12 14:00"))
        self.core.db.commit()

    def test_live_xag_short_never_calls_practice_router(self):
        calls=[]
        # Provide lightweight app router matching minimal FastAPI methods.
        class App:
            def __init__(self): self.router=type("Router",(),{"routes":[]})()
            def add_api_route(self,path,fn,methods,include_in_schema):
                self.router.routes.append(type("Route",(),{"path":path,"endpoint":fn})())
        app=App()
        old_installed=independent._INSTALLED
        independent._INSTALLED=False
        self.addCleanup(setattr,independent,"_INSTALLED",old_installed)
        with patch.object(live,"_reconcile",return_value={"ok":True,"exact_live_ownership":True}), \
             patch.object(live,"_manager_tick",return_value={"ok":True}), \
             patch.object(live,"_execute_live",side_effect=lambda *args: calls.append("live") or {"ok":True,"opened":True}), \
             patch.object(independent,"_candidate",return_value={"demo_candidate":True,"demo_side":"short"}), \
             patch.object(independent,"_preflight",return_value={"approved_for_live":True,"asset":"XAGUSD","side":"short"}):
            install=independent.install(self.core,app)
            result=self.core.execute_metals_demo_candidate(5)
            self.assertTrue(install["installed"])
            self.assertTrue(result["practice_entry_skipped"])
            self.assertEqual(calls,["live"])
            self.assertEqual(self.old_calls,[])
            # XAU continues to delegate to the existing router.
            self.core.execute_metals_demo_candidate(6)
            self.assertEqual(len(self.old_calls),1)

    def test_preflight_blocks_stale_signal(self):
        old=datetime.now(timezone.utc)-timedelta(hours=2)
        self.assertFalse(independent._fresh_signal({"received_at_utc":old.isoformat()}))

    def test_campaign_count_reset_when_broker_flat(self):
        insert_link(self.core,5,status="CLOSED")
        count=independent._live_campaign_entries(self.core,0)
        self.assertEqual(count["count"],0)
        row=self.core.db.execute(
            "SELECT started_at_utc FROM metals_xag_live_campaign_state WHERE id=1").fetchone()
        self.assertTrue(row["started_at_utc"])

    def test_direct_model_governance_rejects_unarmed_live(self):
        row={"id":5,"pair":"XAGUSD","received_at_utc":datetime.now(timezone.utc).isoformat()}
        cand={"demo_candidate":True,"demo_side":"short"}
        with patch.object(independent,"_candidate",return_value=cand), \
             patch.object(live,"config_status",return_value={"orders_allowed":False}):
            out=independent._preflight(self.core,row,cand)
        self.assertEqual(out["reason"],"xag_live_not_armed")


if __name__=="__main__":
    unittest.main()
