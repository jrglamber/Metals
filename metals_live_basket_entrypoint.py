"""Production Metals entrypoint with strict LIVE XAU basket protection separation.

- Installs the explicit long+short live basket-manager adapter.
- Builds the LIVE basket/profit-protection panel only from live XAU state.
- Moves legacy practice/demo basket-manager widgets into demo/research.
- Supports an idempotent one-shot live protection-cycle rebase via Railway token.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import metals_dashboard_clean_entrypoint as base
import xau_live_basket_manager_override

analysis = base.analysis
core = base.core
LIVE_XAU_BASKET_MANAGER_STATUS = xau_live_basket_manager_override.install(core)

RESET_TOKEN_ENV = "METALS_XAU_LIVE_PROTECTION_RESET_TOKEN"
RESET_MARKER_KEY = "live_dashboard_protection_reset_token"
RESET_AT_KEY = "live_dashboard_protection_reset_at"
RESET_SOURCE = "manual_live_xau_dashboard_rebase"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sf(v: Any, default: float = 0.0) -> float:
    try:
        x = core.safe_float(v)
        return float(x) if x is not None else default
    except Exception:
        try:
            return float(v)
        except Exception:
            return default


def _ss(v: Any) -> str:
    try:
        return core.safe_str(v)
    except Exception:
        return "" if v is None else str(v)


def _jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    try:
        return v.isoformat()
    except Exception:
        return str(v)


def _rows(conn: Any, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return [{k: _jsonable(v) for k, v in dict(r).items()} for r in rows]
    except Exception:
        return []


def _live_reset_token_applied(token: str) -> bool:
    if not token:
        return True
    try:
        with core.get_conn() as conn:
            return _ss(core._metals_xau_live_runtime_get(conn, RESET_MARKER_KEY, "")) == token
    except Exception:
        return False


def _reset_live_xau_protection_cycle(token: str) -> Dict[str, Any]:
    """Rebase only the LIVE-XAU HWM/protection cycle to the current live basket.

    No OANDA order is sent and no trade is changed. Historical rows are preserved.
    The reset fails closed unless every owned live XAU trade reconciles exactly.
    """
    if not token:
        return {"ok": True, "skipped": True, "reason": "no_reset_token"}
    if _live_reset_token_applied(token):
        return {"ok": True, "skipped": True, "reason": "token_already_applied", "token": token}

    broker = core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
    if not broker.get("ok"):
        return {"ok": False, "blocked": True, "reason": "live_broker_snapshot_unavailable"}
    if broker.get("ownership_conflict"):
        return {"ok": False, "blocked": True, "reason": "live_xau_ownership_conflict"}

    r_state = core._metals_xau_live_basket_r(broker) or {}
    open_count = int(broker.get("owned_open_count") or 0)
    linked_count = int(r_state.get("linked_count") or 0)
    unlinked_count = int(r_state.get("unlinked_broker_count") or 0)
    if unlinked_count or linked_count != open_count:
        return {
            "ok": False, "blocked": True, "reason": "live_xau_reconciliation_not_exact",
            "open_count": open_count, "linked_count": linked_count,
            "unlinked_count": unlinked_count,
        }

    current_gbp = _sf(broker.get("owned_unrealized_pl"))
    current_r = _sf(r_state.get("basket_r"))
    new_hwm_gbp = max(0.0, current_gbp)
    new_hwm_r = max(0.0, current_r)
    observed = _now()

    with core.get_conn() as conn:
        previous = {
            "open_count": int(_sf(core._metals_xau_live_runtime_get(conn, "broker_hwm_open_count", "0"))),
            "high_water_gbp": _sf(core._metals_xau_live_runtime_get(conn, "broker_hwm_gbp", "0")),
            "high_water_r": _sf(core._metals_xau_live_runtime_get(conn, "broker_hwm_r", "0")),
            "seen_at": _ss(core._metals_xau_live_runtime_get(conn, "broker_hwm_seen_at", "")),
            "active_harvest_cycle_id": _ss(core._metals_xau_live_runtime_get(conn, "active_harvest_cycle_id", "")),
        }
        old_cycle = previous["active_harvest_cycle_id"]
        if old_cycle:
            try:
                conn.execute(
                    """UPDATE metals_xau_live_harvest_stages
                       SET updated_at_utc=?, status='EXPIRED_RESET',
                           reason=COALESCE(NULLIF(reason,''),'superseded by live XAU protection reset')
                       WHERE cycle_id=? AND status NOT IN ('EXECUTED','EXPIRED_FLAT','EXPIRED_RESET')""",
                    (observed, old_cycle),
                )
            except Exception:
                pass

        for key, value in (
            ("broker_hwm_open_count", open_count),
            ("broker_hwm_gbp", new_hwm_gbp),
            ("broker_hwm_r", new_hwm_r),
            ("broker_hwm_seen_at", observed if (new_hwm_gbp > 0 or new_hwm_r > 0) else ""),
            ("active_harvest_cycle_id", ""),
            ("harvest_last_seen_hwm_r", 0.0),
            (RESET_MARKER_KEY, token),
            (RESET_AT_KEY, observed),
        ):
            core._metals_xau_live_runtime_set(conn, key, value)

        try:
            conn.execute(
                """INSERT INTO metals_xau_live_hwm_events(
                       created_at_utc, observed_at_utc, event_type,
                       open_count, current_gbp, current_r,
                       high_water_gbp, high_water_r, source, raw_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    observed, observed, "MANUAL_RESET", open_count,
                    current_gbp, current_r, new_hwm_gbp, new_hwm_r,
                    RESET_SOURCE,
                    json.dumps({
                        "previous": previous,
                        "new_baseline": {
                            "open_count": open_count, "current_gbp": current_gbp,
                            "current_r": current_r, "high_water_gbp": new_hwm_gbp,
                            "high_water_r": new_hwm_r,
                        },
                        "token": token, "no_broker_orders": True,
                        "trades_unchanged": True, "history_preserved": True,
                    }, default=str),
                ),
            )
        except Exception:
            pass
        conn.commit()

    hwm = core._metals_xau_live_highwater_state(broker) or {}
    try:
        with core.get_conn() as conn:
            new_cycle = core._metals_xau_live_harvest_cycle_id(conn, hwm)
    except Exception:
        new_cycle = ""

    try:
        core.log_system_event(
            "metals_xau_live_protection_reset",
            "LIVE XAU basket HWM/protection cycle rebased",
            {"previous": previous, "new_hwm": hwm, "new_cycle": new_cycle, "token": token},
        )
    except Exception:
        pass

    return {
        "ok": True, "reset": True, "token": token, "reset_at": observed,
        "previous": previous, "current_gbp": current_gbp, "current_r": current_r,
        "high_water_gbp": new_hwm_gbp, "high_water_r": new_hwm_r,
        "new_cycle_id": new_cycle, "open_count": open_count,
        "trades_unchanged": True, "history_preserved": True,
    }


_LIVE_RESET_RESULT: Dict[str, Any] = {"ok": True, "skipped": True, "reason": "not_requested"}
_reset_token = os.getenv(RESET_TOKEN_ENV, "").strip()
if _reset_token:
    try:
        _LIVE_RESET_RESULT = _reset_live_xau_protection_cycle(_reset_token)
    except Exception as exc:
        _LIVE_RESET_RESULT = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@core.app.get("/api/live-xau-basket-manager")
def live_xau_basket_manager() -> Dict[str, Any]:
    try:
        broker = core.metals_xau_live_broker_snapshot(include_account_summary=False) or {}
        hwm = core._metals_xau_live_highwater_state(broker) or {}
        cfg = core.metals_xau_live_config_status() or {}
        trades = list(broker.get("owned_open_trades") or [])
        longs = sum(1 for t in trades if _sf(t.get("currentUnits")) > 0)
        shorts = sum(1 for t in trades if _sf(t.get("currentUnits")) < 0)
        with core.get_conn() as conn:
            cycle_id = _ss(core._metals_xau_live_runtime_get(conn, "active_harvest_cycle_id", ""))
            reset_at = _ss(core._metals_xau_live_runtime_get(conn, RESET_AT_KEY, ""))
            stages = _rows(conn, "SELECT * FROM metals_xau_live_harvest_stages ORDER BY id DESC LIMIT 12")
            reviews = _rows(conn, "SELECT * FROM metals_xau_live_manager_reviews ORDER BY id DESC LIMIT 8")
            harvest_events = _rows(conn, "SELECT * FROM metals_xau_live_harvest_events ORDER BY id DESC LIMIT 8")
        return {
            "ok": bool(broker.get("ok", True)), "scope": "LIVE_XAU_ONLY",
            "account_environment": cfg.get("environment"),
            "manager_enabled": bool(cfg.get("live_basket_manager_enabled", getattr(core, "METALS_XAU_LIVE_MANAGER_ENABLED", True))),
            "profit_protection_execution_enabled": bool(cfg.get("live_harvest_execution_enabled", getattr(core, "METALS_XAU_LIVE_HARVEST_EXECUTION_ENABLED", True))),
            "orders_allowed": bool(cfg.get("orders_allowed")),
            "open_count": len(trades), "long_count": longs, "short_count": shorts,
            "current_gbp": _sf(hwm.get("current_gbp")), "current_r": _sf(hwm.get("current_r")),
            "high_water_gbp": _sf(hwm.get("high_water_gbp")), "high_water_r": _sf(hwm.get("high_water_r")),
            "giveback_gbp": _sf(hwm.get("giveback_gbp")), "giveback_r": _sf(hwm.get("giveback_r")),
            "giveback_pct": _sf(hwm.get("giveback_pct")), "high_water_seen_at": hwm.get("high_water_seen_at"),
            "reconciliation": {
                "linked_r_count": hwm.get("linked_r_count"),
                "unlinked_broker_count": hwm.get("unlinked_broker_count"),
                "ownership_conflict": bool(hwm.get("ownership_conflict") or broker.get("ownership_conflict")),
            },
            "active_cycle_id": cycle_id, "reset_at": reset_at,
            "startup_reset_result": _LIVE_RESET_RESULT,
            "recent_protection_stages": stages,
            "recent_manager_reviews": reviews,
            "recent_harvest_events": harvest_events,
            "time_utc": _now(),
        }
    except Exception as exc:
        return {"ok": False, "scope": "LIVE_XAU_ONLY", "error": f"{type(exc).__name__}: {exc}", "time_utc": _now()}


_base_rewrite = analysis._rewrite_dashboard_version

_LIVE_BASKET_LAYOUT = r'''
<style id="pep-live-basket-manager-style">
  #pep-live-basket-manager { margin:12px 0; }
  #pep-live-basket-manager .pep-body { padding:0 14px 14px; }
  #pep-live-basket-manager .pep-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:9px; margin:10px 0; }
  #pep-live-basket-manager .pep-metric { border:1px solid rgba(255,255,255,.11); border-radius:9px; padding:9px 10px; }
  #pep-live-basket-manager .pep-k { opacity:.7; font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; }
  #pep-live-basket-manager .pep-v { font-size:1.05rem; font-weight:760; margin-top:3px; }
  #pep-live-basket-manager table { width:100%; border-collapse:collapse; font-size:.84rem; margin-top:8px; }
  #pep-live-basket-manager th, #pep-live-basket-manager td { padding:6px 7px; border-bottom:1px solid rgba(255,255,255,.08); text-align:left; }
  #pep-live-basket-manager .pep-ok { opacity:.82; margin:7px 0; }
  #pep-live-basket-manager .pep-warn { font-weight:700; }
  #pep-demo-basket-manager-wrap { margin-top:12px; }
</style>
<script id="pep-live-basket-manager-script">
(function(){
  function norm(s){ return (s||'').replace(/\s+/g,' ').trim(); }
  function money(x){ var n=Number(x||0); return (n<0?'-':'')+'£'+Math.abs(n).toFixed(2); }
  function num(x,d){ var n=Number(x||0); return n.toFixed(d==null?2:d); }
  function boolWord(x){ return x?'ON':'OFF'; }
  function metric(k,v){ return '<div class="pep-metric"><div class="pep-k">'+k+'</div><div class="pep-v">'+v+'</div></div>'; }
  function findLiveOpen(){ return document.getElementById('pep-live-open-positions'); }
  function findCombined(){ return document.getElementById('pep-broker-accounting-combined'); }

  function ensureLivePanel(){
    var d=document.getElementById('pep-live-basket-manager');
    if(d) return d;
    d=document.createElement('details'); d.id='pep-live-basket-manager';
    d.innerHTML='<summary>Basket Manager / Profit Protection — LIVE XAU</summary><div class="pep-body"><div class="pep-ok">Loading live XAU protection state…</div></div>';
    var anchor=findLiveOpen();
    if(anchor && anchor.parentNode) anchor.parentNode.insertBefore(d,anchor.nextSibling); else document.body.appendChild(d);
    d.open=false; return d;
  }

  function renderLive(){
    var d=ensureLivePanel(), body=d.querySelector('.pep-body');
    fetch('/api/live-xau-basket-manager',{cache:'no-store'}).then(function(r){return r.json();}).then(function(x){
      if(!x || !x.ok){ body.innerHTML='<div class="pep-warn">Live XAU basket-manager state unavailable.</div>'; return; }
      var html='<div class="pep-ok"><strong>Source:</strong> live OANDA XAU positions only.</div><div class="pep-grid">';
      html+=metric('Open XAU',x.open_count); html+=metric('Long / Short',x.long_count+' / '+x.short_count);
      html+=metric('Open P/L',money(x.current_gbp)); html+=metric('Current R',num(x.current_r,2)+'R');
      html+=metric('High Water',money(x.high_water_gbp)); html+=metric('HWM R',num(x.high_water_r,2)+'R');
      html+=metric('Giveback',money(x.giveback_gbp)+' · '+num(x.giveback_pct,1)+'%');
      html+=metric('Manager',boolWord(x.manager_enabled)); html+=metric('Profit Protection',boolWord(x.profit_protection_execution_enabled));
      html+='</div><table><tbody>';
      html+='<tr><th>Active live cycle</th><td>'+(x.active_cycle_id||'—')+'</td></tr>';
      html+='<tr><th>HWM seen</th><td>'+(x.high_water_seen_at||'—')+'</td></tr>';
      html+='<tr><th>Protection reset</th><td>'+(x.reset_at||'—')+'</td></tr>';
      html+='<tr><th>Reconciliation</th><td>linked '+((x.reconciliation&&x.reconciliation.linked_r_count)||0)+' · unlinked '+((x.reconciliation&&x.reconciliation.unlinked_broker_count)||0)+' · ownership conflict '+(((x.reconciliation&&x.reconciliation.ownership_conflict))?'YES':'NO')+'</td></tr>';
      html+='</tbody></table>';
      var stages=(x.recent_protection_stages||[]).slice(0,6);
      if(stages.length){
        html+='<table><thead><tr><th>Protection stage</th><th>Status</th><th>Bank fraction</th><th>Updated</th></tr></thead><tbody>';
        stages.forEach(function(s){ html+='<tr><td>'+((s.threshold_r==null?'—':s.threshold_r+'R'))+'</td><td>'+(s.status||'')+'</td><td>'+((s.bank_fraction==null?'—':Math.round(Number(s.bank_fraction)*100)+'%'))+'</td><td>'+(s.updated_at_utc||'')+'</td></tr>'; });
        html+='</tbody></table>';
      }
      body.innerHTML=html;
    }).catch(function(){ body.innerHTML='<div class="pep-warn">Live XAU basket-manager state unavailable.</div>'; });
  }

  function moveLegacyPracticeBasketWidgets(){
    var combined=findCombined(); if(!combined) return false;
    var nested=combined.querySelectorAll('.pep-nested'); if(!nested.length) return false;
    var demo=nested[nested.length-1], demoBody=demo.querySelector('.pep-nested-body')||demo;
    var wrap=document.getElementById('pep-demo-basket-manager-wrap');
    if(!wrap){
      wrap=document.createElement('details'); wrap.id='pep-demo-basket-manager-wrap';
      wrap.innerHTML='<summary>Basket Manager / Profit Protection — DEMO / RESEARCH</summary><div class="pep-demo-basket-body"></div>';
      demoBody.appendChild(wrap); wrap.open=false;
    }
    var target=wrap.querySelector('.pep-demo-basket-body');
    Array.prototype.slice.call(document.querySelectorAll('details > summary')).forEach(function(s){
      var t=norm(s.textContent).toLowerCase(), owner=s.parentElement;
      if(!owner || owner.id==='pep-live-basket-manager' || owner.id==='pep-demo-basket-manager-wrap') return;
      if(t.indexOf('basket manager')!==-1 || t.indexOf('profit protection')!==-1 || t.indexOf('basket-manager')!==-1){ target.appendChild(owner); owner.open=false; }
    });
    return true;
  }

  function apply(){ renderLive(); moveLegacyPracticeBasketWidgets(); }
  var attempts=0, timer=setInterval(function(){ attempts++; apply(); if(attempts>40) clearInterval(timer); },300);
  apply();
})();
</script>
'''


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        if "pep-live-basket-manager-script" not in text:
            if "</body>" in text:
                text = text.replace("</body>", _LIVE_BASKET_LAYOUT + "\n</body>", 1)
            else:
                text += _LIVE_BASKET_LAYOUT
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
