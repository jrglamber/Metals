"""Metals dashboard reconciliation patch.

Presentation/read-only diagnostics only:
1. Show LIVE XAU closed broker-linked trades in the live Recently Closed section.
2. Keep DEMO / RESEARCH closed trades completely separate from the live renderer.
3. Expose a current-classifier probe for the latest XAU raw signals so stale
   historical audit wording cannot be mistaken for the current candidate reason.

No trading, entry, exit, sizing, harvest, stacking or risk logic is changed.
"""
from __future__ import annotations

import html
import json
from typing import Any, Dict, List, Optional

VERSION = "metals_recent_closed_live_both_assets_v4_2026_11_25"


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


def _first(row: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def _fmt_money(value: Any) -> str:
    v = _f(value)
    return "—" if v is None else f"£{v:,.2f}"


def _fmt_price(value: Any) -> str:
    v = _f(value)
    return "—" if v is None else f"{v:,.3f}"


def _recent_live_closed(core: Any, limit: int = 30) -> List[Dict[str, Any]]:
    """Both LIVE XAU and XAG trade ledgers; broker-linked closures only."""
    from metals_live_recent_closed_combined import rows
    return rows(core, limit)


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
        return ('<div class="section-note small"><strong>LIVE XAU + XAG SHORT.</strong> '
                'No closed broker-linked trades recorded. Demo/research excluded.</div>')
    cells = []
    for r in rows:
        pnl = r.get("realized_pl")
        rr = r.get("approx_r")
        cells.append(
            "<tr><td>"+html.escape(str(r.get("closed_at_utc") or "—"))+"</td>"
            "<td>"+html.escape(str(r.get("asset") or "—"))+"</td>"
            "<td>"+html.escape(str(r.get("side") or "—"))+"</td>"
            "<td>"+html.escape(str(r.get("broker_trade_id") or "—"))+"</td>"
            "<td>"+html.escape(_fmt_price(r.get("entry_price")))+"</td>"
            "<td>"+html.escape(_fmt_price(r.get("exit_price")))+"</td>"
            "<td>"+html.escape(str(r.get("units") if r.get("units") is not None else "—"))+"</td>"
            "<td>"+html.escape(_fmt_money(pnl))+"</td>"
            "<td>"+(f"{rr:+.2f}R" if rr is not None else "—")+"</td>"
            "<td>"+html.escape(str(r.get("close_reason") or "—"))+"</td></tr>"
        )
    return (
        '<div class="section-note small"><strong>LIVE XAU LONG/SHORT + XAG SHORT.</strong> '
        'Broker-linked closed-trade ledger only. Trade realised P/L is not '
        'necessarily net broker cash after financing or fees. Demo excluded.</div>'
        '<div class="table-scroll"><table><thead><tr>'
        '<th>Closed UTC</th><th>Asset</th><th>Side</th><th>OANDA ID</th>'
        '<th>Entry</th><th>Exit</th><th>Units</th><th>Trade P/L*</th>'
        '<th>Approx R</th><th>Reason</th>'
        '</tr></thead><tbody>'+"".join(cells)+'</tbody></table></div>'
    )


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_RECENT_CLOSED_LIVE_PATCH_INSTALLED", False):
        return dict(getattr(core, "_METALS_RECENT_CLOSED_LIVE_PATCH_STATUS", {}))

    original = getattr(core, "_metals_recently_closed_trades_html", None)
    if not callable(original):
        raise RuntimeError("Recent-closed live patch refused: renderer missing")

    # Preserve the old DEMO/RESEARCH renderer under an explicit research-only name,
    # but never append it to the live dashboard renderer.
    core._metals_demo_recently_closed_trades_html = original

    def patched(limit: int = 30) -> str:
        try:
            return _live_closed_html(core, limit)
        except Exception as exc:
            return (
                '<div class="section-note small"><strong>LIVE ONLY.</strong> '
                'Live Metals recently closed view unavailable: '
                + html.escape(f"{type(exc).__name__}: {exc}")
                + '. Demo/research data has not been substituted.</div>'
            )

    core._metals_recently_closed_trades_html = patched
    core.METALS_RECENT_CLOSED_LIVE_PATCH_VERSION = VERSION
    core._METALS_RECENT_CLOSED_LIVE_PATCH_INSTALLED = True

    def status() -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "installed": True,
            "version": VERSION,
            "presentation_only": True,
            "strategy_changed": False,
            "live_demo_strict_separation": True,
            "live_source_tables": ["metals_xau_live_trade_links","metals_xag_live_trade_links"],
            "live_requires_broker_trade_id": True,
            "demo_renderer_excluded_from_live_section": True,
        }
        try:
            out["recent_live_closed"] = _recent_live_closed(core, 12)
        except Exception as exc:
            out["recent_live_closed_error"] = f"{type(exc).__name__}: {exc}"
        try:
            out["recent_xau_candidate_probe"] = _candidate_probe(core, 8)
        except Exception as exc:
            out["recent_xau_candidate_probe_error"] = f"{type(exc).__name__}: {exc}"
        return out

    try:
        app.add_api_route("/analysis/metals-dashboard-reconciliation", status, methods=["GET"])
    except Exception:
        pass

    initial = status()
    core._METALS_RECENT_CLOSED_LIVE_PATCH_STATUS = initial
    core.metals_dashboard_reconciliation_status = status
    return initial
