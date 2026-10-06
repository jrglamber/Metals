from __future__ import annotations

import inspect


def emit(core) -> None:
    fn = getattr(core, "execute_metals_xau_live_candidate", None)
    if fn is None:
        print("XAU_PROBE function_missing", flush=True)
        return
    try:
        src = inspect.getsource(fn)
    except Exception as exc:
        print(f"XAU_PROBE source_error={type(exc).__name__}", flush=True)
        return
    keys = ("side", "unit", "stop", "order", "risk", "price", "request", "trade")
    print(f"XAU_PROBE signature={inspect.signature(fn)}", flush=True)
    for idx, line in enumerate(src.splitlines(), 1):
        low = line.lower()
        if any(k in low for k in keys):
            clean = line.rstrip()
            if "token" in low or "secret" in low or "account_id" in low:
                continue
            print(f"XAU_PROBE L{idx:03d} {clean}", flush=True)
