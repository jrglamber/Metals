"""Adaptive XAU stacking governance for Project Exit Plan Metals.

Live-XAU strategy promoted 2026-10-14 after manager-aware replay:
- trades 1..20: normal qualifying entries;
- trade 21 onward: same-side campaign equity must be >= +2R;
- campaign equity = open basket R + R already banked by harvest in this cycle;
- trades 21..32: full normal size;
- trade 33 onward: half normal size;
- no strategic hard cap; retain only a high operational fail-safe ceiling;
- harvesting stays unchanged: 50R/100R/150R+ at 20%/20%/25%, every 50R;
- 4h SHORT transition cooldown, XAU direction governance and ATR2/MFE25 exits stay authoritative.

This installs *after* metals_strategy_governance_override. It supersedes only the
live-XAU hard-24 stacking cap. XAG practice caps and family-cap governance remain
untouched.
"""
from __future__ import annotations

import contextvars
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

VERSION = "metals_xau_adaptive_stacking_v1_2026_10_14"
NORMAL_ALLOWANCE = 20
FULL_SIZE_THROUGH_ORDINAL = 32
OVERFLOW_MIN_CAMPAIGN_R = 2.0
OVERFLOW_SIZE_MULTIPLIER = 0.50
OPERATIONAL_FAILSAFE_OPEN_TRADES = 200

_ENTRY_SIZE_MULTIPLIER = contextvars.ContextVar("metals_xau_entry_size_multiplier", default=1.0)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _safe_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_cycle_start(cycle_id: str) -> Optional[datetime]:
    text = str(cycle_id or "").strip()
    m = re.search(r"(\d{8}T\d{6})(\d{1,6})?Z$", text)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        frac = m.group(2) or ""
        if frac:
            dt = dt.replace(microsecond=int(frac.ljust(6, "0")))
        return dt
    except Exception:
        return None


def _parse_ids(value: Any) -> List[int]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple, set)):
        out = []
        for x in value:
            try:
                out.append(int(x))
            except Exception:
                pass
        return out
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return _parse_ids(parsed)
        if isinstance(parsed, (int, float)):
            return [int(parsed)]
    except Exception:
        pass
    out: List[int] = []
    for token in re.split(r"[,;\s]+", text.strip("[](){}")):
        token = token.strip("'\" ")
        if not token:
            continue
        try:
            out.append(int(float(token)))
        except Exception:
            pass
    return out


def _closure_value(fn: Any, name: str) -> Any:
    try:
        names = list(fn.__code__.co_freevars or ())
        cells = list(fn.__closure__ or ())
        return dict(zip(names, [c.cell_contents for c in cells])).get(name)
    except Exception:
        return None


def _candidate(core: Any, raw_signal_id: int) -> Dict[str, Any]:
    with core.get_conn() as conn:
        row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
    if not row:
        return {"ok": False, "reason": "raw_signal_missing"}
    d = _safe_dict(row)
    asset = core._metals_demo_asset(d.get("pair"))
    try:
        raw = core._raw_signal_json(row)
    except Exception:
        raw = core._raw_signal_json(d)
    cand = dict(core.metals_demo_candidate_for_row(raw, row) or {})
    side = core._metals_demo_side(cand.get("demo_side"))
    return {"ok": True, "row": d, "candidate": cand, "asset": asset, "side": side}


def _is_live_xau_candidate(core: Any, c: Dict[str, Any]) -> bool:
    if not c.get("ok") or c.get("asset") != "XAUUSD":
        return False
    cand = c.get("candidate") or {}
    if not cand.get("demo_candidate"):
        return False
    side = c.get("side")
    if side == "long":
        return bool(getattr(core, "METALS_XAU_LONG_LIVE_PROMOTION_ENABLED", False))
    if side == "short":
        return bool(getattr(core, "_metals_xau_short_live_gate_enabled", lambda: False)())
    return False


def _active_cycle(core: Any) -> Tuple[str, Optional[datetime]]:
    getter = getattr(core, "_metals_xau_live_runtime_get", None)
    if not callable(getter):
        return "", None
    try:
        with core.get_conn() as conn:
            cycle = str(getter(conn, "active_harvest_cycle_id", "") or "")
        return cycle, _parse_cycle_start(cycle)
    except Exception:
        return "", None


def _open_links(core: Any) -> List[Dict[str, Any]]:
    with core.get_conn() as conn:
        return [_safe_dict(r) for r in conn.execute(
            "SELECT * FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id"
        ).fetchall()]


def _campaign_entry_count(core: Any, side: str, open_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    cycle_id, start = _active_cycle(core)
    if start is None:
        n = sum(1 for r in open_rows if str(r.get("side") or "").lower() == side)
        return {"count": n, "cycle_id": cycle_id, "cycle_start": None, "exact": False}
    with core.get_conn() as conn:
        rows = [_safe_dict(r) for r in conn.execute(
            "SELECT id, created_at_utc, side FROM metals_xau_live_trade_links WHERE LOWER(side)=? ORDER BY id",
            (side,),
        ).fetchall()]
    count = 0
    for r in rows:
        dt = _parse_dt(r.get("created_at_utc"))
        if dt is not None and dt >= start:
            count += 1
    return {"count": count, "cycle_id": cycle_id, "cycle_start": start.isoformat(), "exact": True}


def _risk_amount(row: Dict[str, Any]) -> Optional[float]:
    for key in ("estimated_risk_amount", "requested_risk_amount", "estimated_risk_gbp", "requested_risk_gbp", "risk_amount"):
        v = _safe_float(row.get(key))
        if v is not None and v > 0:
            return v
    return None


def _banked_harvest_r(core: Any, cycle_id: str) -> Dict[str, Any]:
    if not cycle_id:
        return {"r": 0.0, "executed_stages": 0, "link_ids": [], "exact": True}
    try:
        with core.get_conn() as conn:
            stages = [_safe_dict(r) for r in conn.execute(
                "SELECT * FROM metals_xau_live_harvest_stages WHERE cycle_id=? AND UPPER(status)='EXECUTED' ORDER BY id",
                (cycle_id,),
            ).fetchall()]
    except Exception as exc:
        return {"r": None, "exact": False, "reason": f"harvest_stage_read_failed:{type(exc).__name__}"}
    if not stages:
        return {"r": 0.0, "executed_stages": 0, "link_ids": [], "exact": True}

    link_ids: List[int] = []
    for stage in stages:
        link_ids.extend(_parse_ids(stage.get("selected_link_ids")))
    link_ids = sorted(set(link_ids))
    if not link_ids:
        try:
            with core.get_conn() as conn:
                events = [_safe_dict(r) for r in conn.execute(
                    "SELECT * FROM metals_xau_live_harvest_events WHERE cycle_id=? AND UPPER(status)='CLOSED' ORDER BY id",
                    (cycle_id,),
                ).fetchall()]
            link_ids = sorted(set(int(e.get("link_id")) for e in events if e.get("link_id") is not None))
        except Exception:
            pass
    if not link_ids:
        return {"r": None, "exact": False, "executed_stages": len(stages), "reason": "executed_harvest_has_no_reconstructable_link_ids"}

    total_r = 0.0
    missing: List[int] = []
    with core.get_conn() as conn:
        for lid in link_ids:
            row = conn.execute("SELECT * FROM metals_xau_live_trade_links WHERE id=? LIMIT 1", (int(lid),)).fetchone()
            d = _safe_dict(row)
            realized = _safe_float(d.get("realized_pl"))
            risk = _risk_amount(d)
            if realized is None or risk is None or risk <= 0:
                missing.append(int(lid))
                continue
            total_r += realized / risk
    if missing:
        return {"r": None, "exact": False, "executed_stages": len(stages), "link_ids": link_ids, "missing_link_metrics": missing, "reason": "harvest_r_reconstruction_incomplete"}
    return {"r": total_r, "executed_stages": len(stages), "link_ids": link_ids, "exact": True}


def _open_basket_r(core: Any, side: str, open_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    same = [r for r in open_rows if str(r.get("side") or "").lower() == side]
    if not same:
        return {"r": 0.0, "count": 0, "exact": True}
    broker = core.metals_xau_live_broker_snapshot()
    if not broker.get("ok"):
        return {"r": None, "count": len(same), "exact": False, "reason": "live_broker_snapshot_failed"}
    by_id = {str(_safe_dict(t).get("id")): _safe_dict(t) for t in list(broker.get("owned_open_trades") or [])}
    total = 0.0
    missing: List[str] = []
    for link in same:
        bid = str(link.get("broker_trade_id") or "")
        bt = by_id.get(bid)
        if not bt:
            missing.append(bid or f"link:{link.get('id')}")
            continue
        try:
            metrics = dict(core._metals_xau_live_trade_metrics(link, bt) or {})
        except Exception:
            missing.append(bid or f"link:{link.get('id')}")
            continue
        r = _safe_float(metrics.get("current_r"))
        if r is None:
            missing.append(bid or f"link:{link.get('id')}")
            continue
        total += r
    if missing:
        return {"r": None, "count": len(same), "exact": False, "missing": missing, "reason": "open_basket_r_incomplete"}
    return {"r": total, "count": len(same), "exact": True}


def _campaign_funding(core: Any, side: str, open_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    open_r = _open_basket_r(core, side, open_rows)
    cycle_id, _ = _active_cycle(core)
    banked = _banked_harvest_r(core, cycle_id)
    if not open_r.get("exact") or not banked.get("exact"):
        return {"ok": False, "open": open_r, "banked_harvest": banked, "cycle_id": cycle_id, "reason": "campaign_funding_not_exact"}
    total = float(open_r.get("r") or 0.0) + float(banked.get("r") or 0.0)
    return {"ok": True, "campaign_r": total, "open_r": float(open_r.get("r") or 0.0), "banked_harvest_r": float(banked.get("r") or 0.0), "open_count": int(open_r.get("count") or 0), "cycle_id": cycle_id, "executed_harvest_stages": int(banked.get("executed_stages") or 0)}


def _audit_block(core: Any, raw_signal_id: int, reason: str, response: Dict[str, Any]) -> None:
    try:
        core._metals_demo_audit(int(raw_signal_id), "XAUUSD", core._metals_demo_instrument("XAUUSD"), "adaptive_stacking_governance", "BLOCKED", reason, response=response, candidate_state="CANDIDATE")
    except Exception:
        pass


def install(core: Any, app: Any) -> Dict[str, Any]:
    import metals_strategy_governance_override as governance

    governed_router = core.execute_metals_demo_candidate
    pre_cap_router = _closure_value(governed_router, "original_router")
    if not callable(pre_cap_router):
        raise RuntimeError("Adaptive stacking install refused: pre-cap split router not recoverable")
    original_side_preview = getattr(core, "_metals_xau_side_sizing_preview", None)
    if not callable(original_side_preview):
        raise RuntimeError("Adaptive stacking install refused: side-aware XAU sizing preview missing")

    core.METALS_XAU_LIVE_MAX_OPEN_TRADES = OPERATIONAL_FAILSAFE_OPEN_TRADES
    core.METALS_XAU_ADAPTIVE_STACKING_VERSION = VERSION

    def adaptive_sizing_preview(side: str) -> Dict[str, Any]:
        p = dict(original_side_preview(side) or {})
        mult = float(_ENTRY_SIZE_MULTIPLIER.get())
        if abs(mult - 1.0) < 1e-12:
            p["adaptive_stack_size_multiplier"] = 1.0
            return p
        if mult <= 0 or mult > 1:
            reasons = list(p.get("blocking_reasons") or [])
            reasons.append("adaptive_stack_invalid_size_multiplier")
            p["blocking_reasons"] = reasons
            p["executable"] = False
            return p
        for key in ("units", "requested_units", "estimated_risk_gbp", "estimated_risk_amount", "risk_amount", "requested_risk_gbp", "requested_risk_amount"):
            v = _safe_float(p.get(key))
            if v is not None:
                p[key] = v * mult
        p["adaptive_stack_size_multiplier"] = mult
        p["adaptive_stack_half_risk"] = mult == OVERFLOW_SIZE_MULTIPLIER
        return p

    core._metals_xau_side_sizing_preview = adaptive_sizing_preview

    def adaptive_router(raw_signal_id: int, source: str = "signal_worker") -> Dict[str, Any]:
        core.init_db()
        try:
            c = _candidate(core, int(raw_signal_id))
        except Exception:
            return governed_router(int(raw_signal_id), source=source)
        if not _is_live_xau_candidate(core, c):
            return governed_router(int(raw_signal_id), source=source)
        side = str(c.get("side") or "").lower()

        try:
            with core.get_conn() as conn:
                existing = conn.execute("SELECT id FROM metals_xau_live_trade_links WHERE raw_signal_id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
            if existing:
                return governed_router(int(raw_signal_id), source=source)
        except Exception:
            pass

        open_rows = _open_links(core)
        open_same = [r for r in open_rows if str(r.get("side") or "").lower() == side]
        open_opposite = [r for r in open_rows if str(r.get("side") or "").lower() not in {"", side}]

        if side == "short":
            cooldown = governance._short_transition(core, "XAUUSD", int(raw_signal_id))
            if not cooldown.get("ok"):
                governance._audit(core, raw_signal_id, "XAUUSD", "adaptive_stacking_governance", cooldown.get("reason") or "short_transition_history_unavailable", cooldown)
                return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": cooldown.get("reason") or "short_transition_history_unavailable", "short_flip_cooldown": cooldown}
            if cooldown.get("cooldown_active"):
                governance._audit(core, raw_signal_id, "XAUUSD", "adaptive_stacking_governance", "short_flip_reconfirmation_cooldown", cooldown)
                return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "short_flip_reconfirmation_cooldown", "short_flip_cooldown": cooldown, "existing_trades_unchanged": True}

        if open_opposite:
            token = _ENTRY_SIZE_MULTIPLIER.set(1.0)
            try:
                result = pre_cap_router(int(raw_signal_id), source=source)
            finally:
                _ENTRY_SIZE_MULTIPLIER.reset(token)
            if isinstance(result, dict):
                result = dict(result)
                result["adaptive_stacking"] = {"version": VERSION, "new_direction_campaign": True, "previous_open_count": len(open_rows), "size_multiplier": 1.0, "strategic_hard_cap": None, "operational_failsafe_open_trades": OPERATIONAL_FAILSAFE_OPEN_TRADES}
            return result

        campaign = _campaign_entry_count(core, side, open_rows)
        ordinal = int(campaign.get("count") or 0) + 1
        engaged = int(campaign.get("count") or 0) >= NORMAL_ALLOWANCE or len(open_same) >= NORMAL_ALLOWANCE
        funding: Dict[str, Any] = {"ok": True, "campaign_r": None, "open_r": None, "banked_harvest_r": None}
        if engaged:
            funding = _campaign_funding(core, side, open_rows)
            if not funding.get("ok"):
                detail = {"campaign": campaign, "funding": funding, "required_r": OVERFLOW_MIN_CAMPAIGN_R, "entry_ordinal": ordinal}
                _audit_block(core, raw_signal_id, "xau_adaptive_stack_funding_unavailable", detail)
                return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "xau_adaptive_stack_funding_unavailable", **detail}
            if float(funding.get("campaign_r") or 0.0) < OVERFLOW_MIN_CAMPAIGN_R:
                detail = {"campaign": campaign, "funding": funding, "required_r": OVERFLOW_MIN_CAMPAIGN_R, "entry_ordinal": ordinal}
                _audit_block(core, raw_signal_id, "xau_adaptive_stack_requires_plus_2r", detail)
                return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "xau_adaptive_stack_requires_plus_2r", **detail}

        if len(open_rows) >= OPERATIONAL_FAILSAFE_OPEN_TRADES:
            detail = {"open_count": len(open_rows), "failsafe": OPERATIONAL_FAILSAFE_OPEN_TRADES, "campaign": campaign}
            _audit_block(core, raw_signal_id, "xau_operational_failsafe_open_trade_ceiling", detail)
            return {"ok": True, "blocked": True, "asset": "XAUUSD", "side": side, "reason": "xau_operational_failsafe_open_trade_ceiling", **detail}

        size_multiplier = OVERFLOW_SIZE_MULTIPLIER if ordinal > FULL_SIZE_THROUGH_ORDINAL else 1.0
        token = _ENTRY_SIZE_MULTIPLIER.set(size_multiplier)
        try:
            result = pre_cap_router(int(raw_signal_id), source=source)
        finally:
            _ENTRY_SIZE_MULTIPLIER.reset(token)
        if isinstance(result, dict):
            result = dict(result)
            result["adaptive_stacking"] = {"version": VERSION, "entry_ordinal": ordinal, "normal_allowance": NORMAL_ALLOWANCE, "overflow_engaged": engaged, "required_campaign_r": OVERFLOW_MIN_CAMPAIGN_R if engaged else None, "campaign_funding": funding if engaged else None, "size_multiplier": size_multiplier, "half_risk_after_ordinal": FULL_SIZE_THROUGH_ORDINAL, "strategic_hard_cap": None, "operational_failsafe_open_trades": OPERATIONAL_FAILSAFE_OPEN_TRADES}
        return result

    core.execute_metals_demo_candidate = adaptive_router

    previous_demo_status = core.metals_demo_config_status
    def demo_status() -> Dict[str, Any]:
        d = dict(previous_demo_status() or {})
        stacking = dict(d.get("stacking_governance") or {})
        stacking.update({"practice_per_asset_cap": stacking.get("per_asset_cap", 24), "practice_family_cap": stacking.get("practice_family_cap", 40), "xau_live_policy": "ADAPTIVE_20_PLUS_2R_HALF_AFTER_32", "xau_live_normal_allowance": NORMAL_ALLOWANCE, "xau_live_overflow_min_campaign_r": OVERFLOW_MIN_CAMPAIGN_R, "xau_live_full_size_through_ordinal": FULL_SIZE_THROUGH_ORDINAL, "xau_live_overflow_size_multiplier": OVERFLOW_SIZE_MULTIPLIER, "xau_live_strategic_hard_cap": None, "xau_live_operational_failsafe_open_trades": OPERATIONAL_FAILSAFE_OPEN_TRADES, "xau_live_campaign_r_includes_banked_harvest": True})
        d["stacking_governance"] = stacking
        return d
    core.metals_demo_config_status = demo_status

    previous_governance_status = core.metals_strategy_governance_status
    def combined_governance_status() -> Dict[str, Any]:
        d = dict(previous_governance_status() or {})
        prescribed = dict(d.get("prescribed_changes") or {})
        prescribed["6_stacking"] = {"xau_live": "ADAPTIVE_20_PLUS_2R_HALF_AFTER_32_NO_STRATEGIC_CAP", "xag_practice_per_asset_cap": 24, "practice_family_cap": 40, "short_flip_cooldown_hours": 4.0, "mature_throttle": "SHADOW_ONLY"}
        d["prescribed_changes"] = prescribed
        d["adaptive_xau_stacking"] = {"version": VERSION, "normal_allowance": NORMAL_ALLOWANCE, "overflow_min_campaign_r": OVERFLOW_MIN_CAMPAIGN_R, "campaign_r_definition": "OPEN_R_PLUS_BANKED_HARVEST_R_IN_ACTIVE_CYCLE", "full_size_through_entry_ordinal": FULL_SIZE_THROUGH_ORDINAL, "overflow_size_multiplier_after_ordinal": OVERFLOW_SIZE_MULTIPLIER, "strategic_hard_cap": None, "operational_failsafe_open_trades": OPERATIONAL_FAILSAFE_OPEN_TRADES, "harvest_policy_unchanged": "50R=20%,100R=20%,150R+=25%,every_50R"}
        return d
    core.metals_strategy_governance_status = combined_governance_status

    route_path = "/api/metals-strategy-governance"
    old_routes = [r for r in list(app.router.routes) if getattr(r, "path", None) == route_path]
    for route in old_routes:
        app.router.routes.remove(route)
    app.add_api_route(route_path, combined_governance_status, methods=["GET"])
    app.add_api_route("/api/metals-adaptive-stacking", combined_governance_status, methods=["GET"])

    first = _safe_float(getattr(core, "METALS_HARVEST_FIRST_LEVEL_R", 50.0))
    step = _safe_float(getattr(core, "METALS_HARVEST_STEP_R", 50.0))
    f50 = _safe_float(getattr(core, "METALS_HARVEST_50_FRACTION", 0.20))
    f100 = _safe_float(getattr(core, "METALS_HARVEST_100_FRACTION", 0.20))
    f150 = _safe_float(getattr(core, "METALS_HARVEST_150_PLUS_FRACTION", 0.25))
    checks = {
        "live_xau_operational_ceiling_200": int(core.METALS_XAU_LIVE_MAX_OPEN_TRADES) == OPERATIONAL_FAILSAFE_OPEN_TRADES,
        "adaptive_router_installed": core.execute_metals_demo_candidate is adaptive_router,
        "adaptive_sizing_installed": core._metals_xau_side_sizing_preview is adaptive_sizing_preview,
        "xau_long_atr2_unchanged": core._metals_demo_new_trade_exit_policy("XAUUSD", "long").get("policy") == "ATR2_CHANDELIER",
        "xau_short_mfe25_unchanged": str(getattr(core, "METALS_XAU_SHORT_ACTIVE_POLICY", "")) == "MFE_GIVEBACK_25",
        "harvest_first_50r": abs((first or 0.0) - 50.0) < 1e-9,
        "harvest_step_50r": abs((step or 0.0) - 50.0) < 1e-9,
        "harvest_fractions_20_20_25": all([abs((f50 or 0.0) - 0.20) < 1e-9, abs((f100 or 0.0) - 0.20) < 1e-9, abs((f150 or 0.0) - 0.25) < 1e-9]),
    }
    if not all(checks.values()):
        raise RuntimeError(f"Adaptive XAU stacking startup self-test failed: {checks}")

    return {"installed": True, "version": VERSION, "startup_self_test": checks, "normal_allowance": NORMAL_ALLOWANCE, "overflow_min_campaign_r": OVERFLOW_MIN_CAMPAIGN_R, "full_size_through_entry_ordinal": FULL_SIZE_THROUGH_ORDINAL, "overflow_size_multiplier": OVERFLOW_SIZE_MULTIPLIER, "strategic_hard_cap": None, "operational_failsafe_open_trades": OPERATIONAL_FAILSAFE_OPEN_TRADES, "campaign_r_includes_banked_harvest": True, "harvest_policy_unchanged": {"first_r": first, "step_r": step, "fractions": [f50, f100, f150]}, "xag_practice_caps_unchanged": True, "replaced_governance_routes": len(old_routes)}
