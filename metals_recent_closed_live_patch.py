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

VERSION = "metals_recent_closed_live_v3_strict_separation_2026_10_14"


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
    """Return only broker-linked rows from the dedicated LIVE XAU ledger."""
    core.init_db()
    lim = max(1, min(int(limit or 30), 100))
    with core.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM metals_xau_live_trade_links
            WHERE UPPER(COALESCE(status,'')) <> 'OPEN'
              AND COALESCE(CAST(broker_trade_id AS TEXT),'') <> ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = _d(row)
        risk = _f(_first(d, "risk_amount", "risk_cash", "risk_gbp", "estimated_risk_amount", "requested_risk_amount"))
        pl = _f(_first(d, "realized_pl", "realised_pl", "realized_pnl", "realised_pnl"))
        d["approx_r"] = (pl / risk) if (pl is not None and risk not in (None, 0.0)) else None
        d["account_lane"] = "LIVE"
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
        return (
            '<div class="section-note small"><strong>LIVE ONLY.</strong> '
            'No recently closed broker-linked XAU trades recorded. Demo/research trades are excluded.</div>'
        )
    body: List[str] = []
    for r in rows:
        pl_value = _first(r, "realized_pl", "realised_pl", "realized_pnl", "realised_pnl")
        pl = _f(pl_value)
        r_mult = _f(r.get("approx_r"))
        pl_cls = "pos" if (pl or 0.0) > 0 else "neg" if (pl or 0.0) < 0 else "flat"
        reason = _s(_first(r, "close_reason", "exit_reason", "error", "status")) or "closed"
        closed_time = _first(r, "closed_at_utc", "closed_at", "close_time", "updated_at_utc", "updated_at")
        entry = _first(r, "entry_price", "open_price", "price")
        exit_price = _first(r, "closed_price", "close_price", "exit_price", "last_known_price")
        units = _first(r, "filled_units", "requested_units", "units")
        broker_id = _first(r, "broker_trade_id", "oanda_trade_id", "trade_id")
        body.append(
            "<tr>"
            f"<td>{html.escape(_s(closed_time) or '—')}</td>"
            f"<td><strong>{html.escape((_s(r.get('side')) or '—').upper())}</strong></td>"
            f"<td>{html.escape(_fmt_price(entry))}</td>"
            f"<td>{html.escape(_fmt_price(exit_price))}</td>"
            f"<td>{html.escape(_s(units) or '—')}</td>"
            f"<td class=\"{pl_cls}\"><strong>{html.escape(_fmt_money(pl_value))}</strong></td>"
            f"<td>{'—' if r_mult is None else f'{r_mult:+.2f}R'}</td>"
            f"<td>{html.escape(reason)}</td>"
            f"<td>{html.escape(_s(broker_id) or '—')}</td>"
            "</tr>"
        )
    return (
        '<div class="section-note small"><strong>LIVE XAU ONLY.</strong> '
        'Source: dedicated live broker-link ledger. Demo/research trades are excluded by construction.</div>'
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

    # Preserve the old DEMO/RESEARCH renderer under an explicit research-only name,
    # but never append it to the live dashboard renderer.
    core._metals_demo_recently_closed_trades_html = original

    def patched(limit: int = 30) -> str:
        try:
            return _live_closed_html(core, limit)
        except Exception as exc:
            return (
                '<div class="section-note small"><strong>LIVE ONLY.</strong> '
                'Live XAU recently closed view unavailable: '
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
            "live_source_table": "metals_xau_live_trade_links",
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
