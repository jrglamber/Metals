from __future__ import annotations

import inspect


def _emit_function(label, fn, ranges=None):
    if fn is None:
        print(f"XAU_PROBE {label}_missing", flush=True)
        return
    try:
        src = inspect.getsource(fn)
    except Exception as exc:
        print(f"XAU_PROBE {label}_source_error={type(exc).__name__}", flush=True)
        return
    lines = src.splitlines()
    print(f"XAU_PROBE {label}_signature={inspect.signature(fn)}", flush=True)
    use_ranges = ranges or ((1, len(lines)),)
    for start, end in use_ranges:
        print(f"XAU_PROBE {label}_RANGE {start}-{end}", flush=True)
        for idx in range(start, min(end, len(lines)) + 1):
            line = lines[idx - 1].rstrip()
            low = line.lower()
            if "token" in low or "secret" in low or "account_id" in low:
                continue
            print(f"XAU_PROBE {label}_L{idx:03d} {line}", flush=True)


def emit(core) -> None:
    _emit_function(
        "CANDIDATE",
        getattr(core, "execute_metals_xau_live_candidate", None),
        ((55, 85), (150, 235), (305, 355), (360, 410)),
    )
    _emit_function(
        "SIZING",
        getattr(core, "metals_xau_live_sizing_preview", None),
    )
