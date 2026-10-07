"""Intrahour execution hardening for the live XAU exit managers.

This does not change strategy thresholds.  It keeps the existing 48h ATR2
(long) and MFE25 (short) policies, but ensures the live manager is refreshed
inside the hour and that MFE25 can see the current broker price rather than
waiting for the next completed 1h observation.  The existing live manager
remains responsible for ratcheting the broker-hosted stop; OANDA then executes
that stop independently at any point inside the hour.
"""
from __future__ import annotations

import inspect
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

VERSION = "metals_xau_intrahour_exits_v1_2026_10_07"
DEFAULT_POLL_SECONDS = 5.0


def _f(core: Any, value: Any) -> Optional[float]:
    try:
        v = core.safe_float(value)
        return float(v) if v is not None else None
    except Exception:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manager_broker_stop_evidence(core: Any, manager: Any) -> Dict[str, Any]:
    """Read-only source inspection proving which broker-stop path manager owns."""
    out: Dict[str, Any] = {"manager_source_available": False, "helpers": [], "broker_stop_path_detected": False}
    try:
        src = inspect.getsource(manager)
        out["manager_source_available"] = True
        calls = sorted(set(re.findall(r"\b([A-Za-z_]\w*)\s*\(", src)))
        helper_sources = []
        for name in calls:
            if not (name.startswith("_metals_xau_live") or name.startswith("metals_xau_live")):
                continue
            obj = getattr(core, name, None)
            if not callable(obj):
                continue
            out["helpers"].append(name)
            try:
                helper_sources.append(inspect.getsource(obj))
            except Exception:
                pass
        combined = src + "\n" + "\n".join(helper_sources)
        lo = combined.lower()
        # Do not require one exact implementation spelling; the existing core has
        # evolved through several versions.  These tokens together identify the
        # live stop-candidate -> broker dependent-order path.
        candidate = "_metals_xau_live_stop_candidate" in combined
        broker_write = any(x in lo for x in ("stoploss", "dependent", "/orders", "current_stop_price"))
        request = "_metals_xau_live_request" in combined
        out["broker_stop_path_detected"] = bool(candidate and broker_write and request)
        out["stop_candidate_detected"] = candidate
        out["broker_write_detected"] = broker_write
        out["broker_request_detected"] = request
    except Exception as exc:
        out["inspection_error"] = f"{type(exc).__name__}: {exc}"
    return out


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(core, "_METALS_XAU_INTRAHOUR_EXITS_INSTALLED", False):
        return dict(getattr(core, "_METALS_XAU_INTRAHOUR_EXIT_STATUS", {"installed": True, "version": VERSION}))

    original_metrics = core._metals_xau_live_trade_metrics
    original_manager = core.metals_xau_live_manager_tick
    poll_seconds = max(2.0, float(os.getenv("METALS_XAU_INTRAHOUR_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)) or DEFAULT_POLL_SECONDS))

    price_lock = threading.Lock()
    manager_lock = threading.Lock()
    stop_event = threading.Event()
    price_cache: Dict[str, Any] = {"bid": None, "ask": None, "at_utc": None, "ok": False, "error": None}
    status: Dict[str, Any] = {
        "installed": True,
        "version": VERSION,
        "poll_seconds": poll_seconds,
        "policies": {"XAUUSD_LONG": "ATR2_CHANDELIER", "XAUUSD_SHORT": "MFE_GIVEBACK_25"},
        "minimum_hold_candles": 48,
        "strategy_rules_changed": False,
        "live_price_used_for_mfe": True,
        "broker_hosted_stop_execution": True,
        "started": False,
        "ticks": 0,
        "busy_skips": 0,
        "last_tick_utc": None,
        "last_tick_ok": None,
        "last_error": None,
        "last_price": None,
    }
    status.update(_manager_broker_stop_evidence(core, original_manager))

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
            snap = {"bid": bid, "ask": ask, "at_utc": _utc_now(), "ok": True, "error": None}
        except Exception as exc:
            snap = {"bid": None, "ask": None, "at_utc": _utc_now(), "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        with price_lock:
            price_cache.update(snap)
        return snap

    def intrahour_metrics(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        d = dict(original_metrics(*args, **kwargs) or {})
        link = None
        if args:
            # The first dict containing an entry price/side is the trade link in
            # every current core signature.  Fall back to keyword form.
            for obj in args:
                if isinstance(obj, dict) and ("entry_price" in obj or "side" in obj):
                    link = obj
                    break
        if link is None:
            candidate = kwargs.get("link")
            link = candidate if isinstance(candidate, dict) else {}

        side = str(d.get("side") or link.get("side") or "long").lower()
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
                old_mfe = max(0.0, _f(core, d.get("mfe_r")) or 0.0)
                d["current_price"] = live
                d["current_r"] = live_r
                d["mfe_r"] = max(old_mfe, live_r, 0.0)
                d["intrahour_price_utc"] = snap.get("at_utc")
                d["intrahour_price_source"] = "OANDA_PRICING"
        return d

    def locked_manager(*args: Any, **kwargs: Any):
        if not manager_lock.acquire(blocking=False):
            status["busy_skips"] = int(status.get("busy_skips") or 0) + 1
            return {"ok": True, "skipped": True, "reason": "intrahour_manager_busy"}
        try:
            refresh_price()
            result = original_manager(*args, **kwargs)
            status["ticks"] = int(status.get("ticks") or 0) + 1
            status["last_tick_utc"] = _utc_now()
            status["last_tick_ok"] = True
            status["last_error"] = None
            with price_lock:
                status["last_price"] = {k: price_cache.get(k) for k in ("bid", "ask", "at_utc", "ok", "error")}
            return result
        except Exception as exc:
            status["ticks"] = int(status.get("ticks") or 0) + 1
            status["last_tick_utc"] = _utc_now()
            status["last_tick_ok"] = False
            status["last_error"] = f"{type(exc).__name__}: {exc}"
            # Fail closed: do not manufacture a local exit. Existing broker stop
            # and the core's own retry/maintenance paths remain authoritative.
            print(f"METALS_XAU_INTRAHOUR_TICK_ERROR {type(exc).__name__}: {exc}", flush=True)
            return {"ok": False, "error": status["last_error"]}
        finally:
            manager_lock.release()

    core._metals_xau_live_trade_metrics = intrahour_metrics
    core.metals_xau_live_manager_tick = locked_manager
    core.METALS_XAU_INTRAHOUR_EXIT_VERSION = VERSION
    core._METALS_XAU_INTRAHOUR_EXITS_INSTALLED = True
    core._METALS_XAU_INTRAHOUR_EXIT_STATUS = status

    # If a pre-existing core maintenance loop calls the manager, it now lands in
    # the locked/live-price wrapper above.  This independent daemon is deliberate
    # redundancy and guarantees sub-hour refresh even if that legacy cadence is
    # hourly.  The lock makes simultaneous invocations harmless.
    def loop() -> None:
        status["started"] = True
        print(
            "METALS_XAU_INTRAHOUR_EXITS_STARTED "
            f"version={VERSION} poll={poll_seconds:.1f}s broker_stop_path={status.get('broker_stop_path_detected')}",
            flush=True,
        )
        while not stop_event.is_set():
            locked_manager()
            stop_event.wait(poll_seconds)

    @app.on_event("startup")
    def _start_intrahour_xau_exits() -> None:
        t = threading.Thread(target=loop, name="metals-xau-intrahour-exits", daemon=True)
        t.start()
        status["thread_name"] = t.name

    @app.on_event("shutdown")
    def _stop_intrahour_xau_exits() -> None:
        stop_event.set()

    @app.get("/analysis/intrahour-exit-status")
    def _intrahour_exit_status() -> Dict[str, Any]:
        return dict(status)

    return status
