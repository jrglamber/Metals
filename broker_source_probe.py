from __future__ import annotations

import inspect
from fastapi import HTTPException

_ALLOWED = {
    "candidate": "execute_metals_xau_live_candidate",
    "config": "metals_xau_live_config_status",
    "stop": "_metals_xau_live_stop_candidate",
}


def install(core):
    @core.app.get("/internal/xau-live-source/{name}", include_in_schema=False)
    def xau_live_source(name: str):
        attr = _ALLOWED.get(name)
        if not attr:
            raise HTTPException(status_code=404)
        obj = getattr(core, attr, None)
        if obj is None:
            raise HTTPException(status_code=404)
        return {
            "name": attr,
            "source": inspect.getsource(obj),
            "signature": str(inspect.signature(obj)),
            "names": list(getattr(getattr(obj, "__code__", None), "co_names", ())),
        }
