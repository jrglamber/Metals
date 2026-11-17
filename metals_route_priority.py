"""Fail-closed route ordering for late-installed, read-only Metals APIs.

The production FastAPI app already mounts a catch-all root application.
Routes added afterwards must be moved ahead of that mount, or return 404.
This helper never changes trading controls or submits broker orders.
"""

def promote_read_only_route(app, path, endpoint):
    routes = getattr(getattr(app, "router", None), "routes", None)
    if routes is None:
        raise RuntimeError("Metals API router unavailable")
    matches = [
        r for r in routes
        if getattr(r, "path", None) == path
        and getattr(r, "endpoint", None) is endpoint
        and "GET" in (getattr(r, "methods", set()) or set())
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one registered read-only API route for {path}; found {len(matches)}")
    route = matches[0]
    routes.remove(route)
    routes.insert(0, route)
    if routes[0] is not route:
        raise RuntimeError(f"Could not prioritize read-only API route {path}")
    return {"path": path, "route_index": 0, "read_only": True}
