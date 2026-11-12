"""The admin app's web front. Same image as the agent, own container, reached only through the Cloudflare tunnel's
admin hostname. Every page needs sign-in (SIGNED_IN); only /health answers without it, for Docker.

    uvicorn support_agent.admin.app:app
"""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from support_agent.admin import add_client, client_sheet, clients, history, staff
from support_agent.admin.auth import Refused, current_staff, require_same_origin
from support_agent.admin.channel_names import start_timer, stop_timer
from support_agent.admin.db import name_noter
from support_agent.admin.settings import get_admin_settings
from support_agent.admin.slack import channel_lister
from support_agent.admin.templating import HERE, STATIC_HASH, remember_sign_out, templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# On every answer: no framing by another site (a hidden click on Remove), scripts, styles and fonts from the app only
# (so no inline style attributes in templates), nothing kept in a shared computer's cache.
SAFETY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; "
                               "object-src 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}

# Only a file that was found, under the current hash, is kept: every page, refusal and error (a missing file under the
# hashed address too) keeps no-store. "private": this browser only, never a cache shared between people.
STATIC_PREFIX = f"/static/{STATIC_HASH}/"
KEPT_STATIC = "private, max-age=31536000, immutable"


def _is_kept_static(request: Request, response) -> bool:
    return (request.method in ("GET", "HEAD") and request.url.path.startswith(STATIC_PREFIX)
            and response.status_code in (200, 304))


# Every route except /health carries these; a test walks the route table to prove it.
SIGNED_IN = [Depends(require_same_origin), Depends(current_staff), Depends(remember_sign_out)]


async def _refused_page(request: Request, exc: Refused) -> HTMLResponse:
    logger.info("refused %s %s: %s", request.method, request.url.path, exc)
    page = {"heading": exc.heading, "message": exc.message}
    return templates.TemplateResponse(request, "refused.html", page, status_code=exc.status_code)


async def _server_error(request: Request, exc: Exception) -> PlainTextResponse:
    """An exception that escapes a route is answered outside the middleware above, so the safety headers are added here.
    The answer holds no error text (the server's own log has it)."""
    return PlainTextResponse("Something went wrong.", status_code=500, headers=SAFETY_HEADERS)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings = get_admin_settings()  # a missing or forbidden setting stops the server before /health can say ok
    # The hourly refresh of the clients' Slack channel names: the one write outside a POST, through the
    # name noter alone. It starts only after the settings check, and stops with the app, whatever ends it.
    start_timer(channel_lister(settings), name_noter)
    try:
        yield
    finally:
        stop_timer()


def create_app() -> FastAPI:
    # The API description pages are off: they would list every route to anyone who reached the app.
    app = FastAPI(title="Bluejay support admin", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=_lifespan)
    app.add_exception_handler(Refused, _refused_page)
    app.add_exception_handler(Exception, _server_error)

    @app.middleware("http")
    async def safety_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SAFETY_HEADERS)  # overwrites anything a route set, Cache-Control included
        if _is_kept_static(request, response):
            response.headers["Cache-Control"] = KEPT_STATIC
        return response

    # The app's script, page base and style files. No sign-in: styles only, and the
    # Cloudflare gate stands in front of the whole hostname anyway. Under the hashed address (templating.static_url)
    # a file is kept for a year; the plain address stays for anything that still names it, and is never kept. The
    # hashed one is mounted first: a stale hash matches only the plain mount, where it finds no such folder (404).
    static_files = StaticFiles(directory=HERE / "static")
    app.mount(STATIC_PREFIX.rstrip("/"), static_files, name="static_hashed")
    app.mount("/static", static_files, name="static")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/", dependencies=SIGNED_IN)
    def home() -> RedirectResponse:
        return RedirectResponse("/clients", status_code=303)

    app.include_router(add_client.router, dependencies=SIGNED_IN)  # before clients: /clients/new isn't a client key
    app.include_router(clients.router, dependencies=SIGNED_IN)
    app.include_router(client_sheet.router, dependencies=SIGNED_IN)  # after add_client, for the same reason
    app.include_router(staff.router, dependencies=SIGNED_IN)
    app.include_router(history.router, dependencies=SIGNED_IN)
    return app


app = create_app()
