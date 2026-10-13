"""Project Exit Plan Metals strategy-governance patch — 2026-10-13.

Approved changes:
- quarantine XAG LONG from execution while retaining research collection;
- make direction transitions instrument-specific (never flatten the sibling metal);
- cap stacking at 24 per instrument and 40 on the practice Metals account;
- require a 4h re-confirmation window after a model transition into SHORT;
- assign new XAG SHORT trades to the proven 48h MFE25 manager;
- leave XAU LONG ATR2 unchanged;
- preserve AI AVOID as research-only / zero execution authority;
- expose the fixed-48h stacking replay and governance state for audit.

The patch is fail-closed around broker transitions. Existing open trades are not
bulk-migrated or force-closed merely because this module is installed. Same-
instrument direction flips close only the old direction for that instrument;
the sibling metal is untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

VERSION = "metals_strategy_governance_v1_2026_10_13"
DIRECTION_VERSION = "metals_instrument_direction_flip_v1_2026_10_13"
XAG_SHORT_POLICY = "MFE_GIVEBACK_25"
XAG_SHORT_POLICY_VERSION = "metals_xag_short_mfe25_48h_v1_2026_10_13"
PER_ASSET_CAP = 24
PRACTICE_FAMILY_CAP = 40
SHORT_FLIP_COOLDOWN_HOURS = 4.0

STACKING_REPLAY = {
    "method": "fixed_48h_capacity_proxy_not_dynamic_exit_backtest",
    "eligible_lanes": ["XAUUSD_LONG", "XAUUSD_SHORT", "XAGUSD_SHORT"],
    "xag_long_excluded": True,
    "sample_trades": 530,
    "cap_grid_no_family_cap": {
        "8": {"trades": 192, "r": 14.927},
        "12": {"trades": 260, "r": 34.457},
        "16": {"trades": 319, "r": 57.442},
        "20": {"trades": 372, "r": 71.288},
        "24": {"trades": 417, "r": 81.892},
        "30": {"trades": 463, "r": 92.021},
        "100": {"trades": 530, "r": 109.166},
    },
    "selected_without_cooldown": {
        "per_asset_cap": 24, "family_cap": 40,
        "trades": 386, "r": 73.418, "max_sequential_drawdown_r": 39.225,
    },
    "short_cooldown_grid_at_24_40": {
        "0h": {"trades": 386, "r": 73.418, "max_sequential_drawdown_r": 39.225},
        "2h": {"trades": 367, "r": 81.080, "max_sequential_drawdown_r": 32.426},
        "4h": {"trades": 352, "r": 87.453, "max_sequential_drawdown_r": 27.033},
        "6h": {"trades": 343, "r": 84.555, "max_sequential_drawdown_r": 26.997},
        "8h": {"trades": 332, "r": 79.442, "max_sequential_drawdown_r": 27.122},
    },
    "selected": {
        "per_asset_cap": PER_ASSET_CAP,
        "practice_family_cap": PRACTICE_FAMILY_CAP,
        "short_flip_cooldown_hours": SHORT_FLIP_COOLDOWN_HOURS,
        "trades": 352,
        "r": 87.453,
        "mean_r_per_trade": 0.248,
        "win_rate_pct": 63.1,
        "max_sequential_drawdown_r": 27.033,
    },
    "neighbor_robustness_with_4h": {
        "20_40": {"trades": 328, "r": 80.492, "max_sequential_drawdown_r": 25.369},
        "24_40": {"trades": 352, "r": 87.453, "max_sequential_drawdown_r": 27.033},
        "30_40": {"trades": 370, "r": 90.158, "max_sequential_drawdown_r": 28.972},
    },
    "mature_basket_throttle": "SHADOW_ONLY_NOT_ROBUST_ENOUGH_FOR_EXECUTION",
}


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
        t = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _candidate_for_row(core: Any, row: Any) -> Dict[str, Any]:
    d = _safe_dict(row)
    try:
        raw = core._raw_signal_json(row)
    except Exception:
        try:
            raw = core._raw_signal_json(d)
        except Exception:
            raw = {}
    try:
        return dict(core.metals_demo_candidate_for_row(raw, row) or {})
    except Exception:
        try:
            return dict(core.metals_demo_candidate_for_row(raw, d) or {})
        except Exception:
            return {}


def _audit(core: Any, raw_signal_id: int, asset: str, stage: str, reason: str, response: Optional[Dict[str, Any]] = None, candidate_state: str = "CANDIDATE") -> None:
    try:
        core._metals_demo_audit(
            int(raw_signal_id), asset, core._metals_demo_instrument(asset),
            stage, "BLOCKED", reason,
            response=response or {}, candidate_state=candidate_state,
        )
    except Exception:
        pass


def _short_transition(core: Any, asset: str, raw_signal_id: int) -> Dict[str, Any]:
    """Reconstruct the latest model transition into SHORT from raw candidates.

    received_at_utc is used deliberately: the cooldown starts when the service
    actually observed the change, not from a potentially stale candle label.
    """
    try:
        with core.get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM raw_signals WHERE id<=? ORDER BY id DESC LIMIT 256",
                (int(raw_signal_id),),
            ).fetchall()
    except Exception as exc:
        return {"ok": False, "reason": f"short_transition_history_read_failed:{type(exc).__name__}"}

    candidates: List[Tuple[int, str, Optional[datetime]]] = []
    for row in reversed(list(rows)):
        d = _safe_dict(row)
        try:
            a = core._metals_demo_asset(d.get("pair"))
        except Exception:
            continue
        if a != asset:
            continue
        cand = _candidate_for_row(core, row)
        if not cand.get("demo_candidate"):
            continue
        side = core._metals_demo_side(cand.get("demo_side"))
        if side not in {"long", "short"}:
            continue
        seen = _parse_dt(d.get("received_at_utc")) or _parse_dt(d.get("timestamp_readable"))
        candidates.append((int(d.get("id") or 0), side, seen))

    previous: Optional[str] = None
    flip_at: Optional[datetime] = None
    flip_id: Optional[int] = None
    for rid, side, seen in candidates:
        if side == "short" and previous is not None and previous != "short":
            flip_at = seen
            flip_id = rid
        previous = side

    if flip_at is None:
        return {
            "ok": True, "transition_found": False,
            "cooldown_active": False, "candidate_count": len(candidates),
        }

    now = datetime.now(timezone.utc)
    age_hours = max(0.0, (now - flip_at).total_seconds() / 3600.0)
    remaining = max(0.0, SHORT_FLIP_COOLDOWN_HOURS - age_hours)
    return {
        "ok": True,
        "transition_found": True,
        "flip_raw_signal_id": flip_id,
        "flip_observed_at_utc": flip_at.isoformat(),
        "age_hours": age_hours,
        "cooldown_hours": SHORT_FLIP_COOLDOWN_HOURS,
        "remaining_hours": remaining,
        "cooldown_active": remaining > 0.0,
        "eligible_at_utc": (flip_at + timedelta(hours=SHORT_FLIP_COOLDOWN_HOURS)).isoformat(),
        "candidate_count": len(candidates),
    }


def _practice_counts(core: Any, asset: str) -> Dict[str, int]:
    with core.get_conn() as conn:
        asset_count = int(conn.execute(
            "SELECT COUNT(*) AS c FROM metals_demo_trade_links WHERE asset=? AND status='OPEN'",
            (asset,),
        ).fetchone()["c"] or 0)
        family_count = int(conn.execute(
            "SELECT COUNT(*) AS c FROM metals_demo_trade_links WHERE status='OPEN'"
        ).fetchone()["c"] or 0)
    return {"asset": asset_count, "family": family_count}


def _xau_live_counts(core: Any, side: Optional[str] = None) -> Dict[str, int]:
    with core.get_conn() as conn:
        total = int(conn.execute(
            "SELECT COUNT(*) AS c FROM metals_xau_live_trade_links WHERE status='OPEN'"
        ).fetchone()["c"] or 0)
        opposite = 0
        if side in {"long", "short"}:
            opposite = int(conn.execute(
                "SELECT COUNT(*) AS c FROM metals_xau_live_trade_links WHERE status='OPEN' AND LOWER(side) != ?",
                (side,),
            ).fetchone()["c"] or 0)
    return {"total": total, "opposite": opposite}


def _practice_instrument_direction_flip(core: Any, target_side: str, raw_signal_id: int, source: str = "signal_worker") -> Dict[str, Any]:
    """Close only the old direction of the triggering PRACTICE instrument."""
    target = core._metals_demo_side(target_side)
    if target not in {"long", "short"}:
        return {"ok": False, "blocked": True, "reason": "invalid_instrument_direction_target"}

    try:
        with core.get_conn() as conn:
            row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
        if not row:
            return {"ok": False, "blocked": True, "reason": "instrument_direction_raw_signal_missing"}
        asset = core._metals_demo_asset(_safe_dict(row).get("pair"))
    except Exception as exc:
        return {"ok": False, "blocked": True, "reason": f"instrument_direction_asset_resolution_failed:{type(exc).__name__}"}
    if asset not in {"XAUUSD", "XAGUSD"}:
        return {"ok": False, "blocked": True, "reason": "instrument_direction_non_metal_asset"}

    broker = core.metals_demo_live_broker_snapshot()
    if not broker.get("ok"):
        return {"ok": False, "blocked": True, "reason": "instrument_direction_broker_snapshot_failed", "asset": asset}
    try:
        core.metals_demo_reconcile_broker_only_links(broker)
    except Exception as exc:
        return {"ok": False, "blocked": True, "reason": f"instrument_direction_reconciliation_failed:{type(exc).__name__}", "asset": asset}

    with core.get_conn() as conn:
        links = [_safe_dict(r) for r in conn.execute(
            "SELECT * FROM metals_demo_trade_links WHERE asset=? AND status='OPEN' ORDER BY id",
            (asset,),
        ).fetchall()]
    opposite = [l for l in links if core._metals_demo_side(l.get("side")) != target]
    if not opposite:
        return {
            "ok": True, "flip_required": False, "flat_confirmed": True,
            "asset": asset, "to_direction": target.upper(),
            "sibling_asset_untouched": True, "policy_version": DIRECTION_VERSION,
        }

    reason = f"METALS_INSTRUMENT_DIRECTION_FLIP:{asset}:{target.upper()}:{DIRECTION_VERSION}:{source}"
    close_results = []
    for link in opposite:
        try:
            qid = core._metals_demo_queue_close(link, reason, int(raw_signal_id))
            result = core._metals_demo_close(link, qid)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        close_results.append({"link_id": link.get("id"), "broker_trade_id": link.get("broker_trade_id"), "result": result})

    try:
        core.metals_demo_reconcile_local_ghosts()
    except Exception:
        pass
    after = core.metals_demo_live_broker_snapshot()
    if not after.get("ok"):
        return {"ok": False, "blocked": True, "reason": "instrument_direction_post_close_snapshot_failed", "asset": asset, "close_results": close_results}

    with core.get_conn() as conn:
        remaining_local = int(conn.execute(
            "SELECT COUNT(*) AS c FROM metals_demo_trade_links WHERE asset=? AND status='OPEN' AND LOWER(side) != ?",
            (asset, target),
        ).fetchone()["c"] or 0)
    remaining_broker = []
    for trade in list(after.get("owned_open_trades") or []):
        td = _safe_dict(trade)
        if core._metals_demo_asset(td.get("instrument")) != asset:
            continue
        units = float(core.safe_float(td.get("currentUnits")) or 0.0)
        broker_side = "short" if units < 0 else "long" if units > 0 else "flat"
        if broker_side not in {target, "flat"}:
            remaining_broker.append(core.safe_str(td.get("id")))

    if remaining_local or remaining_broker:
        return {
            "ok": False, "blocked": True,
            "reason": "instrument_direction_flip_incomplete",
            "asset": asset, "target_side": target,
            "remaining_local_opposite": remaining_local,
            "remaining_broker_opposite_ids": remaining_broker,
            "close_results": close_results,
        }

    return {
        "ok": True, "flip_required": True, "flat_confirmed": True,
        "asset": asset, "to_direction": target.upper(),
        "closed_old_direction_count": len(opposite),
        "sibling_asset_untouched": True,
        "family_hwm_reset": False,
        "policy_version": DIRECTION_VERSION,
        "close_results": close_results,
    }


def _xau_live_instrument_flip(core: Any, target_side: str, raw_signal_id: int, source: str) -> Dict[str, Any]:
    """Close only old-direction LIVE XAU links before an XAU side change."""
    target = core._metals_demo_side(target_side)
    if target not in {"long", "short"}:
        return {"ok": False, "blocked": True, "reason": "invalid_live_xau_direction_target"}
    try:
        core.metals_xau_live_reconcile_local_ghosts()
    except Exception as exc:
        return {"ok": False, "blocked": True, "reason": f"live_xau_flip_reconciliation_failed:{type(exc).__name__}"}

    with core.get_conn() as conn:
        links = [_safe_dict(r) for r in conn.execute(
            "SELECT * FROM metals_xau_live_trade_links WHERE status='OPEN' ORDER BY id"
        ).fetchall()]
    opposite = [l for l in links if core._metals_demo_side(l.get("side")) != target]
    if not opposite:
        return {"ok": True, "flip_required": False, "target_side": target, "policy_version": DIRECTION_VERSION}

    reason = f"LIVE_XAU_INSTRUMENT_DIRECTION_FLIP:{target.upper()}:{DIRECTION_VERSION}:{source}"
    close_results = []
    for link in opposite:
        try:
            qid = core._metals_xau_live_queue_close(link, reason, int(raw_signal_id))
            result = core._metals_xau_live_close(link, qid)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}:{exc}"}
        close_results.append({"link_id": link.get("id"), "broker_trade_id": link.get("broker_trade_id"), "result": result})

    try:
        core.metals_xau_live_reconcile_local_ghosts()
    except Exception:
        pass
    broker = core.metals_xau_live_broker_snapshot()
    if not broker.get("ok"):
        return {"ok": False, "blocked": True, "reason": "live_xau_flip_post_close_snapshot_failed", "close_results": close_results}
    with core.get_conn() as conn:
        remaining_local = int(conn.execute(
            "SELECT COUNT(*) AS c FROM metals_xau_live_trade_links WHERE status='OPEN' AND LOWER(side) != ?",
            (target,),
        ).fetchone()["c"] or 0)
    remaining_broker = []
    for trade in list(broker.get("owned_open_trades") or []):
        td = _safe_dict(trade)
        units = float(core.safe_float(td.get("currentUnits")) or 0.0)
        broker_side = "short" if units < 0 else "long" if units > 0 else "flat"
        if broker_side not in {target, "flat"}:
            remaining_broker.append(core.safe_str(td.get("id")))
    if remaining_local or remaining_broker:
        return {
            "ok": False, "blocked": True, "reason": "live_xau_instrument_flip_incomplete",
            "remaining_local_opposite": remaining_local,
            "remaining_broker_opposite_ids": remaining_broker,
            "close_results": close_results,
        }
    return {
        "ok": True, "flip_required": True, "target_side": target,
        "closed_old_direction_count": len(opposite),
        "sibling_asset_untouched": True,
        "policy_version": DIRECTION_VERSION,
        "close_results": close_results,
    }


def install(core: Any, app: Any) -> Dict[str, Any]:
    original_router = core.execute_metals_demo_candidate
    original_live_xau = core.execute_metals_xau_live_candidate
    original_new_policy = core._metals_demo_new_trade_exit_policy
    original_demo_status = core.metals_demo_config_status
    original_exit_status = core.metals_exit_policy_status

    # Stacking controls selected from the broad 20-30 / family-40 plateau.
    core.METALS_DEMO_MAX_OPEN_TRADES_PER_ASSET = PER_ASSET_CAP
    core.METALS_XAU_LIVE_MAX_OPEN_TRADES = PER_ASSET_CAP
    core.METALS_STRATEGY_GOVERNANCE_VERSION = VERSION
    core.METALS_INSTRUMENT_DIRECTION_FLIP_VERSION = DIRECTION_VERSION
    core.METALS_DEMO_FLATTEN_ON_DIRECTION_FLIP_ENABLED = False
    core.METALS_DEMO_DIRECTION_FLIP_REQUIRE_BROKER_FLAT = False
    core.METALS_AI_AVOID_EXECUTION_AUTHORITY = False
    core.METALS_XAG_LONG_EXECUTION_ENABLED = False
    core.METALS_XAG_SHORT_ACTIVE_POLICY = XAG_SHORT_POLICY
    core.METALS_XAG_SHORT_ACTIVE_POLICY_VERSION = XAG_SHORT_POLICY_VERSION

    # Replace family-wide flip authority with same-instrument-only authority.
    core._metals_demo_prepare_direction_flip = lambda target_side, raw_signal_id, source="signal_worker": _practice_instrument_direction_flip(core, target_side, raw_signal_id, source)

    # New XAG SHORTs receive MFE25; stored policies on existing trades remain intact.
    def new_policy(asset: str, side: str) -> Dict[str, str]:
        a = core._metals_demo_asset(asset)
        d = core._metals_demo_side(side)
        if a == "XAGUSD" and d == "short":
            return {"policy": XAG_SHORT_POLICY, "version": XAG_SHORT_POLICY_VERSION}
        return dict(original_new_policy(asset, side) or {})
    core._metals_demo_new_trade_exit_policy = new_policy

    # Add same-instrument transition handling to the dedicated live XAU lane.
    def live_xau_candidate(raw_signal_id: int, source: str = "signal_worker", precomputed_candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        candidate = dict(precomputed_candidate or {})
        if not candidate:
            try:
                with core.get_conn() as conn:
                    row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
                candidate = _candidate_for_row(core, row) if row else {}
            except Exception:
                candidate = {}
        side = core._metals_demo_side(candidate.get("demo_side"))
        if side in {"long", "short"}:
            transition = _xau_live_instrument_flip(core, side, int(raw_signal_id), source)
            if not transition.get("ok"):
                return {
                    "ok": True, "blocked": True,
                    "execution_lane": f"LIVE_XAU_{side.upper()}",
                    "asset": "XAUUSD", "side": side,
                    "reason": transition.get("reason") or "live_xau_instrument_flip_blocked",
                    "instrument_direction_transition": transition,
                    "existing_trades_unchanged_if_close_unconfirmed": True,
                }
        return original_live_xau(int(raw_signal_id), source=source, precomputed_candidate=precomputed_candidate)
    core.execute_metals_xau_live_candidate = live_xau_candidate

    # Preflight execution governance before the existing split router.
    def governed_router(raw_signal_id: int, source: str = "signal_worker") -> Dict[str, Any]:
        core.init_db()
        try:
            with core.get_conn() as conn:
                row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
            if not row:
                return original_router(int(raw_signal_id), source=source)
            d = _safe_dict(row)
            asset = core._metals_demo_asset(d.get("pair"))
            if asset not in {"XAUUSD", "XAGUSD"}:
                return original_router(int(raw_signal_id), source=source)
            candidate = _candidate_for_row(core, row)
            if not candidate.get("demo_candidate"):
                return original_router(int(raw_signal_id), source=source)
            side = core._metals_demo_side(candidate.get("demo_side"))
        except Exception:
            # Preserve existing fail-closed/execution logic if preflight cannot classify.
            return original_router(int(raw_signal_id), source=source)

        # Preserve duplicate semantics before applying any new block.
        try:
            with core.get_conn() as conn:
                demo_existing = conn.execute("SELECT id FROM metals_demo_trade_links WHERE raw_signal_id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
                live_existing = conn.execute("SELECT id FROM metals_xau_live_trade_links WHERE raw_signal_id=? LIMIT 1", (int(raw_signal_id),)).fetchone() if asset == "XAUUSD" else None
            if demo_existing or live_existing:
                return original_router(int(raw_signal_id), source=source)
        except Exception:
            pass

        # 1) XAG LONG is research-only. Raw signal/research pipelines are untouched.
        if asset == "XAGUSD" and side == "long":
            _audit(core, raw_signal_id, asset, "strategy_governance", "xag_long_research_only_quarantine", {"candidate": candidate}, "RESEARCH_ONLY")
            return {
                "ok": True, "blocked": True, "skipped": True,
                "asset": asset, "side": side,
                "reason": "xag_long_research_only_quarantine",
                "research_only": True,
                "candidate": candidate,
            }

        # 6) SHORT transition re-confirmation. This is model-transition based and
        # applies to both XAU and XAG SHORT before any broker side effects.
        cooldown = None
        if side == "short":
            cooldown = _short_transition(core, asset, int(raw_signal_id))
            if not cooldown.get("ok"):
                _audit(core, raw_signal_id, asset, "strategy_governance", cooldown.get("reason") or "short_transition_history_unavailable", cooldown)
                return {"ok": True, "blocked": True, "asset": asset, "side": side, "reason": cooldown.get("reason") or "short_transition_history_unavailable", "short_flip_cooldown": cooldown}
            if cooldown.get("cooldown_active"):
                _audit(core, raw_signal_id, asset, "strategy_governance", "short_flip_reconfirmation_cooldown", cooldown)
                return {
                    "ok": True, "blocked": True,
                    "asset": asset, "side": side,
                    "reason": "short_flip_reconfirmation_cooldown",
                    "short_flip_cooldown": cooldown,
                    "existing_trades_unchanged": True,
                }

        # 6) Capacity controls. Caps block additions; they never force-close a
        # pre-existing oversized basket.
        try:
            if asset == "XAUUSD" and bool(getattr(core, "METALS_XAU_LONG_LIVE_PROMOTION_ENABLED", False) or (side == "short" and getattr(core, "_metals_xau_short_live_gate_enabled", lambda: False)())):
                counts = _xau_live_counts(core, side)
                if counts["total"] >= PER_ASSET_CAP:
                    _audit(core, raw_signal_id, asset, "strategy_governance", "xau_live_stacking_cap_24", counts)
                    return {"ok": True, "blocked": True, "asset": asset, "side": side, "reason": "xau_live_stacking_cap_24", "counts": counts, "cap": PER_ASSET_CAP}
            else:
                counts = _practice_counts(core, asset)
                if counts["asset"] >= PER_ASSET_CAP:
                    _audit(core, raw_signal_id, asset, "strategy_governance", "practice_asset_stacking_cap_24", counts)
                    return {"ok": True, "blocked": True, "asset": asset, "side": side, "reason": "practice_asset_stacking_cap_24", "counts": counts, "cap": PER_ASSET_CAP}
                if counts["family"] >= PRACTICE_FAMILY_CAP:
                    _audit(core, raw_signal_id, asset, "strategy_governance", "practice_family_stacking_cap_40", counts)
                    return {"ok": True, "blocked": True, "asset": asset, "side": side, "reason": "practice_family_stacking_cap_40", "counts": counts, "cap": PRACTICE_FAMILY_CAP}
        except Exception as exc:
            _audit(core, raw_signal_id, asset, "strategy_governance", f"stacking_cap_count_failed:{type(exc).__name__}")
            return {"ok": True, "blocked": True, "asset": asset, "side": side, "reason": "stacking_cap_count_failed"}

        return original_router(int(raw_signal_id), source=source)
    core.execute_metals_demo_candidate = governed_router

    # Correct the legacy status surface so dashboards do not continue reporting
    # family-wide forced flattening or XAG LONG as executable practice.
    def demo_status() -> Dict[str, Any]:
        d = dict(original_demo_status() or {})
        d["direction_flip_policy"] = {
            "version": DIRECTION_VERSION,
            "scope": "PER_INSTRUMENT",
            "flatten_before_opposite_entry": True,
            "broker_flat_confirmation_required": True,
            "family_mixed_direction_supported": True,
            "sibling_asset_forced_flatten": False,
            "same_asset_opposite_direction_supported": False,
            "same_asset_old_direction_closed_before_flip": True,
            "family_hwm_reset_before_new_direction_entry": False,
        }
        split = dict(d.get("split_execution") or {})
        split["xau_short"] = "LIVE_XAU_SHORT" if getattr(core, "_metals_xau_short_live_gate_enabled", lambda: False)() else "PRACTICE"
        split["xag_long"] = "RESEARCH_ONLY"
        split["xag_short"] = "PRACTICE_MFE25"
        d["split_execution"] = split
        d["stacking_governance"] = {
            "per_asset_cap": PER_ASSET_CAP,
            "practice_family_cap": PRACTICE_FAMILY_CAP,
            "live_family_cap_scope": "capital_account_scoped; current live XAU per-asset cap is tighter",
            "short_flip_cooldown_hours": SHORT_FLIP_COOLDOWN_HOURS,
            "mature_basket_throttle": "SHADOW_ONLY",
        }
        d["ai_avoid_execution_authority"] = False
        d["safety_note"] = "Instrument-specific direction governance: XAU never closes XAG and XAG never closes XAU. XAG LONG is research-only."
        return d
    core.metals_demo_config_status = demo_status

    def exit_status() -> Dict[str, Any]:
        d = dict(original_exit_status() or {})
        active = d.setdefault("active_execution", {})
        active["XAGUSD_SHORT_new_trades"] = XAG_SHORT_POLICY
        active["XAGUSD_LONG"] = "RESEARCH_ONLY_NO_EXECUTION"
        shadows = d.setdefault("forward_shadow", {})
        current = list(shadows.get("XAGUSD_SHORT") or [])
        for name in ("ATR2_CHANDELIER", "MFE_GIVEBACK_50"):
            if name not in current:
                current.append(name)
        shadows["XAGUSD_SHORT"] = current
        d["XAUUSD_LONG_change_2026_10_13"] = "NONE_ATR2_UNCHANGED"
        d["xag_short_active_policy_version"] = XAG_SHORT_POLICY_VERSION
        d["xag_short_existing_trades_retroactively_migrated"] = False
        d["ai_avoid_execution_authority"] = False
        return d
    core.metals_exit_policy_status = exit_status

    # Audit/status endpoint. No secrets are returned.
    def governance_status() -> Dict[str, Any]:
        xau_short_gate = bool(getattr(core, "_metals_xau_short_live_gate_enabled", lambda: False)())
        return {
            "ok": True,
            "version": VERSION,
            "prescribed_changes": {
                "1_xag_long": "RESEARCH_ONLY",
                "2_direction": "PER_INSTRUMENT_NO_SIBLING_FLATTEN",
                "3_xau_short": "LIVE_MFE25" if xau_short_gate else "MFE25_READY_GATE_NOT_ENABLED",
                "4_xag_short": "NEW_TRADES_MFE25_PRACTICE",
                "5_xau_long": "ATR2_UNCHANGED_MFE25_CHALLENGER_RESEARCH_ONLY",
                "6_stacking": {"per_asset_cap": PER_ASSET_CAP, "practice_family_cap": PRACTICE_FAMILY_CAP, "short_flip_cooldown_hours": SHORT_FLIP_COOLDOWN_HOURS, "mature_throttle": "SHADOW_ONLY"},
                "7_ai_avoid": "RESEARCH_ONLY_NO_EXECUTION_AUTHORITY",
            },
            "stacking_replay": STACKING_REPLAY,
            "non_retroactive": {
                "existing_xag_short_exit_policy_unchanged": True,
                "existing_oversized_baskets_not_force_reduced": True,
                "xag_long_open_trades_not_force_closed_by_quarantine": True,
            },
        }

    try:
        app.add_api_route("/api/metals-strategy-governance", governance_status, methods=["GET"])
    except Exception:
        pass
    core.metals_strategy_governance_status = governance_status

    # Startup invariants: fail deployment before serving if a critical strategy
    # contract was not installed exactly as approved.
    checks = {
        "xag_short_mfe25": core._metals_demo_new_trade_exit_policy("XAGUSD", "short").get("policy") == XAG_SHORT_POLICY,
        "xau_long_atr2_unchanged": core._metals_demo_new_trade_exit_policy("XAUUSD", "long").get("policy") == "ATR2_CHANDELIER",
        "practice_cap_24": int(core.METALS_DEMO_MAX_OPEN_TRADES_PER_ASSET) == PER_ASSET_CAP,
        "live_xau_cap_24": int(core.METALS_XAU_LIVE_MAX_OPEN_TRADES) == PER_ASSET_CAP,
        "ai_veto_false": core.METALS_AI_AVOID_EXECUTION_AUTHORITY is False,
    }
    status_check = core.metals_demo_config_status()
    direction_check = status_check.get("direction_flip_policy") or {}
    checks["mixed_family_supported"] = bool(direction_check.get("family_mixed_direction_supported"))
    checks["sibling_flatten_disabled"] = direction_check.get("sibling_asset_forced_flatten") is False
    if not all(checks.values()):
        raise RuntimeError(f"Metals governance startup self-test failed: {checks}")

    return {
        "installed": True,
        "version": VERSION,
        "direction_version": DIRECTION_VERSION,
        "startup_self_test": checks,
        "per_asset_cap": PER_ASSET_CAP,
        "practice_family_cap": PRACTICE_FAMILY_CAP,
        "short_flip_cooldown_hours": SHORT_FLIP_COOLDOWN_HOURS,
        "xag_long_execution": False,
        "xag_short_new_trade_policy": XAG_SHORT_POLICY,
        "xau_long_policy_unchanged": "ATR2_CHANDELIER",
        "ai_avoid_execution_authority": False,
        "stacking_replay_selected": STACKING_REPLAY["selected"],
    }
