"""Read-only, broker-verified combined LIVE Metals P&L for the dashboard.

The legacy Metals top tile is XAU-only; the Portfolio Hub already includes
LIVE XAU and LIVE XAG SHORT. This endpoint shares the Hub's strict trade-level
scope and reconciles the same OANDA open-trade P&L without touching execution,
historical XAU realised P&L, high-water marks or risk settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

VERSION = "metals_combined_live_open_pnl_v1_2026_11_16"
SCOPE = "LIVE_XAU_LONG_SHORT_PLUS_LIVE_XAG_SHORT"


def _number(value):
    if value is None or value == "":
        return None
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _count(value):
    n = _number(value)
    return int(n) if n is not None and n >= 0 and n == int(n) else None


def reconcile(payload, max_age_seconds=120):
    """Fail closed rather than present XAU-only or incomplete P&L as combined."""
    if not isinstance(payload, dict) or payload.get("scope") != SCOPE or payload.get("ok") is not True:
        raise ValueError("Combined LIVE Metals broker scope unavailable")
    for flag in ("xau_ok", "xag_ok", "practice_excluded",
                 "xag_broker_link_reconciled", "xag_unrealized_complete"):
        if payload.get(flag) is not True:
            raise ValueError("Combined LIVE Metals broker reconciliation incomplete: " + flag)
    if _count(payload.get("xag_untracked_broker_count")) != 0:
        raise ValueError("Untracked broker XAG positions")
    try:
        ts = datetime.fromisoformat(str(payload["observed_at_utc"]).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError("Naive broker snapshot timestamp")
        age = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
        if not -10 <= age <= max_age_seconds:
            raise ValueError("Stale LIVE Metals broker snapshot")
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("Invalid LIVE Metals broker snapshot timestamp") from exc

    xau_count, xag_count, xag_short_count, total = (
        _count(payload.get(key)) for key in ("xau_count", "xag_count", "xag_short_count", "count")
    )
    trades = payload.get("trades")
    if (None in (xau_count, xag_count, xag_short_count, total)
            or not isinstance(trades, list) or xag_count != xag_short_count
            or total != xau_count + xag_count or len(trades) != total):
        raise ValueError("LIVE Metals broker position counts disagree")

    seen = set()
    xau_n = xag_n = 0
    xau_pnl = xag_pnl = 0.0
    for trade in trades:
        if not isinstance(trade, dict):
            raise ValueError("Invalid broker position")
        broker_id = str(trade.get("broker_trade_id") or "").strip()
        if not broker_id or broker_id in seen:
            raise ValueError("Missing or duplicate broker trade ID")
        seen.add(broker_id)
        pnl = _number(trade.get("unrealized_gbp"))
        instrument = str(trade.get("instrument") or "").upper().replace("_", "").replace("/", "")
        if pnl is None:
            raise ValueError("Missing broker P&L")
        if trade.get("lane") == "XAU_LIVE" and instrument == "XAUUSD":
            xau_n += 1
            xau_pnl += pnl
        elif trade.get("lane") == "XAG_SHORT_LIVE" and instrument == "XAGUSD":
            if str(trade.get("side") or "").upper() != "SHORT" or trade.get("tracking") != "TRACKED":
                raise ValueError("Untracked or non-short XAG position")
            xag_n += 1
            xag_pnl += pnl
        else:
            raise ValueError("Unknown LIVE Metals position ownership")
    xag_given = _number(payload.get("xag_unrealized_gbp"))
    if (xau_n != xau_count or xag_n != xag_count or xag_given is None
            or abs(xag_given - xag_pnl) > 0.05):
        raise ValueError("LIVE Metals broker P&L/position reconciliation mismatch")
    return {
        "ok": True, "version": VERSION, "scope": SCOPE,
        "open_pnl_gbp": round(xau_pnl + xag_pnl, 4),
        "xau_open_pnl_gbp": round(xau_pnl, 4),
        "xag_short_open_pnl_gbp": round(xag_pnl, 4),
        "open_count": total, "xau_count": xau_count, "xag_short_count": xag_count,
        "observed_at_utc": payload["observed_at_utc"],
        "source": "metals:/api/live-metals-open-positions",
        "historical_realised_scope": "XAU_ONLY_XAG_LEDGER_PENDING",
        "broker_writes": False,
    }


_SCRIPT = r'''
<script id="pep-combined-live-metals-pnl-v1">
(function() {
  'use strict';
  var titleText = 'Open P&L · LIVE XAU + XAG SHORT';
  function findTile() {
    var nodes = Array.from(document.querySelectorAll('body *')).filter(function(el) {
      return !el.children.length && /^(Broker P&L|Open P&L · LIVE XAU \+ XAG SHORT)$/.test((el.textContent || '').trim());
    });
    for (var i=0; i<nodes.length; i++) {
      var node=nodes[i], parent=node.parentElement;
      for (var depth=0; parent && depth<6; depth++,parent=parent.parentElement) {
        var amounts=Array.from(parent.querySelectorAll('*')).filter(function(el) {
          return !el.children.length && (el.getAttribute('data-pep-live-metals-pnl-value')==='1' || /^[-+]?£[\d,]+(?:\.\d+)?$|^£-[\d,]+(?:\.\d+)?$/.test((el.textContent||'').trim()));
        });
        if (amounts.length) return {label:node, amount:amounts[0], tile:parent};
      }
    }
    return null;
  }
  function apply(snapshot) {
    var tile=findTile();
    if(!tile) return;
    tile.label.textContent=titleText;
    tile.amount.setAttribute('data-pep-live-metals-pnl-value','1');
    var value=snapshot && snapshot.ok && Number.isFinite(Number(snapshot.open_pnl_gbp))
      ? Number(snapshot.open_pnl_gbp) : null;
    tile.amount.textContent=value === null ? '—' :
      (value < 0 ? '-£' : '£') + Math.abs(value).toLocaleString('en-GB',{minimumFractionDigits:2,maximumFractionDigits:2});
    tile.amount.style.color=value===null?'#fbbf24':value<0?'#f87171':'#4ade80';
    var notes=Array.from(tile.tile.querySelectorAll('*')).filter(function(el) {
      return !el.children.length && (/LIVE Metals open P&L|LIVE XAU \+ XAG SHORT · broker|Realised £/.test(el.textContent||''));
    });
    if(notes.length) {
      var when=snapshot && snapshot.ok ? String(snapshot.observed_at_utc||'').replace('T',' ').slice(0,19)+' UTC' : '';
      notes[0].textContent=value===null
        ? 'LIVE XAU + XAG SHORT broker reconciliation unavailable — do not use prior P&L'
        : 'LIVE XAU + XAG SHORT · Broker-verified '+when+' · Realised/HWM remain XAU-only';
    }
  }
  var active=false;
  async function refresh() {
    if(active)return;
    active=true;
    try {
      var response=await fetch('/api/live-metals-open-pnl',{cache:'no-store'});
      if(!response.ok) throw Error('HTTP '+response.status);
      apply(await response.json());
    } catch(err) { apply({ok:false}); }
    finally { active=false; }
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', refresh);
  else refresh();
  setInterval(refresh,10000);
})();
</script>
'''


def inject_html(html):
    """Inject only once into a full HTML dashboard response."""
    text = str(html)
    if 'pep-combined-live-metals-pnl-v1' not in text:
        text = text.replace("</body>", _SCRIPT + "\n</body>", 1) if "</body>" in text else text + _SCRIPT
    return text


def install(app):
    @app.get("/api/live-metals-open-pnl")
    def live_metals_open_pnl():
        try:
            from metals_dashboard_clean_entrypoint import live_metals_open_positions
            return reconcile(live_metals_open_positions())
        except Exception as exc:
            return {
                "ok": False, "version": VERSION, "scope": SCOPE,
                "open_pnl_gbp": None, "xau_open_pnl_gbp": None,
                "xag_short_open_pnl_gbp": None, "observed_at_utc": None,
                "reason": f"{type(exc).__name__}: {exc}"[:220],
                "broker_writes": False,
            }
    # analysis_entrypoint already mounted core.app at "/" before this wrapper
    # installs late routes. Starlette would send /api/live-metals-open-pnl
    # straight to that catch-all mount (404). Put this API route first, as the
    # other production-level risk and telemetry routes already do.
    candidates = [route for route in app.router.routes
                  if getattr(route, "path", None) == "/api/live-metals-open-pnl"
                  and getattr(route, "endpoint", None) is live_metals_open_pnl]
    if len(candidates) != 1:
        raise RuntimeError("Combined LIVE Metals P&L endpoint registration failed")
    route = candidates[0]
    app.router.routes.remove(route)
    app.router.routes.insert(0, route)
    return {"installed": True, "version": VERSION, "broker_writes": False,
            "route": "/api/live-metals-open-pnl", "route_priority": 0}
