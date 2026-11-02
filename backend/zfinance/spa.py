"""Serve the built React SPA from the FastAPI process.

``npm run build`` writes the bundle to ``backend/zfinance/web/`` (``Settings.web_dir``). This
module registers one catch-all GET route, added after every API router:

* a path that names an existing file inside the bundle is served as that file (``/assets/*.js``,
  ``/favicon.svg``);
* any other non-API path returns ``index.html`` so client-side routes (``/accounts``) survive a
  reload;
* ``/api/...`` paths never fall through to the SPA: unknown API paths get a JSON 404;
* a missing bundle answers 503 with a hint instead of crashing the server.

A single route instead of a ``StaticFiles`` mount keeps the fallback and the API guard in one
place; the path is resolved and checked to stay inside the bundle directory.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

API_PREFIX = "api"

_MISSING_BUNDLE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>z-finance</title></head>
<body><h1>z-finance: frontend not built</h1>
<p>The API is running, but the web interface bundle is missing. Build it with
<code>just build</code> (or <code>npm run build</code> in <code>frontend/</code>) and reload.</p>
</body></html>
"""


def _resolve_asset(web_dir: Path, path: str) -> Path | None:
    """Return the file for ``path`` if it exists inside ``web_dir``; None otherwise (incl. traversal)."""
    if not path:
        return None
    root = web_dir.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def register_spa(app: FastAPI, web_dir: Path) -> None:
    """Add the SPA catch-all route to ``app``. Call after all API routers are included."""

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> FileResponse | HTMLResponse:
        if path == API_PREFIX or path.startswith(f"{API_PREFIX}/"):
            raise HTTPException(status_code=404, detail="Not Found")
        asset = _resolve_asset(web_dir, path)
        if asset is not None:
            return FileResponse(asset)
        index = web_dir / "index.html"
        if not index.is_file():
            return HTMLResponse(_MISSING_BUNDLE_HTML, status_code=503)
        # index.html must not be cached: it names the hashed asset files of the current build.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
