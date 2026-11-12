"""Independent live XAG SHORT signal -> OANDA pilot execution.

Unlike the previous live mirror, this lane NEVER opens a practice position first.
The *same model candidate* and short-transition controls are evaluated, then
XAG-only live exposure and an XAU confirmation check (after ten live campaign
entries) govern one-unit live execution. No XAU strategy or XAG LONG changes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import metals_strategy_governance_override as governance
import xag_short_live_override as live

VERSION = "metals_xag_independent_live_short_v1_2026_11_12"
SIGNAL_MAX_AGE_MINUTES = 30
XAU_CONFIRMATION_LOOKBACK_HOURS = 6
SOLO_ALLOWANCE = 10
_INSTALLED = False


def _blocked(core: Any, signal_id: int, reason: str, details: Dict[str, Any] | None = None):
    out = {"ok": True, "blocked": True, "asset": "XAGUSD", "side": "short",
           "execution_lane": "LIVE_XAG_SHORT", "reason": reason,
           "governed_entry": True, "practice_order_placed": False}
    if details:
        out["details"] = details
    live._audit(core, signal_id, "entry_governance", "BLOCKED", reason, raw=details or {})
    return out


def _candidate(core: Any, row: Any) -> Dict[str, Any]:
    return governance._candidate_for_row(core, row)


def _fresh_signal(row: Any) -> bool:
    observed = governance._parse_dt(governance._safe_dict(row).get("received_at_utc"))
    return bool(observed and timedelta(0) <= datetime.now(timezone.utc) - observed <= timedelta(minutes=SIGNAL_MAX_AGE_MINUTES))


def _live_campaign_entries(core: Any, broker_open_count: int) -> Dict[str, Any]:
    """Live-only campaign ordinal, reset only on broker-confirmed flat.

    Existing live baskets with no recorded epoch use the full historical entry
    count conservatively until the broker first confirms flat.
    """
    with core.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metals_xag_live_campaign_state (
                id INTEGER PRIMARY KEY,
                started_at_utc TEXT,
                last_confirmed_flat_at_utc TEXT
            )
        """)
        row = conn.execute("SELECT started_at_utc FROM metals_xag_live_campaign_state WHERE id=1").fetchone()
        start = governance._safe_dict(row).get("started_at_utc")
        if broker_open_count == 0:
            # Each fresh flat signal resets the allowance until the next trade.
            start = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO metals_xag_live_campaign_state (id, started_at_utc, last_confirmed_flat_at_utc) "
                "VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "started_at_utc=excluded.started_at_utc,last_confirmed_flat_at_utc=excluded.last_confirmed_flat_at_utc",
                (start, start),
            )
            conn.commit()
        if start:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM metals_xag_live_trade_links WHERE created_at_utc>=?",
                (start,),
            ).fetchone()["n"]
        else:
            # Fail conservatively on first deployment with already-open trades.
            count = conn.execute("SELECT COUNT(*) AS n FROM metals_xag_live_trade_links").fetchone()["n"]
    return {"count": int(count), "epoch": start, "legacy_active_campaign": not bool(start) and broker_open_count > 0}


def _xau_short_confirmation(core: Any, signal_row: Any, side: str = "short") -> Dict[str, Any]:
    row = governance._safe_dict(signal_row)
    signal_time = governance._parse_dt(row.get("timestamp_readable"))
    if signal_time is None:
        return {"ok": False, "reason": "xag_signal_time_unparseable"}
    with core.get_conn() as conn:
        xs = conn.execute(
            "SELECT * FROM raw_signals WHERE UPPER(pair) IN ('XAUUSD','XAU') "
            "AND id<=? ORDER BY id DESC LIMIT 24", (int(row["id"]),)
        ).fetchall()
    for x in xs:
        d = governance._safe_dict(x)
        dt = governance._parse_dt(d.get("timestamp_readable"))
        if not dt:
            continue
        age = (signal_time - dt).total_seconds() / 3600.0
        if not 0 <= age <= XAU_CONFIRMATION_LOOKBACK_HOURS:
            continue
        c = _candidate(core, x)
        if c.get("demo_candidate") and str(c.get("demo_side") or "").lower() == side:
            return {"ok": True, "xau_signal_id": int(d["id"]), "age_hours": round(age, 3),
                    "note": "XAU signal qualifies even if XAU stacking prevented an OANDA entry"}
    return {"ok": False, "reason": "xag_requires_recent_same_direction_xau_confirmation"}


def _preflight(core: Any, row: Any, candidate: Dict[str, Any]) -> Dict[str, Any]:
    d = governance._safe_dict(row)
    rid = int(d.get("id") or 0)
    if not (_candidate(core, row).get("demo_candidate") and candidate.get("demo_candidate")
            and str(candidate.get("demo_side") or "").lower() == "short"):
        return _blocked(core, rid, "model_xag_short_candidate_not_confirmed")
    if not _fresh_signal(row):
        return _blocked(core, rid, "live_xag_signal_stale_or_missing_observed_time")
    cfg = live.config_status(core)
    if not cfg.get("orders_allowed"):
        return _blocked(core, rid, "xag_live_not_armed")
    trans = governance._short_transition(core, "XAGUSD", rid)
    if not trans.get("ok"):
        return _blocked(core, rid, trans.get("reason") or "short_transition_history_unavailable")
    if trans.get("cooldown_active"):
        return _blocked(core, rid, "short_flip_reconfirmation_cooldown",
                        {"remaining_hours": trans.get("remaining_hours")})
    # Reconciliation is authoritative; all mismatched positions remain blocked.
    rec = live._reconcile(core)
    if not rec.get("ok") or not rec.get("exact_live_ownership"):
        return _blocked(core, rid, "xag_live_ownership_reconciliation_blocked",
                        {"reason": rec.get("reason"), "unlinked_count": rec.get("unlinked_broker_xag_count")})
    count = int(rec.get("broker_xag_open_count") or 0)
    if rec.get("has_broker_long"):
        return _blocked(core, rid, "xag_broker_opposite_long_present")
    if count >= live.MAX_OPEN:
        return _blocked(core, rid, "live_xag_short_cap_24", {"open_count": count})
    campaign = _live_campaign_entries(core, count)
    if campaign["count"] >= SOLO_ALLOWANCE:
        confirm = _xau_short_confirmation(core, row)
        if not confirm.get("ok"):
            return _blocked(core, rid, "xag_requires_recent_same_direction_xau_confirmation",
                            {"campaign_count": campaign["count"], "reason": confirm.get("reason")})
    else:
        confirm = {"ok": True, "reason": "xag_live_independent_allowance"}
    return {
        "ok": True, "approved_for_live": True, "asset": "XAGUSD", "side": "short",
        "approved_model": candidate, "approval_version": VERSION,
        "active_exit_policy": {"policy": live.POLICY, "version": live.POLICY_VERSION},
        "live_campaign_entry_count_before": campaign["count"],
        "xau_confirmation": confirm,
        "practice_order_placed": False,
    }


def health(core: Any) -> Dict[str, Any]:
    """Read-only health for dashboard and Portfolio Hub, no broker orders."""
    cfg = live.runtime_status(core)
    snapshot = live._open_trades(core)
    with core.get_conn() as conn:
        links = [dict(r) for r in conn.execute(
            "SELECT broker_trade_id,status,side FROM metals_xag_live_trade_links "
            "WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
        ).fetchall()]
        latest = conn.execute(
            "SELECT created_at_utc,action,status,reason FROM metals_xag_live_execution_audit "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        intents = conn.execute(
            "SELECT COUNT(*) AS n FROM metals_xag_live_entry_intents "
            "WHERE status IN ('SUBMITTING','UNKNOWN_NEEDS_RECONCILIATION','OPEN_UNVERIFIED_STOP')"
        ).fetchone()["n"]
    broker = list(snapshot.get("xag") or []) if snapshot.get("ok") else []
    bids = {str(t.get("id")) for t in broker}
    lids = {str(t.get("broker_trade_id")) for t in links if t.get("broker_trade_id")}
    safe = bool(snapshot.get("ok") and bids == lids and len(bids) == len(links) and not intents)
    return {
        "ok": bool(snapshot.get("ok")), "version": VERSION,
        "orders_allowed": bool(cfg.get("orders_allowed")),
        "execution_health": ("READY" if safe and cfg.get("orders_allowed") else
                             "RECONCILIATION_BLOCKED" if not safe else "DISARMED"),
        "broker_open_short_count": sum(float(t.get("currentUnits") or 0) < 0 for t in broker),
        "broker_open_long_count": sum(float(t.get("currentUnits") or 0) > 0 for t in broker),
        "tracked_open_count": len(links),
        "untracked_broker_count": len(bids-lids),
        "orphan_local_count": len(lids-bids),
        "unresolved_entry_intent_count": int(intents),
        "recent_execution": governance._safe_dict(latest),
        "practice_basket_required": False,
        "one_unit_pilot": True,
        "read_only": True,
    }


def install(core: Any, app: Any) -> Dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"installed": True, "version": VERSION}
    old_router = core.execute_metals_demo_candidate

    def independent_router(raw_signal_id: int, source: str = "signal_worker",
                           *args: Any, **kwargs: Any) -> Dict[str, Any]:
        rid = int(raw_signal_id)
        try:
            with core.get_conn() as conn:
                row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (rid,)).fetchone()
            d = governance._safe_dict(row)
            if core._metals_demo_asset(d.get("pair")) != "XAGUSD":
                return old_router(rid, source=source, *args, **kwargs)
            candidate = _candidate(core, row)
            if not candidate.get("demo_candidate") or str(candidate.get("demo_side") or "").lower() != "short":
                # Preserve research/non-candidate diagnostics, with no live order.
                return old_router(rid, source=source, *args, **kwargs)
            # Non-idempotent order submission is serialized; persistent order
            # intents also protect against multiple web/worker processes.
            with live._ENTRY_LOCK:
                approval = _preflight(core, row, candidate)
                if not approval.get("approved_for_live"):
                    out = approval
                else:
                    out = live._execute_live(core, rid, approval)
            try:
                mgr = live._manager_tick(core, rid)
            except Exception as exc:
                mgr = {"ok": False, "reason": f"manager_tick_failed:{type(exc).__name__}"}
            out = dict(out)
            out["xag_short_live"] = dict(out)
            out["xag_short_live_manager"] = mgr
            out["practice_entry_skipped"] = True
            return out
        except Exception as exc:
            live._audit(core, rid, "entry_governance", "EXCEPTION",
                        f"independent_live_route_failed:{type(exc).__name__}", raw={"source":source})
            return {"ok": False, "blocked": True, "asset": "XAGUSD",
                    "reason": f"independent_xag_fail_closed:{type(exc).__name__}"}

    core.execute_metals_demo_candidate = independent_router
    core.xag_short_live_health = lambda: health(core)
    app.add_api_route("/api/xag-short-live-health", lambda: health(core), methods=["GET"], include_in_schema=False)
    target = next((r for r in reversed(app.router.routes)
                   if getattr(r, "path", "") == "/api/xag-short-live-health"), None)
    if target is None:
        raise RuntimeError("XAG live health route missing")
    app.router.routes.remove(target)
    app.router.routes.insert(0, target)
    _INSTALLED = True
    try:
        recovered = live._reconcile(core)
    except Exception as exc:
        recovered = {"ok": False, "reason": f"startup_reconcile_error:{type(exc).__name__}"}
    print("XAG_LIVE_RECOVERY " + json.dumps({k:v for k,v in recovered.items()
          if k not in ("unlinked_broker_xag_ids",)}, default=str), flush=True)
    return {
        "installed": True, "version": VERSION, "entry_scope": "XAG_SHORT_ONLY",
        "practice_orders_for_xag_short": False,
        "broker_authoritative_reconcile": True, "broker_writes_during_install": False,
        "model_candidate_preserved": True, "four_hour_transition_preserved": True,
        "after_ten_entries_xau_6h_confirmation": True,
        "max_open": live.MAX_OPEN,
        "startup_reconcile": recovered.get("ok"),
        "startup_recovered_historical": recovered.get("recovered_historical_live_count", 0),
    }


DASHBOARD_SNIPPET = r"""
<details id="pep-xag-live-health" class="dashboard-group" style="margin:10px 0">
 <summary>LIVE XAG Short — broker &amp; execution health</summary>
 <div id="pep-xag-live-health-info" class="section-note">Checking live XAG positions…</div>
 <div class="section-note">Independent live one-unit pilot · XAU Short +2R does not block XAG · practice basket not used for new XAG Short orders.</div>
</details>
<script>
(function(){
 const d=document.getElementById('pep-xag-live-health'),b=document.getElementById('pep-xag-live-health-info');
 if(!d||!b||d.dataset.wired)return;d.dataset.wired='1';
 async function load(){
 try{
 const r=await fetch('/api/xag-short-live-health',{cache:'no-store'}),x=await r.json();
 if(!r.ok||!x.ok)throw Error(x.error||'Broker read unavailable');
 const last=x.recent_execution||{};
 b.textContent='Execution: '+x.execution_health+' | Broker shorts: '+x.broker_open_short_count+
  ' | Tracked: '+x.tracked_open_count+' | Unlinked: '+x.untracked_broker_count+
  ' | Unresolved orders: '+x.unresolved_entry_intent_count+
  '. Last audit: '+(last.action||'—')+' / '+(last.status||'—')+' / '+(last.reason||'—');
 }catch(e){b.textContent='XAG health unavailable: '+(e.message||e);}
 }
 d.addEventListener('toggle',()=>{if(d.open)load()});
})();
</script>
""";
