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
    print("METALS_GOVERNANCE_PROBE", out, flush=True)
    return out
