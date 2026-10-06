import app_postgres_runtime as core
import xau_short_live_override
import xau_short_research_override

XAU_SHORT_LIVE_OVERRIDE_STATUS = xau_short_live_override.install(core)
XAU_SHORT_RESEARCH_OVERRIDE_STATUS = xau_short_research_override.install(core)


class _LiveMetalsDashboardCopy:
    """Keep legacy dashboard HTML accurate after XAU SHORT live promotion.

    This is presentation-only. JSON/API responses and trading logic are untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        content_type = ""

        async def send_rewrite(message):
            nonlocal content_type
            if message.get("type") == "http.response.start":
                for k, v in message.get("headers", []):
                    if k.lower() == b"content-type":
                        content_type = v.decode("latin-1", errors="ignore").lower()
                await send(message)
                return

            if message.get("type") == "http.response.body" and "text/html" in content_type:
                body = message.get("body", b"")
                if body:
                    text = body.decode("utf-8", errors="replace")
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
                    message = dict(message)
                    message["body"] = text.encode("utf-8")
            await send(message)

        await self.app(scope, receive, send_rewrite)


app = _LiveMetalsDashboardCopy(core.app)
