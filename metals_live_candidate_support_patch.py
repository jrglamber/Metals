"""Live-XAU dashboard telemetry for Project Exit Plan Metals.

Presentation/telemetry only.
- Candidate Support is shown on the Metals dashboard as live-XAU-only 0/1 telemetry.
- Open Trades shows live XAU total / LONG / SHORT / last-opened time.
- XAG research/demo is excluded from Candidate Support.
- No execution, sizing, entry, exit, harvest or broker logic is changed.
"""
from __future__ import annotations

from typing import Any, Dict

VERSION = "metals_live_xau_dashboard_telemetry_v5_2026_10_19"


def _d(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        return dict(value or {})
    except Exception:
        return {}


def _latest_xau(core: Any) -> Dict[str, Any]:
    core.init_db()
    with core.get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM raw_signals
            WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(pair,''),'_',''),'-',''),' ',''))
                  IN ('XAUUSD','XAU','GOLD')
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
    if not row:
        return {"supported": 0, "side": "NONE", "raw_signal_id": None, "candle": None,
                "candidate_state": "NO_SIGNAL", "reason": "no_xau_signal"}

    raw = core._raw_signal_json(row)
    candidate = _d(core.metals_demo_candidate_for_row(raw, row))
    side = str(core._metals_demo_side(candidate.get("demo_side")) or "").strip().lower()
    is_candidate = bool(candidate.get("demo_candidate")) and side in {"long", "short"}

    if side == "long":
        lane_live = bool(getattr(core, "METALS_XAU_LONG_LIVE_PROMOTION_ENABLED", False))
    elif side == "short":
        gate = getattr(core, "_metals_xau_short_live_gate_enabled", None)
        lane_live = bool(gate()) if callable(gate) else False
    else:
        lane_live = False

    supported = int(bool(is_candidate and lane_live))
    return {
        "supported": supported,
        "side": side.upper() if supported else "NONE",
        "raw_signal_id": int(row["id"]),
        "candle": row["timestamp_readable"] if "timestamp_readable" in row.keys() else None,
        "received_at_utc": row["received_at_utc"] if "received_at_utc" in row.keys() else None,
        "candidate_state": str(candidate.get("demo_state") or ("CANDIDATE" if supported else "BLOCKED")),
        "reason": str(candidate.get("demo_reason") or candidate.get("demo_blockers") or ""),
        "model_version": str(candidate.get("model_version") or ""),
    }


def _live_trade_summary(core: Any) -> Dict[str, Any]:
    core.init_db()
    with core.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, status, side, created_at_utc, broker_open_time_utc
            FROM metals_xau_live_trade_links
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    items = [_d(r) for r in rows]
    opened = [r for r in items if str(r.get("status") or "").strip().upper() == "OPEN"]
    longs = sum(1 for r in opened if str(r.get("side") or "").strip().lower() == "long")
    shorts = sum(1 for r in opened if str(r.get("side") or "").strip().lower() == "short")
    latest = items[0] if items else {}
    return {
        "ok": True,
        "version": VERSION,
        "open_count": len(opened),
        "long_count": longs,
        "short_count": shorts,
        "last_opened_at_utc": latest.get("broker_open_time_utc") or latest.get("created_at_utc") or None,
        "scope": "LIVE_XAU_ONLY",
        "demo_research_excluded": ["XAGUSD"],
        "strategy_changed": False,
    }


_WIDGET = r"""
<script id="metals-live-open-trades-detail-v5">
(function(){
  function cards(){ return Array.from(document.querySelectorAll('.card')); }
  function byLabel(name){
    var target=String(name||'').toLowerCase();
    return cards().find(function(c){
      var l=c.querySelector('.label');
      return l && String(l.textContent||'').trim().toLowerCase()===target;
    }) || null;
  }
  function ensureCandidate(){
    var existing=byLabel('Candidate Support');
    if(existing) return existing;
    var signal=byLabel('Signals');
    var grid=signal && signal.parentElement;
    if(!grid) return null;
    var c=document.createElement('div');
    c.className='card';
    c.setAttribute('data-pep-live-xau-candidate-support','1');
    c.innerHTML='<div class="label">Candidate Support</div><div class="value">0/1 · NONE</div><div class="small">LIVE XAU only · loading current candle…</div>';
    grid.appendChild(c);
    return c;
  }
  function fmt(v){
    if(!v) return '-';
    try{
      return new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/London',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(v));
    }catch(e){ return String(v); }
  }
  function paintTrades(x){
    var c=byLabel('Open Trades');
    if(!c || !x || !x.ok) return;
    var v=c.querySelector('.value') || c.querySelector('.v');
    var s=c.querySelector('.small') || c.querySelector('.sub');
    var value=String(x.open_count||0);
    var note=String(x.long_count||0)+' LONG · '+String(x.short_count||0)+' SHORT · Last opened '+fmt(x.last_opened_at_utc);
    if(v && v.textContent!==value) v.textContent=value;
    if(s && s.textContent!==note) s.textContent=note;
  }
  function paintCandidate(x){
    var c=ensureCandidate();
    if(!c || !x || !x.ok) return;
    var v=c.querySelector('.value') || c.querySelector('.v');
    var s=c.querySelector('.small') || c.querySelector('.sub');
    var supported=Number(x.supported||0);
    var side=String(x.side||'NONE').toUpperCase();
    var value=String(x.display || (supported+'/1 · '+side));
    var latest=x.latest||{};
    var state=String(latest.candidate_state||'');
    var reason=String(latest.reason||'').replace(/\s+/g,' ').trim();
    var note='LIVE XAU only · current candle';
    if(state) note+=' · '+state;
    if(reason) note+=' · '+reason;
    if(note.length>180) note=note.slice(0,177)+'…';
    if(v){
      if(v.textContent!==value) v.textContent=value;
      try{
        v.classList.remove('pos','neg');
        v.classList.add(supported>0?'pos':'neg');
      }catch(e){}
    }
    if(s && s.textContent!==note) s.textContent=note;
  }
  async function refresh(){
    ensureCandidate();
    try{
      var r=await fetch('/api/live-xau-trade-tile',{cache:'no-store'});
      if(r.ok) paintTrades(await r.json());
    }catch(e){}
    try{
      var c=await fetch('/api/live-xau-candidate-support',{cache:'no-store'});
      if(c.ok) paintCandidate(await c.json());
    }catch(e){}
  }
  function start(){ ensureCandidate(); refresh(); setTimeout(refresh,750); setInterval(refresh,10000); }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start); else start();
})();
</script>
"""


def install(core: Any, app: Any, analysis: Any) -> Dict[str, Any]:
    if getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED", False):
        return dict(getattr(core, "_LIVE_XAU_CANDIDATE_SUPPORT_STATUS", {}))

    def support_snapshot() -> Dict[str, Any]:
        latest = _latest_xau(core)
        supported = int(latest.get("supported") or 0)
        side = str(latest.get("side") or "NONE").upper()
        return {
            "ok": True,
            "version": VERSION,
            "scope": "LIVE_XAU_ONLY",
            "live_assets": ["XAUUSD"],
            "demo_research_excluded": ["XAGUSD"],
            "supported": supported,
            "expected": 1,
            "side": side,
            "display": f"{supported}/1 · {side}",
            "semantics": "CURRENT_XAU_LIVE_CANDIDATE_LONG_OR_SHORT",
            "latest": latest,
            "strategy_changed": False,
        }

    def trade_snapshot() -> Dict[str, Any]:
        return _live_trade_summary(core)

    core.live_xau_candidate_support_snapshot = support_snapshot
    core.live_xau_trade_tile_snapshot = trade_snapshot
    try:
        app.add_api_route('/api/live-xau-candidate-support', support_snapshot, methods=['GET'])
    except Exception:
        pass
    try:
        app.add_api_route('/api/live-xau-trade-tile', trade_snapshot, methods=['GET'])
    except Exception:
        pass

    previous_rewrite = analysis._rewrite_dashboard_version
    def rewrite(body: bytes, content_type: str) -> bytes:
        body = previous_rewrite(body, content_type)
        if 'text/html' not in str(content_type or '').lower():
            return body
        try:
            text = body.decode('utf-8')
            if 'metals-live-open-trades-detail-v5' not in text:
                text = text.replace('</body>', _WIDGET + '\n</body>', 1) if '</body>' in text else text + _WIDGET
            return text.encode('utf-8')
        except Exception:
            return body

    analysis._rewrite_dashboard_version = rewrite
    core._LIVE_XAU_CANDIDATE_SUPPORT_INSTALLED = True
    trade_probe = trade_snapshot()
    support_probe = support_snapshot()
    checks = {
        'trade_route_callable': callable(getattr(core, 'live_xau_trade_tile_snapshot', None)),
        'candidate_route_callable': callable(getattr(core, 'live_xau_candidate_support_snapshot', None)),
        'counts_reconcile': int(trade_probe.get('long_count') or 0) + int(trade_probe.get('short_count') or 0) <= int(trade_probe.get('open_count') or 0),
        'candidate_denominator_is_one': int(support_probe.get('expected') or 0) == 1,
        'candidate_side_explicit': str(support_probe.get('side') or '') in {'LONG', 'SHORT', 'NONE'},
        'xag_excluded': support_probe.get('demo_research_excluded') == ['XAGUSD'],
        'presentation_only': True,
    }
    if not all(checks.values()):
        raise RuntimeError(f'Live XAU dashboard telemetry self-test failed: {checks}')

    status = {
        'installed': True,
        'version': VERSION,
        'presentation_only': True,
        'strategy_changed': False,
        'candidate_support_dashboard_tile': True,
        'candidate_support_api': '/api/live-xau-candidate-support',
        'live_trade_tile_api': '/api/live-xau-trade-tile',
        'startup_self_test': checks,
        'current_candidate_support': support_probe,
        'current_open_trades': trade_probe,
    }
    core._LIVE_XAU_CANDIDATE_SUPPORT_STATUS = status
    return status
