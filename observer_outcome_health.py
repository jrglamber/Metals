"""Read-only observer capture health and forward market markouts."""
import json
from datetime import datetime, timedelta, timezone

def _date(value):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (ValueError,TypeError): return None


def _eligible(row):
    try:
        return int(row.get("api_eligible") or 0) != 0
    except (ValueError, TypeError):
        return False


def _unresolved_recent_errors(rows, now):
    """A prior failure is not a current incident after same-asset recovery.

    RETRY_WAIT is a retry, not a final failure; the existing overdue check
    handles retries stuck for more than three hours. Old errors remain counted
    in history but do not trigger the RECENT_AI_ERRORS alarm.
    """
    completed_by_asset = {}
    for row in rows:
        if str(row.get("status") or "").upper() != "COMPLETE":
            continue
        asset = str(row.get("asset") or "UNKNOWN")
        when = (_date(row.get("completed_at_utc")) or
                _date(row.get("updated_at_utc")) or
                _date(row.get("created_at_utc")))
        if when and (asset not in completed_by_asset or when > completed_by_asset[asset]):
            completed_by_asset[asset] = when

    active = []
    cutoff = now - timedelta(hours=24)
    for row in rows[:100]:
        if str(row.get("status") or "").upper() != "ERROR" or not _eligible(row):
            continue
        when = (_date(row.get("updated_at_utc")) or
                _date(row.get("completed_at_utc")) or
                _date(row.get("created_at_utc")))
        if not when or when < cutoff:
            continue
        asset = str(row.get("asset") or "UNKNOWN")
        recovered = completed_by_asset.get(asset)
        if recovered and recovered >= when:
            continue
        active.append({
            "asset": asset,
            "failed_at_utc": when.isoformat(),
            "http_status": row.get("last_http_status"),
            "error_class": str(row.get("error") or "").split(":", 1)[0][:64],
        })
    return active

def _safe_error_kind(row):
    """Classify stored provider errors without exposing raw error bodies or secrets."""
    value = str(row.get("error") or "").lower()
    http = str(row.get("last_http_status") or "")
    if "insufficient_quota" in value or "exceeded your current quota" in value or "billing_hard_limit" in value:
        return "OPENAI_BILLING_OR_QUOTA_EXHAUSTED"
    if "rate_limit_exceeded" in value or "rate limit" in value or "tokens per min" in value:
        return "OPENAI_RATE_LIMIT"
    if http == "429" or "429" in value:
        return "OPENAI_HTTP_429_UNCLASSIFIED"
    if "jsondecodeerror" in value or "missing required fields" in value:
        return "MODEL_JSON_PARSE_FAILURE"
    if http in ("401", "403"):
        return "AUTHORIZATION_REJECTED"
    if http in ("500", "502", "503", "504"):
        return "UPSTREAM_SERVER_ERROR"
    return "OTHER_ERROR" if value or http else "NO_ERROR_DETAIL"


def _safe_pipeline_diagnostics(core, rows, now):
    from collections import Counter
    statuses = Counter(str(r.get("status") or "UNKNOWN").upper() for r in rows)
    outstanding = [r for r in rows if _eligible(r)
                   and str(r.get("status") or "").upper() not in ("COMPLETE", "ERROR")]
    by_status = Counter(str(r.get("status") or "UNKNOWN").upper() for r in outstanding)
    faults = [r for r in rows[:100] if _eligible(r) and
              str(r.get("status") or "").upper() in ("ERROR", "RETRY_WAIT")]
    kinds = Counter(_safe_error_kind(r) for r in faults)
    thread = getattr(core, "_ai_regime_worker_thread", None)
    return {
        "status_counts_last_500": dict(statuses),
        "outstanding_eligible_by_status": dict(by_status),
        "recent_error_categories": dict(kinds),
        "recent_eligible_records": [
            {"status": str(r.get("status") or ""),
             "age_hours": (round((now - _date(r.get("created_at_utc"))).total_seconds()/3600, 2)
                           if _date(r.get("created_at_utc")) else None),
             "attempt_count": r.get("attempt_count"),
             "http_status": r.get("last_http_status"),
             "error_category": _safe_error_kind(r)}
            for r in rows if _eligible(r)
        ][:8],
        "runtime_enabled": bool(getattr(core, "AI_SHADOW_ENABLED", False)),
        "runtime_key_present": bool(getattr(core, "AI_SHADOW_OPENAI_API_KEY", "")),
        "observer_worker_thread_present": thread is not None,
        "observer_worker_thread_alive": bool(thread and thread.is_alive()),
    }


def audit(core, table):
    now=datetime.now(timezone.utc)
    try:
        with core.get_conn() as conn:
            rows=[dict(x) for x in conn.execute(
                "SELECT * FROM "+table+" ORDER BY id DESC LIMIT 500").fetchall()]
    except Exception as exc:
        return {"ok":False,"research_only":True,
                "alerts":["OBSERVER_AUDIT_QUERY_FAILED"],
                "reason":type(exc).__name__+": "+str(exc)[:200],
                "table":table}
    latest=rows[0] if rows else None
    complete=next((r for r in rows if str(r.get("status")).upper()=="COMPLETE"),None)
    eligible=next((r for r in rows if _eligible(r)),None)
    def age(r,field):
        dt=_date(r.get(field)) if r else None
        return round((now-dt).total_seconds()/3600,2) if dt else None
    capture_age=age(latest,"created_at_utc")
    complete_age=age(complete,"completed_at_utc")
    eligible_age=age(eligible,"created_at_utc")
    recent=rows[:100]
    errors=[r for r in recent if str(r.get("status")).upper() in ("ERROR","RETRY_WAIT")]
    active_errors = _unresolved_recent_errors(rows, now)
    pending=[r for r in rows if _eligible(r)
             and str(r.get("status")).upper() not in ("COMPLETE","ERROR")
             and (age(r,"created_at_utc") or 0)>3]
    alerts=[]
    if capture_age is None or capture_age>4: alerts.append("CAPTURE_STALE")
    if pending: alerts.append("ELIGIBLE_CALL_OVERDUE")
    if active_errors: alerts.append("RECENT_AI_ERRORS")
    if eligible and eligible_age is not None and eligible_age<24 and (complete_age is None or complete_age>24):
        alerts.append("NO_COMPLETED_AI_CALL_DESPITE_RECENT_ELIGIBLE_EVENTS")
    # Market markout only; do not mistake price direction for realised bot P&L.
    prices=[]
    for r in reversed(rows):
        try:
            snap=json.loads(r.get("snapshot_json") or "{}")
            event=snap.get("event_state") or {}
            model=snap.get("model_input") or {}
            signal=model.get("current_signal") or {}
            price=event.get("incoming_price") or signal.get("exec_close") or signal.get("close")
            dt=_date(r.get("created_at_utc"))
            if dt and price is not None and float(price)>0:
                prices.append((dt,float(price),r))
        except (ValueError,TypeError,AttributeError): pass
    outcomes=[]
    for i,(dt,price,r) in enumerate(prices):
        if str(r.get("status")).upper()!="COMPLETE":continue
        future=next((p for t,p,_ in prices[i+1:] if (t-dt).total_seconds()>=86400),None)
        if future is None:continue
        view=str(r.get("long_view") or r.get("entry_view") or "").upper()
        outcomes.append({"raw_signal_id":r.get("raw_signal_id"),"asset":r.get("asset"),
                         "bot_candidate":bool(r.get("live_candidate")),
                         "ai_long_view":view,"market_24h_pct":round(100*(future/price-1),4)})
    return {"ok":not alerts,"research_only":True,"alerts":alerts,
            "capture_age_hours":capture_age,"latest_complete_age_hours":complete_age,
            "latest_eligible_age_hours":eligible_age,"error_count_recent_100":len(errors),
            "active_ai_error_count":len(active_errors),
            "active_ai_errors":active_errors[:5],
            "overdue_eligible_count":len(pending),"captured_rows":len(rows),
            "pipeline_diagnostics":_safe_pipeline_diagnostics(core, rows, now),
            "forward_24h":{"metric":"market_price_not_broker_trade_pnl",
                           "settled_count":len(outcomes),"recent":outcomes[-50:]}}
