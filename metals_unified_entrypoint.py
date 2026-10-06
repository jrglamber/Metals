"""Unified Metals production entrypoint.

Restores the read-only analysis wrapper while keeping the XAU SHORT live
promotion and expanded XAU SHORT challenger suite installed into the same core
runtime. Dashboard wording/layout is updated through the existing safe dashboard
passthrough in analysis_entrypoint, which rebuilds response headers rather than
mutating an already-sized ASGI body.

Build marker: 2026-10-06 live/demo dashboard separation.
"""
from __future__ import annotations

import analysis_entrypoint as analysis
import xau_short_live_override
import xau_short_management_override
import xau_short_research_override

# analysis_entrypoint has already installed metals_exit_override into this same
# core module. Layer the approved XAU SHORT live adapter, side-aware live
# management/reconciliation, and research-only challenger declaration on top.
XAU_SHORT_LIVE_OVERRIDE_STATUS = xau_short_live_override.install(analysis.core)
XAU_SHORT_MANAGEMENT_STATUS = xau_short_management_override.install(analysis.core)
XAU_SHORT_CHALLENGER_STATUS = xau_short_research_override.install(analysis.core)

# Extend the existing safe dashboard rewrite. analysis_entrypoint's dashboard
# passthrough removes Content-Length/content-encoding before returning the
# rewritten response, preventing the upstream error caused by the prior ASGI
# body-only mutation.
_base_rewrite = analysis._rewrite_dashboard_version

_DASHBOARD_SEPARATION_SCRIPT = r'''
<style id="metals-live-demo-separation-style">
  .pep-live-demo-divider {
    margin: 28px 0 18px 0;
    padding: 16px 18px;
    border: 1px solid rgba(255,255,255,.16);
    border-radius: 14px;
    background: rgba(255,255,255,.045);
  }
  .pep-live-demo-divider h2 {
    margin: 0 0 6px 0;
    font-size: 1.35rem;
    letter-spacing: .02em;
  }
  .pep-live-demo-divider p {
    margin: 0;
    opacity: .78;
    line-height: 1.4;
  }
  .pep-live-badge {
    display: inline-block;
    margin-right: 8px;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: .72rem;
    font-weight: 800;
    border: 1px solid rgba(95,220,145,.45);
    background: rgba(40,160,90,.14);
  }
  .pep-demo-badge {
    display: inline-block;
    margin-right: 8px;
    padding: 3px 8px;
    border-radius: 999px;
    font-size: .72rem;
    font-weight: 800;
    border: 1px solid rgba(245,190,75,.45);
    background: rgba(190,125,20,.14);
  }
</style>
<script id="metals-live-demo-separation-script">
(function () {
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
  function allEls() { return Array.prototype.slice.call(document.querySelectorAll('body *')); }

  function findExactOrContains(needle) {
    var n = needle.toLowerCase();
    var els = allEls();
    for (var i = 0; i < els.length; i++) {
      var t = norm(els[i].textContent);
      if (t && (t.toLowerCase() === n || t.toLowerCase().indexOf(n) !== -1)) return els[i];
    }
    return null;
  }

  function nearestSectionish(el) {
    if (!el) return null;
    var p = el;
    for (var i = 0; i < 5 && p && p.parentElement; i++, p = p.parentElement) {
      var cls = String(p.className || '').toLowerCase();
      if (/section|panel|card|block|group|wrap|container/.test(cls)) return p;
    }
    return el;
  }

  // Give the live half an explicit identity without changing any values.
  var liveAnchor = findExactOrContains('NAV');
  if (liveAnchor && !document.getElementById('pep-live-section-label')) {
    var liveSection = nearestSectionish(liveAnchor);
    var live = document.createElement('div');
    live.id = 'pep-live-section-label';
    live.className = 'pep-live-demo-divider';
    live.innerHTML = '<h2><span class="pep-live-badge">LIVE</span>XAU LONG + XAU SHORT</h2>' +
      '<p>Only live XAU positions, live broker P&amp;L, HWM/giveback and live manager state belong in this section.</p>';
    if (liveSection && liveSection.parentNode) liveSection.parentNode.insertBefore(live, liveSection);
  }

  // Keep practice/demo broker positions visually isolated from live XAU.
  var demoAnchor = findExactOrContains('Broker / OANDA / Accounting');
  if (demoAnchor && !document.getElementById('pep-demo-section-label')) {
    var demoSection = nearestSectionish(demoAnchor);
    var demo = document.createElement('div');
    demo.id = 'pep-demo-section-label';
    demo.className = 'pep-live-demo-divider';
    demo.innerHTML = '<h2><span class="pep-demo-badge">DEMO / RESEARCH</span>XAG + LEGACY PRACTICE POSITIONS</h2>' +
      '<p>XAG remains demo/research. Any pre-promotion XAU practice trades remain here until they close naturally; they are not part of live XAU P&amp;L or live portfolio statistics.</p>';
    if (demoSection && demoSection.parentNode) demoSection.parentNode.insertBefore(demo, demoSection);
  }

  // Relabel the old combined heading where present.
  var els = allEls();
  for (var i = 0; i < els.length; i++) {
    var txt = norm(els[i].textContent);
    if (txt === 'Broker / OANDA / Accounting') {
      els[i].textContent = 'Demo Broker / OANDA / Accounting';
    }
  }
})();
</script>
'''


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        replacements = (
            (
                "XAU LONG live pilot · XAU SHORT/XAG practice",
                "XAU LONG + XAU SHORT live · XAG demo/research",
            ),
            (
                "XAU LONG + XAU SHORT live · XAG practice/research",
                "XAU LONG + XAU SHORT live · XAG demo/research",
            ),
            (
                "METALS SPLIT EXECUTION.",
                "LIVE METALS — XAU LONG + XAU SHORT.",
            ),
            (
                "Top tiles show LIVE Metals only (currently XAU LONG). XAU SHORT and XAG remain practice",
                "Top tiles are LIVE XAU only: XAU LONG + XAU SHORT. XAG is demo/research",
            ),
            (
                "Top tiles show LIVE Metals only (XAU LONG + XAU SHORT). XAG remains practice/research",
                "Top tiles are LIVE XAU only: XAU LONG + XAU SHORT. XAG is demo/research",
            ),
            (
                "XAU SHORT and XAG remain practice and are shown lower in Broker / OANDA / Accounting.",
                "Demo/research positions are shown separately below and never contribute to live XAU portfolio statistics.",
            ),
            (
                "XAG remains practice/research and is shown lower in Broker / OANDA / Accounting.",
                "Demo/research positions are shown separately below and never contribute to live XAU portfolio statistics.",
            ),
        )
        for old, new in replacements:
            text = text.replace(old, new)
        if "metals-live-demo-separation-script" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _DASHBOARD_SEPARATION_SCRIPT + "\n</body>", 1)
            else:
                text += _DASHBOARD_SEPARATION_SCRIPT
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
