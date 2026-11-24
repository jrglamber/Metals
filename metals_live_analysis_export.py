"""Download LIVE Metals cash evidence, not the old practice/research bundle.

Read-only and broker-scoped. XAU figures are from the local, broker-synced
XAU accounting epoch; XAG SHORT movements are independently attributed to
OANDA and retained privately by its existing cash auditor. Incomplete proof is
shown as INCOMPLETE with blank combined amounts, never zero or fabricated GBP.
"""
from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi.responses import Response

VERSION = "metals_live_analysis_zip_v1_2026_11_18"
SCOPE = "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"
_LONDON = ZoneInfo("Europe/London")
_MAX_ROWS = 100000


def _dt(value):
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if d.tzinfo is None:
            return None
        return d.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _cash(r):
    return round(float(r), 6)


def _csv(zf, name, rows, fields):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f) for f in fields})
    zf.writestr(name, out.getvalue())


def _day(d):
    return d.astimezone(_LONDON).date().isoformat()


def _week(d):
    local = d.astimezone(_LONDON).date()
    return (local - timedelta(days=local.weekday())).isoformat()


def _month(d):
    return d.astimezone(_LONDON).strftime("%Y-%m")


def _periods(xau, xag, xag_ok, fn):
    buckets = defaultdict(lambda: {"xau": 0.0, "xag": 0.0, "xau_events": 0, "xag_events": 0})
    for row in xau:
        p = fn(_dt(row["transaction_time_utc"]))
        buckets[p]["xau"] += row["net_realized_gbp"]
        buckets[p]["xau_events"] += 1
    if xag_ok:
        for row in xag:
            p = fn(_dt(row["transaction_time_utc"]))
            buckets[p]["xag"] += row["net_realized_gbp"]
            buckets[p]["xag_events"] += 1
    result = []
    for period, v in sorted(buckets.items()):
        result.append({
            "period_london": period,
            "xau_long_short_net_gbp": _cash(v["xau"]),
            "xag_short_net_gbp": _cash(v["xag"]) if xag_ok else None,
            "combined_live_net_gbp": _cash(v["xau"] + v["xag"]) if xag_ok else None,
            "xau_cash_events": v["xau_events"],
            "xag_cash_events": v["xag_events"] if xag_ok else None,
            "status": "XAU_EPOCH_PLUS_XAG_BROKER_AUDITED" if xag_ok else "INCOMPLETE_XAG_NOT_VERIFIED",
        })
    return result


def _limited_rows(conn, table, cols, order):
    # Only fixed, source-controlled identifiers; never interpolate user input.
    row = conn.execute("SELECT COUNT(*) AS n FROM " + table).fetchone()
    count = int(dict(row).get("n") if row is not None else 0)
    if count > _MAX_ROWS:
        raise ValueError(table + " exceeds safe full-history export cap; refusing truncated ledger")
    q = "SELECT " + cols + " FROM " + table + " ORDER BY " + order
    return [dict(r) for r in conn.execute(q).fetchall()]


def _link_records(core, table, fields):
    with core.get_conn() as conn:
        items = _limited_rows(conn, table, "*", "id ASC")
    return [{f: row.get(f) for f in fields} for row in items]


_BUDGET_BLOCK_FIELDS = [
    "recorded_at_utc", "raw_signal_id", "asset", "side", "reason",
    "proof_valid", "nav_gbp", "risk_used_gbp", "cap_gbp",
    "new_risk_buffered_gbp", "cap_pct_nav", "open_count",
]


def _budget_blocks(core, lane):
    """Read persisted NAV budget rejects. Never consult/write OANDA or alter orders.

    Both source tables already exist and are populated by the currently installed
    entry gates. Parse only allow-listed numerical proof, never raw audit JSON:
    the full raw JSON can include broker responses or other sensitive material.
    """
    if lane == "XAU":
        table = "metals_demo_execution_audit"
        columns = "created_at_utc,raw_signal_id,message,raw_json"
        where = "action='xau_nav_budget' AND status='BLOCKED' AND message='xau_nav_budget_exhausted'"
        instrument,side = "XAUUSD", "UNKNOWN_MATCH_VIA_SIGNAL"
    elif lane == "XAG":
        table = "metals_xag_live_execution_audit"
        columns = "created_at_utc,raw_signal_id,reason,raw_json"
        where = "action='entry_governance' AND status='BLOCKED' AND reason='xag_nav_risk_budget_exhausted'"
        instrument,side = "XAGUSD", "SHORT"
    else:
        raise ValueError("unexpected_budget_lane")
    with core.get_conn() as conn:
        c = conn.execute("SELECT COUNT(*) AS n FROM " + table + " WHERE " + where).fetchone()
        count = int(dict(c).get("n") or 0) if c else 0
        if count > _MAX_ROWS:
            raise ValueError("budget_reject_export_exceeds_safe_cap")
        items = [dict(row) for row in conn.execute(
            "SELECT " + columns + " FROM " + table + " WHERE " + where
            + " ORDER BY id ASC"
        ).fetchall()]
    out = []
    for item in items:
        raw = {}
        try:
            raw = json.loads(item.get("raw_json") or "{}")
            if not isinstance(raw, dict):
                raw = {}
            if lane == "XAU":
                raw = raw.get("response") or {}
        except (TypeError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        def safe(field):
            return _num(raw.get(field))
        newrisk = safe("new_risk_with_buffer_gbp") if lane == "XAU" else safe("candidate_risk_buffered_gbp")
        used = safe("risk_used_gbp") if lane == "XAU" else safe("current_risk_gbp")
        cap = safe("cap_gbp") if lane == "XAU" else safe("budget_gbp")
        nav = safe("nav_gbp")
        cap_pct = safe("cap_pct_nav")
        open_count = safe("open_xau") if lane == "XAU" else safe("open_xag")
        valid = all(v is not None and math.isfinite(v) for v in (used,cap,nav,newrisk,cap_pct))
        out.append({
            "recorded_at_utc": item.get("created_at_utc"),
            "raw_signal_id": item.get("raw_signal_id"),
            "asset": instrument, "side": side,
            "reason": item.get("message") if lane == "XAU" else item.get("reason"),
            "proof_valid": valid,
            "nav_gbp": nav if valid else None,
            "risk_used_gbp": used if valid else None,
            "cap_gbp": cap if valid else None,
            "new_risk_buffered_gbp": newrisk if valid else None,
            "cap_pct_nav": cap_pct if valid else None,
            "open_count": open_count if valid else None,
        })
    return out


def build_bundle(core, xag_reader=None, now=None):
    now = now or datetime.now(timezone.utc)
    if xag_reader is None:
        from metals_xag_transaction_audit import export_verified_cash_events
        xag_reader = export_verified_cash_events

    xau = []
    invalid_xau = []
    with core.get_conn() as conn:
        source_rows = _limited_rows(
            conn, "metals_xau_live_broker_transactions",
            "transaction_id,transaction_time,transaction_type,instrument,pl_gbp,financing_gbp,net_realized_gbp,synced_at_utc",
            "transaction_time ASC,transaction_id ASC",
        )
        try:
            epoch_row = conn.execute("SELECT value FROM metals_xau_live_runtime_state WHERE key='accounting_epoch_utc'").fetchone()
            epoch = str(dict(epoch_row).get("value")) if epoch_row else None
            cursor_row = conn.execute("SELECT updated_at_utc FROM metals_xau_live_runtime_state WHERE key='broker_transaction_cursor'").fetchone()
            cursor_synced_at = str(dict(cursor_row).get("updated_at_utc")) if cursor_row else None
        except Exception:
            epoch = None
            cursor_synced_at = None
        try:
            combined_hwm = conn.execute(
                "SELECT started_at_utc,high_water_gbp,high_water_at_utc FROM metals_combined_live_pnl_observed_hwm WHERE id=1"
            ).fetchone()
            combined_hwm = dict(combined_hwm) if combined_hwm else None
        except Exception:
            combined_hwm = None
    for r in source_rows:
        d = _dt(r.get("transaction_time"))
        pl, fin, net = (_num(r.get(k)) for k in ("pl_gbp", "financing_gbp", "net_realized_gbp"))
        if (d is None or pl is None or fin is None or net is None
                or abs(pl + fin - net) > 0.02 or str(r.get("instrument")) != "XAU_USD"):
            invalid_xau.append(str(r.get("transaction_id")))
            continue
        xau.append({
            "transaction_id": r.get("transaction_id"),
            "transaction_time_utc": d.isoformat(), "transaction_type": r.get("transaction_type"),
            "instrument": "XAU_USD", "pl_gbp": _cash(pl),
            "financing_gbp": _cash(fin), "net_realized_gbp": _cash(net),
            "synced_at_utc": r.get("synced_at_utc"), "source": "LIVE_XAU_BROKER_SYNC_LEDGER",
        })

    xag = []
    xag_proof = None
    xag_error = None
    try:
        out = xag_reader(core)
        xag_proof = out["proof"]
        for idx, r in enumerate(out["events"], start=1):
            date = _dt(r.get("transaction_time_utc"))
            amount = _num(r.get("net_realized_gbp"))
            if date is None or amount is None:
                raise ValueError("XAG audit cash event malformed")
            xag.append({
                "event_number": idx, "transaction_time_utc": date.isoformat(),
                "transaction_type": r.get("transaction_type"), "instrument": "XAG_USD",
                "net_realized_gbp": _cash(amount), "source": "XAG_SHORT_VERIFIED_OANDA_CASH_AUDIT",
            })
        expected = _num(xag_proof.get("realised_all_time_gbp"))
        if expected is None or abs(sum(z["net_realized_gbp"] for z in xag) - expected) > 0.02:
            raise ValueError("XAG events do not match full-history verified proof")
    except Exception as exc:
        xag_error = type(exc).__name__ + ": " + str(exc)
        xag, xag_proof = [], None

    cursor_dt = _dt(cursor_synced_at)
    cursor_age = (now-cursor_dt).total_seconds() if cursor_dt is not None else None
    xau_fresh = bool(cursor_age is not None and 0 <= cursor_age <= 900)
    xau_good = not invalid_xau and bool(epoch) and _dt(epoch) is not None and xau_fresh
    xag_good = xag_proof is not None
    complete = bool(xau_good and xag_good)
    net_xau = _cash(sum(r["net_realized_gbp"] for r in xau)) if xau_good else None
    net_xag = _cash(sum(r["net_realized_gbp"] for r in xag)) if xag_good else None
    all_total = _cash(net_xau + net_xag) if complete else None
    day_rows = _periods(xau,xag,xag_good,_day)
    week_rows = _periods(xau,xag,xag_good,_week)
    month_rows = _periods(xau,xag,xag_good,_month)
    current_day, current_week, current_month = _day(now), _week(now), _month(now)
    def current_value(records, period):
        row = next((r for r in records if r["period_london"] == period), None)
        return _cash(row["combined_live_net_gbp"] if row else 0) if complete else None

    manifest = {
        "version": VERSION, "generated_at_utc": now.isoformat(),
        "scope": SCOPE, "currency": "GBP", "reporting_timezone": "Europe/London",
        "status": "COMPLETE_FOR_RECORDED_XAU_EPOCH_AND_VERIFIED_XAG_HISTORY" if complete else "INCOMPLETE_DO_NOT_USE_COMBINED_TOTALS",
        "combined_cash_totals_available": complete,
        "broker_execution_authority": False, "live_trading_controls_changed": False,
        "xau": {"scope": "LIVE_XAU_LONG_PLUS_SHORT_AGGREGATE",
                "source": "metals_xau_live_broker_transactions (owned XAU broker transactions only)",
                "accounting_epoch_utc": epoch, "event_count": len(xau),
                "broker_cursor_last_synced_at_utc": cursor_synced_at,
                "broker_cursor_age_seconds": round(cursor_age,1) if cursor_age is not None else None,
                "transaction_sync_fresh_within_15_minutes": xau_fresh,
                "invalid_transaction_ids": invalid_xau[:25], "accounting_epoch_valid": xau_good,
                "all_time_since_epoch_gbp": net_xau,
                "historical_broker_backfill_before_epoch_verified": False,
                "per_side_long_vs_short_cash_attribution_verified": False},
        "xag": {"scope": "LIVE_XAG_SHORT_ONLY", "source": "OANDA broker audited cash events",
                "verified": xag_good, "audit_time_utc": (xag_proof or {}).get("time_utc"),
                "event_count": len(xag), "net_all_time_gbp": net_xag, "error": xag_error},
        "all_time_net_gbp": all_total,
        "this_week_net_gbp": current_value(week_rows,current_week),
        "this_month_net_gbp": current_value(month_rows,current_month),
        "today_net_gbp": current_value(day_rows,current_day),
        "combined_open_basket_hwm": combined_hwm,
        "combined_hwm_note": "Observed open P&L peak only; began on 16 November 2026, not a lifetime economic P&L series",
        "note": "XAU transaction series starts at the persisted live-accounting epoch, not automatically the first traded XAU. XAG is entirely separate from demo transactions. Open P&L is not mixed with realised GBP. Any missing proof leaves combined totals BLANK, never zero.",
    }

    zip_mem = io.BytesIO()
    with zipfile.ZipFile(zip_mem, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        _csv(zf,"live-xau-broker-cash-events.csv",xau,
             ["transaction_id","transaction_time_utc","transaction_type","instrument","pl_gbp","financing_gbp","net_realized_gbp","synced_at_utc","source"])
        _csv(zf,"live-xag-short-broker-cash-events.csv",xag,
             ["event_number","transaction_time_utc","transaction_type","instrument","net_realized_gbp","source"])
        period_fields=["period_london","xau_long_short_net_gbp","xag_short_net_gbp","combined_live_net_gbp","xau_cash_events","xag_cash_events","status"]
        _csv(zf,"live-metals-weekly-realised-gbp.csv",week_rows,period_fields)
        _csv(zf,"live-metals-monthly-realised-gbp.csv",month_rows,period_fields)
        _csv(zf,"live-metals-daily-realised-gbp.csv",day_rows,period_fields)
        link_fields=["broker_trade_id","side","status","opened_at_utc","closed_at_utc","realized_pl","financing","estimated_risk_amount","requested_risk_amount","close_reason"]
        for name,table in [("live-xau-trade-links.csv","metals_xau_live_trade_links"),
                           ("live-xag-short-trade-links.csv","metals_xag_live_trade_links")]:
            try:
                records = _link_records(core,table,link_fields)
                _csv(zf,name,records,link_fields)
            except Exception as exc:
                zf.writestr(name.replace(".csv","-unavailable.json"),json.dumps({"error_type":type(exc).__name__}))
        _csv(zf,"live-xau-hwm-events.csv",
             _optional_records(core,"metals_xau_live_hwm_events"),
             ["created_at_utc","observed_at_utc","event_type","open_count","current_gbp","current_r","high_water_gbp","high_water_r","source"])
        _csv(zf,"live-xau-harvest-events.csv",
             _optional_records(core,"metals_xau_live_harvest_events"),
             ["created_at_utc","cycle_id","threshold_r","status","broker_trade_id","expected_upl_gbp","realized_pl_gbp","reason"])
        # Research-only entry-admission evidence, collected from existing
        # persisted gate audits. Never changes strategies or broker settings.
        budget_meta = {}
        for lane, name in [
            ("XAU", "live-xau-nav-budget-blocks.csv"),
            ("XAG", "live-xag-nav-budget-blocks.csv"),
        ]:
            try:
                blocks = _budget_blocks(core, lane)
                _csv(zf, name, blocks, _BUDGET_BLOCK_FIELDS)
                budget_meta[lane] = {
                    "status": "COMPLETE_FROM_RECORDED_AUDIT",
                    "count": len(blocks), "with_valid_proof": sum(
                        bool(row["proof_valid"]) for row in blocks),
                    "first_recorded_at_utc": blocks[0]["recorded_at_utc"] if blocks else None,
                    "last_recorded_at_utc": blocks[-1]["recorded_at_utc"] if blocks else None,
                }
            except Exception as exc:
                zf.writestr(name.replace(".csv", "-unavailable.json"),
                    json.dumps({"reason_type": type(exc).__name__, "records_not_exported": True}))
                budget_meta[lane] = {"status": "UNAVAILABLE",
                    "reason_type": type(exc).__name__}
        manifest["budget_block_research"] = {
            "scope": "ACTUAL_LIVE_NAV_GATE_REJECTIONS_ONLY",
            "generated_at_utc": now.isoformat(),
            "shadow_only": True, "broker_order_writes": False,
            "admission_or_management_logic_changed": False,
            "xau_side_requires_directional_signal_join": True,
            "lanes": budget_meta,
            "notes": "Historical rejects are only as complete as persisted audit records. "
                     "Rejected trades have no broker P&L. Join by raw_signal_id and "
                     "asset/direction to prospective research outcomes; no actual "
                     "counterfactual execution is claimed.",
        }
        # Forward-only research recorder: timestamped economic-equity path and
        # all underlying LIVE broker marks, independent of demo history.
        # A failed/empty capture is explicitly disclosed, never silently
        # replaced by reconstructed or fabricated historical values.
        forward_fields=[
            "sampled_at_utc","source_generated_at_utc","status","reason",
            "xau_open_count","xag_open_count","total_open_count",
            "xag_campaign_epoch_utc","xag_campaign_entries","xag_open_r",
            "xag_banked_r_proxy","xag_funding_r_proxy","xag_funding_status",
            "xau_verified_since_epoch_gbp","xag_verified_lifetime_gbp",
            "combined_realised_since_epochs_gbp","combined_open_pnl_gbp",
            "family_campaign_id","family_bootstrap_partial",
            "family_baseline_realised_gbp","realised_since_family_baseline_gbp",
            "economic_equity_gbp","economic_high_water_gbp",
            "economic_giveback_gbp","mark_to_market_complete",
            "broker_population_matched","cash_broker_verified",
            "position_marks_json","execution_authority"
        ]
        try:
            from metals_forward_economic_capture import export_rows,VERSION as forward_version
            forward = export_rows(core)
            _csv(zf,"live-metals-forward-economic-equity.csv",forward,forward_fields)
            manifest["forward_capture"]={
                "version":forward_version,
                "scope":"LIVE_METALS_FORWARD_SINCE_CAPTURE_DEPLOYMENT",
                "status":"ACTIVE_WITH_COMPLETE_SAMPLES" if any(
                    r.get("status")=="COMPLETE" for r in forward
                ) else "NO_COMPLETE_SAMPLES_YET",
                "sample_count":len(forward),
                "complete_count":sum(r.get("status")=="COMPLETE" for r in forward),
                "first_sample_utc":forward[0].get("sampled_at_utc") if forward else None,
                "last_sample_utc":forward[-1].get("sampled_at_utc") if forward else None,
                "captures_broker_marks_as_json":True,
                "xag_banked_r_is_proxy_excluding_unattributed_financing":True,
                "prior_history_backfilled":False,
                "execution_authority":False,
            }
        except Exception as exc:
            zf.writestr("live-metals-forward-economic-equity-unavailable.json",
                json.dumps({"status":"UNAVAILABLE","reason_type":type(exc).__name__,
                    "prior_history_backfilled":False,"execution_authority":False}))
            manifest["forward_capture"]={
                "status":"UNAVAILABLE","reason_type":type(exc).__name__,
                "prior_history_backfilled":False,"execution_authority":False}
        # Forward-only basket-full early MFE25 shadow, never broker fills.
        try:
            from metals_basket_full_early_exit_shadow import (
                export_rows as _basket_shadow_export, VERSION as _basket_shadow_version)
            shadow = _basket_shadow_export(core)
            shadow_sample_fields = [
                "id","sampled_at_utc","asset","status","reason",
                "broker_risk_used_gbp","broker_risk_cap_gbp","nav_gbp",
                "broker_open_count","budget_full","young_short_count",
                "legacy_young_excluded","execution_authority"]
            shadow_trade_fields = [
                "asset","broker_trade_id","policy","first_seen_utc",
                "opened_at_utc","last_observed_utc","observed_mfe_r",
                "armed_at_utc","armed_mfe_r","early_exit_at_utc",
                "early_exit_observed_r","early_exit_mark_gbp",
                "actual_broker_close_at_utc","actual_gross_realised_gbp",
                "last_status","notes","execution_authority"]
            _csv(zf,"live-metals-basket-full-shadow-budget.csv",
                 shadow["budget"],shadow_sample_fields)
            _csv(zf,"live-metals-basket-full-shadow-trades.csv",
                 shadow["trades"],shadow_trade_fields)
            manifest["basket_full_early_exit_shadow"] = {
                "version":_basket_shadow_version,
                "status":"COLLECTING" if shadow["budget"] else "NO_FORWARD_SAMPLES_YET",
                "sample_count":len(shadow["budget"]),
                "trade_policy_rows":len(shadow["trades"]),
                "scope":"XAU_SHORT_AND_XAG_SHORT;XAU_LONG_ATR2_NOT_MODELLED",
                "hypothetical_policies":["ANY_POSITIVE","MFE_1R",
                                         "MFE_1R_12H","MFE_2R"],
                "requires_actual_broker_budget":True,
                "uses_only_existing_confirmed_live_positions":True,
                "not_full_path_or_broker_fill_backtest":True,
                "no_backfill_pre_deployment":True,
                "execution_authority":False,"broker_order_writes":False,
                "live_entry_stop_exit_unchanged":True,
            }
        except Exception as exc:
            zf.writestr("live-metals-basket-full-shadow-unavailable.json",
                json.dumps({"status":"UNAVAILABLE",
                            "error_type":type(exc).__name__,
                            "execution_authority":False}))
            manifest["basket_full_early_exit_shadow"] = {
                "status":"UNAVAILABLE","error_type":type(exc).__name__,
                "execution_authority":False}
        zf.writestr("manifest.json",json.dumps(manifest,indent=2,default=str))
        zf.writestr("README.txt",("LIVE Metals analysis, not practice/demo. UTC events bucketed into Europe/London dates.\n"
          "XAU source = broker-synced XAU transaction ledger since its accounting epoch; verify separately before treating as independently complete lifetime history.\n"
          "XAG source = independently verified short-only OANDA cash attribution, including daily financing and fees.\n"
          "Partial/missing XAG cash proof -> BLANK combined amounts, NOT zero.\n"
          "Forward economic-equity samples begin only when the research recorder is installed; no backfilled live portfolio history.\n"
          "XAG campaign funding R is explicitly a trade-link proxy, which excludes unallocated broker financing.\n"
          "Position marks for future counterfactual replays are JSON within the forward-equity CSV.\n"
          "No live order placement, exits, stake sizing, or other trading changes.\n"))
    return zip_mem.getvalue(), manifest


def _optional_records(core, table):
    with core.get_conn() as conn:
        return _limited_rows(conn,table,"*","id ASC")


def install(app, core, prioritize):
    """Late read-only route must precede the pre-existing mounted legacy app."""
    def live_export():
        payload, _ = build_bundle(core)
        return Response(payload,media_type="application/zip",headers={
            "Content-Disposition":'attachment; filename="metals-live-analysis.zip"',
            "Cache-Control":"no-store"})
    app.add_api_route("/export/metals-live-analysis.zip",live_export,methods=["GET"],include_in_schema=False)
    status=prioritize(app,"/export/metals-live-analysis.zip",live_export)
    return {"installed":True,"version":VERSION,"route":status,"scope":SCOPE,
            "execution_authority":False,"replaces_old_demo_analysis_button":True}