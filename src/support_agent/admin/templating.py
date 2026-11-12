"""The page templates, shared by the app and its screens."""

import re
from pathlib import Path

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from support_agent.admin.settings import AdminSettings, get_admin_settings
from support_agent.admin.static_files import content_hash

HERE = Path(__file__).parent


def remember_sign_out(request: Request, settings: AdminSettings = Depends(get_admin_settings)) -> None:
    """Worked out on each request from the settings dependency (so a test's override reaches it), for the page frame."""
    request.state.sign_out = settings.sign_out


def _frame_context(request: Request) -> dict:
    """Every page's frame gets `sign_out`: the logout address behind a real Cloudflare team, empty everywhere else, and
    empty for a page whose request never ran remember_sign_out."""
    return {"sign_out": getattr(request.state, "sign_out", "")}


templates = Jinja2Templates(directory=HERE / "templates", context_processors=[_frame_context])

# The design system's icons travel inside each page (base.html): a page that asks for the file again after a save
# shows a blank icon until the answer arrives, since nothing the app sends is kept. Our own file.
_ICONS = (HERE / "static" / "ds" / "ds-icons.svg").read_text(encoding="utf-8")
templates.env.globals["icon_symbols"] = Markup("".join(re.findall(r"<symbol.*?</symbol>", _ICONS, re.S)))

# Every static file is named by an address holding a hash of all of them, taken once at start: any rebuild that changes
# a file changes the address, so a browser that kept the old file for a year never shows it again.
STATIC_HASH = content_hash(HERE / "static")


def static_url(path: str) -> str:
    """/static/<hash>/<path>: the address pages use for a static file (app.py serves it, kept for a year)."""
    return f"/static/{STATIC_HASH}/{path}"


templates.env.globals["static_url"] = static_url
