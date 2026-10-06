"""Temporary read-only runtime diagnostic for XAU short live promotion."""
from __future__ import annotations

import inspect
import json
from typing import Any, Dict


def _pick(row: Dict[str, Any]) -> Dict[str, Any]:
    keep = {}
    for k, v in row.items():
        lk = str(k).lower()
        if any(t in lk for t in ("id", "time", "asset", "symbol", "instrument", "side", "direction", "action", "signal", "status", "model", "decision", "reason", "candidate", "long", "short")):
            if "secret" not in lk and "token" not in lk and "key" not in lk:
                keep[str(k)] = v
    return keep


def _source_matches(obj: Any, needles: tuple[str, ...]) -> list[str]:
    try:
        src = inspect.getsource(obj)
    except Exception:
        return []
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        lo = line.lower()
        if any(n in lo for n in needles):
            out.append(f"{i}: {line.strip()}")
    return out[:160]


def run(core: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "gate": bool(getattr(core, "_metals_xau_short_live_gate_enabled", lambda: False)()),
        "candidate_fn": getattr(getattr(core, "execute_metals_xau_live_candidate", None), "__name__", None),
        "router_fn": getattr(getattr(core, "execute_metals_demo_candidate", None), "__name__", None),
        "config": {},
        "recent_raw_signals": [],
        "recent_live_links": [],
        "broker_open_trades": [],
        "candidate_callers": {},
        "webhook_source_matches": [],
        "management_source_matches": {},
    }
    try:
        cfg = dict(core.metals_xau_live_config_status() or {})
        out["config"] = {k: v for k, v in cfg.items() if "token" not in str(k).lower() and "secret" not in str(k).lower() and "key" not in str(k).lower()}
    except Exception as exc:
        out["config_error"] = f"{type(exc).__name__}: {exc}"

    try:
        with core.get_conn() as conn:
            for table, target in (("raw_signals", "recent_raw_signals"), ("metals_xau_live_trade_links", "recent_live_links")):
                try:
                    rows = conn.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 30").fetchall()
                    vals = []
                    for r in rows:
                        try:
                            d = dict(r)
                        except Exception:
                            continue
                        vals.append(_pick(d))
                    out[target] = vals
                except Exception as exc:
                    out[target + "_error"] = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        out["db_error"] = f"{type(exc).__name__}: {exc}"

    try:
        for name, obj in vars(core).items():
            if not callable(obj):
                continue
            try:
                src = inspect.getsource(obj)
            except Exception:
                continue
            if "execute_metals_xau_live_candidate" in src and name != "execute_metals_xau_live_candidate":
                out["candidate_callers"][name] = _source_matches(
                    obj,
                    ("execute_metals_xau_live_candidate", "xau", "side", "long", "short", "candidate", "production"),
                )
    except Exception as exc:
        out["candidate_callers_error"] = f"{type(exc).__name__}: {exc}"

    try:
        for route in getattr(core.app, "routes", []):
            if getattr(route, "path", None) == "/webhook/tradingview":
                endpoint = getattr(route, "endpoint", None)
                out["webhook_endpoint"] = getattr(endpoint, "__name__", None)
                out["webhook_source_matches"] = _source_matches(
                    endpoint,
                    ("execute_metals_xau_live_candidate", "xau", "side", "long", "short", "candidate", "production"),
                )
                break
    except Exception as exc:
        out["webhook_source_error"] = f"{type(exc).__name__}: {exc}"

    # Read-only inspection of the downstream live-XAU ownership/management path.
    for name in (
        "metals_xau_live_broker_snapshot",
        "metals_xau_live_manager_tick",
        "_metals_xau_live_highwater_state",
        "metals_xau_live_harvest_maintenance_tick",
        "_metals_xau_live_recover_broker_only",
        "_metals_xau_live_transaction_owned",
        "_metals_xau_live_pending_close_retry_tick",
    ):
        obj = getattr(core, name, None)
        if callable(obj):
            out["management_source_matches"][name] = _source_matches(
                obj,
                ("currentunits", "units", "side", "long", "short", "owned", "xau_usd", "instrument", "trade_links", "active_exit_policy", "mfe", "close"),
            )

    try:
        account = getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "")
        resp = core._metals_xau_live_request(f"/v3/accounts/{account}/openTrades")
        data = (resp.get("data") or {}) if isinstance(resp, dict) else {}
        trades = data.get("trades") or []
        for t in trades:
            units = t.get("currentUnits")
            try:
                u = float(units)
            except Exception:
                u = None
            out["broker_open_trades"].append({
                "id": t.get("id"),
                "instrument": t.get("instrument"),
                "currentUnits": units,
                "side": "short" if u is not None and u < 0 else ("long" if u is not None and u > 0 else "flat"),
                "openTime": t.get("openTime"),
                "price": t.get("price"),
            })
        out["broker_open_ok"] = bool(resp.get("ok")) if isinstance(resp, dict) else False
        out["broker_open_error"] = resp.get("error") if isinstance(resp, dict) else None
    except Exception as exc:
        out["broker_error"] = f"{type(exc).__name__}: {exc}"

    print("XAU_SHORT_RUNTIME_DIAGNOSTIC " + json.dumps(out, default=str, separators=(",", ":")), flush=True)
    return out
