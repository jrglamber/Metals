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
    def test_recovery_accepts_broker_pending_stop_below_entry_after_profit_ratchet(self):
        insert_link(self.core,126)
        trade=open_trade(126,price=43.0,stop=42.0)
        with self.stub([trade],{"126":trade}):
            result=live._reconcile(self.core)
        self.assertTrue(result["ok"])
        self.assertEqual(result["recovered_historical_live_count"],1)
        row=self.core.db.execute(
            "SELECT current_stop_price,status FROM metals_xag_live_trade_links WHERE id=126"
        ).fetchone()
        self.assertEqual(row["current_stop_price"],42.0)
        self.assertEqual(row["status"],"OPEN")

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


class LiveOrderAttemptTests(unittest.TestCase):
    def setUp(self):
        self.core = Core()
        self.addCleanup(self.core.db.close)

    def test_governed_live_approval_submits_once_without_practice_link(self):
        approval = {
            "approved_for_live": True, "asset": "XAGUSD", "side": "short",
            "active_exit_policy": {"policy": live.POLICY, "version": live.POLICY_VERSION},
            "practice_order_placed": False,
        }
        probe = {
            "ok": True, "oanda_account_currency": "GBP", "price_status": "tradeable",
            "recommended_units": 1.0, "minimum_trade_size": 1.0,
            "recommended_effective_risk_gbp": 2.0, "target_risk_gbp": 2.0,
            "entry_price": 39.0, "emergency_stop_price": 41.0, "display_precision": 3,
        }
        writes = []
        def request(_core, path, method="GET", body=None):
            if method == "POST":
                writes.append(body)
                return {"ok": False, "error": "SIMULATED_BROKER_REJECTION"}
            raise AssertionError("Unexpected OANDA method " + method)
        with patch.object(live, "config_status", return_value={"orders_allowed": True}), \
             patch.object(live, "_reconcile", return_value={
                 "ok": True, "exact_live_ownership": True,
                 "unlinked_broker_xag_count": 0, "local_xag_open_count": 0,
                 "has_broker_long": False
             }), \
             patch.object(live.sizing_probe, "snapshot", return_value=probe), \
             patch.object(live, "_request", side_effect=request):
            first = live._execute_live(self.core, 5, approval)
            second = live._execute_live(self.core, 5, approval)
        self.assertFalse(first["ok"])
        self.assertEqual(second["reason"], "xag_live_entry_intent_already_exists")
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]["order"]["units"], "-1")
        self.assertEqual(writes[0]["order"]["instrument"], "XAG_USD")
        self.assertEqual(self.core.db.execute(
            "SELECT status FROM metals_xag_live_entry_intents WHERE raw_signal_id=5"
        ).fetchone()[0], "BROKER_REJECTED")

    def test_http_200_close_must_not_mark_closed_until_broker_confirms(self):
        insert_link(self.core, 123, status="OPEN")
        responses = []
        def request(_core,path,method="GET",body=None):
            responses.append(method)
            if method == "GET":
                return {"ok": True, "data": {"trade": {
                    "id": "123", "state": "OPEN", "currentUnits": "-1", "instrument": "XAG_USD"
                }}}
            if method == "PUT":
                return {"ok": True, "data": {"orderFillTransaction": {"pl": "0.5"}}}
            raise AssertionError(method)
        with patch.object(live, "config_status", return_value={"management_writes_allowed": True}), \
             patch.object(live, "_request", side_effect=request):
            result=live._close_link(self.core, {"id": 123, "raw_signal_id":123,
                         "broker_trade_id":"123"},"TEST_CLOSE")
        self.assertFalse(result["ok"])
        self.assertTrue(result["retry"])
        self.assertEqual(responses, ["GET","PUT","GET"])
        self.assertEqual(self.core.db.execute(
            "SELECT status FROM metals_xag_live_trade_links WHERE id=123"
        ).fetchone()[0],"CLOSE_RETRY")


    def test_live_health_uses_oanda_gbp_unrealized_not_local_closed_pl(self):
        insert_link(self.core,123,status="OPEN")
        insert_link(self.core,124,status="OPEN")
        trades=[
            {"id":"123","instrument":"XAG_USD","currentUnits":"-1","unrealizedPL":"1.2500"},
            {"id":"124","instrument":"XAG_USD","currentUnits":"-1","unrealizedPL":"1.7500"},
        ]
        with patch.object(live,"runtime_status",return_value={"orders_allowed":True}), \
             patch.object(live,"_open_trades",return_value={"ok":True,"xag":trades}):
            x=independent.health(self.core)
        self.assertEqual(x["execution_health"],"READY")
        self.assertEqual(x["tracked_open_count"],2)
        self.assertEqual(x["broker_open_unrealized_gbp"],3.0)
        self.assertTrue(x["broker_open_unrealized_complete"])
        self.assertFalse(x["realised_xag_ledger_verified"])

    def test_missing_oanda_unrealized_is_not_zero_filled(self):
        insert_link(self.core,123,status="OPEN")
        with patch.object(live,"runtime_status",return_value={"orders_allowed":True}), \
             patch.object(live,"_open_trades",return_value={
                 "ok":True,"xag":[{"id":"123","currentUnits":"-1"}]
             }):
            x=independent.health(self.core)
        self.assertIsNone(x["broker_open_unrealized_gbp"])
        self.assertFalse(x["broker_open_unrealized_complete"])


class MatureStopTransitionTests(unittest.TestCase):
    """Existing 48-candle policy should run regardless of entry candidacy."""

    def setUp(self):
        self.core=Core()
        self.addCleanup(self.core.db.close)
        self.old_calls=[]
        self.core.execute_metals_demo_candidate=lambda *a,**kw: self.old_calls.append(a) or {"ok":True}
        for sid,pair,age in ((101,"XAGUSD",0),(102,"XAUUSD",0),(103,"XAGUSD",180)):
            stamp=(datetime.now(timezone.utc)-timedelta(minutes=age)).isoformat()
            self.core.db.execute(
                "INSERT INTO raw_signals(id,pair,received_at_utc,timestamp_readable) "
                "VALUES(?,?,?,?)",(sid,pair,stamp,"2026-11-25 06:00"))
        self.core.db.commit()

    def _app(self):
        class App:
            def __init__(self): self.router=type("Router",(),{"routes":[]})()
            def add_api_route(self,path,fn,methods,include_in_schema):
                self.router.routes.append(type("Route",(),{"path":path,"endpoint":fn})())
        return App()

    def test_fresh_non_candidate_xag_still_triggers_live_exit_manager_no_new_entries(self):
        old=independent._INSTALLED
        independent._INSTALLED=False
        self.addCleanup(setattr, independent, "_INSTALLED", old)
        with patch.object(live,"_reconcile",return_value={"ok":True}), \
             patch.object(independent,"_candidate",return_value={"demo_candidate":False}), \
             patch.object(live,"_manager_tick",return_value={"ok":True,"reviewed":4,"results":[]}) as mgr, \
             patch.object(live,"_execute_live") as entry:
            independent.install(self.core,self._app())
            ret=self.core.execute_metals_demo_candidate(101)
            self.assertTrue(ret["ok"])
            mgr.assert_called_once_with(self.core,101)
            entry.assert_not_called()
            self.assertEqual(len(self.old_calls),1)

    def test_xau_stays_unchanged_and_stale_xag_signal_does_not_run_manager(self):
        old=independent._INSTALLED
        independent._INSTALLED=False
        self.addCleanup(setattr, independent, "_INSTALLED", old)
        with patch.object(live,"_reconcile",return_value={"ok":True}), \
             patch.object(independent,"_candidate",return_value={"demo_candidate":False}), \
             patch.object(live,"_manager_tick",return_value={"ok":True}) as mgr:
            independent.install(self.core,self._app())
            self.core.execute_metals_demo_candidate(102)
            self.core.execute_metals_demo_candidate(103)
            mgr.assert_not_called()
            self.assertEqual(len(self.old_calls),2)

    def test_actual_broker_stop_ratchet_verified_only_after_successful_readback(self):
        insert_link(self.core,201,status="OPEN")
        self.core.db.execute("UPDATE metals_xag_live_trade_links SET current_stop_price=44.0 WHERE id=201")
        self.core.db.commit()
        states=iter([44.0,42.2])
        writes=[]
        def req(_core,path,method="GET",body=None):
            if method=="PUT":
                writes.append((path,body))
                return {"ok":True,"status_code":200,"data":{"stopLossOrderTransaction":{}}}
            stop=next(states)
            return {"ok":True,"data":{"trade":{"id":"201","instrument":"XAG_USD",
                "state":"OPEN","currentUnits":"-1","price":"43.0",
                "stopLossOrder":{"price":str(stop),"state":"PENDING",
                                 "tradeID":"201"}}}}
        link={"id":201,"raw_signal_id":201,"broker_trade_id":"201"}
        with patch.object(live,"config_status",return_value={"management_writes_allowed":True}), \
             patch.object(live.sizing_probe,"snapshot",return_value={"display_precision":3}), \
             patch.object(live,"_request",side_effect=req):
            result=live._update_stop(self.core,link,42.2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"],"UPDATED_BROKER_VERIFIED")
        self.assertEqual(len(writes),1)
        saved=self.core.db.execute("SELECT current_stop_price FROM metals_xag_live_trade_links WHERE id=201").fetchone()[0]
        self.assertEqual(saved,42.2)

    def test_http_200_without_a_new_broker_stop_is_not_reported_success(self):
        insert_link(self.core,202,status="OPEN")
        self.core.db.execute("UPDATE metals_xag_live_trade_links SET current_stop_price=44.0 WHERE id=202")
        self.core.db.commit()
        def req(_core,path,method="GET",body=None):
            if method=="PUT":
                return {"ok":True,"status_code":200,"data":{"orderCancelTransaction":{"reason":"MARKET_HALTED"}}}
            return {"ok":True,"data":{"trade":{"id":"202","instrument":"XAG_USD",
                "state":"OPEN","currentUnits":"-1","price":"43.0",
                "stopLossOrder":{"price":"44.0","state":"PENDING",
                                 "tradeID":"202"}}}}
        link={"id":202,"raw_signal_id":202,"broker_trade_id":"202"}
        with patch.object(live,"config_status",return_value={"management_writes_allowed":True}), \
             patch.object(live.sizing_probe,"snapshot",return_value={"display_precision":3}), \
             patch.object(live,"_request",side_effect=req):
            result=live._update_stop(self.core,link,42.0)
        self.assertFalse(result["ok"])
        self.assertEqual(self.core.db.execute(
            "SELECT current_stop_price FROM metals_xag_live_trade_links WHERE id=202"
        ).fetchone()[0],44.0)

    def test_broker_stop_stronger_than_local_never_loosened(self):
        insert_link(self.core,203,status="OPEN")
        self.core.db.execute("UPDATE metals_xag_live_trade_links SET current_stop_price=44.0 WHERE id=203")
        self.core.db.commit()
        def req(_core,path,method="GET",body=None):
            self.assertEqual(method,"GET")
            return {"ok":True,"data":{"trade":{"id":"203","instrument":"XAG_USD",
                "state":"OPEN","currentUnits":"-1","price":"43.0",
                "stopLossOrder":{"price":"41.0","state":"PENDING",
                                 "tradeID":"203"}}}}
        link={"id":203,"raw_signal_id":203,"broker_trade_id":"203"}
        with patch.object(live,"config_status",return_value={"management_writes_allowed":True}), \
             patch.object(live.sizing_probe,"snapshot",return_value={"display_precision":3}), \
             patch.object(live,"_request",side_effect=req):
            result=live._update_stop(self.core,link,42.0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"],"UNCHANGED_BROKER_TIGHTER")
        self.assertEqual(self.core.db.execute(
            "SELECT current_stop_price FROM metals_xag_live_trade_links WHERE id=203"
        ).fetchone()[0],41.0)

    def test_no_broker_stop_verification_never_writes(self):
        insert_link(self.core,204,status="OPEN")
        link={"id":204,"raw_signal_id":204,"broker_trade_id":"204"}
        with patch.object(live,"config_status",return_value={"management_writes_allowed":True}), \
             patch.object(live.sizing_probe,"snapshot",return_value={"display_precision":3}), \
             patch.object(live,"_request",return_value={"ok":False}) as req:
            result=live._update_stop(self.core,link,42.0)
        self.assertFalse(result["ok"])
        self.assertEqual(req.call_count,1)



class XagManagerPostgresMaturityRegressionTests(unittest.TestCase):
    """Guard against PostgreSQL IndeterminateDatatype in 48-candle manager."""

    def test_mature_xag_reconciles_and_writes_typed_48h_checkpoint(self):
        from contextlib import contextmanager
        reviewed_sql = []
        class PostgresLikeConnection:
            def execute(self, sql, params):
                self.sql = sql
                # PostgreSQL cannot infer the type of a bare ? IS NOT NULL.
                if "AND ? IS NOT NULL" in sql:
                    raise RuntimeError("IndeterminateDatatype: could not determine data type of parameter")
                reviewed_sql.append((sql, params))
                return None
            def commit(self):
                pass
        pg = PostgresLikeConnection()
        @contextmanager
        def get_conn():
            yield pg
        class LiveCore:
            get_conn = staticmethod(get_conn)
            safe_float = staticmethod(Core.safe_float)
            _metals_demo_trade_metrics = staticmethod(lambda link: {
                "ok": True, "hold_candles": 48, "current_r": 0.9,
                "mfe_r": 1.0, "mae_r": -0.2,
                "current_price": 40.0, "fixed_48h_price": 40.0,
                "fixed_48h_r": 0.9,
            })
        live_core = LiveCore()
        link = {
            "id": 1001, "raw_signal_id": 800, "broker_trade_id": "123456",
            "status": "OPEN", "entry_price": 43.0, "stop_price": 45.2,
            "current_stop_price": 45.2, "manager_last_review_signal_id": None,
            "manager_high_water_r": 0.0,
        }
        with patch.object(live, "_reconcile", return_value={"ok": True}), \
             patch.object(live, "_local_open", return_value=[link]), \
             patch.object(live, "_update_stop",
                          return_value={"ok": True, "status": "UPDATED_BROKER_VERIFIED"}) as stop:
            outcome = live._manager_tick(live_core, 900)
        self.assertTrue(outcome["ok"])
        self.assertEqual(outcome["reviewed"], 1)
        self.assertEqual(outcome["results"][0]["decision"], "EXTEND")
        stop.assert_called_once()
        self.assertEqual(len(reviewed_sql), 1)
        self.assertIn("CAST(? AS DOUBLE PRECISION) IS NOT NULL", reviewed_sql[0][0])
        self.assertEqual(reviewed_sql[0][1][-1], 1001)


if __name__=="__main__":
    unittest.main()
