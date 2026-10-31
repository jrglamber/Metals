"""Metals presentation wrapper for authoritative XAG SHORT live wording.

Presentation/export only. Execution remains owned by metals_live_risk_entrypoint.
This wrapper removes stale XAG demo/research labels now that the XAG SHORT live
executor is armed, while keeping XAG LONG explicitly research-only and making
clear that legacy top tiles remain XAU-only until aggregate accounting expands.
"""
from __future__ import annotations

from typing import Any, Dict

import metals_execution_contract_20261031 as execution_contract
from metals_live_risk_entrypoint import app as app

VERSION = "metals_xag_live_wording_v1_2026_10_31"

# These also flow through future ZIP export repairs because the existing
# execution-contract exporter reads this tuple at response time.
_EXPORT_REPLACEMENTS = (
    (
        "XAUUSD LONG + XAUUSD SHORT live lanes; XAG practice/research excluded from live totals",
        "XAUUSD LONG + XAUUSD SHORT + XAGUSD SHORT live lanes; XAGUSD LONG remains research-only; XAG SHORT accounting is reported separately from legacy XAU top-tile totals",
    ),
    (
        "LIVE metals capital is used by XAU LONG + XAU SHORT. XAG SHORT live trial is separately gated.",
        "LIVE metals capital is used by XAU LONG + XAU SHORT + XAG SHORT. XAG LONG remains research-only.",
    ),
    (
        "XAG SHORT live pilot status is reported from the authoritative executor state",
        "XAG SHORT is LIVE and reported from the authoritative executor state; XAG LONG remains research-only",
    ),
    (
        "XAG practice/research excluded from live totals",
        "XAG SHORT is LIVE and reported separately from legacy XAU totals; XAG LONG remains research-only",
    ),
)

_existing = tuple(getattr(execution_contract, "_STALE_REPLACEMENTS", ()) or ())
execution_contract._STALE_REPLACEMENTS = _existing + tuple(
    item for item in _EXPORT_REPLACEMENTS if item not in _existing
)

_HTML_REPLACEMENTS = (
    (
        "XAU LONG + XAU SHORT live · XAG demo/research",
        "XAU LONG + XAU SHORT + XAG SHORT live · XAG LONG research-only",
    ),
    (
        "XAU LONG + XAU SHORT live · XAG demo / research",
        "XAU LONG + XAU SHORT + XAG SHORT live · XAG LONG research-only",
    ),
    (
        "LIVE METALS — XAU LONG + XAU SHORT.",
        "LIVE METALS — XAU LONG + XAU SHORT + XAG SHORT.",
    ),
    (
        "Top tiles are LIVE XAU only: XAU LONG + XAU SHORT. XAG is demo/research and are shown lower in Broker / OANDA / Accounting.",
        "Top tiles remain LIVE XAU only: XAU LONG + XAU SHORT. XAG SHORT is LIVE and reported separately until aggregate accounting includes it; XAG LONG remains research-only.",
    ),
    (
        "XAG is demo/research",
        "XAG SHORT is LIVE; XAG LONG is research-only",
    ),
    (
        "XAG demo/research",
        "XAG SHORT live · XAG LONG research-only",
    ),
)


class XagLiveWordingMiddleware:
    def __init__(self, inner: Any):
        self.inner = inner

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or str(scope.get("path") or "") not in {"/", "/dashboard", "/dashboard/top"}:
            await self.inner(scope, receive, send)
            return

        start = None
        chunks = []

        async def capture(message: Dict[str, Any]) -> None:
            nonlocal start
            if message.get("type") == "http.response.start":
                start = dict(message)
                return
            if message.get("type") != "http.response.body":
                await send(message)
                return

            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return

            body = b"".join(chunks)
            headers = list((start or {}).get("headers", []))
            content_type = ""
            for key, value in headers:
                if key.lower() == b"content-type":
                    content_type = value.decode("latin-1", errors="ignore").lower()
                    break

            if "text/html" in content_type:
                try:
                    text = body.decode("utf-8")
                    for old, new in _HTML_REPLACEMENTS:
                        text = text.replace(old, new)
                    body = text.encode("utf-8")
                except Exception:
                    pass

            clean = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"content-encoding"}]
            clean.append((b"content-length", str(len(body)).encode("ascii")))
            head = dict(start or {"type": "http.response.start", "status": 200})
            head["headers"] = clean
            await send(head)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.inner(scope, receive, capture)


app.add_middleware(XagLiveWordingMiddleware)

print(
    "METALS_XAG_LIVE_WORDING "
    f"version={VERSION} xag_short=LIVE xag_long=RESEARCH_ONLY "
    "legacy_top_tiles=XAU_ONLY presentation_only=True",
    flush=True,
)
