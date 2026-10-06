from fastapi import FastAPI, HTTPException
import inspect
import app_postgres_runtime as core

app = FastAPI(title="temporary source probe")

_ALLOWED = {
    "candidate": "execute_metals_xau_live_candidate",
    "config": "metals_xau_live_config_status",
    "stop": "_metals_xau_live_stop_candidate",
}

@app.get("/probe/{name}")
def probe(name: str):
    attr = _ALLOWED.get(name)
    if not attr:
        raise HTTPException(status_code=404)
    obj = getattr(core, attr, None)
    if obj is None:
        raise HTTPException(status_code=404)
    return {"name": attr, "source": inspect.getsource(obj), "names": list(getattr(obj, "__code__", type("X",(),{"co_names":()})()).co_names)}
