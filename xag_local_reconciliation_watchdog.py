"""Broker-authoritative XAG Short local-ledger reconciliation watchdog.

Broker-authoritative reconciliation uses OANDA GET endpoints. A separate,
bounded safety path retries ONLY a preexisting protective CLOSE_RETRY after
fresh identity/size and broker-state verification. New entries, stops and
strategy are unaffected; broker CLOSED confirmation is mandatory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import threading
import time

VERSION = "xag_local_broker_reconcile_watchdog_v2_close_retry_2026_11_23"
INTERVAL_SECONDS = 60
MAX_RETRIES_PER_POLL = 2
_lock = threading.Lock()
_thread = None
_last = {
    "installed": False, "thread_alive": False, "last_run_utc": None,
    "last_ok": None, "reason": "not_started",
    "last_changed_count": 0, "consecutive_errors": 0,
    "last_close_retry_attempted": 0, "last_close_retry_confirmed": 0,
    "last_close_retry_pending": 0, "last_close_retry_reason": "not_checked",
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


def _retry_confirmed_xag_close_requests(core, live, reconciliation, current_links):
    """Reattempt ONLY existing failed protective exits; never submit new entries."""
    state = {"attempted": 0, "confirmed": 0, "pending": 0, "reason": "none"}
    pending = [link for link in current_links
               if isinstance(link, dict)
               and str(link.get("status") or "").upper() == "CLOSE_RETRY"]
    state["pending"] = len(pending)
    if not pending:
        return state
    if not (reconciliation.get("ok") is True
            and reconciliation.get("exact_live_ownership") is True):
        state["reason"] = "broker_ownership_unverified"
        return state
    if not live.config_status(core).get("management_writes_allowed"):
        state["reason"] = "management_writes_disabled"
        return state
    for link in pending[:MAX_RETRIES_PER_POLL]:
        bid = str(link.get("broker_trade_id") or "").strip()
        if not bid or str(link.get("side") or "").lower() != "short":
            state["reason"] = "local_short_ownership_unverified"
            continue
        response = live._request(
            core, f"/v3/accounts/{live._account_id(core)}/trades/{bid}", "GET")
        trade = (response.get("data") or {}).get("trade") if response.get("ok") else None
        if not isinstance(trade, dict):
            state["reason"] = "broker_trade_detail_unavailable"
            continue
        broker_state = str(trade.get("state") or "").upper()
        units = live._num(core, trade.get("currentUnits"))
        if (str(trade.get("id") or "") != bid
                or str(trade.get("instrument") or "").upper() != "XAG_USD"
                or broker_state not in ("OPEN", "CLOSED")
                or (broker_state == "OPEN" and
                    (units is None or abs(units + live.PILOT_UNITS) > 1e-8))):
            state["reason"] = "broker_short_identity_or_units_unverified"
            continue
        state["attempted"] += 1
        result = live._close_link(
            core, link, str(link.get("close_reason") or "MFE25_CLOSE_RETRY"))
        if result.get("ok") is True:
            state["confirmed"] += 1
            state["reason"] = "broker_closed_confirmed"
        else:
            state["reason"] = str(
                result.get("reason") or result.get("error") or
                "broker_close_retry_pending")[:120]
            response = result.get("response") or {}
            payload = response.get("data") or {}
            reject = payload.get("orderRejectTransaction") or {}
            cancelled = payload.get("orderCancelTransaction") or {}
            filled = payload.get("orderFillTransaction") or {}
            state["http_status"] = response.get("status_code")
            state["broker_reply_type"] = (
                "cancelled" if cancelled else "filled" if filled else
                "rejected" if reject else "other")
            state["broker_error_code"] = str(
                payload.get("errorCode") or reject.get("rejectReason") or
                cancelled.get("reason") or payload.get("errorMessage") or
                ("fill_not_reflected_in_trade_state" if filled else
                 "response_success_without_broker_closed" if response.get("ok")
                 else "broker_error_unclassified"))[:96]
    if state["confirmed"]:
        _invalidate_local_ownership_caches()
    return state


def once(core):
    import xag_short_live_override as live
    # Use the same lock as new XAG entry preflight and manager actions.
    # Do not race entry submission with local status reconciliation.
    with live._ENTRY_LOCK:
        before = len(live._local_open(core))
        result = live._reconcile(core)
        current_links = live._local_open(core)
        retries = _retry_confirmed_xag_close_requests(core, live, result, current_links)
        after = (len(live._local_open(core)) if retries["confirmed"]
                 else len(current_links))
    changed = abs(before-after) + int(result.get("recovered_historical_live_count") or 0)
    if changed:
        _invalidate_local_ownership_caches()
    with _lock:
        _last["last_run_utc"] = datetime.now(timezone.utc).isoformat()
        _last["last_ok"] = result.get("ok") is True
        _last["reason"] = str(result.get("reason") or
                              ("reconciled" if result.get("ok") is True else "broker_reconciliation_unavailable"))[:140]
        _last["last_changed_count"] = changed
        _last["last_close_retry_attempted"] = retries["attempted"]
        _last["last_close_retry_confirmed"] = retries["confirmed"]
        _last["last_close_retry_pending"] = retries["pending"]
        _last["last_close_retry_reason"] = retries["reason"]
        _last["consecutive_errors"] = (0 if result.get("ok") is True
                                       else _last["consecutive_errors"]+1)
    if changed or result.get("ok") is not True:
        print("XAG_LOCAL_RECONCILE " +
              str({"ok": result.get("ok"), "reason": _last["reason"],
                   "changed_local_links": changed,
                   "broker_open_count": result.get("broker_xag_open_count"),
                   "local_open_count": result.get("local_xag_open_count")}),
              flush=True)
    if retries["pending"]:
        print("XAG_PROTECTIVE_CLOSE_RETRY " + str({
            "pending": retries["pending"], "attempted": retries["attempted"],
            "confirmed": retries["confirmed"], "reason": retries["reason"],
            "http_status": retries.get("http_status"),
            "broker_reply_type": retries.get("broker_reply_type"),
            "broker_error_code": retries.get("broker_error_code")
        }), flush=True)
    return {"ok": result.get("ok") is True, "changed_local_links": changed,
            "reason": _last["reason"], "close_retries": retries}


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
            return dict(_last, thread_alive=True, version=VERSION)
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
