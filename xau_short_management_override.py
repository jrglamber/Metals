"""Side-aware ownership/reconciliation/manager support for live XAU SHORT.

This module is intentionally fail-closed. It patches only specific, asserted
long-only assumptions in the existing live-XAU management path. XAU LONG
behaviour remains unchanged; XAU SHORT uses its linked side/policy and MFE25.
"""
from __future__ import annotations

import inspect
import textwrap
from typing import Any, Dict

VERSION = "metals_xau_short_live_management_v1_2026_10_06"


def _replace_once(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count != 1:
        raise RuntimeError(f"XAU short management patch refused: expected one {label}, found {count}")
    return src.replace(old, new, 1)


def _retry_side_for_raw(core: Any, raw_signal_id: int) -> str:
    """Recover the queued entry side from the exact original raw signal."""
    try:
        with core.get_conn() as conn:
            row = conn.execute("SELECT * FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
        if not row:
            return "long"
        d = dict(row)
        try:
            candidate = core.metals_demo_candidate_for_row(core._raw_signal_json(d), d) or {}
            side = core._metals_demo_side(candidate.get("demo_side"))
            if side in {"long", "short"}:
                return side
        except Exception:
            pass
        sig = core.safe_str(d.get("signal_id")).lower()
        if sig.endswith("_short"):
            return "short"
        if sig.endswith("_long"):
            return "long"
    except Exception:
        pass
    return "long"


def install(core: Any) -> Dict[str, Any]:
    ns = core.__dict__

    # 1) Broker ownership: once both XAU directions are promoted, this Metals
    # service owns XAU_USD on the shared account in either direction. Other
    # instruments remain ignored exactly as before.
    original_snapshot = core.metals_xau_live_broker_snapshot
    snap = textwrap.dedent(inspect.getsource(original_snapshot))
    snap = _replace_once(snap, "def metals_xau_live_broker_snapshot(", "def _metals_xau_live_broker_snapshot_side_aware(", "broker snapshot header")
    snap = _replace_once(
        snap,
        '    owned = [t for t in xau if float(safe_float(t.get("currentUnits")) or 0.0) > 0]\n    shorts = [t for t in xau if float(safe_float(t.get("currentUnits")) or 0.0) < 0]\n',
        '    owned = [t for t in xau if float(safe_float(t.get("currentUnits")) or 0.0) != 0.0]\n    shorts = []\n',
        "long-only XAU ownership filter",
    )
    exec(compile(snap, "<xau-short-live-broker-ownership>", "exec"), ns, ns)
    patched_snapshot = ns.get("_metals_xau_live_broker_snapshot_side_aware")
    if not callable(patched_snapshot):
        raise RuntimeError("XAU short management patch refused: broker snapshot not callable")
    core.metals_xau_live_broker_snapshot = patched_snapshot

    # 2) Per-trade R/MFE/MAE metrics. The legacy implementation hard-coded LONG
    # math and max(high)/min(low). For SHORT, favourable excursion is the low
    # and adverse excursion is the high.
    original_metrics = core._metals_xau_live_trade_metrics
    met = textwrap.dedent(inspect.getsource(original_metrics))
    met = _replace_once(met, "def _metals_xau_live_trade_metrics(", "def _metals_xau_live_trade_metrics_side_aware(", "trade metrics header")
    met = _replace_once(
        met,
        '    entry=safe_float(link.get("entry_price"));\n',
        '    entry=safe_float(link.get("entry_price")); side=safe_str(link.get("side")).lower() or "long"; side=side if side in {"long","short"} else "long"\n',
        "trade metrics side initialization",
    )
    met = met.replace('_metals_demo_r_from_price("long",', '_metals_demo_r_from_price(side,')
    met = _replace_once(
        met,
        'mfe_price=max(highs) if highs else current; mae_price=min(lows) if lows else current;',
        'mfe_price=((min(lows) if lows else current) if side=="short" else (max(highs) if highs else current)); mae_price=((max(highs) if highs else current) if side=="short" else (min(lows) if lows else current));',
        "MFE/MAE direction math",
    )
    met = _replace_once(met, '"side":"long"', '"side":side', "trade metrics returned side")
    if '_metals_demo_r_from_price("long",' in met:
        raise RuntimeError("XAU short management patch refused: long-only R math remains")
    exec(compile(met, "<xau-short-live-trade-metrics>", "exec"), ns, ns)
    patched_metrics = ns.get("_metals_xau_live_trade_metrics_side_aware")
    if not callable(patched_metrics):
        raise RuntimeError("XAU short management patch refused: trade metrics not callable")
    core._metals_xau_live_trade_metrics = patched_metrics

    # 3) Broker-only recovery. If the process dies after an OANDA fill but
    # before the local link commit, recover the exact audited trade with its
    # actual sign/side and the appropriate live exit policy.
    original_recovery = core._metals_xau_live_recover_broker_only
    rec = textwrap.dedent(inspect.getsource(original_recovery))
    rec = _replace_once(rec, "def _metals_xau_live_recover_broker_only(", "def _metals_xau_live_recover_broker_only_side_aware(", "broker recovery header")
    rec = _replace_once(
        rec,
        '    for bt in broker.get("owned_open_trades") or []:\n',
        '    for bt in broker.get("owned_open_trades") or []:\n        recovery_side = "short" if float(safe_float(bt.get("currentUnits")) or 0.0) < 0 else "long"\n        recovery_policy = METALS_XAU_SHORT_ACTIVE_POLICY if recovery_side == "short" else METALS_XAU_LONG_MFE50_POLICY\n        recovery_policy_version = METALS_XAU_SHORT_ACTIVE_POLICY_VERSION if recovery_side == "short" else METALS_XAU_LONG_MFE50_POLICY_VERSION\n',
        "broker recovery side derivation",
    )
    rec = _replace_once(rec, 'METALS_XAU_LIVE_ALLOWED_INSTRUMENT, "long",', 'METALS_XAU_LIVE_ALLOWED_INSTRUMENT, recovery_side,', "broker recovery hard-coded side")
    rec = _replace_once(rec, '            METALS_XAU_LONG_MFE50_POLICY,\n            METALS_XAU_LONG_MFE50_POLICY_VERSION,\n', '            recovery_policy,\n            recovery_policy_version,\n', "broker recovery hard-coded policy")
    exec(compile(rec, "<xau-short-live-broker-recovery>", "exec"), ns, ns)
    patched_recovery = ns.get("_metals_xau_live_recover_broker_only_side_aware")
    if not callable(patched_recovery):
        raise RuntimeError("XAU short management patch refused: broker recovery not callable")
    core._metals_xau_live_recover_broker_only = patched_recovery

    # 4) Deferred market-reopen entries must revalidate the SAME direction as
    # the queued signal, rather than always asking whether XAU is still LONG.
    core._metals_xau_live_retry_side_for_raw = lambda rid: _retry_side_for_raw(core, rid)
    original_retry = core._metals_xau_live_pending_open_market_reopen_tick
    retry = textwrap.dedent(inspect.getsource(original_retry))
    retry = _replace_once(retry, "def _metals_xau_live_pending_open_market_reopen_tick(", "def _metals_xau_live_pending_open_market_reopen_tick_side_aware(", "market reopen retry header")
    retry = _replace_once(
        retry,
        '        support = _metals_latest_same_direction_support("XAUUSD", "long")\n',
        '        retry_side = _metals_xau_live_retry_side_for_raw(rid)\n        support = _metals_latest_same_direction_support("XAUUSD", retry_side)\n',
        "market reopen hard-coded LONG revalidation",
    )
    exec(compile(retry, "<xau-short-live-market-reopen>", "exec"), ns, ns)
    patched_retry = ns.get("_metals_xau_live_pending_open_market_reopen_tick_side_aware")
    if not callable(patched_retry):
        raise RuntimeError("XAU short management patch refused: market reopen retry not callable")
    core._metals_xau_live_pending_open_market_reopen_tick = patched_retry

    # Make the ownership contract explicit in config/reporting. The underlying
    # account is shared with indices, but Metals owns XAU_USD in both directions.
    previous_cfg = core.metals_xau_live_config_status
    def cfg_status() -> Dict[str, Any]:
        d = dict(previous_cfg() or {})
        d["label"] = "XAU LONG + SHORT — LIVE"
        d["owned_side"] = "BOTH"
        d["xau_short_manager_side_aware"] = True
        d["xau_short_broker_snapshot_side_aware"] = True
        d["xau_short_recovery_side_aware"] = True
        d["xau_short_market_reopen_retry_side_aware"] = True
        d["xau_short_management_version"] = VERSION
        return d
    core.metals_xau_live_config_status = cfg_status
    core.METALS_XAU_SHORT_LIVE_MANAGEMENT_VERSION = VERSION

    return {
        "installed": True,
        "version": VERSION,
        "owned_side": "BOTH",
        "broker_snapshot_side_aware": True,
        "trade_metrics_side_aware": True,
        "broker_recovery_side_aware": True,
        "market_reopen_retry_side_aware": True,
        "fail_closed_source_assertions": True,
    }
