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
<script id="pep-combined-live-metals-pnl-v2">
(function() {
  'use strict';
  // Never climb from one KPI into the grid and overwrite another KPI.
  // Legacy Giveback has a composite value like "£58.81 · 95.2%": treating
  // that as non-money previously made the lookup climb into the NAV card.
  function text(el) {
    return String(el.textContent || '').replace(/\s+/g,' ').trim();
  }
  function metricTitle(t) {
    return /^(?:NAV|Broker P&L(?: · LIVE XAU \+ XAG SHORT)?|Open P&L(?: · LIVE XAU \+ XAG SHORT)?|High[- ]Water(?: · LIVE XAU \+ XAG SHORT)?|Giveback(?: · LIVE XAU \+ XAG SHORT)?|This Week|This Month|Open Trades|48h\+ Trades|Signal Health|Signals|Candidate Support)$/i.test(t);
  }
  function metricValue(t) {
    return /^(?:[-+]?£|£-)[\d,]+(?:\.\d+)?(?:\s*[·•]\s*\d+(?:\.\d+)?%)?$/.test(t) || /^[—–-]$/.test(t);
  }
  function findTile(labelPattern) {
    var nodes=Array.from(document.querySelectorAll('body *')).filter(function(el) {
      return !el.children.length && labelPattern.test(text(el));
    });
    for(var i=0;i<nodes.length;i++) {
      var node=nodes[i], parent=node.parentElement;
      for(var depth=0;parent && depth<6;depth++,parent=parent.parentElement) {
        var leaves=Array.from(parent.querySelectorAll('*')).filter(function(el) {
          return !el.children.length;
        });
        // An ancestor containing another KPI title is never this card.
        if(leaves.some(function(el) { return el!==node && metricTitle(text(el)); })) break;
        var amounts=leaves.filter(function(el) {
          return el!==node && (el.getAttribute('data-pep-metal-money')==='1' ||
                               metricValue(text(el)));
        });
        if(amounts.length===1) return {label:node,amount:amounts[0],tile:parent};
        if(amounts.length>1) break;
      }
    }
    return null;
  }
  function fmtMoney(value) {
    return (value < 0 ? '-£' : '£') + Math.abs(value).toLocaleString('en-GB',{
      minimumFractionDigits:2, maximumFractionDigits:2
    });
  }
  function fmtWhen(value) {
    return value ? String(value).replace('T',' ').slice(0,16) + ' UTC' : 'unknown';
  }
  function valid(value) {
    return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  }
  function note(tile, text, match) {
    var els=Array.from(tile.tile.querySelectorAll('*')).filter(function(el) {
      return !el.children.length && el !== tile.label && el !== tile.amount &&
        match.test((el.textContent||'').trim());
    });
    var n=els[0];
    if(n) n.textContent=text;
  }
  function paint(tile, label, value, color) {
    if(!tile)return;
    tile.label.textContent=label;
    tile.amount.setAttribute('data-pep-metal-money','1');
    tile.amount.textContent=value === null ? '—' : fmtMoney(value);
    tile.amount.style.color=value === null ? '#fbbf24' : color;
  }
  function apply(snap) {
    var broker=findTile(/^(Broker P&L|Open P&L · LIVE XAU \+ XAG SHORT)$/i);
    var hwm=findTile(/^(High-Water|High water)( · LIVE XAU \+ XAG SHORT)?$/i);
    var give=findTile(/^(Giveback)( · LIVE XAU \+ XAG SHORT)?$/i);
    var verified=!!(snap && snap.ok);
    var pnl=verified && valid(snap.open_pnl_gbp) ? Number(snap.open_pnl_gbp) : null;
    paint(broker,'Broker P&L · LIVE XAU + XAG SHORT',pnl,
      pnl !== null && pnl < 0 ? '#f87171' : '#4ade80');
    if(broker)note(broker,pnl === null ?
      'Combined broker data unavailable — no previous figure reused' :
      'XAU + XAG SHORT broker-verified '+fmtWhen(snap.observed_at_utc)+
      ' · Realised remains XAU-only',/LIVE Metals open P&L|LIVE XAU \+ XAG SHORT|Realised £/i);

    var peakOK=verified && snap.high_water_status === 'OBSERVED_COMBINED_LIVE' &&
      valid(snap.high_water_gbp) && valid(snap.giveback_gbp);
    var peak=peakOK ? Number(snap.high_water_gbp) : null;
    var given=peakOK ? Number(snap.giveback_gbp) : null;
    paint(hwm,'High-Water · LIVE XAU + XAG SHORT',peak,'#4ade80');
    paint(give,'Giveback · LIVE XAU + XAG SHORT',given,
      given !== null && given > 0 ? '#f87171' : '#4ade80');
    if(hwm)note(hwm,peakOK ?
      'Combined peak observed since '+fmtWhen(snap.high_water_tracking_started_at_utc)+
      ' · peaked '+fmtWhen(snap.high_water_at_utc) :
      'Combined peak tracking unavailable · XAU protection unchanged',
      /(?:\d+(?:\.\d+)?R|LIVE Metals|XAU \+ XAG SHORT)/i);
    if(give)note(give,peakOK ?
      'Combined open P&L below observed peak'+
      (valid(snap.giveback_pct)?' · '+Number(snap.giveback_pct).toFixed(1)+'%':'')+
      ' · XAU protection unchanged' :
      'Combined giveback unavailable · XAU protection unchanged',
      /(?:\d+(?:\.\d+)?R|XAU \+ XAG SHORT|giveback)/i);
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
    if 'pep-combined-live-metals-pnl-v2' not in text:
        text = text.replace("</body>", _SCRIPT + "\n</body>", 1) if "</body>" in text else text + _SCRIPT
    return text


def install(app, core):
    @app.get("/api/live-metals-open-pnl")
    def live_metals_open_pnl():
        try:
            from metals_dashboard_clean_entrypoint import live_metals_open_positions
            snapshot = reconcile(live_metals_open_positions())
            try:
                from metals_combined_observed_hwm import observe
                snapshot.update(observe(core, snapshot))
            except Exception as exc:
                # A database outage must not corrupt broker-verified open P&L.
                snapshot.update({
                    "high_water_status": "UNAVAILABLE",
                    "high_water_gbp": None, "high_water_at_utc": None,
                    "high_water_tracking_started_at_utc": None,
                    "giveback_gbp": None, "giveback_pct": None,
                    "high_water_error": f"{type(exc).__name__}: {exc}"[:160],
                })
            return snapshot
        except Exception as exc:
            return {
                "ok": False, "version": VERSION, "scope": SCOPE,
                "open_pnl_gbp": None, "high_water_status": "UNAVAILABLE",
                "high_water_gbp": None, "giveback_gbp": None, "xau_open_pnl_gbp": None,
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
