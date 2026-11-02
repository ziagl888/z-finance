"""Keep the local server reachable only as a local app.

z-finance binds to 127.0.0.1 and has no login: the loopback interface is the security boundary.
Binding alone does not hold that boundary against a browser, though:

* **DNS rebinding** — a web page can re-point its own host name to 127.0.0.1 and then read
  ``/api/...`` as a same-origin request. Such requests carry the attacker's name in ``Host``, so
  only requests whose ``Host`` is a loopback name (or the configured bind host) are served.
* **Cross-site form posts** — a page can submit a form (``multipart/form-data``, e.g. a backup
  restore or CSV import) to ``http://127.0.0.1:8765`` without a CORS preflight. Browsers send an
  ``Origin`` header on such requests, so state-changing methods with a foreign ``Origin`` are
  refused. JSON bodies are already protected by FastAPI's strict content-type check.

Invariants:
    * Every request is checked, API and SPA alike; there is no allowlist of paths.
    * A missing ``Origin`` is accepted (same-origin navigations, curl, the test client); a present
      one must name an allowed host.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_name(value: str) -> str:
    """Return the host part of a ``Host`` header value (``"127.0.0.1:8765"`` → ``"127.0.0.1"``)."""
    return (urlsplit(f"//{value}").hostname or "").lower()


class LocalOnlyMiddleware:
    """ASGI middleware refusing requests whose ``Host`` or ``Origin`` is not a local name (403)."""

    def __init__(self, app: ASGIApp, extra_hosts: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed = LOOPBACK_HOSTS | {h.lower() for h in extra_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 403 for a foreign ``Host`` or ``Origin``, otherwise hand the request on."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        reason = None
        if _host_name(headers.get("host", "")) not in self.allowed:
            reason = "host not allowed"
        elif (
            scope["method"] not in _SAFE_METHODS
            and "origin" in headers
            and (urlsplit(headers["origin"]).hostname or "").lower() not in self.allowed
        ):
            reason = "cross-site request refused"
        if reason is None:
            await self.app(scope, receive, send)
            return
        body = f'{{"detail":"{reason}"}}'.encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
