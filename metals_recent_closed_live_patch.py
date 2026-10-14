"""Metals dashboard reconciliation patch.

Presentation/read-only diagnostics only:
1. Include LIVE XAU closed broker-linked trades in Recently Closed Metals Trades.
2. Expose a current-classifier probe for the latest XAU raw signals so stale
   historical audit wording cannot be mistaken for the current candidate reason.

No trading, entry, exit, sizing, harvest, stacking or risk logic is changed.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

VERSION = "metals_recent_closed_live_v1_2026_10_14"


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


def _f(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _fmt_money(value: Any) -> str:
    v = _f(value)
    return "—" if v is None else f"£{v:,.2f}"


def _fmt_price(value: Any) -> str:
    v = _f(value)
    return "—" if v is None else f"{v:,.3f}"


def _recent_live_closed(core: Any, limit: int = 30) -> List[Dict[str, Any]]:
    core.init_db()
    lim = max(1, min(int(limit or 30), 100))
    with core.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, raw_signal_id, signal_id, candidate_reason, side, status,
                   broker_trade_id, linked_txn_id, units, entry_price,
                   risk_amount, risk_distance, risk_pct_nav,
                   created_at_utc, updated_at_utc, closed_at_utc,
                   closed_price, realized_pl, error
            FROM metals_xau_live_trade_links
            WHERE UPPER(COALESCE(status,'')) <> 'OPEN'
              AND (
                    closed_at_utc IS NOT NULL
                 OR closed_price IS NOT NULL
                 OR realized_pl IS NOT NULL
                 OR UPPER(COALESCE(status,'')) LIKE 'CLOSED%'
              )
            ORDER BY COALESCE(closed_at_utc, updated_at_utc, created_at_utc) DESC, id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = _d(row)
        risk = _f(d.get("risk_amount"))
        pl = _f(d.get("realized_pl"))
        approx_r = (pl / risk) if (pl is not None and risk not in (None, 0.0)) else None
        d["approx_r"] = approx_r
        out.append(d)
    return out


def _payload(core: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        fn = getattr(core, "_raw_signal_json", None)
        if callable(fn):
            val = fn(row)
            if isinstance(val, dict):
                return val
    except Exception:
        pass
    raw = row.get("raw_json")
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(str(raw or "{}"))
        return val if isinstance(val, dict) else {}
    except Exception:
        return {}


def _candidate_probe(core: Any, limit: int = 8) -> List[Dict[str, Any]]:
    core.init_db()
    lim = max(1, min(int(limit or 8), 30))
    with core.get_conn() as conn:
        rows = [
            _d(r)
            for r in conn.execute(
                """
                SELECT * FROM raw_signals
                WHERE UPPER(pair) IN ('XAUUSD','XAU')
                ORDER BY id DESC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        ]
    out: List[Dict[str, Any]] = []
    candidate_fn = getattr(core, "metals_demo_candidate_for_row", None)
    for row in rows:
        p = _payload(core, row)
        current: Dict[str, Any] = {}
        if callable(candidate_fn):
            try:
                val = candidate_fn(p, row)
                if isinstance(val, dict):
                    current = dict(val)
            except Exception as exc:
                current = {"probe_error": f"{type(exc).__name__}: {exc}"}
        out.append({
            "id": row.get("id"),
            "timestamp_readable": row.get("timestamp_readable"),
            "received_at_utc": row.get("received_at_utc"),
            "signal_id": row.get("signal_id"),
            "signal_quality": p.get("signal_quality"),
            "signal_side": p.get("signal_side"),
            "root_candidate_side": p.get("root_candidate_side"),
            "metal_short_candidate": p.get("metal_short_candidate"),
            "metal_long_candidate": p.get("metal_long_candidate"),
            "forward_test_candidate": p.get("forward_test_candidate"),
            "forward_rule_name": p.get("forward_rule_name"),
            "current_candidate": current,
        })
    return out


def _live_closed_html(core: Any, limit: int = 30) -> str:
    rows = _recent_live_closed(core, limit)
    if not rows:
        return '<div class="section-note small">No recently closed LIVE XAU trades recorded.</div>'
    body: List[str] = []
    for r in rows:
        pl = _f(r.get("realized_pl"))
        r_mult = _f(r.get("approx_r"))
        pl_cls = "pos" if (pl or 0.0) > 0 else "neg" if (pl or 0.0) < 0 else "flat"
        reason = _s(r.get("error")) or _s(r.get("status")) or "closed"
        body.append(
            "<tr>"
            f"<td>{html.escape(_s(r.get('closed_at_utc')) or '—')}</td>"
            f"<td><strong>{html.escape((_s(r.get('side')) or '—').upper())}</strong></td>"
            f"<td>{html.escape(_fmt_price(r.get('entry_price')))}</td>"
            f"<td>{html.escape(_fmt_price(r.get('closed_price')))}</td>"
            f"<td>{html.escape(_s(r.get('units')) or '—')}</td>"
            f"<td class=\"{pl_cls}\"><strong>{html.escape(_fmt_money(r.get('realized_pl')))}</strong></td>"
            f"<td>{'—' if r_mult is None else f'{r_mult:+.2f}R'}</td>"
            f"<td>{html.escape(reason)}</td>"
            f"<td>{html.escape(_s(r.get('broker_trade_id')) or '—')}</td>"
            "</tr>"
        )
    return (
        '<div class="section-note small"><strong>LIVE XAU recently closed.</strong> '
        'These rows come from the live XAU broker-link ledger and were previously omitted from this dashboard section.</div>'
        '<div class="table-scroll"><table><thead><tr>'
        '<th>Closed UTC</th><th>Side</th><th>Entry</th><th>Exit</th><th>Units</th>'
        '<th>Realised</th><th>Approx R</th><th>Status / Reason</th><th>Broker Trade</th>'
        '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
    )


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_RECENT_CLOSED_LIVE_PATCH_INSTALLED", False):
        return dict(getattr(core, "_METALS_RECENT_CLOSED_LIVE_PATCH_STATUS", {}))

    original = getattr(core, "_metals_recently_closed_trades_html", None)
    if not callable(original):
        raise RuntimeError("Recent-closed live patch refused: renderer missing")

    def patched(limit: int = 30) -> str:
        try:
            live = _live_closed_html(core, limit)
        except Exception as exc:
            live = (
                '<div class="section-note small">LIVE XAU recently closed view unavailable: '
                + html.escape(f"{type(exc).__name__}: {exc}")
                + '</div>'
            )
        legacy = str(original(limit) or "")
        if legacy:
            legacy = (
                '<details><summary>DEMO / RESEARCH recently closed</summary>'
                '<div class="research-inner-body">' + legacy + '</div></details>'
            )
        return live + legacy

    core._metals_recently_closed_trades_html = patched
    core.METALS_RECENT_CLOSED_LIVE_PATCH_VERSION = VERSION
    core._METALS_RECENT_CLOSED_LIVE_PATCH_INSTALLED = True

    def status() -> Dict[str, Any]:
        return {
            "installed": True,
            "version": VERSION,
            "presentation_only": True,
            "strategy_changed": False,
            "recent_live_closed": _recent_live_closed(core, 12),
            "recent_xau_candidate_probe": _candidate_probe(core, 8),
        }

    try:
        app.add_api_route("/analysis/metals-dashboard-reconciliation", status, methods=["GET"])
    except Exception:
        pass

    initial = status()
    core._METALS_RECENT_CLOSED_LIVE_PATCH_STATUS = initial
    core.metals_dashboard_reconciliation_status = status
    return initial
