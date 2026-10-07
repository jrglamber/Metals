"""Temporary no-secret bytecode diagnostics for live XAU manager gating."""
from __future__ import annotations

import dis
import json
from typing import Any, Dict, List


def _unwrap_manager(fn: Any) -> Any:
    try:
        free = list(getattr(fn.__code__, "co_freevars", ()) or ())
        cells = list(getattr(fn, "__closure__", ()) or ())
        closure = {name: cell.cell_contents for name, cell in zip(free, cells)}
        return closure.get("original_manager") or fn
    except Exception:
        return fn


def _windows(fn: Any, needles: set[str], radius: int = 16) -> List[Dict[str, Any]]:
    try:
        ins = list(dis.get_instructions(fn))
    except Exception as exc:
        return [{"error": f"{type(exc).__name__}: {exc}"}]
    hits = []
    for i, op in enumerate(ins):
        text = f"{op.argval!s} {op.argrepr!s}"
        if any(n in text for n in needles):
            lo = max(0, i - radius)
            hi = min(len(ins), i + radius + 1)
            hits.append({
                "hit": {"offset": op.offset, "op": op.opname, "arg": op.argrepr},
                "window": [
                    {"offset": x.offset, "op": x.opname, "arg": x.argrepr}
                    for x in ins[lo:hi]
                ],
            })
    return hits


def run(core: Any) -> None:
    manager = _unwrap_manager(core.metals_xau_live_manager_tick)
    candidate = getattr(core, "_metals_xau_live_stop_candidate", None)
    out = {
        "manager": _windows(manager, {"force", "allow_new_manager_actions", "METALS_DEMO_MANAGER_FRESH_SIGNAL_SECONDS"}),
        "candidate": _windows(candidate, {"48", "age", "hold", "eligible", "MIN_HOLD"}) if callable(candidate) else [],
    }
    print("METALS_XAU_FORCE_DIAGNOSTIC " + json.dumps(out, separators=(",", ":")), flush=True)
