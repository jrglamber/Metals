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
    lines = src.splitlines()
    print(f"XAU_PROBE signature={inspect.signature(fn)}", flush=True)
    ranges = ((55, 85), (195, 235), (305, 355), (360, 410))
    for start, end in ranges:
        print(f"XAU_PROBE RANGE {start}-{end}", flush=True)
        for idx in range(start, min(end, len(lines)) + 1):
            line = lines[idx - 1].rstrip()
            low = line.lower()
            if "token" in low or "secret" in low or "account_id" in low:
                continue
            print(f"XAU_PROBE L{idx:03d} {line}", flush=True)
