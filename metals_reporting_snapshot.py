"""Single read-only, provenance-rich LIVE Metals reporting snapshot.

The browser and Portfolio Hub consume this contract instead of separately
sampling positions, peak, cash and risk. An independently verified field can
remain available while another field is unavailable. All changes here are
reporting-only: no order, stop, signal, risk sizing, or trading-state mutations.
The observed combined high-water is a separate reporting-only table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
import time

VERSION = "metals_reporting_snapshot_v1_20261118"
SCOPE = "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"
CACHE_SECONDS = 4.0
_lock = RLock()
_cache = None
_cache_at = 0.0


def _now():
    return datetime.now(timezone.utc).isoformat()


def _count(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if n >= 0 and n == int(n) else None
    except (TypeError, ValueError):
        return None


def _risk_matches_positions(risk, positions):
    """The broker risk feed MUST describe the identical open trade IDs."""
    if not isinstance(risk, dict) or risk.get("ok") is not True or risk.get("stale"):
        return False
    if not isinstance(positions, dict) or positions.get("ok") is not True:
        return False
    rows = positions.get("trades")
    risk_ids = risk.get("broker_trade_ids")
    count = _count(positions.get("count"))
    if not isinstance(rows, list) or not isinstance(risk_ids, list) or count is None:
        return False
    ids = [str(r.get("broker_trade_id") or "").strip() for r in rows if isinstance(r, dict)]
    known = [str(i or "").strip() for i in risk_ids]
    if (len(ids) != count or len(known) != count
            or len(set(ids)) != count or len(set(known)) != count
            or not all(ids) or not all(known) or set(ids) != set(known)):
        return False
    if _count(risk.get("open_trade_count")) != count:
        return False
    quant = _count(risk.get("quantified_trade_count"))
    unquant = _count(risk.get("unquantified_trade_count"))
    if quant is None or unquant is None or quant + unquant != count:
        return False
    by = risk.get("by_instrument")
    if not isinstance(by, dict):
        return False
    a = by.get("XAU_USD") or {}
    s = by.get("XAG_USD") or {}
    xau = _count(positions.get("xau_count"))
    xag = _count(positions.get("xag_short_count"))
    return bool(
        xau is not None and xag is not None and xau + xag == count
        and _count(a.get("open_trade_count", 0 if xau == 0 else None)) == xau
        and _count(s.get("open_trade_count", 0 if xag == 0 else None)) == xag
        and _count(s.get("short_trade_count", 0 if xag == 0 else None)) == xag
        and _count(s.get("long_trade_count", 0)) == 0
    )


def _capture(core):
    from metals_dashboard_clean_entrypoint import live_metals_open_positions
    from metals_live_open_pnl_display import reconcile
    from metals_combined_observed_hwm import observe
    from metals_xag_realised_verified import verify
    from metals_xag_transaction_audit import snapshot as cash_snapshot
    import portfolio_live_risk

    report = {
        "version": VERSION,
        "scope": SCOPE,
        "generated_at_utc": _now(),
        "read_only": True,
        "execution_authority": False,
        "broker_order_writes": False,
    }
    try:
        positions = live_metals_open_positions()
        combined = reconcile(positions)
        try:
            combined.update(observe(core, combined))
        except Exception as exc:
            combined.update({
                "high_water_status": "UNAVAILABLE",
                "high_water_gbp": None,
                "giveback_gbp": None,
                "high_water_error_type": type(exc).__name__,
            })
        report["positions"] = positions
        report["combined"] = combined
    except Exception as exc:
        report["positions"] = {
            "ok": False, "scope": SCOPE, "count": None,
            "reason_type": type(exc).__name__,
        }
        report["combined"] = {
            "ok": False, "scope": SCOPE,
            "open_pnl_gbp": None, "open_count": None,
            "high_water_status": "UNAVAILABLE", "high_water_gbp": None,
            "giveback_gbp": None, "reason_type": type(exc).__name__,
        }


    # Use the *same* reconciled broker population for XAG open R. The XAG
    # ledger stores the actual initial GBP risk per fill; it is not the
    # separately reported current-stop capital-at-risk.
    try:
        from metals_xag_live_basket_r import project as project_xag_r
        with core.get_conn() as conn:
            xag_links = [dict(row) for row in conn.execute(
                """SELECT broker_trade_id, status, estimated_risk_amount
                   FROM metals_xag_live_trade_links
                   WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"""
            ).fetchall()]
        report["xag_short_open_basket"] = project_xag_r(
            report["positions"], report["combined"], xag_links)
    except Exception as exc:
        report["xag_short_open_basket"] = {
            "ok": False, "scope": "LIVE_XAG_SHORT_OPEN_INITIAL_RISK",
            "current_r": None, "open_pnl_gbp": None,
            "status": "UNVERIFIED", "reason_type": type(exc).__name__,
            "broker_writes": False,
        }

    try:
        raw_cash = cash_snapshot(core)
        verified_cash = verify(raw_cash)
        report["cash"] = {
            "ok": True, "status": "BROKER_VERIFIED", "proof": verified_cash,
            "observed_at_utc": verified_cash["time_utc"],
            "audit_refresh_in_progress": bool(raw_cash.get("audit_refresh_in_progress")),
        }
    except Exception as exc:
        refreshing = bool(locals().get("raw_cash", {}).get("audit_refresh_in_progress"))
        report["cash"] = {
            "ok": False, "status": "REFRESHING" if refreshing else "UNVERIFIED",
            "proof": None, "observed_at_utc": None,
            "audit_refresh_in_progress": refreshing,
            "reason_type": type(exc).__name__,
        }

    # Account-wide broker cash is NOT proof of strategy ownership.
    try:
        from metals_xau_broker_direction_audit import snapshot as xau_snapshot
        proof = xau_snapshot(core)
        valid = (proof.get("ok") is True
                 and proof.get("scope") == "OANDA_ACCOUNT_ALL_XAU_NOT_STRATEGY_OWNERSHIP"
                 and proof.get("transaction_history_complete") is True
                 and proof.get("trade_history_complete") is True)
        report["xau_account_cash"] = {
            "ok": bool(valid),
            "status": "BROKER_ACCOUNT_VERIFIED_NOT_STRATEGY_ATTRIBUTED" if valid else "UNVERIFIED",
            "scope": "OANDA_ACCOUNT_ALL_XAU_NOT_STRATEGY_OWNERSHIP",
            "directions": proof.get("directions") if valid else None,
            "trade_counts": proof.get("trade_counts") if valid else None,
            "account_currency": proof.get("account_currency") if valid else None,
        }
    except Exception as exc:
        report["xau_account_cash"] = {
            "ok": False, "status": "UNVERIFIED",
            "scope": "OANDA_ACCOUNT_ALL_XAU_NOT_STRATEGY_OWNERSHIP",
            "directions": None, "reason_type": type(exc).__name__,
        }

    try:
        risk = portfolio_live_risk._read("metals")
        # A cached risk calculation can precede a newly closed trade.
        # Retry one fresh read when its IDs disagree with this exact sample.
        if not _risk_matches_positions(risk, report["positions"]):
            risk = portfolio_live_risk._calculate("metals")
        if _risk_matches_positions(risk, report["positions"]):
            report["risk"] = {
                "ok": True, "status": "BROKER_POPULATION_MATCHED",
                "data": risk, "calculated_at_utc": risk.get("calculated_at_utc"),
            }
        else:
            report["risk"] = {
                "ok": False, "status": "SNAPSHOT_MISMATCH",
                "data": None, "reason_type": "BrokerTradePopulationMismatch",
            }
    except Exception as exc:
        report["risk"] = {
            "ok": False, "status": "UNVERIFIED",
            "data": None, "reason_type": type(exc).__name__,
        }
    report["ok"] = report["combined"].get("ok") is True
    report["complete"] = bool(
        report["ok"] and report["cash"]["ok"] and report["risk"]["ok"]
        and report["combined"].get("high_water_status") == "OBSERVED_COMBINED_LIVE"
    )
    return report


def snapshot(core):
    """Small single-flight TTL prevents separate readers assembling mixed data."""
    global _cache, _cache_at
    with _lock:
        if _cache is not None and time.monotonic() - _cache_at < CACHE_SECONDS:
            # Avoid callers mutating the shared report, especially top cards.
            import copy
            return copy.deepcopy(_cache)
        result = _capture(core)
        _cache = result
        _cache_at = time.monotonic()
        import copy
        return copy.deepcopy(result)
