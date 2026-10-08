"""Production wrapper enabling intrahour ATR2/MFE25 XAU protection."""
from __future__ import annotations

import threading
import time

# The dashboard-v2 presentation layer still contains the one-shot reset call that
# was used during the live/demo separation migration. That migration is complete;
# repeated process starts must never rebase the live XAU HWM. Suppress only that
# import-time call, then restore the real reset function for explicit/manual use.
import metals_live_basket_entrypoint as basket_base

_real_reset = basket_base._reset_live_xau_protection_cycle


def _retired_startup_reset(reset_id: str):
    return {
        "ok": True,
        "skipped": True,
        "reason": "one_shot_dashboard_reset_retired",
        "reset_id": reset_id,
    }


basket_base._reset_live_xau_protection_cycle = _retired_startup_reset
try:
    import metals_live_dashboard_v2 as base
finally:
    basket_base._reset_live_xau_protection_cycle = _real_reset

# Presentation-only stability layer. It prevents the legacy layout retry loops
# from repeatedly refetching live state and keeps LIVE XAU basket protection as
# a top-level accordion after Broker / OANDA / Accounting. No broker or strategy
# function is modified.
import metals_dashboard_stability_patch as dashboard_stability
DASHBOARD_STABILITY_STATUS = dashboard_stability.install(base.analysis)

import metals_intrahour_exit_override as intrahour

INTRAHOUR_EXIT_STATUS = intrahour.install(base.core, base.app)
app = base.app

# Permanent low-volume heartbeat. This is deliberately read-only: it makes the
# protective execution path fail-loud without changing trading decisions.
_health_stop = threading.Event()


def _health_loop() -> None:
    _health_stop.wait(20.0)
    while not _health_stop.is_set():
        s = dict(getattr(base.core, "_METALS_XAU_INTRAHOUR_EXIT_STATUS", {}) or {})
        last = dict(s.get("last_sync") or {})
        print(
            "METALS_XAU_INTRAHOUR_HEALTH "
            f"version={s.get('version')} ticks={s.get('ticks')} "
            f"last_ok={s.get('last_tick_ok')} checked={last.get('checked')} "
            f"mature={last.get('mature')} eligible={last.get('eligible')} "
            f"updated={last.get('updated')} unchanged={last.get('unchanged')} "
            f"catchup_closed={last.get('catchup_closed')} errors={last.get('errors')} "
            f"integrity_errors={s.get('integrity_errors')}",
            flush=True,
        )
        _health_stop.wait(60.0)


@app.on_event("startup")
def _start_intrahour_health() -> None:
    threading.Thread(target=_health_loop, name="metals-xau-intrahour-health", daemon=True).start()


@app.on_event("shutdown")
def _stop_intrahour_health() -> None:
    _health_stop.set()
