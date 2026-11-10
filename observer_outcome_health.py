"""Read-only observer capture health and forward market markouts."""
import json
from datetime import datetime, timezone

def _date(value):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except (ValueError,TypeError): return None

def audit(core, table):
    now=datetime.now(timezone.utc)
    with core.get_conn() as conn:
        rows=[dict(x) for x in conn.execute(
            "SELECT * FROM "+table+" ORDER BY id DESC LIMIT 3000").fetchall()]
    latest=rows[0] if rows else None
    complete=next((r for r in rows if str(r.get("status")).upper()=="COMPLETE"),None)
    eligible=next((r for r in rows if int(r.get("api_eligible") or 0)),None)
    def age(r,field):
        dt=_date(r.get(field)) if r else None
        return round((now-dt).total_seconds()/3600,2) if dt else None
    capture_age=age(latest,"created_at_utc")
    complete_age=age(complete,"completed_at_utc")
    eligible_age=age(eligible,"created_at_utc")
    recent=rows[:100]
    errors=[r for r in recent if str(r.get("status")).upper() in ("ERROR","RETRY_WAIT")]
    pending=[r for r in rows if int(r.get("api_eligible") or 0)
             and str(r.get("status")).upper() not in ("COMPLETE","ERROR")
             and (age(r,"created_at_utc") or 0)>3]
    alerts=[]
    if capture_age is None or capture_age>4: alerts.append("CAPTURE_STALE")
    if pending: alerts.append("ELIGIBLE_CALL_OVERDUE")
    if errors: alerts.append("RECENT_AI_ERRORS")
    if eligible and (eligible_age or 999)<24 and (complete_age is None or complete_age>24):
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
            "overdue_eligible_count":len(pending),"captured_rows":len(rows),
            "forward_24h":{"metric":"market_price_not_broker_trade_pnl",
                           "settled_count":len(outcomes),"recent":outcomes[-50:]}}
