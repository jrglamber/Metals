"""Metals monetisation research v1 — zero execution authority.

Research-only layers:
- deterministic 25% XAU SHORT runner cohort (every 4th live-short link), comparing
  current MFE25 management with a looser MFE50 hold signal;
- economic campaign value/HWM = realised since campaign baseline + current open P&L;
- explicit separation of price giveback from profit already crystallised.

This module is inert until install(core, app) is called by a wrapper. It never
places/closes orders, moves stops, changes sizing, or changes the live manager.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any, Dict, List
from fastapi.responses import Response

VERSION = "metals_monetisation_research_v1_2026_10_24"


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_MONETISATION_RESEARCH_V1_INSTALLED", False):
        return dict(getattr(core, "_METALS_MONETISATION_RESEARCH_V1_STATUS", {}) or {})

    def sf(v: Any, default: float = 0.0) -> float:
        try:
            x = core.safe_float(v)
            return float(x) if x is not None else default
        except Exception:
            try: return float(v)
            except Exception: return default

    def rows(cur) -> List[Dict[str, Any]]:
        try: return [dict(x) for x in cur.fetchall()]
        except Exception: return []

    def ensure(conn) -> None:
        id_type = "BIGSERIAL PRIMARY KEY" if getattr(conn, "postgres", False) else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS metals_economic_campaign_research (
                id {id_type}, created_at_utc TEXT NOT NULL, raw_signal_id BIGINT NOT NULL UNIQUE,
                campaign_id TEXT, open_count BIGINT, realised_since_baseline_gbp DOUBLE PRECISION,
                open_pnl_gbp DOUBLE PRECISION, economic_value_gbp DOUBLE PRECISION,
                economic_hwm_gbp DOUBLE PRECISION, economic_giveback_gbp DOUBLE PRECISION,
                baseline_realised_gbp DOUBLE PRECISION, bootstrap_partial INTEGER,
                execution_authority INTEGER NOT NULL DEFAULT 0, point_in_time_json TEXT
            )
        """)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS metals_xau_runner_cohort_research (
                id {id_type}, created_at_utc TEXT NOT NULL, raw_signal_id BIGINT NOT NULL,
                link_id BIGINT NOT NULL, trade_id TEXT, side TEXT, cohort_rank BIGINT,
                actual_policy TEXT, runner_policy TEXT, age_hours DOUBLE PRECISION,
                current_R DOUBLE PRECISION, mfe_R DOUBLE PRECISION, giveback_pct DOUBLE PRECISION,
                mature_48h INTEGER, mfe50_would_exit_now INTEGER,
                execution_authority INTEGER NOT NULL DEFAULT 0, point_in_time_json TEXT,
                UNIQUE(raw_signal_id,link_id)
            )
        """)

    def realised_total() -> float:
        try:
            snap = core.metals_xau_live_accounting_snapshot() or {}
            periods = snap.get("periods") or {}
            all_time = periods.get("all_time") or periods.get("all") or {}
            return sf(all_time.get("net_realized_gbp") if isinstance(all_time, dict) else None)
        except Exception:
            return 0.0

    state = {"campaign_id": None, "baseline_realised_gbp": None, "hwm_gbp": 0.0, "bootstrap_partial": True}

    def capture(raw_signal_id: int) -> None:
        broker = core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
        open_trades = list(broker.get("owned_open_trades") or [])
        open_count = int(broker.get("owned_open_count") or len(open_trades))
        open_pnl = sf(broker.get("owned_unrealized_pl"))
        total_realised = realised_total()
        now = core.now_utc_iso() if hasattr(core, "now_utc_iso") else ""

        with core.get_conn() as conn:
            ensure(conn)
            if open_count <= 0:
                state["campaign_id"] = None
                state["baseline_realised_gbp"] = total_realised
                state["hwm_gbp"] = 0.0
                state["bootstrap_partial"] = False
                return
            if state["campaign_id"] is None:
                state["campaign_id"] = f"XAU_ECON_{int(raw_signal_id)}"
                if state["baseline_realised_gbp"] is None:
                    state["baseline_realised_gbp"] = total_realised
                    state["bootstrap_partial"] = True
                state["hwm_gbp"] = max(0.0, open_pnl)
            realised_since = total_realised - float(state["baseline_realised_gbp"] or 0.0)
            economic = realised_since + open_pnl
            state["hwm_gbp"] = max(float(state["hwm_gbp"] or 0.0), economic)
            giveback = max(0.0, float(state["hwm_gbp"]) - economic)
            pit = {
                "campaign_id": state["campaign_id"], "open_count": open_count,
                "realised_since_baseline_gbp": realised_since, "open_pnl_gbp": open_pnl,
                "economic_value_gbp": economic, "economic_hwm_gbp": state["hwm_gbp"],
                "economic_giveback_gbp": giveback,
                "baseline_realised_gbp": state["baseline_realised_gbp"],
                "bootstrap_partial": bool(state["bootstrap_partial"]),
                "execution_authority": False,
            }
            if not conn.execute("SELECT id FROM metals_economic_campaign_research WHERE raw_signal_id=? LIMIT 1", (int(raw_signal_id),)).fetchone():
                conn.execute("""
                    INSERT INTO metals_economic_campaign_research(
                        created_at_utc,raw_signal_id,campaign_id,open_count,realised_since_baseline_gbp,
                        open_pnl_gbp,economic_value_gbp,economic_hwm_gbp,economic_giveback_gbp,
                        baseline_realised_gbp,bootstrap_partial,execution_authority,point_in_time_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (now,int(raw_signal_id),state["campaign_id"],open_count,realised_since,open_pnl,economic,
                      state["hwm_gbp"],giveback,state["baseline_realised_gbp"],1 if state["bootstrap_partial"] else 0,0,
                      json.dumps({"future_data_included":False,**pit},separators=(",",":"),default=str)))

            links = rows(conn.execute(
                "SELECT * FROM metals_xau_live_trade_links WHERE LOWER(COALESCE(side,''))='short' ORDER BY id ASC"
            ))
            broker_by_id = {str(x.get("id")): x for x in open_trades}
            for rank, link in enumerate(links, start=1):
                if rank % 4 != 0:
                    continue
                link_id = int(sf(link.get("id")))
                if conn.execute("SELECT id FROM metals_xau_runner_cohort_research WHERE raw_signal_id=? AND link_id=? LIMIT 1", (int(raw_signal_id),link_id)).fetchone():
                    continue
                tid = str(link.get("broker_trade_id") or link.get("trade_id") or link.get("oanda_trade_id") or "")
                bt = broker_by_id.get(tid, {})
                try: metrics = core._metals_xau_live_trade_metrics(link, bt) or {}
                except Exception: metrics = {}
                age = sf(metrics.get("age_hours"), sf(link.get("hold_hours")))
                current_r = sf(metrics.get("current_r"))
                mfe_r = sf(metrics.get("mfe_r"))
                mature = age >= 48.0
                gb = ((mfe_r-current_r)/mfe_r*100.0) if mfe_r > 0 and current_r < mfe_r else 0.0
                mfe50_exit = bool(mature and mfe_r > 0 and current_r <= 0.50*mfe_r)
                actual_policy = str(link.get("active_exit_policy") or link.get("exit_policy") or "MFE_GIVEBACK_25")
                row = {
                    "trade_id": str(link.get("trade_id") or tid), "side":"short", "cohort_rank":rank,
                    "actual_policy":actual_policy, "runner_policy":"MFE_GIVEBACK_50",
                    "age_hours":age, "current_R":current_r, "mfe_R":mfe_r, "giveback_pct":gb,
                    "mature_48h":mature, "mfe50_would_exit_now":mfe50_exit,
                    "execution_authority":False,
                }
                conn.execute("""
                    INSERT INTO metals_xau_runner_cohort_research(
                        created_at_utc,raw_signal_id,link_id,trade_id,side,cohort_rank,actual_policy,runner_policy,
                        age_hours,current_R,mfe_R,giveback_pct,mature_48h,mfe50_would_exit_now,execution_authority,point_in_time_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (now,int(raw_signal_id),link_id,row["trade_id"],"short",rank,actual_policy,"MFE_GIVEBACK_50",
                      age,current_r,mfe_r,gb,1 if mature else 0,1 if mfe50_exit else 0,0,
                      json.dumps({"future_data_included":False,**row},separators=(",",":"),default=str)))

    previous = core.process_signal
    def wrapped(raw_signal_id: int, payload: Dict[str, Any]):
        out = previous(raw_signal_id, payload)
        try:
            pair = str((payload or {}).get("pair") or (payload or {}).get("ticker") or "").upper().replace("_","")
            if "XAU" in pair:
                capture(int(raw_signal_id))
        except Exception as exc:
            try: print(f"METALS_MONETISATION_RESEARCH_ERROR {type(exc).__name__}: {exc}", flush=True)
            except Exception: pass
        return out
    core.process_signal = wrapped

    def research_payload() -> Dict[str, Any]:
        with core.get_conn() as conn:
            ensure(conn)
            econ = rows(conn.execute("SELECT * FROM metals_economic_campaign_research ORDER BY id DESC LIMIT 5000"))
            runner = rows(conn.execute("SELECT * FROM metals_xau_runner_cohort_research ORDER BY id DESC LIMIT 20000"))
        return {"ok":True,"version":VERSION,"execution_authority":False,
                "runner_selection":"every 4th live XAU SHORT trade (25% deterministic cohort)",
                "runner_policy":"MFE_GIVEBACK_50","actual_policy":"MFE_GIVEBACK_25",
                "economic_campaign":econ,"runner_cohort":runner}

    @app.get("/analysis/metals-monetisation-research")
    def status_endpoint() -> Dict[str, Any]:
        return research_payload()

    def csv_text(data: List[Dict[str, Any]]) -> str:
        out = io.StringIO()
        if not data:
            return "note\nNo rows yet\n"
        fields: List[str] = []
        for row in data:
            for key in row:
                if key not in fields: fields.append(key)
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(data)
        return out.getvalue()

    @app.get("/analysis/metals-monetisation-research.zip")
    def export_endpoint() -> Response:
        payload = research_payload()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("economic-campaign-research.csv", csv_text(payload.get("economic_campaign") or []))
            zf.writestr("xau-runner-cohort-research.csv", csv_text(payload.get("runner_cohort") or []))
            zf.writestr("manifest.json", json.dumps({
                "version":VERSION,"execution_authority":False,
                "runner_selection":payload.get("runner_selection"),
                "runner_policy":payload.get("runner_policy"),"actual_policy":payload.get("actual_policy"),
            },separators=(",",":")))
        return Response(content=buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition":'attachment; filename="metals-monetisation-research.zip"'})

    status = {"installed":True,"version":VERSION,"execution_authority":False,
              "economic_campaign_hwm":True,"runner_cohort":"EVERY_4TH_XAU_SHORT_MFE50",
              "export":"/analysis/metals-monetisation-research.zip"}
    core._METALS_MONETISATION_RESEARCH_V1_INSTALLED = True
    core._METALS_MONETISATION_RESEARCH_V1_STATUS = status
    return status
