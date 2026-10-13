"""Read-only startup probe for Metals execution-governance patching.

No strategy or broker behaviour is altered. This emits compact runtime/source
introspection so the approved governance patch can target production safely.
"""
from __future__ import annotations

import inspect
from typing import Any, Dict


def _fn_info(fn: Any) -> Dict[str, Any]:
    try:
        sig = str(inspect.signature(fn))
    except Exception as exc:
        sig = f"<signature-error:{type(exc).__name__}>"
    code = getattr(fn, "__code__", None)
    if code is None:
        return {"signature": sig, "code": False}
    strings = []
    for value in code.co_consts:
        if isinstance(value, str):
            low = value.lower()
            if any(k in low for k in ("direction", "flip", "candidate", "basket", "open", "cap", "xag", "xau", "flat", "side")):
                strings.append(value[:400])
    return {
        "signature": sig,
        "code": True,
        "varnames": list(code.co_varnames[:80]),
        "names": [x for x in code.co_names if any(k in x.lower() for k in ("direction", "flip", "candidate", "basket", "open", "cap", "xag", "xau", "flat", "side"))][:120],
        "strings": strings[:80],
    }


def _source_window(lines, needle: str, before: int, after: int):
    for i, line in enumerate(lines):
        if needle in line:
            lo = max(0, i - before)
            hi = min(len(lines), i + after + 1)
            return {"line": i + 1, "excerpt": "\n".join(f"{j+1}:{lines[j]}" for j in range(lo, hi))}
    return {"line": None, "excerpt": ""}


def _source_probe(core: Any) -> Dict[str, Any]:
    path = getattr(core, "__file__", None)
    if not path:
        return {"ok": False, "reason": "core_file_missing"}
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    return {
        "ok": True,
        "path": path,
        "prepare_direction_flip": _source_window(lines, "def _metals_demo_prepare_direction_flip", 8, 150),
        "demo_router": _source_window(lines, "def execute_metals_demo_candidate", 3, 190),
        "xau_live_candidate": _source_window(lines, "def execute_metals_xau_live_candidate", 3, 180),
    }


def install(core: Any) -> Dict[str, Any]:
    targets = (
        "execute_metals_demo_candidate",
        "execute_metals_xau_live_candidate",
        "_metals_demo_prepare_direction_flip",
        "metals_demo_candidate_for_row",
        "_metals_demo_new_trade_exit_policy",
        "_metals_demo_decision",
    )
    out: Dict[str, Any] = {"read_only": True, "execution_mutated": False, "targets": {}}
    for name in targets:
        fn = getattr(core, name, None)
        if callable(fn):
            out["targets"][name] = _fn_info(fn)
    out["source_probe"] = _source_probe(core)
    print("METALS_GOVERNANCE_PROBE", out, flush=True)
    return out
