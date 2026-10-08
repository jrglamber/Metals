"""Presentation-only Metals dashboard stability patch.

Stops legacy dashboard retry loops from re-fetching live XAU state on every
300ms layout retry and prevents the genuine LIVE XAU protection panel from
being re-parented into the DEMO section. Trading, broker and protection logic
are untouched.
"""
from __future__ import annotations

from typing import Any, Dict

PATCH_VERSION = "metals_dashboard_stability_v1_2026_10_08"

_FINAL_PLACEMENT = r'''
<script id="pep-metals-stable-protection-position">
(function(){
  var attempts=0;
  var timer=setInterval(function(){
    attempts++;
    var live=document.getElementById('pep-live-basket-manager');
    var broker=document.getElementById('pep-broker-accounting-combined');
    if(live && broker && broker.parentNode){
      var summary=live.querySelector(':scope > summary') || live.querySelector('summary');
      if(summary) summary.textContent='Basket Manager / Profit Protection';
      if(broker.nextSibling!==live) broker.parentNode.insertBefore(live,broker.nextSibling);
      live.open=false;
      var duplicate=document.getElementById('pep-live-xau-protection-v2-ui');
      if(duplicate) duplicate.style.display='none';
      clearInterval(timer);
    } else if(attempts>=50){
      clearInterval(timer);
    }
  },100);
})();
</script>
'''


def install(analysis: Any) -> Dict[str, Any]:
    original = analysis._rewrite_dashboard_version

    def stable_rewrite(body: bytes, content_type: str) -> bytes:
        body = original(body, content_type)
        if "text/html" not in (content_type or "").lower():
            return body
        try:
            text = body.decode("utf-8")

            # The live-basket layout retries because the combined Broker panel may
            # not exist immediately. It only needs to FETCH live state once; later
            # retries are layout-only.
            text = text.replace(
                "function apply(){ renderLive(); moveLegacyPracticeBasketWidgets(); }",
                "var pepLiveBasketRendered=false; function apply(){ if(!pepLiveBasketRendered){ pepLiveBasketRendered=true; renderLive(); } moveLegacyPracticeBasketWidgets(); }",
            )

            # Strict-v2's demo collector must never adopt the genuine live panel.
            text = text.replace(
                "if(!d||d===wrap||d.closest('#pep-live-xau-ops-v2'))return;",
                "if(!d||d===wrap||d.id==='pep-live-basket-manager'||d.closest('#pep-live-xau-ops-v2'))return;",
            )

            # Likewise, live accounting/protection only needs one data fetch once
            # its nested LIVE accounting container exists. Continue layout retries
            # without reissuing broker/database reads every 300ms.
            text = text.replace(
                "function apply(){removeStalePilot();renderLiveAccounting();moveAllDemoProtection();collapse();}",
                "var pepStrictRendered=false; function apply(){removeStalePilot();if(!pepStrictRendered){pepStrictRendered=renderLiveAccounting();}moveAllDemoProtection();collapse();}",
            )

            if "pep-metals-stable-protection-position" not in text:
                if "</body>" in text:
                    text = text.replace("</body>", _FINAL_PLACEMENT + "\n</body>", 1)
                else:
                    text += _FINAL_PLACEMENT
            return text.encode("utf-8")
        except Exception:
            return body

    analysis._rewrite_dashboard_version = stable_rewrite
    status = {
        "ok": True,
        "version": PATCH_VERSION,
        "live_basket_fetch_once": True,
        "strict_accounting_fetch_once": True,
        "live_protection_excluded_from_demo_reparent": True,
        "top_level_order": "broker_then_basket_protection",
        "execution_logic_changed": False,
    }
    print("METALS_DASHBOARD_STABILITY", status, flush=True)
    return status
