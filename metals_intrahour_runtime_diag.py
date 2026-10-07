"""Temporary safe runtime diagnostics for the live XAU manager."""
from __future__ import annotations

import dis
import inspect
import json
from typing import Any


def _str_consts(fn: Any):
    try:
        return [x for x in fn.__code__.co_consts if isinstance(x, str)]
    except Exception:
        return []


def _closure(fn: Any):
    try:
        free = list(getattr(fn.__code__, "co_freevars", ()) or ())
        cells = list(getattr(fn, "__closure__", ()) or ())
        return {name: cell.cell_contents for name, cell in zip(free, cells)}
    except Exception:
        return {}


def _windows(fn: Any, needles: tuple[str, ...], radius: int = 20):
    try:
        ins = list(dis.get_instructions(fn))
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    out = []
    for i, op in enumerate(ins):
        text = f"{op.argval!s} {op.argrepr!s}"
        if any(n in text for n in needles):
            lo, hi = max(0, i - radius), min(len(ins), i + radius + 1)
            out.append({
                "hit": {"offset": op.offset, "op": op.opname, "arg": op.argrepr},
                "window": [
                    {"offset": x.offset, "op": x.opname, "arg": x.argrepr}
                    for x in ins[lo:hi]
                ],
            })
    return out


def _describe(fn: Any):
    item = {"name": getattr(fn, "__name__", None)}
    try:
        item["signature"] = str(inspect.signature(fn))
    except Exception:
        pass
    try:
        item["freevars"] = list(fn.__code__.co_freevars)
        item["names"] = [n for n in fn.__code__.co_names if any(k in n.lower() for k in (
            "stop", "order", "request", "trade", "broker", "oanda", "execute", "pool", "link", "close", "metric"
        ))]
    except Exception:
        pass
    item["constants"] = [
        s for s in _str_consts(fn)
        if any(k in s.lower() for k in ("select ", "insert ", "update ", "stop", "/v3/", "/orders", "trade", "link"))
    ][:80]
    return item


def run(core: Any) -> None:
    mgr = getattr(core, "metals_xau_live_manager_tick", None)
    out = {"manager": _describe(mgr) if callable(mgr) else None}
    closure = _closure(mgr) if callable(mgr) else {}
    orig = closure.get("original_manager")
    if callable(orig):
        out["original_manager"] = _describe(orig)
        out["manager_call_windows"] = _windows(orig, (
            "_metals_xau_live_trade_metrics",
            "_metals_xau_live_stop_candidate",
            "_metals_xau_live_update_stop",
            "metals_xau_live_broker_snapshot",
            "metals_xau_live_trade_links",
            "allow_new_manager_actions",
        ), radius=24)

    metrics = getattr(core, "_metals_xau_live_trade_metrics", None)
    if callable(metrics):
        out["metrics_wrapper"] = _describe(metrics)
        mclosure = _closure(metrics)
        om = mclosure.get("original_metrics")
        if callable(om):
            out["original_metrics"] = _describe(om)
            out["metrics_windows"] = _windows(om, ("hold_candles", "entry_price", "broker_trade", "current_price"), radius=18)

    for helper_name in (
        "_metals_xau_live_stop_candidate",
        "_metals_xau_live_update_stop",
        "metals_xau_live_broker_snapshot",
        "_metals_xau_live_request",
    ):
        fn = getattr(core, helper_name, None)
        if callable(fn):
            out[helper_name] = _describe(fn)
            if helper_name in {"_metals_xau_live_stop_candidate", "_metals_xau_live_update_stop"}:
                out[helper_name + "_windows"] = _windows(fn, ("stop_price", "current_stop_price", "broker_trade_id", "48", "hold"), radius=18)

    print("METALS_XAU_INTRAHOUR_DIAGNOSTIC_V2 " + json.dumps(out, default=str, separators=(",", ":")), flush=True)
