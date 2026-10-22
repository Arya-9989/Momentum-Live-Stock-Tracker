"""The production HTTP layer (momentum.server), exercised as plain ASGI."""

from __future__ import annotations

import ast
import asyncio
import gzip
from pathlib import Path
from typing import Any

import pytest

from momentum import server

ROOT = Path(__file__).resolve().parents[1]
INDEX = b"<!doctype html><html><head><title>Streamlit</title></head><body></body></html>"
BUNDLE = b"const x = 1;\n" * 2_000


class FakeStreamlit:
    """Answers like Streamlit: hashed JS with an ETag, the SPA document elsewhere."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(
        self, scope: server.Scope, receive: server.Receive, send: server.Send
    ) -> None:
        self.calls += 1
        path = scope["path"]
        if path.startswith("/static/"):
            headers = [
                (b"content-type", b"application/javascript"),
                (b"etag", b'"abc123"'),
                (b"cache-control", b"public, immutable, max-age=31536000"),
                (b"content-length", str(len(BUNDLE)).encode()),
            ]
            body = BUNDLE
        elif path.startswith("/media/"):
            headers = [(b"content-type", b"text/csv")]
            body = b"a,b\n" * 1_000
        else:
            headers = [(b"content-type", b"text/html; charset=utf-8")]
            body = INDEX
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body[:100], "more_body": True})
        await send({"type": "http.response.body", "body": body[100:], "more_body": False})


def call(
    app: Any, path: str, *, method: str = "GET", headers: list[tuple[bytes, bytes]] | None = None
) -> tuple[int, dict[bytes, bytes], bytes]:
    scope = {"type": "http", "method": method, "path": path, "headers": headers or []}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    start = sent[0]
    body = b"".join(m.get("body", b"") for m in sent[1:])
    return start["status"], {k.lower(): v for k, v in start["headers"]}, body


GZIP = [(b"accept-encoding", b"gzip, deflate, br")]


def test_static_assets_are_gzipped_once_and_cached() -> None:
    upstream = FakeStreamlit()
    app = server.FrontendMiddleware(upstream)
    status, headers, body = call(app, "/static/js/index.abc.js", headers=GZIP)
    assert status == 200
    assert headers[b"content-encoding"] == b"gzip"
    assert headers[b"vary"] == b"Accept-Encoding"
    assert headers[b"content-length"] == str(len(body)).encode()
    assert headers[b"etag"] == b'W/"abc123-gzip"'  # its own validator
    assert headers[b"cache-control"].startswith(b"public, immutable")
    assert gzip.decompress(body) == BUNDLE
    assert len(body) < len(BUNDLE) / 10
    again = call(app, "/static/js/index.abc.js", headers=GZIP)[2]
    assert again == body  # byte-identical (mtime=0) and served from the cache
    assert app._cache  # the cache is the point of this test


def test_clients_without_gzip_get_the_original() -> None:
    app = server.FrontendMiddleware(FakeStreamlit())
    _, headers, body = call(app, "/static/js/index.abc.js")
    assert b"content-encoding" not in headers
    assert body == BUNDLE
    _, refused, _ = call(app, "/static/a.js", headers=[(b"accept-encoding", b"gzip;q=0")])
    assert b"content-encoding" not in refused


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/media/abc.csv", GZIP),  # downloads stream through untouched
        ("/static/js/index.abc.js", [*GZIP, (b"range", b"bytes=0-99")]),
    ],
)
def test_pass_through(path: str, headers: list[tuple[bytes, bytes]]) -> None:
    status, out, _ = call(server.FrontendMiddleware(FakeStreamlit()), path, headers=headers)
    assert status == 200
    assert b"content-encoding" not in out


def test_head_requests_pass_through_unchanged() -> None:
    _, headers, _ = call(
        server.FrontendMiddleware(FakeStreamlit()), "/static/a.js", method="HEAD", headers=GZIP
    )
    assert headers[b"content-length"] == str(len(BUNDLE)).encode()


def test_document_gets_title_description_and_critical_css() -> None:
    _, headers, body = call(server.FrontendMiddleware(FakeStreamlit()), "/")
    text = body.decode()
    assert "<title>Momentum · Markets</title>" in text
    assert "<title>Streamlit</title>" not in text
    assert '<meta name="description" content="Momentum is a fast' in text
    assert '[data-layout="narrow"]' in text
    assert text.index("</style>") < text.index("</head>")
    assert headers[b"content-length"] == str(len(body)).encode()


def test_rewrite_adds_a_title_when_there_is_none() -> None:
    text = server.rewrite_document(b"<html><head></head></html>").decode()
    assert text.count("<title>") == 1
    assert server.DESCRIPTION.split(":")[0] in text


def test_robots_txt_route() -> None:
    route = next(r for r in server.ROUTES if getattr(r, "path", None) == "/robots.txt")
    status, headers, body = call(route.app, "/robots.txt")
    assert status == 200
    assert headers[b"content-type"].startswith(b"text/plain")
    assert body.decode() == "User-agent: *\nAllow: /\n"


def test_serve_py_is_detected_as_an_st_app() -> None:
    """`streamlit run serve.py` finds the app by static analysis of this assignment."""
    from streamlit.web.server.app_discovery import discover_asgi_app

    result = discover_asgi_app(ROOT / "serve.py")
    assert result.is_asgi_app
    assert result.app_name == "app"
    tree = ast.parse((ROOT / "serve.py").read_text(encoding="utf-8"))
    call_node = next(n for n in ast.walk(tree) if isinstance(n, ast.Call))
    assert ast.literal_eval(call_node.args[0]) == "streamlit_app.py"
