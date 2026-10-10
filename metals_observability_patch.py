"""Read-only observability for live XAU accounting and manager maturity.

No broker writes, strategy decisions, stop changes, harvesting, entries or exits.
Designed to be installed by the production intrahour entrypoint after all live
manager overrides are in place.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

VERSION = "metals_observability_v1.1_2026_10_10"


def _f(core: Any, v: Any, default: float = 0.0) -> float:
    try:
        x = core.safe_float(v)
        return float(x) if x is not None else default
    except Exception:
        try:
            return float(v)
        except Exception:
            return default


def _s(core: Any, v: Any) -> str:
    try:
        return str(core.safe_str(v) or "")
    except Exception:
        return str(v or "")


def _dt(core: Any, v: Any):
    try:
        return core.parse_dt(v)
    except Exception:
        return None


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_OBSERVABILITY_INSTALLED", False):
        return dict(getattr(core, "_METALS_OBSERVABILITY_STATUS", {}))

    def broker_snapshot() -> Dict[str, Any]:
        fn = getattr(core, "metals_xau_live_broker_snapshot", None)
        if not callable(fn):
            return {"ok": False, "error": "broker snapshot helper unavailable"}
        try:
            return dict(fn() or {})
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def open_links():
        try:
            with core.get_conn() as conn:
                rows = conn.execute("SELECT * FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id").fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def manager_audit() -> Dict[str, Any]:
        broker = broker_snapshot()
        by_id = {}
        for bt in broker.get("owned_open_trades") or []:
            if isinstance(bt, dict):
                tid = _s(core, bt.get("id") or bt.get("tradeID"))
                if tid:
                    by_id[tid] = bt
        rows = []
        now = datetime.now(timezone.utc)
        for link in open_links():
            tid = _s(core, link.get("broker_trade_id"))
            bt = by_id.get(tid, {})
            try:
                metrics = dict(core._metals_xau_live_trade_metrics(dict(link)) or {})
            except Exception as exc:
                metrics = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            units = _f(core, bt.get("currentUnits"), 0.0)
            side = _s(core, metrics.get("side") or link.get("side")).lower()
            if side not in {"long", "short"}:
                side = "short" if units < 0 else "long"
            hold = int(_f(core, metrics.get("hold_candles"), 0.0))
            opened = _dt(core, link.get("opened_at_utc") or link.get("open_time") or bt.get("openTime"))
            age_h = None
            if opened:
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                age_h = max(0.0, (now - opened).total_seconds() / 3600.0)
            rows.append({
                "broker_trade_id": tid,
                "side": side,
                "age_hours": age_h,
                "hold_candles": hold,
                "mature_by_manager": hold >= 48,
                "age_48h_plus": bool(age_h is not None and age_h >= 48.0),
                "maturity_mismatch": bool(age_h is not None and age_h >= 48.0 and hold < 48),
                "current_r": _f(core, metrics.get("current_r")),
                "mfe_r": _f(core, metrics.get("mfe_r")),
                "mae_r": _f(core, metrics.get("mae_r")),
                "broker_unrealized_pl": _f(core, bt.get("unrealizedPL")),
                "metrics_ok": bool(metrics.get("ok", True)),
                "metrics_reason": metrics.get("reason"),
            })
        return {
            "ok": broker.get("ok") is not False,
            "scope": "READ_ONLY_LIVE_XAU_MANAGER_AUDIT",
            "version": VERSION,
            "execution_authority": False,
            "open_count": len(rows),
            "long_count": sum(1 for r in rows if r["side"] == "long"),
            "short_count": sum(1 for r in rows if r["side"] == "short"),
            "manager_mature_count": sum(1 for r in rows if r["mature_by_manager"]),
            "age_48h_plus_count": sum(1 for r in rows if r["age_48h_plus"]),
            "maturity_mismatch_count": sum(1 for r in rows if r["maturity_mismatch"]),
            "trades": rows,
            "time_utc": now.isoformat(),
        }

    def lane_accounting() -> Dict[str, Any]:
        snap = broker_snapshot()
        by_id = {}
        for bt in snap.get("owned_open_trades") or []:
            if isinstance(bt, dict):
                tid = _s(core, bt.get("id") or bt.get("tradeID"))
                if tid:
                    by_id[tid] = bt
        lanes = {
            "long": {"open_count": 0, "open_upl_gbp": 0.0, "closed_count": 0, "realised_pl_gbp": 0.0},
            "short": {"open_count": 0, "open_upl_gbp": 0.0, "closed_count": 0, "realised_pl_gbp": 0.0},
        }
        links = []
        try:
            with core.get_conn() as conn:
                links = [dict(r) for r in conn.execute("SELECT * FROM metals_xau_live_trade_links ORDER BY id").fetchall()]
        except Exception:
            pass
        for link in links:
            tid = _s(core, link.get("broker_trade_id"))
            bt = by_id.get(tid, {})
            side = _s(core, link.get("side")).lower()
            if side not in lanes:
                side = "short" if _f(core, bt.get("currentUnits"), 0.0) < 0 else "long"
            status = _s(core, link.get("status")).upper()
            if status == "OPEN":
                lanes[side]["open_count"] += 1
                lanes[side]["open_upl_gbp"] += _f(core, bt.get("unrealizedPL"))
            else:
                lanes[side]["closed_count"] += 1
                lanes[side]["realised_pl_gbp"] += _f(core, link.get("realized_pl"))
        return {
            "ok": True,
            "scope": "LIVE_XAU_LONG_SHORT_LANE_SPLIT",
            "version": VERSION,
            "execution_authority": False,
            "note": "Lane realised P/L is trade-link realised P/L. Account financing remains in authoritative aggregate accounting to avoid allocating shared/non-trade cash flows incorrectly.",
            "lanes": lanes,
            "time_utc": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/analysis/live-xau-manager-audit")
    def _manager_audit_route():
        return manager_audit()

    @app.get("/analysis/live-xau-lane-accounting")
    def _lane_accounting_route():
        return lane_accounting()

    # Make the exact same read-only snapshots available to the existing health
    # loop and weekly exporter without duplicating maturity/accounting logic.
    core._METALS_XAU_MANAGER_AUDIT_SNAPSHOT = manager_audit
    core._METALS_XAU_LANE_ACCOUNTING_SNAPSHOT = lane_accounting

    # One startup reconciliation line makes the 48h dashboard-vs-manager mismatch
    # visible in Railway logs without changing manager eligibility or protection.
    try:
        audit = manager_audit()
        print(
            "METALS_XAU_MATURITY_AUDIT "
            f"open={audit.get('open_count')} long={audit.get('long_count')} short={audit.get('short_count')} "
            f"age48={audit.get('age_48h_plus_count')} manager48={audit.get('manager_mature_count')} "
            f"mismatch={audit.get('maturity_mismatch_count')}",
            flush=True,
        )
    except Exception as exc:
        print(f"METALS_XAU_MATURITY_AUDIT unavailable={type(exc).__name__}:{exc}", flush=True)

    status = {"installed": True, "version": VERSION, "read_only": True, "execution_authority": False}
    core._METALS_OBSERVABILITY_INSTALLED = True
    core._METALS_OBSERVABILITY_STATUS = status
    return status
