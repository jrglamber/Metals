"""Intrahour broker-stop hardening for the live XAU exit managers.

Strategy rules:
* XAU long: 48 completed 1h candles, then ATR2 Chandelier.
* XAU short: 48 completed 1h candles, then MFE25 (retain 75% MFE),
  with early activation at 2R for the live broker lane only.

The completed-hour research/strategy logic remains the source of the stop
candidate.  This wrapper independently synchronises the candidate to an OANDA
GTC stop every few seconds so execution does not depend on a fresh hourly
TradingView signal.  MFE25 additionally sees fresh OANDA pricing so favourable
intrahour excursion can ratchet the short stop before the next hourly candle.
"""
from __future__ import annotations

import os
import threading
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from datetime import datetime, timezone
from typing import Any, Dict, Optional

VERSION = "metals_xau_intrahour_broker_stops_v4_early2r_2026_11_21"
EARLY_MFE_THRESHOLD_R = 2.0
EARLY_MFE_RETENTION = 0.75
EARLY_MFE_POLICY = "MFE_GIVEBACK_25"
DEFAULT_POLL_SECONDS = 5.0
MIN_HOLD_CANDLES = 48


def _f(core: Any, value: Any) -> Optional[float]:
    try:
        v = core.safe_float(value)
        return float(v) if v is not None else None
    except Exception:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None


def _s(core: Any, value: Any) -> str:
    try:
        return str(core.safe_str(value) or "")
    except Exception:
        return str(value or "")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _broker_stop_price(core: Any, trade: Dict[str, Any]) -> Optional[float]:
    """Return the stop actually reported by OANDA, if one is attached."""
    for key in ("stopLossOrder", "stopLoss"):
        obj = trade.get(key)
        if isinstance(obj, dict):
            p = _f(core, obj.get("price"))
            if p is not None and p > 0:
                return p
    return None


def _safe_stop_write_failure_detail(written: Dict[str, Any]) -> Dict[str, str]:
    """Only emit broker rejection codes and messages, never raw account/API data."""
    detail: Dict[str, str] = {}
    visit = [("write", written)]
    allowed_values = ("reason", "error", "message", "errorMessage", "errorCode", "rejectReason", "status_code", "status")
    allowed_children = ("response", "data", "result", "orderRejectTransaction",
                        "stopLossOrderRejectTransaction", "orderCancelRejectTransaction",
                        "orderCreateRejectTransaction", "broker")
    while visit:
        prefix, obj = visit.pop(0)
        if not isinstance(obj, dict):
            continue
        for name in allowed_values:
            value = obj.get(name)
            if isinstance(value, (str, int, float, bool)) and value != "":
                detail[f"{prefix}.{name}"] = str(value)[:180]
        for name in allowed_children:
            child = obj.get(name)
            if isinstance(child, dict):
                visit.append((f"{prefix}.{name}", child))
    return detail


def _xau_broker_stop_price(price: Optional[float], side: str) -> Optional[float]:
    """Align XAU_USD stop with OANDA's 3-decimal display precision.

    For a short buy-stop, round upward; for a long sell-stop, round downward.
    Rounding away from the market avoids accidentally crossing a live price
    and never claims more protection than the unrounded strategy candidate.
    """
    if price is None or side not in {"long", "short"}:
        return None
    try:
        number = float(price)
        if not math.isfinite(number) or number <= 0:
            return None
        direction = ROUND_CEILING if side == "short" else ROUND_FLOOR
        return float(Decimal(str(number)).quantize(Decimal("0.001"), rounding=direction))
    except (ValueError, TypeError, ArithmeticError):
        return None


def _early_short_candidate(core: Any, link: Dict[str, Any], metrics: Dict[str, Any],
                           actual_stop: Optional[float]) -> Dict[str, Any]:
    """Only live XAU MFE25 shorts below 48 completed candles; never loosen a stop.
    Use immutable opening stop distance, not current target risk or trailing stop.
    A broker stop already at/inside the 1.5R floor proves a previous activation
    survived a restart. No existing stop is cancelled or moved outwards.
    """
    if (_s(core, link.get("side")).lower() != "short"
            or _s(core, metrics.get("side")).lower() != "short"
            or _s(core, link.get("active_exit_policy")).upper() != EARLY_MFE_POLICY):
        return {"eligible": False, "reason": "not_live_xau_mfe25_short"}
    h = _f(core, metrics.get("hold_candles"))
    entry = _f(core, link.get("entry_price"))
    hard = _f(core, link.get("stop_price"))
    mfe = _f(core, metrics.get("mfe_r"))
    current = _f(core, metrics.get("current_price"))
    if (h is None or h < 0 or h >= MIN_HOLD_CANDLES
            or any(x is None or not math.isfinite(x) for x in (entry, hard, mfe, current))
            or entry <= 0 or hard <= entry or current <= 0):
        return {"eligible": False, "reason": "early_mfe_missing_or_invalid_metrics"}
    risk = hard - entry
    activation_stop = _xau_broker_stop_price(entry - EARLY_MFE_THRESHOLD_R * EARLY_MFE_RETENTION * risk, "short")
    if activation_stop is None:
        return {"eligible": False, "reason": "early_mfe_invalid_activation_stop"}
    activated = actual_stop is not None and actual_stop > 0 and actual_stop <= activation_stop
    if mfe < EARLY_MFE_THRESHOLD_R and not activated:
        return {"eligible": False, "reason": "early_mfe_below_2r"}
    peak = max(mfe, EARLY_MFE_THRESHOLD_R)
    floor_r = peak * EARLY_MFE_RETENTION
    price = _xau_broker_stop_price(entry - floor_r * risk, "short")
    if price is None:
        return {"eligible": False, "reason": "early_mfe_invalid_stop"}
    # Require the existing manager's minimum tightening step after first arming.
    # First arm from a hard stop is necessarily a substantial improvement.
    step_pct = _f(core, getattr(core, "METALS_DEMO_MANAGER_MIN_STOP_STEP_PCT", 0.02)) or 0.02
    previous = actual_stop if actual_stop is not None else hard
    if price >= previous - max(0.0, entry * step_pct / 100.0):
        return {"eligible": False, "reason": "early_mfe_already_protected_or_step", "activated": activated}
    return {"eligible": True, "stop_price": price, "mfe_r": peak,
            "mfe_floor_r": floor_r, "giveback_fraction": 0.25,
            "reason": f"XAU SHORT early 2R MFE25 retains 75% of {peak:.4f}R peak",
            "early_2r": True}


def _early_short_selftest() -> Dict[str, Any]:
    class Core:
        safe_float = staticmethod(lambda x: float(x) if x is not None else None)
        safe_str = staticmethod(lambda x: str(x or ""))
    c = Core()
    link = {"side": "short", "active_exit_policy": EARLY_MFE_POLICY,
            "entry_price": 100.0, "stop_price": 102.0}
    m = {"side": "short", "hold_candles": 12, "mfe_r": 2.0, "current_price": 96.0}
    def candidate(l=link, metrics=m, stop=102.0):
        return _early_short_candidate(c, l, metrics, stop)
    assert not candidate(metrics={**m, "mfe_r": 1.999})["eligible"]
    assert candidate()["stop_price"] == 97.0
    assert candidate(metrics={**m, "mfe_r": 3.0})["stop_price"] == 95.5
    assert candidate(metrics={**m, "current_price": 97.5})["eligible"]  # catchup
    assert not candidate(stop=96.5)["eligible"]  # never loosen
    assert not candidate(metrics={**m, "hold_candles": 48})["eligible"]
    assert not candidate(l={**link, "side": "long"}, metrics={**m, "side": "long"})["eligible"]
    assert not candidate(l={**link, "active_exit_policy": "ATR2_CHANDELIER"})["eligible"]
    assert not candidate(l={**link, "stop_price": None})["eligible"]
    assert not candidate(metrics={**m, "mfe_r": float("nan")})["eligible"]
    assert not candidate(metrics={**m, "mfe_r": 1.0}, stop=97.0)["eligible"]  # restart latch preserves floor
    assert candidate(metrics={**m, "mfe_r": 2.5}, stop=97.0)["stop_price"] == 96.25
    return {"ok": True, "cases": 12, "version": VERSION}


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_INTRAHOUR_EXITS_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_INTRAHOUR_EXIT_STATUS", {"installed": True, "version": VERSION}))

    selftest = _early_short_selftest()
    print(f"METALS_XAU_EARLY2R_SELFTEST {selftest}", flush=True)
    original_metrics = core._metals_xau_live_trade_metrics
    original_manager = core.metals_xau_live_manager_tick
    poll_seconds = max(2.0, float(os.getenv("METALS_XAU_INTRAHOUR_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)) or DEFAULT_POLL_SECONDS))

    required = (
        "_metals_xau_live_stop_candidate",
        "_metals_xau_live_update_stop",
        "_metals_xau_live_queue_close",
        "_metals_xau_live_close",
        "metals_xau_live_broker_snapshot",
        "get_conn",
    )
    missing = [name for name in required if not callable(getattr(core, name, None))]
    if missing:
        raise RuntimeError(f"METALS_XAU_INTRAHOUR_INTEGRITY missing required live helpers: {missing}")

    price_lock = threading.Lock()
    manager_lock = threading.Lock()
    stop_event = threading.Event()
    price_cache: Dict[str, Any] = {"bid": None, "ask": None, "at_utc": None, "ok": False, "error": None}
    # Preserve favourable intrahour excursion for MFE25 between polling passes.
    mfe_cache: Dict[str, float] = {}

    status: Dict[str, Any] = {
        "installed": True,
        "version": VERSION,
        "poll_seconds": poll_seconds,
        "policies": {"XAUUSD_LONG": "ATR2_CHANDELIER", "XAUUSD_SHORT": "MFE_GIVEBACK_25"},
        "minimum_hold_candles": MIN_HOLD_CANDLES,
        "strategy_rules_changed": True,
        "early_mfe_policy": {"scope": "LIVE_XAU_SHORT_ONLY", "threshold_r": EARLY_MFE_THRESHOLD_R, "retention": EARLY_MFE_RETENTION, "normal_hold_candles": MIN_HOLD_CANDLES, "selftest": selftest},
        "live_price_used_for_mfe": True,
        "broker_hosted_stop_execution": True,
        "broker_stop_writer": "_metals_xau_live_update_stop",
        "hourly_fresh_signal_gate_bypassed_for_protective_stop_sync": True,
        "reconciliation_gate_preserved": True,
        "started": False,
        "ticks": 0,
        "busy_skips": 0,
        "last_tick_utc": None,
        "last_tick_ok": None,
        "last_error": None,
        "last_price": None,
        "last_sync": None,
        "integrity_errors": 0,
    }

    def integrity(message: str, **fields: Any) -> None:
        status["integrity_errors"] = int(status.get("integrity_errors") or 0) + 1
        payload = {"message": message, **fields}
        status["last_integrity_error"] = payload
        print(f"METALS_XAU_INTRAHOUR_INTEGRITY {payload}", flush=True)

    def refresh_price() -> Dict[str, Any]:
        try:
            account = getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "")
            instrument = getattr(core, "METALS_XAU_LIVE_ALLOWED_INSTRUMENT", "XAU_USD") or "XAU_USD"
            if not account:
                raise RuntimeError("missing live OANDA account id")
            resp = core._metals_xau_live_request(f"/v3/accounts/{account}/pricing?instruments={instrument}")
            data = (resp.get("data") or {}) if isinstance(resp, dict) else {}
            prices = data.get("prices") or []
            p = prices[0] if prices else {}
            bid = _f(core, p.get("closeoutBid"))
            ask = _f(core, p.get("closeoutAsk"))
            if bid is None:
                bids = p.get("bids") or []
                bid = _f(core, (bids[0] if bids else {}).get("price"))
            if ask is None:
                asks = p.get("asks") or []
                ask = _f(core, (asks[0] if asks else {}).get("price"))
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                raise RuntimeError("live XAU bid/ask unavailable")
            # Early writes require a fresh executable price, not a weekend quote.
            quote_time = str(p.get("time") or "")
            try:
                quote_age = (datetime.now(timezone.utc) - datetime.fromisoformat(quote_time.replace("Z", "+00:00"))).total_seconds()
            except (ValueError, TypeError):
                quote_age = None
            early_quote_ok = (p.get("tradeable") is True or str(p.get("status") or "").lower() == "tradeable") and quote_age is not None and -5 <= quote_age <= 30
            snap = {"bid": bid, "ask": ask, "at_utc": _utc_now(), "ok": True, "error": None,
                    "early_quote_ok": early_quote_ok, "broker_quote_time": quote_time}
        except Exception as exc:
            snap = {"bid": None, "ask": None, "at_utc": _utc_now(), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        with price_lock:
            price_cache.update(snap)
        return snap

    def intrahour_metrics(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        d = dict(original_metrics(*args, **kwargs) or {})
        link: Dict[str, Any] = {}
        if args:
            for obj in args:
                if isinstance(obj, dict) and ("entry_price" in obj or "side" in obj):
                    link = obj
                    break
        if not link:
            candidate = kwargs.get("link")
            link = candidate if isinstance(candidate, dict) else {}

        side = _s(core, d.get("side") or link.get("side") or "long").lower()
        side = side if side in {"long", "short"} else "long"
        with price_lock:
            snap = dict(price_cache)
        live = _f(core, snap.get("ask" if side == "short" else "bid")) if snap.get("ok") else None
        if live is None:
            return d

        entry = _f(core, link.get("entry_price")) or _f(core, d.get("entry_price"))
        hard_stop = _f(core, link.get("stop_price"))
        if entry is not None and entry > 0:
            if hard_stop is not None:
                risk_price = (hard_stop - entry) if side == "short" else (entry - hard_stop)
            else:
                sl_pct = _f(core, getattr(core, "METALS_XAU_LIVE_SL_PCT", 2.21)) or 2.21
                risk_price = entry * sl_pct / 100.0
            if risk_price > 0:
                live_r = ((entry - live) if side == "short" else (live - entry)) / risk_price
                key = _s(core, link.get("broker_trade_id") or link.get("id"))
                previous_mfe = max(0.0, _f(core, d.get("mfe_r")) or 0.0, mfe_cache.get(key, 0.0))
                new_mfe = max(previous_mfe, live_r, 0.0)
                if key:
                    mfe_cache[key] = new_mfe
                d["current_price"] = live
                d["current_r"] = live_r
                d["mfe_r"] = new_mfe
                d["intrahour_price_utc"] = snap.get("at_utc")
                d["intrahour_price_source"] = "OANDA_PRICING"
        return d

    core._metals_xau_live_trade_metrics = intrahour_metrics

    def load_open_links() -> list[Dict[str, Any]]:
        with core.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM metals_xau_live_trade_links
                WHERE status='OPEN'
                ORDER BY id
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def sync_broker_stops() -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "checked": 0,
            "mature": 0,
            "early_2r_eligible": 0,
            "early_2r_updated": 0,
            "early_2r_catchup_closed": 0,
            "eligible": 0,
            "updated": 0,
            "unchanged": 0,
            "catchup_closed": 0,
            "not_broker_open": 0,
            "errors": 0,
            "at_utc": _utc_now(),
        }
        if not bool(getattr(core, "METALS_XAU_LIVE_MANAGER_ENABLED", False)):
            summary["skipped"] = "live_manager_disabled"
            return summary

        broker = core.metals_xau_live_broker_snapshot()
        if not isinstance(broker, dict) or broker.get("ok") is False:
            summary["errors"] += 1
            integrity("broker_snapshot_failed", broker_error=(broker or {}).get("error") if isinstance(broker, dict) else None)
            return summary

        # Preserve the production reconciliation safety gate.  The only gate this
        # hardening intentionally removes is the hourly-signal freshness gate.
        recon_fn = getattr(core, "_metals_xau_live_reconciliation_snapshot", None)
        if callable(recon_fn):
            try:
                recon = dict(recon_fn(broker) or {})
                summary["reconciliation"] = {"execution_safe": recon.get("execution_safe"), "matched_count": len(recon.get("matched") or [])}
                if recon.get("execution_safe") is not True:
                    summary["skipped"] = "reconciliation_not_execution_safe"
                    return summary
            except Exception as exc:
                summary["errors"] += 1
                integrity("reconciliation_check_failed", error=f"{type(exc).__name__}: {exc}")
                return summary

        broker_trades = broker.get("owned_open_trades") or []
        by_id: Dict[str, Dict[str, Any]] = {}
        for bt in broker_trades:
            if not isinstance(bt, dict):
                continue
            bid = _s(core, bt.get("id") or bt.get("tradeID"))
            if bid:
                by_id[bid] = bt

        for link in load_open_links():
            summary["checked"] += 1
            broker_id = _s(core, link.get("broker_trade_id"))
            bt = by_id.get(broker_id)
            if not broker_id or bt is None:
                summary["not_broker_open"] += 1
                continue
            try:
                work = dict(link)
                # Broker is authoritative for the stop that actually exists.  If
                # OANDA reports no dependent stop, do not let a stale DB value
                # suppress a repair write.
                actual_stop = _broker_stop_price(core, bt)
                if actual_stop is not None:
                    work["current_stop_price"] = actual_stop
                else:
                    work["current_stop_price"] = None

                metrics = dict(core._metals_xau_live_trade_metrics(work) or {})
                if not metrics.get("ok"):
                    summary["errors"] += 1
                    integrity("metrics_failed", link_id=link.get("id"), broker_trade_id=broker_id, reason=metrics.get("reason"))
                    continue
                hold = int(_f(core, metrics.get("hold_candles")) or 0)
                early = hold < MIN_HOLD_CANDLES
                if early:
                    with price_lock:
                        early_quote_ok = price_cache.get("early_quote_ok") is True
                    if not early_quote_ok:
                        continue
                    # Require authoritative broker side, instrument and matched link.
                    if (_s(core, bt.get("instrument")).upper() != "XAU_USD"
                            or (_f(core, bt.get("currentUnits")) or 0.0) >= 0):
                        continue
                    cand = _early_short_candidate(core, work, metrics, actual_stop)
                    if cand.get("eligible"):
                        summary["early_2r_eligible"] += 1
                else:
                    summary["mature"] += 1
                    cand = dict(core._metals_xau_live_stop_candidate(work, metrics) or {})
                if not cand.get("eligible"):
                    summary["unchanged"] += 1
                    continue
                raw_stop_price = _f(core, cand.get("stop_price"))
                current_price = _f(core, metrics.get("current_price"))
                side = _s(core, metrics.get("side") or work.get("side") or "long").lower()
                stop_price = _xau_broker_stop_price(raw_stop_price, side)
                if stop_price is not None:
                    # The writer consumes cand["stop_price"] when building the
                    # OANDA replacement order.  Never send excess precision.
                    cand["stop_price"] = stop_price
                if stop_price is None or stop_price <= 0 or current_price is None or current_price <= 0:
                    summary["errors"] += 1
                    integrity(
                        "invalid_mature_stop_candidate",
                        link_id=link.get("id"), broker_trade_id=broker_id,
                        side=side, stop_price=stop_price, current_price=current_price,
                    )
                    continue
                summary["eligible"] += 1

                # A newly repaired process may discover that market price already
                # crossed the stop that should have been resident at OANDA.  Such
                # a stop cannot be installed behind market, so use the existing
                # audited live-close path immediately instead.
                breached = (side == "long" and current_price <= stop_price) or (side == "short" and current_price >= stop_price)
                if breached:
                    reason = "intrahour_broker_stop_catchup_early2r_mfe25" if early else f"intrahour_broker_stop_catchup_{'mfe25' if side == 'short' else 'atr2'}"
                    raw_id = int(_f(core, metrics.get("latest_signal_id")) or _f(core, link.get("raw_signal_id")) or 0)
                    qid = core._metals_xau_live_queue_close(link, reason, raw_id)
                    closed = dict(core._metals_xau_live_close(link, qid) or {})
                    if closed.get("ok"):
                        summary["catchup_closed"] += 1
                        if early:
                            summary["early_2r_catchup_closed"] += 1
                        mfe_cache.pop(broker_id, None)
                    else:
                        summary["errors"] += 1
                        integrity(
                            "catchup_close_failed", link_id=link.get("id"), broker_trade_id=broker_id,
                            side=side, stop_price=stop_price, current_price=current_price,
                            close_status=closed.get("status"),
                        )
                    continue

                written = dict(core._metals_xau_live_update_stop(link, cand) or {})
                if written.get("ok"):
                    if _s(core, written.get("status")).upper() == "UPDATED":
                        summary["updated"] += 1
                        if early:
                            summary["early_2r_updated"] += 1
                    else:
                        summary["unchanged"] += 1
                else:
                    summary["errors"] += 1
                    integrity(
                        "broker_stop_write_failed", link_id=link.get("id"), broker_trade_id=broker_id,
                        side=side, stop_price=stop_price, broker_current_stop=actual_stop,
                        status=written.get("status"), writer_failure=_safe_stop_write_failure_detail(written),
                    )
            except Exception as exc:
                summary["errors"] += 1
                integrity(
                    "per_trade_sync_exception", link_id=link.get("id"), broker_trade_id=broker_id,
                    error=f"{type(exc).__name__}: {exc}",
                )

        return summary

    def locked_manager(*args: Any, **kwargs: Any):
        if not manager_lock.acquire(blocking=False):
            status["busy_skips"] = int(status.get("busy_skips") or 0) + 1
            return {"ok": True, "skipped": True, "reason": "intrahour_manager_busy"}
        try:
            price = refresh_price()
            # Keep all legacy manager/review/harvest/reconciliation maintenance
            # intact. It may suppress new stop writes when its hourly signal is
            # stale; the independent broker-stop sync immediately below does not.
            legacy = original_manager(*args, **kwargs)
            sync = sync_broker_stops() if price.get("ok") else {"skipped": "live_price_unavailable", "at_utc": _utc_now()}
            status["last_sync"] = sync
            status["ticks"] = int(status.get("ticks") or 0) + 1
            status["last_tick_utc"] = _utc_now()
            status["last_tick_ok"] = int(sync.get("errors") or 0) == 0
            status["last_error"] = None if status["last_tick_ok"] else f"{sync.get('errors')} intrahour stop sync error(s)"
            with price_lock:
                status["last_price"] = {k: price_cache.get(k) for k in ("bid", "ask", "at_utc", "ok", "error")}
            return {"ok": bool(status["last_tick_ok"]), "legacy": legacy, "intrahour_stop_sync": sync}
        except Exception as exc:
            status["ticks"] = int(status.get("ticks") or 0) + 1
            status["last_tick_utc"] = _utc_now()
            status["last_tick_ok"] = False
            status["last_error"] = f"{type(exc).__name__}: {exc}"
            integrity("tick_exception", error=status["last_error"])
            return {"ok": False, "error": status["last_error"]}
        finally:
            manager_lock.release()

    core.metals_xau_live_manager_tick = locked_manager
    core.METALS_XAU_INTRAHOUR_EXIT_VERSION = VERSION
    core._METALS_XAU_INTRAHOUR_EXITS_INSTALLED = True
    core._METALS_XAU_INTRAHOUR_EXIT_STATUS = status

    def loop() -> None:
        status["started"] = True
        print(
            "METALS_XAU_INTRAHOUR_EXITS_STARTED "
            f"version={VERSION} poll={poll_seconds:.1f}s broker_hosted=True hold={MIN_HOLD_CANDLES} early_xau_short_mfe_r={EARLY_MFE_THRESHOLD_R}",
            flush=True,
        )
        while not stop_event.is_set():
            result = locked_manager(source="intrahour_broker_stop_sync")
            sync = (result or {}).get("intrahour_stop_sync") if isinstance(result, dict) else None
            if isinstance(sync, dict) and (sync.get("updated") or sync.get("catchup_closed") or sync.get("errors")):
                print(f"METALS_XAU_INTRAHOUR_STOP_SYNC {sync}", flush=True)
            stop_event.wait(poll_seconds)

    @app.on_event("startup")
    def _start_intrahour_xau_exits() -> None:
        t = threading.Thread(target=loop, name="metals-xau-intrahour-broker-stops", daemon=True)
        t.start()
        status["thread_name"] = t.name

    @app.on_event("shutdown")
    def _stop_intrahour_xau_exits() -> None:
        stop_event.set()

    @app.get("/analysis/intrahour-exit-status")
    def _intrahour_exit_status() -> Dict[str, Any]:
        return dict(status)

    return status
