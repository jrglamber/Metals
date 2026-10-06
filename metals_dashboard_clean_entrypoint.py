"""Metals production entrypoint with strict LIVE vs DEMO dashboard separation.

Presentation-only layer on top of metals_unified_entrypoint. Trading/execution
logic is unchanged. Live XAU and demo/research controls are kept in separate,
collapsed footer sections so practice positions/accounting cannot visually leak
into the live portfolio area.
"""
from __future__ import annotations

from typing import Any, Dict

import metals_unified_entrypoint as base

analysis = base.analysis
core = analysis.core


def _scalar(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return None


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


@core.app.get("/api/live-xau-broker-accounting")
def live_xau_broker_accounting() -> Dict[str, Any]:
    """Small read-only accounting summary from the LIVE XAU broker snapshot."""
    try:
        snap = core.metals_xau_live_broker_snapshot() or {}
        wanted = (
            "nav", "balance", "unrealized", "unrealised", "realized", "realised",
            "margin", "pl", "pnl", "open_count", "trade_count", "position_count",
        )
        metrics: Dict[str, Any] = {}

        def collect(prefix: str, obj: Any, depth: int = 0) -> None:
            if depth > 2 or len(metrics) >= 30:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = str(k)
                    lk = key.lower()
                    path = f"{prefix}.{key}" if prefix else key
                    scalar = _scalar(v)
                    if scalar is not None and any(w in lk for w in wanted):
                        metrics[path] = scalar
                    elif isinstance(v, dict):
                        collect(path, v, depth + 1)

        collect("", snap)
        return {
            "ok": bool(snap.get("ok", True)),
            "scope": "LIVE_XAU_ONLY",
            "owned_open_count": len(snap.get("owned_open_trades") or []),
            "metrics": metrics,
        }
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_ONLY", "owned_open_count": 0, "metrics": {}, "error": f"{type(exc).__name__}: {exc}"}


_base_rewrite = analysis._rewrite_dashboard_version

_DASHBOARD_FOOTER_LAYOUT = r'''
<style id="pep-dashboard-footer-layout-style">
  #pep-live-open-positions,
  #pep-live-broker-accounting {
    margin: 12px 0;
    border: 1px solid rgba(95,220,145,.30);
    border-radius: 12px;
    background: rgba(40,160,90,.055);
    overflow: hidden;
  }
  #pep-live-open-positions summary,
  #pep-live-broker-accounting summary {
    cursor: pointer;
    padding: 13px 15px;
    font-weight: 800;
  }
  #pep-live-open-positions .pep-live-pos-body,
  #pep-live-broker-accounting .pep-live-account-body { padding: 0 15px 14px; }
  #pep-live-open-positions table,
  #pep-live-broker-accounting table { width: 100%; border-collapse: collapse; font-size: .88rem; }
  #pep-live-open-positions th, #pep-live-open-positions td,
  #pep-live-broker-accounting th, #pep-live-broker-accounting td {
    padding: 7px 8px;
    border-bottom: 1px solid rgba(255,255,255,.08);
    text-align: left;
  }
  .pep-empty { opacity: .72; padding: 8px 0 2px; }
  #pep-dashboard-footer-separation { margin-top: 42px; }
</style>
<script id="pep-dashboard-footer-layout-script">
(function () {
  function norm(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
  function allEls() { return Array.prototype.slice.call(document.querySelectorAll('body *')); }

  function nearestWidget(el) {
    var p = el;
    for (var i = 0; i < 8 && p; i++, p = p.parentElement) {
      if (!p) break;
      var tag = (p.tagName || '').toLowerCase();
      var cls = String(p.className || '').toLowerCase();
      if (tag === 'details' || tag === 'section' || /card|panel|block|widget|section/.test(cls)) return p;
    }
    return el;
  }

  function findText(needle) {
    var n = needle.toLowerCase();
    var els = allEls();
    for (var i = 0; i < els.length; i++) {
      var t = norm(els[i].textContent).toLowerCase();
      if (t === n || t.indexOf(n) !== -1) return els[i];
    }
    return null;
  }

  function findLegacyOpenTrades() {
    var els = allEls();
    for (var i = 0; i < els.length; i++) {
      var t = norm(els[i].textContent).toLowerCase();
      if ((t === 'open trades / positions' || t === 'open trades/positions' || t === 'demo open trades / positions') &&
          !els[i].closest('#pep-live-open-positions')) return els[i];
    }
    return null;
  }

  function relabelDemoOpen(widget) {
    if (!widget) return;
    var candidates = widget.querySelectorAll('summary,h1,h2,h3,h4,h5,strong,b,div,span');
    for (var i = 0; i < candidates.length; i++) {
      var t = norm(candidates[i].textContent).toLowerCase();
      if (t === 'open trades / positions' || t === 'open trades/positions' || t === 'demo open trades / positions') {
        candidates[i].textContent = 'Demo Open Trades / Positions';
        break;
      }
    }
    widget.setAttribute('data-pep-demo-only', '1');
  }

  function ensureLiveOpenPositions() {
    var d = document.getElementById('pep-live-open-positions');
    if (!d) {
      d = document.createElement('details');
      d.id = 'pep-live-open-positions';
      d.innerHTML = '<summary>Live Open Trades / Positions — XAU only</summary><div class="pep-live-pos-body"><div class="pep-empty">Loading live XAU positions…</div></div>';
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
    d.open = false;
    return d;
  }

  function ensureLiveAccounting() {
    var d = document.getElementById('pep-live-broker-accounting');
    if (!d) {
      d = document.createElement('details');
      d.id = 'pep-live-broker-accounting';
      d.innerHTML = '<summary>Live Broker / OANDA / Accounting — XAU only</summary><div class="pep-live-account-body"><div class="pep-empty">Loading live broker/accounting summary…</div></div>';
      fetch('/api/live-xau-broker-accounting', {cache:'no-store'})
        .then(function(r){ return r.json(); })
        .then(function(data){
          var body = d.querySelector('.pep-live-account-body');
          var metrics = (data && data.metrics) || {};
          var keys = Object.keys(metrics);
          var html = '<div class="pep-empty">Live owned open trades: '+((data && data.owned_open_count) || 0)+'</div>';
          if (keys.length) {
            html += '<table><tbody>';
            keys.forEach(function(k){ html += '<tr><th>'+k+'</th><td>'+metrics[k]+'</td></tr>'; });
            html += '</tbody></table>';
          }
          body.innerHTML = html;
        })
        .catch(function(){
          var body = d.querySelector('.pep-live-account-body');
          if (body) body.innerHTML = '<div class="pep-empty">Live broker/accounting view unavailable.</div>';
        });
    }
    d.open = false;
    return d;
  }

  function findDemoAccountingWidget() {
    var el = findText('demo broker / oanda / accounting') || findText('broker / oanda / accounting');
    if (!el) return null;
    var w = nearestWidget(el);
    var cands = w.querySelectorAll ? w.querySelectorAll('summary,h1,h2,h3,h4,h5,strong,b,div,span') : [];
    for (var i = 0; i < cands.length; i++) {
      var t = norm(cands[i].textContent).toLowerCase();
      if (t === 'broker / oanda / accounting' || t === 'demo broker / oanda / accounting') {
        cands[i].textContent = 'Demo Broker / OANDA / Accounting';
        break;
      }
    }
    w.setAttribute('data-pep-demo-only', '1');
    return w;
  }

  function collapseEverything(root) {
    (root || document).querySelectorAll('details').forEach(function(d){ d.open = false; });
  }

  function applyFooterLayout() {
    var liveLabel = document.getElementById('pep-live-section-label');
    var demoLabel = document.getElementById('pep-demo-section-label');
    var liveOpen = ensureLiveOpenPositions();
    var liveAccounting = ensureLiveAccounting();
    var legacyHeading = findLegacyOpenTrades();
    var demoOpen = legacyHeading ? nearestWidget(legacyHeading) : null;
    if (demoOpen && demoOpen.id !== 'pep-live-open-positions') relabelDemoOpen(demoOpen);
    var demoAccounting = findDemoAccountingWidget();

    var footer = document.getElementById('pep-dashboard-footer-separation');
    if (!footer) {
      footer = document.createElement('div');
      footer.id = 'pep-dashboard-footer-separation';
      document.body.appendChild(footer);
    }

    // Requested order: all live controls together, then all demo/research controls.
    if (liveLabel) footer.appendChild(liveLabel);
    footer.appendChild(liveOpen);
    footer.appendChild(liveAccounting);
    if (demoLabel) footer.appendChild(demoLabel);
    if (demoOpen && demoOpen !== footer && !footer.contains(demoOpen)) footer.appendChild(demoOpen);
    if (demoAccounting && demoAccounting !== footer && !footer.contains(demoAccounting)) footer.appendChild(demoAccounting);

    collapseEverything(document);
    document.documentElement.setAttribute('data-pep-footer-layout', '1');
    return true;
  }

  // Run after the older separation script, and keep late-rendered details collapsed.
  var attempts = 0;
  var timer = setInterval(function(){
    attempts += 1;
    applyFooterLayout();
    if (attempts > 24) clearInterval(timer);
  }, 250);

  var observer = new MutationObserver(function(mutations){
    mutations.forEach(function(m){
      m.addedNodes.forEach(function(n){
        if (n && n.nodeType === 1) collapseEverything(n);
      });
    });
  });
  observer.observe(document.documentElement, {childList:true, subtree:true});

  applyFooterLayout();
})();
</script>
'''


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        if "pep-dashboard-footer-layout-script" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _DASHBOARD_FOOTER_LAYOUT + "\n</body>", 1)
            else:
                text += _DASHBOARD_FOOTER_LAYOUT
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
