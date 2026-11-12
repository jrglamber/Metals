"""Read-only authoritative XAG Short live result next to legacy practice guard.

The existing practice XAG confirmation guard can say PENDING_FAIL_CLOSED
even when independent live XAG Short executed successfully. Display BOTH
states clearly; never upgrade a research candidate or place broker orders.
"""
from __future__ import annotations

from html import escape as _esc
from typing import Any, Dict, List

VERSION = "xag_live_latest_signal_explanatory_panel_v1_2026_11_12"


def rows(core: Any, limit: int = 12) -> List[Dict[str, Any]]:
    with core.get_conn() as conn:
        signals = [dict(r) for r in conn.execute(
            "SELECT id, timestamp_readable, received_at_utc FROM raw_signals "
            "WHERE UPPER(pair) IN ('XAGUSD','XAG') ORDER BY id DESC LIMIT ?",
            (min(max(1, int(limit)), 30),),
        ).fetchall()]
        result: List[Dict[str, Any]] = []
        for sig in signals:
            rid = int(sig["id"])
            linked = conn.execute(
                "SELECT status, side, broker_trade_id FROM metals_xag_live_trade_links "
                "WHERE raw_signal_id=? ORDER BY id DESC LIMIT 1", (rid,),
            ).fetchone()
            audit = conn.execute(
                "SELECT action, status, reason, created_at_utc "
                "FROM metals_xag_live_execution_audit WHERE raw_signal_id=? "
                "ORDER BY id DESC LIMIT 1", (rid,),
            ).fetchone()
            l = dict(linked) if linked else {}
            a = dict(audit) if audit else {}
            if l.get("broker_trade_id") and str(l.get("status") or "").upper() in {
                "OPEN","OPEN_UNVERIFIED_STOP","CLOSE_RETRY"
            }:
                state = "LIVE TRACKED"
                reason = "Broker trade linked: " + str(l["broker_trade_id"])
            elif l.get("broker_trade_id"):
                state = "LIVE " + str(l.get("status") or "RECORDED").upper()
                reason = "Broker trade ID: " + str(l["broker_trade_id"])
            elif a.get("status"):
                state = "LIVE " + str(a.get("status")).upper()
                reason = str(a.get("reason") or "")
            else:
                state = "NO LIVE EXECUTION AUDIT"
                reason = "No live order recorded for this signal; not evidence of eligibility"
            result.append({
                "signal_id":rid,"candle":sig.get("timestamp_readable") or "",
                "state":state,"reason":reason,"audit_time":a.get("created_at_utc") or "",
            })
    return result


def render(core: Any, limit: int = 12) -> str:
    try:
        data = rows(core, limit=limit)
    except Exception as exc:
        return ('<div class="section-note warn" id="pep-xag-live-signal-status">'
                'Live XAG Short execution audit unavailable: '+
                _esc(type(exc).__name__)+'. Legacy practice guard is NOT proof of live execution.</div>')
    table = []
    for r in data:
        state = str(r["state"])
        state_class = "pos" if state == "LIVE TRACKED" else "warn" if "BLOCKED" in state or "FAILED" in state else "flat"
        table.append("<tr><td>"+_esc(str(r["signal_id"]))+"</td><td>"+
                     _esc(str(r["candle"]))+"</td><td class=\""+state_class+"\"><strong>"+
                     _esc(state)+"</strong></td><td>"+_esc(str(r["reason"]))+"</td></tr>")
    return (
        '<div id="pep-xag-live-signal-status" class="section-note">'
        '<strong>Actual LIVE XAG Short execution (authoritative broker-linked record)</strong> '
        '— distinct from the legacy XAG practice guard below. '
        'An XAG practice guard can display PENDING_FAIL_CLOSED when an independently '
        'governed LIVE XAG Short has already opened successfully. '
        'Use the live execution audit and broker-linked trade, not the practice '
        'Production State column, to determine whether a live short opened.'
        '</div><div class="table-scroll"><table><thead><tr>'
        '<th>Signal ID</th><th>Candle</th><th>LIVE XAG Short</th><th>Execution evidence</th>'
        '</tr></thead><tbody>'+("".join(table) or
        '<tr><td colspan="4">No XAG signals</td></tr>')+'</tbody></table></div>'
    )
