"""Internet-facing portal: keep operator APIs on the private listener only."""

from fastapi.routing import APIRoute

from .app import app


app.router.routes = [
    route for route in app.router.routes
    if not (isinstance(route, APIRoute) and route.path.startswith("/api/admin/"))
]


@app.get("/api/public-config")
def public_config():
    from .app import REGISTRATION_CODE

    return {"registration_code_required": bool(REGISTRATION_CODE), "operator_enabled": False}
