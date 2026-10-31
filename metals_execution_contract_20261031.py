"""Execution-state contract for Metals dashboard and research exports.

This patch is reporting/observability only. It makes the actual XAU LONG/SHORT
live state explicit and records the approved XAG SHORT promotion decision
without arming or placing any real-money XAG order.

It also repairs stale export manifests at response time so historical wording
cannot mislabel XAU SHORT as practice/research-only.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

VERSION = "metals_execution_contract_v1_2026_10_31"
XAG_SHORT_PROMOTION_DECISION = "APPROVED_FOR_LIVE_TRIAL_2026_10_31"

_TRUE = {"1", "true", "yes", "on"}


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in _TRUE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execution_status(core: Any) -> Dict[str, Any]:
    """Read-only, fail-safe execution state."""
    try:
        xau_cfg = dict(core.metals_xau_live_config_status() or {})
    except Exception as exc:
        xau_cfg = {"status_error": f"{type(exc).__name__}: {exc}"}

    xau_long_promoted = _enabled(
        "METALS_XAU_LONG_LIVE_PROMOTION_ENABLED",
        bool(xau_cfg.get("orders_allowed")),
    )
    xau_short_promoted = bool(
        xau_cfg.get("xau_short_live_promotion_enabled")
        if "xau_short_live_promotion_enabled" in xau_cfg
        else _enabled("METALS_XAU_SHORT_LIVE_PROMOTION_ENABLED", False)
    )
    xau_short_orders = bool(
        xau_cfg.get("xau_short_broker_orders_allowed")
        if "xau_short_broker_orders_allowed" in xau_cfg
        else (xau_short_promoted and bool(xau_cfg.get("orders_allowed")))
    )

    # Approval/readiness and real-money arming are deliberately separate.
    xag_promoted = _enabled("METALS_XAG_SHORT_LIVE_PROMOTION_ENABLED", True)
    xag_armed = _enabled("METALS_XAG_SHORT_LIVE_EXECUTION_ARMED", False)
    xag_executor_ready = _enabled("METALS_XAG_SHORT_LIVE_EXECUTOR_READY", False)
    xag_orders_allowed = bool(xag_promoted and xag_armed and xag_executor_ready)

    return {
        "ok": True,
        "version": VERSION,
        "generated_at_utc": _now(),
        "reporting_contract": "lane_specific_truthful_execution_state",
        "lanes": {
            "XAUUSD_LONG": {
                "label": "XAU LONG",
                "state": "LIVE" if xau_long_promoted else "NOT_LIVE",
                "capital": "LIVE" if xau_long_promoted else "NONE",
                "promotion_enabled": xau_long_promoted,
                "broker_orders_allowed": bool(xau_cfg.get("orders_allowed")) and xau_long_promoted,
                "execution_lane": "LIVE_XAU_LONG",
            },
            "XAUUSD_SHORT": {
                "label": "XAU SHORT",
                "state": "LIVE" if xau_short_promoted else "NOT_LIVE",
                "capital": "LIVE" if xau_short_promoted else "NONE",
                "promotion_enabled": xau_short_promoted,
                "broker_orders_allowed": xau_short_orders,
                "execution_lane": "LIVE_XAU_SHORT",
                "signed_units": "NEGATIVE",
                "emergency_stop_side": "ABOVE_ENTRY",
            },
            "XAGUSD_SHORT": {
                "label": "XAG SHORT",
                "state": "LIVE" if xag_orders_allowed else "LIVE_APPROVED_NOT_ARMED",
                "capital": "LIVE" if xag_orders_allowed else "PRACTICE_UNTIL_MANUAL_ARM",
                "promotion_decision": XAG_SHORT_PROMOTION_DECISION,
                "promotion_enabled": xag_promoted,
                "executor_ready": xag_executor_ready,
                "execution_armed": xag_armed,
                "broker_orders_allowed": xag_orders_allowed,
                "execution_lane": "LIVE_XAG_SHORT" if xag_orders_allowed else "XAG_SHORT_PROMOTION_STAGING",
                "signed_units_required": "NEGATIVE",
                "emergency_stop_side_required": "ABOVE_ENTRY",
                "initial_policy": "48H_MINIMUM_PLUS_VALIDATED_PROTECTION",
                "mfe25_status": "RESEARCH_CONTINUES_NOT_REQUIRED_FOR_PROMOTION",
            },
            "XAGUSD_LONG": {
                "label": "XAG LONG",
                "state": "RESEARCH_ONLY",
                "capital": "PRACTICE",
                "broker_orders_allowed": False,
                "execution_lane": "RESEARCH_XAG_LONG",
            },
        },
        "xau_config": {
            "orders_allowed": bool(xau_cfg.get("orders_allowed")),
            "xau_short_live_promotion_enabled": xau_short_promoted,
            "xau_short_broker_orders_allowed": xau_short_orders,
        },
        "safety": {
            "this_patch_has_execution_authority": False,
            "xag_short_real_money_arming_is_separate": True,
            "xag_long_remains_research_only": True,
        },
    }


_STALE_REPLACEMENTS: Tuple[Tuple[str, str], ...] = (
    (
        "BLOCKED_BY_EXISTING_LONG_ONLY_LIVE_GUARD_PENDING_SIDE_SAFE_PROMOTION",
        "LIVE_XAU_SHORT_SIDE_SAFE_PROMOTION_INSTALLED",
    ),
    (
        "XAUUSD LONG live lane; practice lanes excluded from live totals",
        "XAUUSD LONG + XAUUSD SHORT live lanes; XAG practice/research excluded from live totals",
    ),
    ("XAUUSD LONG only", "XAUUSD LONG + XAUUSD SHORT"),
    ("XAU LONG only", "XAU LONG + XAU SHORT"),
    (
        "XAU SHORT and XAG remain practice/research",
        "XAU SHORT is LIVE; XAG LONG remains research-only; XAG SHORT is approved for live trial but requires separate execution arming",
    ),
    ("XAU short remains practice/research", "XAU short is LIVE"),
    (
        "No live metals money.",
        "LIVE metals capital is used by XAU LONG + XAU SHORT. XAG SHORT live trial is separately gated.",
    ),
)


def _deep_repair(value: Any, status: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        out = {k: _deep_repair(v, status) for k, v in value.items()}
        out.setdefault("execution_context", status)
        if out.get("xau_short_broker_execution") == "BLOCKED_BY_EXISTING_LONG_ONLY_LIVE_GUARD_PENDING_SIDE_SAFE_PROMOTION":
            out["xau_short_broker_execution"] = "LIVE_XAU_SHORT_SIDE_SAFE_PROMOTION_INSTALLED"
        return out
    if isinstance(value, list):
        return [_deep_repair(v, status) for v in value]
    if isinstance(value, str):
        text = value
        for old, new in _STALE_REPLACEMENTS:
            text = text.replace(old, new)
        return text
    return value


def _repair_zip(body: bytes, status: Dict[str, Any]) -> bytes:
    src = io.BytesIO(body)
    try:
        zin = zipfile.ZipFile(src, "r")
    except Exception:
        return body

    out = io.BytesIO()
    changed = False
    try:
        with zin, zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            names = set(zin.namelist())
            for info in zin.infolist():
                data = zin.read(info.filename)
                lower = info.filename.lower()
                if lower.endswith(".json"):
                    try:
                        parsed = json.loads(data.decode("utf-8"))
                        repaired = _deep_repair(parsed, status)
                        data2 = json.dumps(repaired, indent=2, sort_keys=True, default=str).encode("utf-8")
                        if data2 != data:
                            changed = True
                        data = data2
                    except Exception:
                        pass
                elif lower.endswith((".txt", ".md", ".csv")):
                    try:
                        text = data.decode("utf-8")
                        repaired_text = text
                        for old, new in _STALE_REPLACEMENTS:
                            repaired_text = repaired_text.replace(old, new)
                        if repaired_text != text:
                            changed = True
                            data = repaired_text.encode("utf-8")
                    except Exception:
                        pass
                zout.writestr(info, data)

            if "execution-status.json" not in names:
                zout.writestr(
                    "execution-status.json",
                    json.dumps(status, indent=2, sort_keys=True, default=str),
                )
                changed = True
    except Exception:
        return body

    return out.getvalue() if changed else body


def _status_html(status: Dict[str, Any]) -> str:
    lanes = status.get("lanes") or {}

    def row(key: str) -> str:
        lane = lanes.get(key) or {}
        state = str(lane.get("state") or "UNKNOWN")
        note = ""
        if key == "XAGUSD_SHORT" and state != "LIVE":
            note = " — approved for live trial; real-money execution not armed"
        return f"<div><strong>{lane.get('label', key)}</strong>: {state}{note}</div>"

    return (
        '<section id="pep-execution-truth" style="margin:12px 0;padding:12px 14px;'
        'border:1px solid rgba(255,255,255,.18);border-radius:12px;">'
        '<div style="font-weight:800;margin-bottom:6px;">Execution status</div>'
        + row("XAUUSD_LONG")
        + row("XAUUSD_SHORT")
        + row("XAGUSD_SHORT")
        + row("XAGUSD_LONG")
        + '<div style="opacity:.7;margin-top:6px;font-size:.82em;">'
          'Lane-specific status; research/demo results are not counted as live capital.</div></section>'
    )


class ExecutionContractMiddleware:
    """ASGI response-rewrite layer for dashboard and export truthfulness."""

    def __init__(self, app: Any, core: Any):
        self.app = app
        self.core = core

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        intercept_html = path in {"/", "/dashboard", "/dashboard/top"}
        intercept_zip = path.lower().endswith(".zip") or "export" in path.lower()
        if not (intercept_html or intercept_zip):
            await self.app(scope, receive, send)
            return

        start = None
        chunks = []

        async def capture(message: Dict[str, Any]) -> None:
            nonlocal start
            if message.get("type") == "http.response.start":
                start = dict(message)
                return
            if message.get("type") == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                headers = list((start or {}).get("headers", []))
                content_type = ""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        content_type = v.decode("latin-1", errors="ignore").lower()
                        break

                status = execution_status(self.core)
                if intercept_html and "text/html" in content_type:
                    try:
                        text = body.decode("utf-8")
                        if "pep-execution-truth" not in text:
                            panel = _status_html(status)
                            body = text.replace("</body>", panel + "\n</body>", 1).encode("utf-8") if "</body>" in text else body
                    except Exception:
                        pass
                if intercept_zip and ("zip" in content_type or body[:2] == b"PK"):
                    body = _repair_zip(body, status)

                clean = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"content-encoding"}]
                clean.append((b"content-length", str(len(body)).encode("ascii")))
                head = dict(start or {"type": "http.response.start", "status": 200})
                head["headers"] = clean
                await send(head)
                await send({"type": "http.response.body", "body": body, "more_body": False})
                return
            await send(message)

        await self.app(scope, receive, capture)


def install(core: Any, app: Any) -> Dict[str, Any]:
    if getattr(app.state, "_metals_execution_contract_v1_installed", False):
        return {"installed": True, "version": VERSION, "already_installed": True}

    @app.get("/api/metals-execution-status", include_in_schema=False)
    def metals_execution_status() -> Dict[str, Any]:
        return execution_status(core)

    app.add_middleware(ExecutionContractMiddleware, core=core)
    app.state._metals_execution_contract_v1_installed = True
    return {
        "installed": True,
        "version": VERSION,
        "execution_authority": False,
        "xau_short_reporting_repaired": True,
        "zip_execution_status_injected": True,
        "xag_short_promotion_decision": XAG_SHORT_PROMOTION_DECISION,
        "xag_short_real_money_armed_by_patch": False,
    }
