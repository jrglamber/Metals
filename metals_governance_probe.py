"""Read-only startup probe for Metals execution-governance patching.

This module does not alter strategy or broker behaviour. It only emits compact
runtime introspection so the follow-up governance patch can target the actual
production call signatures safely.
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
            if any(k in low for k in ("direction", "flip", "candidate", "basket", "open", "cap", "xag", "xau")):
                strings.append(value[:240])
    return {
        "signature": sig,
        "code": True,
        "varnames": list(code.co_varnames[:40]),
        "names": [x for x in code.co_names if any(k in x.lower() for k in ("direction", "flip", "candidate", "basket", "open", "cap", "xag", "xau"))][:80],
        "strings": strings[:40],
    }


def _source_probe(core: Any) -> Dict[str, Any]:
    path = getattr(core, "__file__", None)
    if not path:
        return {"ok": False, "reason": "core_file_missing"}
    try:
        lines = open(path, "r", encoding="utf-8").read().splitlines()
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}:{exc}"}
    needles = (
        "def execute_metals_demo_candidate",
        "METALS_DIRECTION_FLIP_FLATTEN",
        "metals_family_direction_flip_flat",
        "direction_flip_flatten",
        "family_mixed_direction",
    )
    hits = []
    used = set()
    for i, line in enumerate(lines):
        if any(n.lower() in line.lower() for n in needles):
            lo = max(0, i - 10)
            hi = min(len(lines), i + 31)
            key = (lo, hi)
            if key in used:
                continue
            used.add(key)
            hits.append({"line": i + 1, "excerpt": "\n".join(f"{j+1}:{lines[j]}" for j in range(lo, hi))})
    return {"ok": True, "path": path, "hits": hits[:12]}


def install(core: Any) -> Dict[str, Any]:
    targets = (
        "execute_metals_demo_candidate",
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
