"""Temporary safe runtime diagnostics for the live XAU manager."""
from __future__ import annotations

import inspect
import json
from typing import Any


def _str_consts(fn: Any):
    try:
        return [x for x in fn.__code__.co_consts if isinstance(x, str)]
    except Exception:
        return []


def run(core: Any) -> None:
    mgr = getattr(core, "metals_xau_live_manager_tick", None)
    out = {"manager_name": getattr(mgr, "__name__", None)}
    try:
        out["manager_signature"] = str(inspect.signature(mgr))
    except Exception as exc:
        out["manager_signature_error"] = f"{type(exc).__name__}: {exc}"

    orig = None
    try:
        free = list(getattr(mgr.__code__, "co_freevars", ()) or ())
        cells = list(getattr(mgr, "__closure__", ()) or ())
        closure = {name: cell.cell_contents for name, cell in zip(free, cells)}
        out["wrapper_freevars"] = free
        orig = closure.get("original_manager")
    except Exception as exc:
        out["closure_error"] = f"{type(exc).__name__}: {exc}"

    if callable(orig):
        out["original_name"] = getattr(orig, "__name__", None)
        try:
            out["original_signature"] = str(inspect.signature(orig))
        except Exception:
            pass
        try:
            names = list(orig.__code__.co_names)
            out["original_relevant_names"] = [n for n in names if any(k in n.lower() for k in ("stop", "broker", "trade", "manager", "exit", "review", "oanda", "link"))]
        except Exception:
            pass
        consts = _str_consts(orig)
        out["original_relevant_constants"] = [
            s for s in consts
            if any(k in s.lower() for k in ("insert ", "update ", "stop", "broker", "trade", "manager", "review", "oanda", "link"))
        ][:80]
        # For every manager-referenced helper, report its signature and only code
        # constants/names relevant to stop/order execution. No env values are read.
        helpers = {}
        for name in out.get("original_relevant_names", []):
            obj = getattr(core, name, None)
            if not callable(obj):
                continue
            item = {"name": getattr(obj, "__name__", None)}
            try:
                item["signature"] = str(inspect.signature(obj))
            except Exception:
                pass
            try:
                item["relevant_names"] = [n for n in obj.__code__.co_names if any(k in n.lower() for k in ("stop", "order", "request", "trade", "broker", "oanda", "execute"))]
            except Exception:
                pass
            item["relevant_constants"] = [
                s for s in _str_consts(obj)
                if any(k in s.lower() for k in ("insert ", "update ", "stop", "/v3/", "/orders", "trade"))
            ][:50]
            helpers[name] = item
        out["helpers"] = helpers

    print("METALS_XAU_INTRAHOUR_DIAGNOSTIC " + json.dumps(out, default=str, separators=(",", ":")), flush=True)
