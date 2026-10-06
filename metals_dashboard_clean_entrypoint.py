"""Metals production entrypoint with strict LIVE vs DEMO dashboard separation.

Presentation-only layer on top of metals_unified_entrypoint. Trading/execution
logic is unchanged. The legacy Open Trades / Positions widget is moved into the
DEMO / RESEARCH area, while a new live-only widget is populated from the live
XAU broker snapshot.
"""
from __future__ import annotations

from typing import Any, Dict

import metals_unified_entrypoint as base

analysis = base.analysis
core = analysis.core


@core.app.get("/api/live-xau-open-positions")
def live_xau_open_positions() -> Dict[str, Any]:
    """Read-only live XAU position view; excludes all demo/practice positions."""
    try:
        snap = core.metals_xau_live_broker_snapshot() or {}
        trades = []
        for t in snap.get("owned_open_trades") or []:
            units = float(core.safe_float(t.get("currentUnits")) or 0.0)
            trades.append({
                "id": core.safe_str(t.get("id")),
                "instrument": core.safe_str(t.get("instrument")),
                "side": "short" if units < 0 else "long",
                "units": units,
                "open_time": core.safe_str(t.get("openTime")),
                "price": core.safe_str(t.get("price")),
                "unrealized_pl": core.safe_float(t.get("unrealizedPL")),
            })
        return {
            "ok": bool(snap.get("ok", True)),
            "scope": "LIVE_XAU_ONLY",
            "count": len(trades),
            "trades": trades,
        }
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_ONLY", "count": 0, "trades": [], "error": f"{type(exc).__name__}: {exc}"}


_base_rewrite = analysis._rewrite_dashboard_version

_STRICT_POSITION_SEPARATION = r'''
<style id="pep-strict-position-separation-style">
  #pep-live-open-positions {
    margin: 14px 0 18px 0;
    border: 1px solid rgba(95,220,145,.30);
    border-radius: 12px;
    background: rgba(40,160,90,.055);
    overflow: hidden;
  }
  #pep-live-open-positions summary {
    cursor: pointer;
    padding: 13px 15px;
    font-weight: 800;
  }
  #pep-live-open-positions .pep-live-pos-body { padding: 0 15px 14px; }
  #pep-live-open-positions table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  #pep-live-open-positions th, #pep-live-open-positions td { padding: 7px 8px; border-bottom: 1px solid rgba(255,255,255,.08); text-align: left; }
  #pep-live-open-positions .pep-empty { opacity: .72; padding: 8px 0 2px; }
</style>
<script id="pep-strict-position-separation-script">
(function () {
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
  function allEls() { return Array.prototype.slice.call(document.querySelectorAll('body *')); }

  function findHeading() {
    var els = allEls();
    for (var i = 0; i < els.length; i++) {
      var t = norm(els[i].textContent).toLowerCase();
      if (t === 'open trades / positions' || t === 'open trades/positions' ||
          (t.indexOf('open trades') !== -1 && t.indexOf('positions') !== -1 && t.length < 100)) {
        return els[i];
      }
    }
    return null;
  }

  function nearestWidget(el) {
    var p = el;
    for (var i = 0; i < 7 && p; i++, p = p.parentElement) {
      if (!p) break;
      var tag = (p.tagName || '').toLowerCase();
      var cls = String(p.className || '').toLowerCase();
      if (tag === 'details' || tag === 'section' || /card|panel|block|widget|section/.test(cls)) return p;
    }
    return el;
  }

  function relabelDemo(widget) {
    if (!widget) return;
    var candidates = widget.querySelectorAll('summary,h1,h2,h3,h4,h5,strong,b,div,span');
    for (var i = 0; i < candidates.length; i++) {
      var t = norm(candidates[i].textContent).toLowerCase();
      if (t === 'open trades / positions' || t === 'open trades/positions') {
        candidates[i].textContent = 'Demo Open Trades / Positions';
        return;
      }
    }
  }

  function demoDestination() {
    return document.getElementById('pep-demo-section-label') || (function(){
      var els = allEls();
      for (var i = 0; i < els.length; i++) {
        var t = norm(els[i].textContent).toLowerCase();
        if (t.indexOf('demo broker / oanda / accounting') !== -1 || t.indexOf('demo / research') !== -1) return els[i];
      }
      return null;
    })();
  }

  function makeLiveWidget(beforeNode) {
    if (document.getElementById('pep-live-open-positions')) return;
    var d = document.createElement('details');
    d.id = 'pep-live-open-positions';
    d.open = true;
    d.innerHTML = '<summary>LIVE Open Trades / Positions — XAU only</summary><div class="pep-live-pos-body"><div class="pep-empty">Loading live XAU positions…</div></div>';
    if (beforeNode && beforeNode.parentNode) beforeNode.parentNode.insertBefore(d, beforeNode);
    else {
      var liveLabel = document.getElementById('pep-live-section-label');
      if (liveLabel && liveLabel.parentNode) liveLabel.parentNode.insertBefore(d, liveLabel.nextSibling);
      else document.body.appendChild(d);
    }

    fetch('/api/live-xau-open-positions', {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(data){
        var body = d.querySelector('.pep-live-pos-body');
        var rows = (data && data.trades) || [];
        if (!rows.length) {
          body.innerHTML = '<div class="pep-empty">No live XAU positions open.</div>';
          return;
        }
        var html = '<table><thead><tr><th>Instrument</th><th>Side</th><th>Units</th><th>Entry</th><th>Unrealised P/L</th><th>Opened</th></tr></thead><tbody>';
        rows.forEach(function(x){
          html += '<tr><td>'+(x.instrument||'XAU_USD')+'</td><td>'+String(x.side||'').toUpperCase()+'</td><td>'+x.units+'</td><td>'+(x.price||'')+'</td><td>'+(x.unrealized_pl == null ? '' : x.unrealized_pl)+'</td><td>'+(x.open_time||'')+'</td></tr>';
        });
        html += '</tbody></table>';
        body.innerHTML = html;
      })
      .catch(function(){
        var body = d.querySelector('.pep-live-pos-body');
        if (body) body.innerHTML = '<div class="pep-empty">Live XAU position view unavailable.</div>';
      });
  }

  function apply() {
    if (document.documentElement.getAttribute('data-pep-position-separated') === '1') return true;
    var heading = findHeading();
    if (!heading) return false;
    var demoWidget = nearestWidget(heading);
    if (!demoWidget || demoWidget.id === 'pep-live-open-positions') return false;

    makeLiveWidget(demoWidget);
    relabelDemo(demoWidget);

    var dest = demoDestination();
    if (dest) {
      var destWidget = nearestWidget(dest);
      var parent = (destWidget && destWidget.parentNode) ? destWidget.parentNode : dest.parentNode;
      var anchor = destWidget || dest;
      if (parent) parent.insertBefore(demoWidget, anchor.nextSibling);
    }

    demoWidget.setAttribute('data-pep-demo-only', '1');
    document.documentElement.setAttribute('data-pep-position-separated', '1');
    return true;
  }

  if (!apply()) {
    var attempts = 0;
    var timer = setInterval(function(){
      attempts += 1;
      if (apply() || attempts > 40) clearInterval(timer);
    }, 250);
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
        if "pep-strict-position-separation-script" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _STRICT_POSITION_SEPARATION + "\n</body>", 1)
            else:
                text += _STRICT_POSITION_SEPARATION
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
