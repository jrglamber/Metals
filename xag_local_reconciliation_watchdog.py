"""Broker-authoritative XAG Short local-ledger reconciliation watchdog.

Only OANDA GET endpoints are used by xag_short_live_override._reconcile.
Local ledger rows may be updated ONLY when broker trade details explicitly
prove OPEN with a protective stop or CLOSED. No trade submission, stop change,
or broker close. A stale local OPEN row must not disable combined reporting
indefinitely when the original broker trade has closed.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time

VERSION = "xag_local_broker_reconcile_watchdog_v1_2026_11_18"
INTERVAL_SECONDS = 60
_lock = threading.Lock()
_thread = None
_last = {
    "installed": False, "thread_alive": False, "last_run_utc": None,
    "last_ok": None, "reason": "not_started",
    "last_changed_count": 0, "consecutive_errors": 0,
}


def _invalidate_local_ownership_caches():
    # Never invalidate valid broker cash proof unless ownership rows actually
    # changed. Re-audit is required before numbers become current again.
    try:
        import metals_xag_transaction_audit as cash
        with cash._lock:
            cash._cache = None
            cash._at = 0.0
            cash._consecutive_failures = 0
    except Exception:
        pass
    try:
        import metals_xag_ledger_audit as ledger
        with ledger._lock:
            ledger._cache = None
            ledger._cache_at = 0.0
    except Exception:
        pass


def once(core):
    import xag_short_live_override as live
    # Use the same lock as new XAG entry preflight and manager actions.
    # Do not race entry submission with local status reconciliation.
    with live._ENTRY_LOCK:
        before = len(live._local_open(core))
        result = live._reconcile(core)
        after = len(live._local_open(core))
    changed = abs(before-after) + int(result.get("recovered_historical_live_count") or 0)
    if changed:
        _invalidate_local_ownership_caches()
    with _lock:
        _last["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        _last["last_ok"] = result.get("ok") is True
        _last["reason"] = str(result.get("reason") or
                              ("reconciled" if result.get("ok") is True else "broker_reconciliation_unavailable"))[:140]
        _last["last_changed_count"] = changed
        _last["consecutive_errors"] = (0 if result.get("ok") is True
                                       else _last["consecutive_errors"]+1)
    if changed or result.get("ok") is not True:
        print("XAG_LOCAL_RECONCILE " +
              str({"ok": result.get("ok"), "reason": _last["reason"],
                   "changed_local_links": changed,
                   "broker_open_count": result.get("broker_xag_open_count"),
                   "local_open_count": result.get("local_xag_open_count")}),
              flush=True)
    return {"ok": result.get("ok") is True, "changed_local_links": changed,
            "reason": _last["reason"]}


def _worker(core):
    # Startup reconciliation is already run separately by the lane installer;
    # delay the next check to avoid duplicating its broker requests.
    while True:
        time.sleep(INTERVAL_SECONDS)
        try:
            once(core)
        except Exception as exc:
            with _lock:
                _last["last_run_utc"] = datetime.now(timezone.utc).isoformat()
                _last["last_ok"] = False
                _last["reason"] = type(exc).__name__
                _last["consecutive_errors"] += 1
            print("XAG_LOCAL_RECONCILE failure_class=" + type(exc).__name__, flush=True)


def start(core):
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return health()
        if os.getenv("METALS_XAG_RECONCILE_WATCHDOG_ENABLED","true").strip().lower() in ("0","false","off","no"):
            _last["reason"] = "disabled_by_operator"
            return dict(_last)
        _thread = threading.Thread(target=_worker,args=(core,),
                                   name="xag-local-broker-closure-reconciler",
                                   daemon=True)
        _thread.start()
        _last["installed"] = True
        _last["thread_alive"] = _thread.is_alive()
        _last["reason"] = "running"
        return dict(_last)


def health():
    with _lock:
        return dict(_last, thread_alive=bool(_thread and _thread.is_alive()),
                    version=VERSION, interval_seconds=INTERVAL_SECONDS,
                    broker_order_writes=False)
