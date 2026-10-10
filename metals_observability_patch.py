"""Read-only observability for live XAU manager state.

No broker writes, entries, exits, stops, harvest changes, risk changes or strategy
decisions. Installed after production manager overrides.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict

VERSION="metals_observability_v1_2026_10_10"

def _f(core,v,default=0.0):
    try:
        x=core.safe_float(v); return float(x) if x is not None else default
    except Exception:
        try:return float(v)
        except Exception:return default

def _s(core,v):
    try:return str(core.safe_str(v) or "")
    except Exception:return str(v or "")

def _dt(core,v):
    try:return core.parse_dt(v)
    except Exception:return None

def install(core:Any,app:Any)->Dict[str,Any]:
    if getattr(core,"_METALS_OBSERVABILITY_INSTALLED",False): return dict(getattr(core,"_METALS_OBSERVABILITY_STATUS",{}))
    def snapshot():
        fn=getattr(core,"metals_xau_live_broker_snapshot",None)
        if not callable(fn): return {"ok":False,"error":"broker snapshot unavailable"}
        try:return dict(fn() or {})
        except Exception as exc:return {"ok":False,"error":f"{type(exc).__name__}: {exc}"}
    def audit():
        broker=snapshot(); byid={}
        for bt in broker.get("owned_open_trades") or []:
            if isinstance(bt,dict):
                tid=_s(core,bt.get("id") or bt.get("tradeID"));
                if tid:byid[tid]=bt
        try:
            with core.get_conn() as conn: links=[dict(r) for r in conn.execute("SELECT * FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id").fetchall()]
        except Exception:links=[]
        rows=[]; now=datetime.now(timezone.utc)
        for link in links:
            tid=_s(core,link.get("broker_trade_id")); bt=byid.get(tid,{})
            try:metrics=dict(core._metals_xau_live_trade_metrics(dict(link)) or {})
            except Exception as exc:metrics={"ok":False,"reason":f"{type(exc).__name__}: {exc}"}
            units=_f(core,bt.get("currentUnits")); side=_s(core,metrics.get("side") or link.get("side")).lower()
            if side not in {"long","short"}:side="short" if units<0 else "long"
            hold=int(_f(core,metrics.get("hold_candles")))
            opened=_dt(core,link.get("opened_at_utc") or link.get("open_time") or bt.get("openTime")); age=None
            if opened:
                if opened.tzinfo is None:opened=opened.replace(tzinfo=timezone.utc)
                age=max(0.0,(now-opened).total_seconds()/3600.0)
            rows.append({"broker_trade_id":tid,"side":side,"age_hours":age,"hold_candles":hold,"mature_by_manager":hold>=48,"age_48h_plus":bool(age is not None and age>=48),"maturity_mismatch":bool(age is not None and age>=48 and hold<48),"current_r":_f(core,metrics.get("current_r")),"mfe_r":_f(core,metrics.get("mfe_r")),"mae_r":_f(core,metrics.get("mae_r")),"broker_unrealized_pl":_f(core,bt.get("unrealizedPL")),"metrics_ok":bool(metrics.get("ok",True)),"metrics_reason":metrics.get("reason")})
        return {"ok":broker.get("ok") is not False,"scope":"READ_ONLY_LIVE_XAU_MANAGER_AUDIT","version":VERSION,"execution_authority":False,"open_count":len(rows),"long_count":sum(r["side"]=="long" for r in rows),"short_count":sum(r["side"]=="short" for r in rows),"manager_mature_count":sum(r["mature_by_manager"] for r in rows),"age_48h_plus_count":sum(r["age_48h_plus"] for r in rows),"maturity_mismatch_count":sum(r["maturity_mismatch"] for r in rows),"trades":rows,"time_utc":now.isoformat()}
    @app.get("/analysis/live-xau-manager-audit")
    def route():return audit()
    status={"installed":True,"version":VERSION,"read_only":True,"execution_authority":False}; core._METALS_OBSERVABILITY_INSTALLED=True; core._METALS_OBSERVABILITY_STATUS=status; return status
