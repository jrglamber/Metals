"""Fail-closed XAG SHORT live pilot.

Promotes only NEW governed XAGUSD SHORT practice entries into the existing
Metals live OANDA account. It deliberately mirrors the post-2026-10-13
governance path so XAG LONG remains research-only and the 24-position /
4-hour transition controls remain authoritative.

Safety:
- XAG_USD only, SHORT only
- exactly one whole XAG unit during pilot
- ~£2 target risk with 5.24% emergency stop; fail closed above +25% risk
- broker stop must be attached and verified immediately after fill
- dedicated XAG live tables; never writes XAG rows into XAU live tables
- MFE25 after 48h, matching the current new-XAG-SHORT policy
- close retries remain durable and are retried on later signal ticks
- no retroactive mirroring of pre-promotion practice trades
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import xag_short_live_probe as sizing_probe

VERSION = "xag_short_live_pilot_v1_2026_10_31"
INSTRUMENT = "XAG_USD"
ASSET = "XAGUSD"
SIDE = "short"
POLICY = "MFE_GIVEBACK_25"
POLICY_VERSION = "metals_xag_short_mfe25_48h_v1_2026_10_13"
TARGET_RISK_GBP = 2.0
SL_PCT = 5.24
PILOT_UNITS = 1.0
MAX_RISK_OVERAGE_PCT = 25.0
MAX_OPEN = 24
MIN_HOLD = 48
GIVEBACK_FRACTION = 0.25
_TRUE = {"1", "true", "yes", "on"}


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in _TRUE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _num(core: Any, value: Any) -> Optional[float]:
    try:
        return core.safe_float(value)
    except Exception:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None


def config_status(core: Any) -> Dict[str, Any]:
    try:
        shared = dict(core.metals_xau_live_config_status() or {})
    except Exception as exc:
        shared = {"status_error": f"{type(exc).__name__}: {exc}"}
    promoted = _enabled("METALS_XAG_SHORT_LIVE_PROMOTION_ENABLED", False)
    ready = _enabled("METALS_XAG_SHORT_LIVE_EXECUTOR_READY", False)
    armed = _enabled("METALS_XAG_SHORT_LIVE_EXECUTION_ARMED", False)
    live_environment_ok = str(shared.get("environment") or "").lower() == "live"
    credentials_ok = bool(getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "")) and bool(getattr(core, "METALS_XAU_LIVE_OANDA_API_TOKEN", ""))
    manager_enabled = _enabled("METALS_XAG_SHORT_LIVE_MANAGER_ENABLED", True)
    orders_allowed = bool(promoted and ready and armed and live_environment_ok and credentials_ok)
    management_writes_allowed = bool(ready and manager_enabled and live_environment_ok and credentials_ok)
    return {
        "ok": True,
        "version": VERSION,
        "asset": ASSET,
        "instrument": INSTRUMENT,
        "side": SIDE,
        "promotion_enabled": promoted,
        "executor_ready": ready,
        "execution_armed": armed,
        "orders_allowed": orders_allowed,
        "management_writes_allowed": management_writes_allowed,
        "manager_enabled": manager_enabled,
        "shared_live_transport_write_ready": bool(shared.get("orders_allowed")),
        "live_environment_ok": live_environment_ok,
        "credentials_ok": credentials_ok,
        "account_currency_required": "GBP",
        "pilot_units": PILOT_UNITS,
        "target_risk_gbp": float(os.getenv("METALS_XAG_SHORT_LIVE_RISK_AMOUNT", TARGET_RISK_GBP)),
        "sl_pct": float(os.getenv("METALS_XAG_SHORT_LIVE_SL_PCT", SL_PCT)),
        "max_risk_overage_pct": MAX_RISK_OVERAGE_PCT,
        "max_open_trades": MAX_OPEN,
        "active_exit_policy": POLICY,
        "active_exit_policy_version": POLICY_VERSION,
        "min_hold_candles": MIN_HOLD,
        "mfe_giveback_fraction": GIVEBACK_FRACTION,
        "xag_long_live_allowed": False,
        "retroactive_mirroring": False,
    }


def _ensure_tables(core: Any) -> None:
    core.init_db()
    with core.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metals_xag_live_trade_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                raw_signal_id INTEGER NOT NULL UNIQUE,
                demo_link_id INTEGER,
                asset TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                signal_time TEXT,
                entry_signal_id INTEGER,
                requested_risk_amount REAL,
                estimated_risk_amount REAL,
                requested_units REAL,
                filled_units REAL,
                entry_price REAL,
                stop_price REAL,
                current_stop_price REAL,
                broker_trade_id TEXT UNIQUE,
                broker_order_id TEXT,
                broker_open_time_utc TEXT,
                status TEXT NOT NULL,
                active_exit_policy TEXT,
                active_exit_policy_version TEXT,
                manager_high_water_r REAL,
                manager_mfe_r REAL,
                manager_mae_r REAL,
                manager_last_review_signal_id INTEGER,
                manager_last_review_candles INTEGER,
                manager_last_decision TEXT,
                manager_last_reason TEXT,
                fixed_48h_price REAL,
                fixed_48h_r REAL,
                fixed_48h_recorded_at_utc TEXT,
                realized_pl REAL,
                closed_at_utc TEXT,
                close_reason TEXT,
                last_reconciled_at_utc TEXT,
                raw_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metals_xag_live_execution_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at_utc TEXT NOT NULL,
                raw_signal_id INTEGER,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                link_id INTEGER,
                broker_trade_id TEXT,
                raw_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_xag_live_links_status ON metals_xag_live_trade_links(status, created_at_utc)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_xag_live_audit_raw_signal ON metals_xag_live_execution_audit(raw_signal_id, created_at_utc)")
        conn.commit()


def _audit(core: Any, raw_signal_id: Optional[int], action: str, status: str, reason: str = "",
           link_id: Optional[int] = None, broker_trade_id: str = "", raw: Optional[Dict[str, Any]] = None) -> None:
    try:
        with core.get_conn() as conn:
            conn.execute(
                """INSERT INTO metals_xag_live_execution_audit
                   (created_at_utc,raw_signal_id,action,status,reason,link_id,broker_trade_id,raw_json)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (_now(), int(raw_signal_id) if raw_signal_id else None, str(action), str(status),
                 str(reason or ""), int(link_id) if link_id else None, str(broker_trade_id or ""),
                 json.dumps(raw or {}, default=str)[:20000]),
            )
            conn.commit()
    except Exception:
        pass


def _request(core: Any, path: str, method: str = "GET", body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Dedicated XAG transport over the existing live Metals OANDA credentials.

    It intentionally does NOT inherit XAU's entry-arm boolean. XAG entry writes
    are gated by config_status().orders_allowed, while protective stop/close
    writes remain available for already-open XAG positions when new entries are
    disarmed.
    """
    account_id = str(getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "") or "").strip()
    token = str(getattr(core, "METALS_XAU_LIVE_OANDA_API_TOKEN", "") or "").strip()
    env = str(getattr(core, "METALS_XAU_LIVE_OANDA_ENV", "") or "").strip().lower()
    base = str(getattr(core, "METALS_XAU_LIVE_OANDA_API_BASE", "https://api-fxtrade.oanda.com") or "").rstrip("/")
    if not account_id or not token:
        return {"ok": False, "blocked": True, "error": "live_metals_credentials_missing"}
    if env != "live":
        return {"ok": False, "blocked": True, "error": "xag_live_lane_requires_live_oanda_environment"}
    clean_path = "/" + str(path or "").lstrip("/")
    data = None if str(method).upper() == "GET" else json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        base + clean_path,
        data=data,
        method=str(method).upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    timeout = float(getattr(core, "OANDA_TIMEOUT_SECONDS", 15.0) or 15.0)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            try:
                payload = json.loads(text) if text else {}
            except Exception:
                payload = {"raw_body": text}
            return {"ok": True, "status_code": int(getattr(resp, "status", 200) or 200), "data": payload, "url_path": clean_path}
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else {}
        except Exception:
            payload = {"raw_body": text}
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "data": payload,
            "error": str(payload.get("errorMessage") or payload.get("errorCode") or text or exc),
            "url_path": clean_path,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": None,
            "error": f"{type(exc).__name__}: {exc}",
            "url_path": clean_path,
        }


def _account_id(core: Any) -> str:
    return str(getattr(core, "METALS_XAU_LIVE_OANDA_ACCOUNT_ID", "") or "").strip()


def _open_trades(core: Any) -> Dict[str, Any]:
    aid = _account_id(core)
    if not aid:
        return {"ok": False, "error": "live_account_id_missing", "xag": []}
    resp = _request(core, f"/v3/accounts/{aid}/openTrades", "GET")
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error") or "open_trades_read_failed", "response": resp, "xag": []}
    trades = list((resp.get("data") or {}).get("trades") or [])
    xag = []
    for t in trades:
        td = _d(t)
        if str(td.get("instrument") or "").upper() == INSTRUMENT:
            xag.append(td)
    return {"ok": True, "xag": xag, "all_count": len(trades)}


def _local_open(core: Any) -> List[Dict[str, Any]]:
    with core.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM metals_xag_live_trade_links
               WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')
               ORDER BY id"""
        ).fetchall()
    return [_d(r) for r in rows]


def _reconcile(core: Any) -> Dict[str, Any]:
    snap = _open_trades(core)
    if not snap.get("ok"):
        return {"ok": False, "reason": "broker_snapshot_failed", "snapshot": snap}
    broker = {str(t.get("id") or ""): t for t in snap.get("xag") or [] if str(t.get("id") or "")}
    local = _local_open(core)
    local_ids = {str(x.get("broker_trade_id") or "") for x in local if str(x.get("broker_trade_id") or "")}
    unlinked = [t for bid, t in broker.items() if bid not in local_ids]

    for link in local:
        bid = str(link.get("broker_trade_id") or "")
        if not bid or bid in broker:
            continue
        detail = _request(core, f"/v3/accounts/{_account_id(core)}/trades/{bid}", "GET")
        trade = _d((detail.get("data") or {}).get("trade")) if detail.get("ok") else {}
        if str(trade.get("state") or "").upper() == "CLOSED":
            realized = _num(core, trade.get("realizedPL"))
            close_price = _num(core, trade.get("averageClosePrice"))
            with core.get_conn() as conn:
                conn.execute(
                    """UPDATE metals_xag_live_trade_links
                       SET updated_at_utc=?,status='CLOSED_RECONCILED',realized_pl=?,
                           closed_at_utc=COALESCE(NULLIF(closed_at_utc,''),?),
                           close_reason=COALESCE(NULLIF(close_reason,''),'BROKER_RECONCILED_CLOSED'),
                           last_reconciled_at_utc=?,raw_json=raw_json
                       WHERE id=?""",
                    (_now(), realized, _now(), _now(), int(link["id"])),
                )
                conn.commit()
            _audit(core, link.get("raw_signal_id"), "reconcile", "CLOSED_RECONCILED",
                   f"broker trade closed; averageClosePrice={close_price}", int(link["id"]), bid)

    return {
        "ok": True,
        "broker_xag_open_count": len(broker),
        "local_xag_open_count": len(local),
        "unlinked_broker_xag_count": len(unlinked),
        "unlinked_broker_xag_ids": [str(t.get("id") or "") for t in unlinked],
        "has_broker_long": any(float(_num(core, t.get("currentUnits")) or 0.0) > 0 for t in broker.values()),
    }


def _verify_stop(core: Any, broker_trade_id: str, fill_price: float) -> Dict[str, Any]:
    resp = _request(core, f"/v3/accounts/{_account_id(core)}/trades/{broker_trade_id}", "GET")
    if not resp.get("ok"):
        return {"ok": False, "reason": "trade_detail_read_failed", "response": resp}
    trade = _d((resp.get("data") or {}).get("trade"))
    sl = _d(trade.get("stopLossOrder"))
    price = _num(core, sl.get("price"))
    units = _num(core, trade.get("currentUnits"))
    valid = bool(
        str(trade.get("instrument") or "").upper() == INSTRUMENT
        and units is not None and units < 0
        and price is not None and price > float(fill_price)
    )
    return {
        "ok": valid,
        "reason": "verified" if valid else "missing_or_invalid_short_stop",
        "stop_price": price,
        "current_units": units,
        "state": trade.get("state"),
    }


def _close_link(core: Any, link: Dict[str, Any], reason: str) -> Dict[str, Any]:
    cfg = config_status(core)
    if not cfg.get("management_writes_allowed"):
        return {"ok": False, "blocked": True, "reason": "xag_live_management_writes_not_ready"}
    bid = str(link.get("broker_trade_id") or "")
    if not bid:
        return {"ok": False, "reason": "broker_trade_id_missing"}
    last: Dict[str, Any] = {}
    for attempt in range(1, 4):
        resp = _request(core, f"/v3/accounts/{_account_id(core)}/trades/{bid}/close", "PUT", {"units": "ALL"})
        last = resp
        if resp.get("ok"):
            data = resp.get("data") or {}
            fill = data.get("orderFillTransaction") or {}
            realized = _num(core, fill.get("pl"))
            with core.get_conn() as conn:
                conn.execute(
                    """UPDATE metals_xag_live_trade_links
                       SET updated_at_utc=?,status='CLOSED',realized_pl=?,closed_at_utc=?,
                           close_reason=?,last_reconciled_at_utc=?
                       WHERE id=?""",
                    (_now(), realized, _now(), str(reason), _now(), int(link["id"])),
                )
                conn.commit()
            _audit(core, link.get("raw_signal_id"), "close", "CLOSED", reason, int(link["id"]), bid,
                   {"attempt": attempt, "response": data})
            return {"ok": True, "attempt": attempt, "realized_pl": realized}
        try:
            if bool(core.is_oanda_timeout_or_unknown_response(resp)):
                break
        except Exception:
            pass

    with core.get_conn() as conn:
        conn.execute(
            """UPDATE metals_xag_live_trade_links
               SET updated_at_utc=?,status='CLOSE_RETRY',close_reason=?
               WHERE id=?""",
            (_now(), str(reason), int(link["id"])),
        )
        conn.commit()
    _audit(core, link.get("raw_signal_id"), "close", "CLOSE_RETRY", reason, int(link["id"]), bid, {"response": last})
    return {"ok": False, "retry": True, "response": last}


def _update_stop(core: Any, link: Dict[str, Any], stop_price: float) -> Dict[str, Any]:
    cfg = config_status(core)
    if not cfg.get("management_writes_allowed"):
        return {"ok": False, "blocked": True, "reason": "xag_live_management_writes_not_ready"}
    bid = str(link.get("broker_trade_id") or "")
    dp = 3
    try:
        probe = sizing_probe.snapshot(core)
        dp = int(probe.get("display_precision") or 3)
    except Exception:
        pass
    body = {"stopLoss": {"timeInForce": "GTC", "price": f"{float(stop_price):.{dp}f}"}}
    resp = _request(core, f"/v3/accounts/{_account_id(core)}/trades/{bid}/orders", "PUT", body)
    if resp.get("ok"):
        with core.get_conn() as conn:
            conn.execute(
                "UPDATE metals_xag_live_trade_links SET updated_at_utc=?,current_stop_price=? WHERE id=?",
                (_now(), float(stop_price), int(link["id"])),
            )
            conn.commit()
        _audit(core, link.get("raw_signal_id"), "stop_update", "UPDATED",
               f"MFE25 stop={stop_price}", int(link["id"]), bid)
        return {"ok": True, "stop_price": stop_price}
    _audit(core, link.get("raw_signal_id"), "stop_update", "FAILED",
           "MFE25 stop update failed", int(link["id"]), bid, {"response": resp})
    return {"ok": False, "response": resp}


def _manager_tick(core: Any, raw_signal_id: int) -> Dict[str, Any]:
    rec = _reconcile(core)
    if not rec.get("ok"):
        return {"ok": False, "reason": "reconciliation_failed", "reconcile": rec}
    results = []
    for link in _local_open(core):
        if str(link.get("status") or "") == "CLOSE_RETRY":
            results.append({"link_id": link.get("id"), "action": "close_retry",
                            "result": _close_link(core, link, str(link.get("close_reason") or "MFE25_CLOSE_RETRY"))})
            continue
        if int(_num(core, link.get("manager_last_review_signal_id")) or 0) == int(raw_signal_id):
            continue
        metrics = core._metals_demo_trade_metrics(link)
        if not metrics.get("ok"):
            results.append({"link_id": link.get("id"), "action": "metrics_failed", "metrics": metrics})
            continue

        hold = int(metrics.get("hold_candles") or 0)
        current_r = _num(core, metrics.get("current_r"))
        mfe_r = max(0.0, float(_num(core, metrics.get("mfe_r")) or 0.0))
        mae_r = _num(core, metrics.get("mae_r"))
        hwm = max(float(_num(core, link.get("manager_high_water_r")) or 0.0), mfe_r, float(current_r or 0.0))
        decision = "HOLD_MIN_48" if hold < MIN_HOLD else "EXTEND"
        reason = "XAG SHORT MFE25 normal exit locked before 48h; emergency broker stop active."

        if hold >= MIN_HOLD and mfe_r > 0 and current_r is not None:
            floor_r = mfe_r * (1.0 - GIVEBACK_FRACTION)
            if current_r <= floor_r:
                decision = "CLOSE_MFE25_GIVEBACK"
                reason = f"XAG SHORT MFE25 floor reached: current={current_r:.3f}R floor={floor_r:.3f}R."
                close_result = _close_link(core, link, reason)
                results.append({"link_id": link.get("id"), "decision": decision, "result": close_result})
            else:
                entry = float(_num(core, link.get("entry_price")) or 0.0)
                current = float(_num(core, metrics.get("current_price")) or 0.0)
                initial_stop = float(_num(core, link.get("stop_price")) or (entry * (1.0 + SL_PCT / 100.0)))
                risk_price = max(0.0, initial_stop - entry)
                stop_candidate = entry - floor_r * risk_price if entry > 0 and risk_price > 0 else None
                previous = _num(core, link.get("current_stop_price"))
                min_step = entry * 0.0002
                if (
                    stop_candidate is not None and current > 0 and stop_candidate > current
                    and (previous is None or stop_candidate < float(previous) - min_step)
                ):
                    stop_result = _update_stop(core, link, stop_candidate)
                    results.append({"link_id": link.get("id"), "decision": "EXTEND",
                                    "stop_candidate": stop_candidate, "stop_result": stop_result})
                else:
                    results.append({"link_id": link.get("id"), "decision": "EXTEND"})
                reason = f"XAG SHORT MFE25 active; retaining 75% of MFE ({floor_r:.3f}R floor)."
        elif hold < MIN_HOLD:
            results.append({"link_id": link.get("id"), "decision": decision})

        with core.get_conn() as conn:
            fixed_price = _num(core, metrics.get("fixed_48h_price"))
            fixed_r = _num(core, metrics.get("fixed_48h_r"))
            conn.execute(
                """UPDATE metals_xag_live_trade_links
                   SET updated_at_utc=?,manager_high_water_r=?,manager_mfe_r=?,manager_mae_r=?,
                       manager_last_review_signal_id=?,manager_last_review_candles=?,
                       manager_last_decision=?,manager_last_reason=?,
                       fixed_48h_price=COALESCE(fixed_48h_price,?),
                       fixed_48h_r=COALESCE(fixed_48h_r,?),
                       fixed_48h_recorded_at_utc=CASE WHEN fixed_48h_r IS NULL AND ? IS NOT NULL THEN ? ELSE fixed_48h_recorded_at_utc END
                   WHERE id=?""",
                (_now(), hwm, mfe_r, mae_r, int(raw_signal_id), hold, decision, reason,
                 fixed_price, fixed_r, fixed_r, _now(), int(link["id"])),
            )
            conn.commit()

    return {"ok": True, "reconcile": rec, "reviewed": len(results), "results": results}


def _execute_live(core: Any, raw_signal_id: int, demo_result: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config_status(core)
    if not cfg.get("orders_allowed"):
        return {"ok": True, "blocked": True, "reason": "xag_live_not_armed", "config": cfg}

    if not (
        demo_result.get("opened")
        and str(demo_result.get("asset") or "").upper() == ASSET
        and str(demo_result.get("side") or "").lower() == SIDE
    ):
        return {"ok": True, "skipped": True, "reason": "governed_practice_entry_not_opened"}

    pol = _d(demo_result.get("active_exit_policy"))
    if str(pol.get("policy") or "").upper() != POLICY:
        _audit(core, raw_signal_id, "entry", "BLOCKED", "governed_xag_policy_not_mfe25", raw={"demo_result": demo_result})
        return {"ok": False, "blocked": True, "reason": "governed_xag_policy_not_mfe25", "policy": pol}

    with core.get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM metals_xag_live_trade_links WHERE raw_signal_id=? LIMIT 1",
            (int(raw_signal_id),),
        ).fetchone()
    if existing:
        return {"ok": True, "duplicate": True, "link_id": _d(existing).get("id")}

    rec = _reconcile(core)
    if not rec.get("ok"):
        return {"ok": False, "blocked": True, "reason": "pre_entry_reconciliation_failed", "reconcile": rec}
    if rec.get("unlinked_broker_xag_count"):
        _audit(core, raw_signal_id, "entry", "BLOCKED", "unlinked_broker_xag_trade_present", raw=rec)
        return {"ok": False, "blocked": True, "reason": "unlinked_broker_xag_trade_present", "reconcile": rec}
    if rec.get("has_broker_long"):
        _audit(core, raw_signal_id, "entry", "BLOCKED", "live_xag_long_present", raw=rec)
        return {"ok": False, "blocked": True, "reason": "live_xag_long_present", "reconcile": rec}
    if int(rec.get("local_xag_open_count") or 0) >= MAX_OPEN:
        return {"ok": True, "blocked": True, "reason": "live_xag_short_cap_24", "count": rec.get("local_xag_open_count")}

    probe = sizing_probe.snapshot(core)
    if not probe.get("ok"):
        _audit(core, raw_signal_id, "entry", "BLOCKED", "xag_live_sizing_probe_failed", raw=probe)
        return {"ok": False, "blocked": True, "reason": "xag_live_sizing_probe_failed", "probe": probe}
    if str(probe.get("oanda_account_currency") or "").upper() != "GBP":
        return {"ok": False, "blocked": True, "reason": "live_account_not_gbp"}
    if str(probe.get("price_status") or "").lower() != "tradeable":
        return {"ok": True, "blocked": True, "reason": "xag_market_not_tradeable", "price_status": probe.get("price_status")}
    units = float(_num(core, probe.get("recommended_units")) or 0.0)
    min_units = float(_num(core, probe.get("minimum_trade_size")) or 0.0)
    effective_risk = float(_num(core, probe.get("recommended_effective_risk_gbp")) or 0.0)
    target_risk = float(_num(core, probe.get("target_risk_gbp")) or TARGET_RISK_GBP)
    max_risk = target_risk * (1.0 + MAX_RISK_OVERAGE_PCT / 100.0)
    if units != PILOT_UNITS or min_units != PILOT_UNITS:
        _audit(core, raw_signal_id, "entry", "BLOCKED", "pilot_requires_exactly_one_xag_unit", raw=probe)
        return {"ok": False, "blocked": True, "reason": "pilot_requires_exactly_one_xag_unit", "probe": probe}
    if effective_risk <= 0 or effective_risk > max_risk:
        return {"ok": False, "blocked": True, "reason": "xag_effective_risk_outside_pilot_limit",
                "effective_risk_gbp": effective_risk, "max_risk_gbp": max_risk}

    entry = float(_num(core, probe.get("entry_price")) or 0.0)
    stop = float(_num(core, probe.get("emergency_stop_price")) or 0.0)
    if entry <= 0 or stop <= entry:
        return {"ok": False, "blocked": True, "reason": "short_stop_safety_invariant_failed"}
    dp = int(probe.get("display_precision") or 3)
    body = {
        "order": {
            "units": "-1",
            "instrument": INSTRUMENT,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{stop:.{dp}f}", "timeInForce": "GTC"},
        }
    }
    attempted_at = _now()
    _audit(core, raw_signal_id, "entry", "SUBMITTING", "governed XAG SHORT live pilot", raw={
        "demo_link_id": demo_result.get("link_id"),
        "units": -1,
        "target_risk_gbp": target_risk,
        "estimated_risk_gbp": effective_risk,
        "stop_price": stop,
    })
    response = _request(core, f"/v3/accounts/{_account_id(core)}/orders", "POST", body)
    data = response.get("data") or {}
    fill = data.get("orderFillTransaction") or {}
    opened = fill.get("tradeOpened") or {}
    broker_trade_id = str(opened.get("tradeID") or "")
    broker_order_id = str(fill.get("id") or (data.get("orderCreateTransaction") or {}).get("id") or "")
    fill_units = float(_num(core, opened.get("units")) or _num(core, fill.get("units")) or -1.0)
    fill_price = float(_num(core, fill.get("price")) or entry)

    if not response.get("ok") or not broker_trade_id:
        ambiguous = False
        try:
            ambiguous = bool(core.is_oanda_timeout_or_unknown_response(response))
        except Exception:
            ambiguous = False
        if ambiguous:
            snap = _open_trades(core)
            if snap.get("ok"):
                with core.get_conn() as conn:
                    known_rows = conn.execute(
                        "SELECT broker_trade_id FROM metals_xag_live_trade_links WHERE broker_trade_id IS NOT NULL"
                    ).fetchall()
                known = {str(_d(r).get("broker_trade_id") or "") for r in known_rows}
                candidates = []
                for t in snap.get("xag") or []:
                    bid = str(t.get("id") or "")
                    u = float(_num(core, t.get("currentUnits")) or 0.0)
                    ot = str(t.get("openTime") or "")
                    if bid and bid not in known and u < 0 and (not attempted_at or ot >= attempted_at[:19]):
                        candidates.append(t)
                if len(candidates) == 1:
                    t = candidates[0]
                    broker_trade_id = str(t.get("id") or "")
                    fill_units = float(_num(core, t.get("currentUnits")) or -1.0)
                    fill_price = float(_num(core, t.get("price")) or entry)
        if not broker_trade_id:
            _audit(core, raw_signal_id, "entry", "FAILED", "live XAG order failed or ambiguous", raw={"response": response})
            return {"ok": False, "reason": "live_xag_order_failed", "response": response}

    if fill_units >= 0:
        _audit(core, raw_signal_id, "entry", "CRITICAL", "broker returned non-short units", broker_trade_id=broker_trade_id)
        _request(core, f"/v3/accounts/{_account_id(core)}/trades/{broker_trade_id}/close", "PUT", {"units": "ALL"})
        return {"ok": False, "reason": "broker_short_direction_invariant_failed", "broker_trade_id": broker_trade_id}

    now = _now()
    with core.get_conn() as conn:
        er = conn.execute("SELECT timestamp_readable FROM raw_signals WHERE id=? LIMIT 1", (int(raw_signal_id),)).fetchone()
        signal_time = str(_d(er).get("timestamp_readable") or "")
        link_id = int(core.db_insert_returning_id(
            conn,
            """INSERT INTO metals_xag_live_trade_links
               (created_at_utc,updated_at_utc,raw_signal_id,demo_link_id,asset,instrument,side,
                signal_time,entry_signal_id,requested_risk_amount,estimated_risk_amount,
                requested_units,filled_units,entry_price,stop_price,current_stop_price,
                broker_trade_id,broker_order_id,broker_open_time_utc,status,
                active_exit_policy,active_exit_policy_version,last_reconciled_at_utc,raw_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, now, int(raw_signal_id), int(demo_result.get("link_id") or 0), ASSET, INSTRUMENT, SIDE,
             signal_time, int(raw_signal_id), target_risk, effective_risk, PILOT_UNITS, fill_units,
             fill_price, stop, stop, broker_trade_id, broker_order_id,
             str(fill.get("time") or attempted_at), "OPEN_UNVERIFIED_STOP",
             POLICY, POLICY_VERSION, now,
             json.dumps({"demo_result": demo_result, "probe": probe, "response": data}, default=str)),
        ))
        conn.commit()

    verified = _verify_stop(core, broker_trade_id, fill_price)
    if not verified.get("ok"):
        with core.get_conn() as conn:
            conn.execute(
                "UPDATE metals_xag_live_trade_links SET updated_at_utc=?,status='CLOSE_RETRY',close_reason=? WHERE id=?",
                (_now(), "UNVERIFIED_EMERGENCY_STOP", link_id),
            )
            conn.commit()
        link = {"id": link_id, "raw_signal_id": raw_signal_id, "broker_trade_id": broker_trade_id,
                "close_reason": "UNVERIFIED_EMERGENCY_STOP"}
        closed = _close_link(core, link, "UNVERIFIED_EMERGENCY_STOP")
        _audit(core, raw_signal_id, "entry_stop_verify", "FAILED", "stop not verified; emergency close attempted",
               link_id, broker_trade_id, {"verified": verified, "close": closed})
        return {"ok": False, "reason": "emergency_stop_not_verified", "emergency_close": closed}

    with core.get_conn() as conn:
        conn.execute(
            """UPDATE metals_xag_live_trade_links
               SET updated_at_utc=?,status='OPEN',current_stop_price=?,last_reconciled_at_utc=?
               WHERE id=?""",
            (_now(), verified.get("stop_price"), _now(), link_id),
        )
        conn.commit()
    _audit(core, raw_signal_id, "entry", "OPENED",
           f"LIVE XAG SHORT opened; {POLICY}; one-unit pilot", link_id, broker_trade_id,
           {"estimated_risk_gbp": effective_risk, "verified_stop": verified})
    return {
        "ok": True,
        "opened": True,
        "execution_lane": "LIVE_XAG_SHORT",
        "asset": ASSET,
        "side": SIDE,
        "link_id": link_id,
        "broker_trade_id": broker_trade_id,
        "filled_units": fill_units,
        "entry_price": fill_price,
        "effective_risk_gbp": effective_risk,
        "active_exit_policy": {"policy": POLICY, "version": POLICY_VERSION},
        "emergency_stop_verified": True,
    }


def runtime_status(core: Any) -> Dict[str, Any]:
    cfg = config_status(core)
    try:
        _ensure_tables(core)
        with core.get_conn() as conn:
            open_count = int(conn.execute(
                "SELECT COUNT(*) AS c FROM metals_xag_live_trade_links WHERE status IN ('OPEN','OPEN_UNVERIFIED_STOP','CLOSE_RETRY')"
            ).fetchone()["c"] or 0)
            total = int(conn.execute("SELECT COUNT(*) AS c FROM metals_xag_live_trade_links").fetchone()["c"] or 0)
        tables_ok = True
    except Exception as exc:
        open_count, total, tables_ok = 0, 0, False
        cfg["table_error"] = f"{type(exc).__name__}: {exc}"
    cfg.update({
        "installed": True,
        "tables_ok": tables_ok,
        "open_live_xag_short_count": open_count,
        "total_live_xag_short_links": total,
        "safe_to_mark_executor_ready": bool(tables_ok and cfg.get("live_environment_ok") and cfg.get("credentials_ok")),
    })
    return cfg


def install(core: Any, app: Any) -> Dict[str, Any]:
    _ensure_tables(core)
    original_router = core.execute_metals_demo_candidate

    def xag_live_router(raw_signal_id: int, source: str = "signal_worker", *args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_router(int(raw_signal_id), source=source, *args, **kwargs)
        live_result: Dict[str, Any] = {"ok": True, "skipped": True, "reason": "not_new_governed_xag_short"}
        try:
            if (
                result.get("opened")
                and str(result.get("asset") or "").upper() == ASSET
                and str(result.get("side") or "").lower() == SIDE
            ):
                live_result = _execute_live(core, int(raw_signal_id), result)
        except Exception as exc:
            live_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "fail_closed": True}
            _audit(core, raw_signal_id, "entry", "EXCEPTION", live_result["error"])
        try:
            manager_result = _manager_tick(core, int(raw_signal_id))
        except Exception as exc:
            manager_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "fail_closed": True}
        if str(result.get("asset") or "").upper() == ASSET:
            out = dict(result)
            out["xag_short_live"] = live_result
            out["xag_short_live_manager"] = manager_result
            return out
        return result

    core.execute_metals_demo_candidate = xag_live_router
    core.xag_short_live_config_status = lambda: config_status(core)
    core.xag_short_live_runtime_status = lambda: runtime_status(core)
    core.xag_short_live_manager_tick = lambda raw_signal_id=0: _manager_tick(core, int(raw_signal_id or 0))
    core.XAG_SHORT_LIVE_VERSION = VERSION

    @app.get("/api/xag-short-live-status", include_in_schema=False)
    def xag_short_live_status_route() -> Dict[str, Any]:
        return runtime_status(core)

    @app.get("/api/xag-short-live-links", include_in_schema=False)
    def xag_short_live_links_route() -> Dict[str, Any]:
        with core.get_conn() as conn:
            rows = conn.execute(
                """SELECT id,created_at_utc,updated_at_utc,raw_signal_id,demo_link_id,asset,instrument,side,
                          requested_risk_amount,estimated_risk_amount,requested_units,filled_units,entry_price,
                          stop_price,current_stop_price,broker_trade_id,broker_open_time_utc,status,
                          active_exit_policy,active_exit_policy_version,manager_high_water_r,manager_mfe_r,
                          manager_last_review_candles,manager_last_decision,fixed_48h_r,realized_pl,closed_at_utc,close_reason
                   FROM metals_xag_live_trade_links ORDER BY id DESC LIMIT 100"""
            ).fetchall()
        return {"ok": True, "version": VERSION, "rows": [_d(r) for r in rows]}

    status = runtime_status(core)
    return {
        "installed": True,
        "version": VERSION,
        "execution_authority": True,
        "fail_closed": True,
        "xag_short_only": True,
        "xag_long_live_allowed": False,
        "pilot_units": PILOT_UNITS,
        "target_risk_gbp": TARGET_RISK_GBP,
        "sl_pct": SL_PCT,
        "active_exit_policy": POLICY,
        "runtime_status": status,
    }
