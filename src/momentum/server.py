"""The production HTTP layer around the Streamlit app (``streamlit run serve.py``).

Streamlit 1.66 leaves a few things a public page needs to the host:

* It serves its frontend bundle under ``/static/`` **uncompressed** (it bypasses
  gzip there on purpose, to save CPU per request), so a first visit downloads
  4.6 MB of JavaScript for Plotly and the grid.
* It answers ``/robots.txt`` with ``index.html`` (the SPA fallback), and the
  document has the generic title "Streamlit" and no description.
* Its first paint is a centred ("narrow") shell that turns wide when the page
  config arrives, which moves the empty main container (a ~0.02 layout shift).

``st.App`` (Streamlit's ASGI entry point) accepts extra routes and middleware,
so this module fixes all of that in-process, without a reverse proxy:

* ``FrontendMiddleware`` gzips ``/static/`` assets once per process (they are
  content-hashed and immutable, so the compressed bytes are cached by path and
  ETag, and compression runs in a worker thread, off the event loop), and it
  rewrites the HTML document: title, description and one critical CSS rule
  that keeps the main block hidden until the wide layout applies.
* ``ROUTES`` adds a real ``robots.txt``.

Everything here is plain ASGI and is unit-tested without a server.
"""

from __future__ import annotations

import gzip
import html
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

import anyio.to_thread
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

TITLE: Final = "Momentum · Markets"
DESCRIPTION: Final = (
    "Momentum is a fast, mobile-friendly dashboard for global stocks, indices, ETFs, FX "
    "and crypto: area and candlestick charts, technical indicators, comparison and a "
    "watchlist."
)
# Streamlit's first paint is the centred shell; this app always runs wide, so the
# main block stays hidden until the wide layout applies (nothing visible can move).
CRITICAL_CSS: Final = (
    '[data-testid="stAppViewContainer"][data-layout="narrow"] '
    '[data-testid="stMainBlockContainer"]{visibility:hidden}'
)
ROBOTS_TXT: Final = "User-agent: *\nAllow: /\n"

_COMPRESSIBLE: Final = re.compile(
    r"^(text/|application/(javascript|json|xml|wasm|manifest\+json)|image/svg\+xml)"
)
_PASS_THROUGH: Final = ("/_stcore/", "/media/", "/component/", "/app/")
_MIN_SIZE: Final = 1024
_GZIP_LEVEL: Final = 9  # paid once per asset per process; the bytes are cached


def head_tags() -> str:
    """The tags added to the document head (escaped)."""
    return (
        f'<meta name="description" content="{html.escape(DESCRIPTION, quote=True)}" />'
        f"<style>{CRITICAL_CSS}</style>"
    )


def rewrite_document(body: bytes) -> bytes:
    """Give Streamlit's index.html a real title, a description and the critical CSS."""
    text = body.decode("utf-8")
    title = f"<title>{html.escape(TITLE)}</title>"
    text, replaced = re.subn(r"<title>.*?</title>", title, text, count=1, flags=re.S)
    tags = head_tags() if replaced else title + head_tags()
    if "</head>" in text:
        text = text.replace("</head>", tags + "</head>", 1)
    return text.encode("utf-8")


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    return next((value for key, value in headers if key.lower() == name), None)


def _accepts_gzip(scope: Scope) -> bool:
    raw = _header(list(scope.get("headers", [])), b"accept-encoding") or b""
    return any(
        part.split(b";")[0].strip() == b"gzip" and b"q=0" not in part.replace(b" ", b"")
        for part in raw.lower().split(b",")
    )


class FrontendMiddleware:
    """Compress Streamlit's static assets and polish its HTML document."""

    def __init__(self, app: ASGIApp, static_prefix: str = "/static/") -> None:
        self.app = app
        self.static_prefix = static_prefix
        self._cache: dict[tuple[str, bytes], bytes] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # HEAD and other methods pass through: a HEAD body is empty, so there is
        # nothing to rewrite or compress (and its Content-Length must stay as sent).
        if scope["type"] != "http" or scope.get("method") != "GET":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        headers = list(scope.get("headers", []))
        # Only the hashed bundle and the HTML document are buffered; API calls,
        # media (downloads) and component assets stream through untouched.
        if path.startswith(_PASS_THROUGH) or _header(headers, b"range") is not None:
            await self.app(scope, receive, send)
            return
        await self._buffered(scope, receive, send, static=path.startswith(self.static_prefix))

    async def _buffered(self, scope: Scope, receive: Receive, send: Send, *, static: bool) -> None:
        start: Message = {}
        chunks: list[bytes] = []

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self.app(scope, receive, capture)
        headers: list[tuple[bytes, bytes]] = list(start.get("headers", []))
        body = b"".join(chunks)
        content_type = (_header(headers, b"content-type") or b"").decode("latin-1")
        status = int(start.get("status", 500))

        if status == 200 and content_type.startswith("text/html"):
            body = rewrite_document(body)
        encoded = None
        if (
            status == 200
            and _accepts_gzip(scope)
            and _header(headers, b"content-encoding") is None
            and _COMPRESSIBLE.match(content_type)
            and len(body) >= _MIN_SIZE
        ):
            encoded = await self._gzip(scope["path"], headers, body, cache=static)
        if encoded is not None:
            body = encoded
            headers = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"etag"}]
            etag = _header(list(start.get("headers", [])), b"etag")
            if etag:
                # A different representation needs its own (weak) validator.
                bare = etag.removeprefix(b"W/").strip(b'"')
                headers.append((b"etag", b'W/"' + bare + b'-gzip"'))
            headers += [(b"content-encoding", b"gzip"), (b"vary", b"Accept-Encoding")]
        elif status == 200:
            headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
        if status == 200:
            headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _gzip(
        self, path: str, headers: list[tuple[bytes, bytes]], body: bytes, *, cache: bool
    ) -> bytes:
        key = (path, _header(headers, b"etag") or b"")
        if cache and key in self._cache:
            return self._cache[key]
        # mtime=0 keeps the output byte-identical between runs (stable ETags).
        encoded: bytes = await anyio.to_thread.run_sync(
            lambda: gzip.compress(body, compresslevel=_GZIP_LEVEL, mtime=0)
        )
        if cache:
            self._cache[key] = encoded
        return encoded


async def robots(_: Request) -> PlainTextResponse:
    return PlainTextResponse(ROBOTS_TXT, headers={"Cache-Control": "public, max-age=86400"})


ROUTES: Final = [Route("/robots.txt", robots, methods=["GET", "HEAD"])]
MIDDLEWARE: Final = [Middleware(FrontendMiddleware)]
