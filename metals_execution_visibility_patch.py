"""Read-only final execution visibility for recent XAU signals.

The Metals dashboard's historical "Production Accepted" column describes the
candidate/peer-guard state before live-XAU adaptive stacking.  With the approved
post-32 broker-safe half-frequency policy, an otherwise valid candidate can be
intentionally skipped after that point.  This patch makes that distinction
explicit without changing any trading, sizing, entry, exit, harvest or risk
logic.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List

import metals_adaptive_minimum_size_override as overflow

VERSION = "metals_xau_execution_visibility_v1_2026_10_14"


def _d(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _overflow_state_summary(core: Any) -> Dict[str, Any]:
    try:
        state = dict(overflow._load_state(core) or {})
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    decisions = dict(state.get("decisions") or {})
    recent: List[Dict[str, Any]] = []
    for rid, raw in decisions.items():
        item = _d(raw)
        try:
            opportunity = int(item.get("opportunity") or 0)
        except Exception:
            opportunity = 0
        recent.append({
            "raw_signal_id": rid,
            "opportunity": opportunity,
            "allow": bool(item.get("allow")),
        })
    recent.sort(key=lambda x: (int(x.get("opportunity") or 0), str(x.get("raw_signal_id") or "")))
    return {
        "ok": True,
        "campaign_key": _s(state.get("campaign_key")),
        "counter": int(state.get("counter") or 0),
        "recent_decisions": recent[-12:],
    }


def _audit_text(row: Dict[str, Any]) -> str:
    message = _s(row.get("message"))
    raw_text = _s(row.get("raw_json"))
    if message:
        return message
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                for key in ("reason", "message", "error"):
                    if _s(parsed.get(key)):
                        return _s(parsed.get(key))
        except Exception:
            pass
    return ""


def _recent_xau_outcomes(core: Any, limit: int = 10) -> List[Dict[str, Any]]:
    core.init_db()
    out: List[Dict[str, Any]] = []
    with core.get_conn() as conn:
        signals = [
            _d(r)
            for r in conn.execute(
                """
                SELECT id, timestamp_readable, received_at_utc, signal_id
                FROM raw_signals
                WHERE UPPER(pair) IN ('XAUUSD','XAU')
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 30)),),
            ).fetchall()
        ]
        for sig in signals:
            rid = int(sig.get("id") or 0)
            link_row = conn.execute(
                """
                SELECT id, broker_trade_id, side, status, created_at_utc
                FROM metals_xau_live_trade_links
                WHERE raw_signal_id=?
                ORDER BY id DESC LIMIT 1
                """,
                (rid,),
            ).fetchone()
            audit_row = conn.execute(
                """
                SELECT id, created_at_utc, action, status, message, raw_json
                FROM metals_demo_execution_audit
                WHERE raw_signal_id=? AND UPPER(COALESCE(asset,''))='XAUUSD'
                ORDER BY id DESC LIMIT 1
                """,
                (rid,),
            ).fetchone()
            link = _d(link_row)
            audit = _d(audit_row)
            audit_text = _audit_text(audit)
            combined = (audit_text + " " + _s(audit.get("raw_json"))).lower()
            if link:
                state = "OPENED"
                reason = "broker trade linked"
            elif "xau_overflow_half_frequency_skip" in combined:
                state = "INTENTIONAL_HALF_FREQUENCY_SKIP"
                reason = "post-32 broker-safe alternating overflow policy"
            elif "xau_overflow_half_frequency_state_unavailable" in combined:
                state = "BLOCKED_STATE_UNAVAILABLE"
                reason = "overflow throttle state unavailable"
            elif audit:
                state = _s(audit.get("status")) or "NOT_OPENED"
                reason = audit_text or _s(audit.get("action")) or "latest execution audit"
            else:
                state = "NO_EXECUTION_RECORD"
                reason = "no live link or execution audit found"
            out.append({
                "raw_signal_id": rid,
                "candle": _s(sig.get("timestamp_readable")),
                "received_at_utc": _s(sig.get("received_at_utc")),
                "signal_id": _s(sig.get("signal_id")),
                "execution_state": state,
                "reason": reason,
                "broker_trade_id": _s(link.get("broker_trade_id")),
                "side": _s(link.get("side")),
                "audit_action": _s(audit.get("action")),
                "audit_status": _s(audit.get("status")),
                "audit_message": audit_text,
            })
    return out


def _outcomes_html(core: Any) -> str:
    try:
        rows = _recent_xau_outcomes(core, 12)
        state = _overflow_state_summary(core)
    except Exception as exc:
        return (
            '<div class="section-note small">Live XAU execution outcome unavailable: '
            + html.escape(f"{type(exc).__name__}: {exc}")
            + '</div>'
        )
    body: List[str] = []
    for row in rows:
        status = _s(row.get("execution_state"))
        cls = "pos" if status == "OPENED" else "warn" if status == "INTENTIONAL_HALF_FREQUENCY_SKIP" else "neg"
        body.append(
            "<tr>"
            f"<td>{html.escape(_s(row.get('candle')))}</td>"
            f"<td>{html.escape(_s(row.get('raw_signal_id')))}</td>"
            f"<td class=\"{cls}\"><strong>{html.escape(status)}</strong></td>"
            f"<td>{html.escape(_s(row.get('reason')))}</td>"
            f"<td>{html.escape(_s(row.get('broker_trade_id')) or '—')}</td>"
            "</tr>"
        )
    state_note = ""
    if state.get("ok"):
        state_note = (
            f"Current alternating-overflow state: campaign {html.escape(_s(state.get('campaign_key')) or '—')} · "
            f"qualified post-32 opportunities counted {int(state.get('counter') or 0)}."
        )
    return (
        '<details><summary>Live XAU Execution Outcomes</summary>'
        '<div class="research-inner-body">'
        '<div class="section-note small"><strong>Final execution state.</strong> Production Eligible means the signal passed candidate/peer-guard classification; it is not a promise of a broker order. '
        'After campaign entry #32, the approved broker-safe 0.5× average-exposure policy executes every other otherwise-qualified XAU opportunity while the broker minimum prevents a true half-size order. '
        + state_note
        + '</div>'
        '<div class="table-scroll"><table><thead><tr><th>Candle</th><th>Raw ID</th><th>Final Execution</th><th>Reason</th><th>Broker Trade</th></tr></thead><tbody>'
        + ''.join(body)
        + '</tbody></table></div></div></details>'
    )


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_EXECUTION_VISIBILITY_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_EXECUTION_VISIBILITY_STATUS", {}))

    original = getattr(core, "_metals_latest_30_signals_html", None)
    if not callable(original):
        raise RuntimeError("Execution visibility install refused: latest-signals renderer missing")

    def patched_latest_signals(limit: int = 30) -> str:
        rendered = str(original(limit) or "")
        rendered = rendered.replace("Production Accepted", "Production Eligible")
        old_note = (
            "Model Candidate is the deterministic signal before the XAG peer guard. "
            "Production Eligible is the only candidate state allowed to count toward execution/support. "
            "Guard-blocked XAG rows remain visible for research but count as FALSE for production."
        )
        new_note = (
            "Model Candidate is the deterministic signal before the XAG peer guard. "
            "Production Eligible means the candidate is permitted into execution routing; it does not guarantee a broker order. "
            "Live XAU can still be intentionally throttled by adaptive stacking, including the post-32 alternating overflow rule. "
            "Guard-blocked XAG rows remain visible for research but count as FALSE for production."
        )
        rendered = rendered.replace(old_note, new_note)
        return rendered + _outcomes_html(core)

    core._metals_latest_30_signals_html = patched_latest_signals
    core.METALS_XAU_EXECUTION_VISIBILITY_VERSION = VERSION
    core._METALS_XAU_EXECUTION_VISIBILITY_INSTALLED = True

    def status() -> Dict[str, Any]:
        return {
            "installed": True,
            "version": VERSION,
            "read_only": True,
            "strategy_changed": False,
            "overflow_state": _overflow_state_summary(core),
            "recent_xau_outcomes": _recent_xau_outcomes(core, 12),
        }

    try:
        app.add_api_route("/analysis/live-xau-execution-outcomes", status, methods=["GET"])
    except Exception:
        pass

    initial = status()
    core._METALS_XAU_EXECUTION_VISIBILITY_STATUS = initial
    core.metals_xau_execution_visibility_status = status
    return initial
