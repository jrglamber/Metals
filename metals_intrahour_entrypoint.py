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


# Presentation-only repair: dashboard-v2's demo-protection cleanup matches the
# words "basket manager / profit protection" and can therefore re-parent the
# genuine LIVE XAU panel into the DEMO accounting container. Keep the live panel
# as a standard top-level accordion, matching BCO's dashboard order:
# Broker / OANDA / Accounting -> Basket Manager / Profit Protection -> Research.
# This wrapper changes HTML placement only; it has no execution/broker authority.
_dashboard_rewrite_base = base.analysis._rewrite_dashboard_version

_BASKET_PROTECTION_LINE_FIX = r'''
<script id="pep-metals-top-level-protection-fix">
(function(){
  function place(){
    var live=document.getElementById('pep-live-basket-manager');
    var broker=document.getElementById('pep-broker-accounting-combined');
    if(!live || !broker || !broker.parentNode) return false;

    var summary=live.querySelector(':scope > summary') || live.querySelector('summary');
    if(summary) summary.textContent='Basket Manager / Profit Protection';

    // Always keep LIVE protection immediately after the top-level broker panel.
    // insertBefore also removes it automatically from any nested DEMO container.
    if(broker.nextSibling!==live){
      broker.parentNode.insertBefore(live, broker.nextSibling);
    }
    live.open=false;

    // v2 already has a full live protection panel; avoid showing the same state
    // a second time inside the LIVE broker/accounting subsection.
    var duplicate=document.getElementById('pep-live-xau-protection-v2-ui');
    if(duplicate) duplicate.style.display='none';
    return true;
  }

  // The older cleanup script runs briefly after page load. Re-assert the intended
  // top-level placement after each DOM move, then stop once the page settles.
  var busy=false;
  var observer=new MutationObserver(function(){
    if(busy) return;
    busy=true;
    try{ place(); }finally{ busy=false; }
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});

  var n=0;
  var timer=setInterval(function(){
    n++;
    place();
    if(n>=45){ clearInterval(timer); observer.disconnect(); place(); }
  },300);
  place();
})();
</script>
'''


def _rewrite_dashboard_with_top_level_protection(body: bytes, content_type: str) -> bytes:
    body = _dashboard_rewrite_base(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        if "pep-metals-top-level-protection-fix" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _BASKET_PROTECTION_LINE_FIX + "\n</body>", 1)
            else:
                text += _BASKET_PROTECTION_LINE_FIX
        return text.encode("utf-8")
    except Exception:
        return body


base.analysis._rewrite_dashboard_version = _rewrite_dashboard_with_top_level_protection

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
