"""Unified Metals production entrypoint.

Restores the read-only analysis wrapper while keeping the XAU SHORT live
promotion and expanded XAU SHORT challenger suite installed into the same core
runtime. Dashboard wording is updated through the existing safe dashboard
passthrough in analysis_entrypoint, which rebuilds response headers rather than
mutating an already-sized ASGI body.

Build marker: 2026-10-06 upstream-error recovery.
"""
from __future__ import annotations

import analysis_entrypoint as analysis
import xau_short_live_override
import xau_short_challenger_suite

# analysis_entrypoint has already installed metals_exit_override into this same
# core module. Layer the approved XAU SHORT live adapter and research-only
# challenger declaration on top.
XAU_SHORT_LIVE_OVERRIDE_STATUS = xau_short_live_override.install(analysis.core)
XAU_SHORT_CHALLENGER_STATUS = xau_short_challenger_suite.install(analysis.core)

# Extend the existing safe dashboard rewrite. analysis_entrypoint's dashboard
# passthrough removes Content-Length/content-encoding before returning the
# rewritten response, preventing the upstream error caused by the prior ASGI
# body-only mutation.
_base_rewrite = analysis._rewrite_dashboard_version


def _rewrite_dashboard_version(body: bytes, content_type: str) -> bytes:
    body = _base_rewrite(body, content_type)
    if "text/html" not in (content_type or "").lower():
        return body
    try:
        text = body.decode("utf-8")
        replacements = (
            (
                "XAU LONG live pilot · XAU SHORT/XAG practice",
                "XAU LONG + XAU SHORT live · XAG practice/research",
            ),
            (
                "Top tiles show LIVE Metals only (currently XAU LONG). XAU SHORT and XAG remain practice",
                "Top tiles show LIVE Metals only (XAU LONG + XAU SHORT). XAG remains practice/research",
            ),
            (
                "XAU SHORT and XAG remain practice and are shown lower in Broker / OANDA / Accounting.",
                "XAG remains practice/research and is shown lower in Broker / OANDA / Accounting.",
            ),
        )
        for old, new in replacements:
            text = text.replace(old, new)
        return text.encode("utf-8")
    except Exception:
        return body


analysis._rewrite_dashboard_version = _rewrite_dashboard_version
app = analysis.app
