"""The production HTTP layer around the Streamlit app (``streamlit run serve.py``).

Streamlit 1.66 leaves a few things a public page needs to the host:

* It serves its frontend bundle under ``/static/`` **uncompressed** (it bypasses
  gzip there on purpose, to save CPU per request), so a first visit downloads
  4.6 MB of JavaScript for Plotly alone.
* It answers ``/robots.txt`` with ``index.html`` (the SPA fallback), and the
  document has the generic title "Streamlit", no description, and a preload for
  the Source Sans font, which this app's theme (Inter) never uses.
* Its first paint is a centred ("narrow") shell that turns wide when the page
  config arrives, which moves the empty main container (a ~0.02 layout shift).

``st.App`` (Streamlit's ASGI entry point) accepts extra routes and middleware,
so this module fixes all of that in-process, without a reverse proxy:

* ``FrontendMiddleware`` compresses ``/static/`` assets. If the image build left a
  brotli file beside an asset (``scripts/precompress.py``), browsers that accept
  ``br`` get it; otherwise the asset is gzipped once per process. Assets are
  content-hashed and immutable, so the encoded bytes are cached by path and
  ETag, and gzip runs in a worker thread, off the event loop.
* It rewrites the HTML document: title, description, no unused font preload,
  and one critical CSS rule that keeps the main block hidden until the wide
  layout applies.
* ``ROUTES`` adds a real ``robots.txt``.

Everything here is plain ASGI and is unit-tested without a server.
"""

from __future__ import annotations

import gzip
import html
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

import anyio.to_thread
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from streamlit.runtime.secrets import SecretsValue

from momentum.identity import DeviceCookieMiddleware, derive_key

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
_UNUSED_PRELOAD: Final = re.compile(r"<link\b[^>]*rel=\"preload\"[^>]*SourceSans[^>]*>\s*", re.S)


def _streamlit_static_root() -> Path:
    import streamlit

    return Path(streamlit.__file__).parent / "static"


def head_tags() -> str:
    """The tags added to the document head (escaped)."""
    return (
        f'<meta name="description" content="{html.escape(DESCRIPTION, quote=True)}" />'
        f"<style>{CRITICAL_CSS}</style>"
    )


def rewrite_document(body: bytes) -> bytes:
    """Give Streamlit's index.html a real title, a description and the critical CSS,
    and drop the preload of a font the theme never uses."""
    text = _UNUSED_PRELOAD.sub("", body.decode("utf-8"))
    title = f"<title>{html.escape(TITLE)}</title>"
    text, replaced = re.subn(r"<title>.*?</title>", title, text, count=1, flags=re.S)
    tags = head_tags() if replaced else title + head_tags()
    if "</head>" in text:
        text = text.replace("</head>", tags + "</head>", 1)
    return text.encode("utf-8")


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
    return next((value for key, value in headers if key.lower() == name), None)


def accepts(scope: Scope, coding: bytes) -> bool:
    """Whether the request's Accept-Encoding allows ``coding`` (``q=0`` refuses it)."""
    raw = _header(list(scope.get("headers", [])), b"accept-encoding") or b""
    for part in raw.lower().split(b","):
        name, _, params = part.strip().partition(b";")
        if name.strip() == coding:
            return params.replace(b" ", b"") not in {b"q=0", b"q=0.0", b"q=0.00", b"q=0.000"}
    return False


class FrontendMiddleware:
    """Compress Streamlit's static assets and polish its HTML document."""

    def __init__(
        self,
        app: ASGIApp,
        static_prefix: str = "/static/",
        static_root: Path | None = None,
    ) -> None:
        self.app = app
        self.static_prefix = static_prefix
        self.static_root = static_root or _streamlit_static_root()
        self._cache: dict[tuple[str, bytes, bytes], bytes | None] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only GET is rewritten: a HEAD body is empty, so there is nothing to
        # compress (and its Content-Length must stay as Streamlit sent it).
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
        if status != 200:
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        if content_type.startswith("text/html"):
            body = rewrite_document(body)
        coding, encoded = None, None
        if (
            _header(headers, b"content-encoding") is None
            and _COMPRESSIBLE.match(content_type)
            and len(body) >= _MIN_SIZE
        ):
            coding, encoded = await self._encode(scope, headers, body, static=static)
        headers = [(k, v) for k, v in headers if k.lower() != b"content-length"]
        if coding is not None and encoded is not None:
            body = encoded
            headers = [(k, v) for k, v in headers if k.lower() != b"etag"]
            etag = _header(list(start.get("headers", [])), b"etag")
            if etag:
                # A different representation needs its own (weak) validator.
                bare = etag.removeprefix(b"W/").strip(b'"')
                headers.append((b"etag", b'W/"' + bare + b"-" + coding + b'"'))
            headers += [(b"content-encoding", coding), (b"vary", b"Accept-Encoding")]
        headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _encode(
        self, scope: Scope, headers: list[tuple[bytes, bytes]], body: bytes, *, static: bool
    ) -> tuple[bytes | None, bytes | None]:
        path: str = scope["path"]
        etag = _header(headers, b"etag") or b""
        if static and accepts(scope, b"br"):
            brotli = self._precompressed(path, etag)
            if brotli is not None:
                return b"br", brotli
        if not accepts(scope, b"gzip"):
            return None, None
        key = (path, etag, b"gzip")
        cached = self._cache.get(key) if static else None
        if cached is not None:
            return b"gzip", cached
        # mtime=0 keeps the output byte-identical between runs (stable ETags).
        encoded: bytes = await anyio.to_thread.run_sync(
            lambda: gzip.compress(body, compresslevel=_GZIP_LEVEL, mtime=0)
        )
        if static:
            self._cache[key] = encoded
        return b"gzip", encoded

    def _precompressed(self, path: str, etag: bytes) -> bytes | None:
        """The build-time ``.br`` file beside a static asset, if there is one.

        Only called after Streamlit itself answered 200 for ``path``, and the
        resolved file must stay inside the static root.
        """
        key = (path, etag, b"br")
        if key not in self._cache:
            root = self.static_root.resolve()
            candidate = (root / path.lstrip("/")).resolve()
            brotli_file = candidate.with_name(candidate.name + ".br")
            inside = candidate.is_relative_to(root)
            self._cache[key] = (
                brotli_file.read_bytes() if inside and brotli_file.is_file() else None
            )
        return self._cache[key]


async def robots(_: Request) -> PlainTextResponse:
    return PlainTextResponse(ROBOTS_TXT, headers={"Cache-Control": "public, max-age=86400"})


ROUTES: Final = [Route("/robots.txt", robots, methods=["GET", "HEAD"])]
# The device cookie is outermost, so it sees the final document response.
MIDDLEWARE: Final = [Middleware(DeviceCookieMiddleware), Middleware(FrontendMiddleware)]


@asynccontextmanager
async def lifespan(_app: object) -> AsyncIterator[None]:
    """Run the alert loop for as long as the server runs, and stop it cleanly.

    The worker module is imported here, not at the top: it pulls in the data
    layer, whose Streamlit caches should be created once the runtime exists.
    """
    from momentum import store
    from momentum.alerts import worker

    worker.ensure_started()
    try:
        yield
    finally:
        await anyio.to_thread.run_sync(worker.stop_running)
        store.close_all()


_OIDC_ENV: Final = {
    "server_metadata_url": "MOMENTUM_OIDC_SERVER_METADATA_URL",
    "client_id": "MOMENTUM_OIDC_CLIENT_ID",
    "client_secret": "MOMENTUM_OIDC_CLIENT_SECRET",
    "redirect_uri": "MOMENTUM_OIDC_REDIRECT_URI",
}


def oidc_secrets(env: Mapping[str, str]) -> dict[str, SecretsValue]:
    """``st.login`` configuration from environment variables (for containers).

    ``secrets.toml`` works as usual; this only lets a deployment configure OIDC
    without mounting a file. It is all-or-nothing: a partial configuration is
    an error, not a silently disabled sign-in. The session-cookie secret is
    ``MOMENTUM_OIDC_COOKIE_SECRET`` or, failing that, derived from
    ``MOMENTUM_SECRET_KEY``.
    """
    values = {key: env.get(name, "").strip() for key, name in _OIDC_ENV.items()}
    if not any(values.values()):
        return {}
    missing = [_OIDC_ENV[key] for key, value in values.items() if not value]
    if missing:
        msg = f"OIDC sign-in is partly configured; also set {', '.join(missing)}"
        raise ValueError(msg)
    cookie_secret = env.get("MOMENTUM_OIDC_COOKIE_SECRET", "").strip()
    if not cookie_secret:
        base = env.get("MOMENTUM_SECRET_KEY", "").strip()
        if len(base) < 32:
            msg = "Set MOMENTUM_OIDC_COOKIE_SECRET or MOMENTUM_SECRET_KEY (32+ characters)"
            raise ValueError(msg)
        cookie_secret = derive_key(base, b"momentum/oidc-cookie/v1").hex()
    auth: dict[str, SecretsValue] = {**values, "cookie_secret": cookie_secret}
    return {"auth": auth}
