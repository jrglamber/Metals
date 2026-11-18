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
        zf.writestr("manifest.json",json.dumps(manifest,indent=2,default=str))
        zf.writestr("README.txt",("LIVE Metals analysis, not practice/demo. UTC events bucketed into Europe/London dates.\n"
          "XAU source = broker-synced XAU transaction ledger since its accounting epoch; verify separately before treating as independently complete lifetime history.\n"
          "XAG source = independently verified short-only OANDA cash attribution, including daily financing and fees.\n"
          "Partial/missing XAG cash proof -> BLANK combined amounts, NOT zero.\n"
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