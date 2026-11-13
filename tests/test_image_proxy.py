"""Image proxy rewrites auth-gated RAG URLs and fetches with the bearer token."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from mcp_chat.image_proxy import (
    ImageFetchError,
    extract_image_http_url,
    fetch_image_bytes,
    http_url_for_image_id,
    resolve_image_http_url,
    scrub_rag_image_urls,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


def test_page_image_url_and_scrub() -> None:
    """Page ids map to RAG HTTP paths; scrubbing points at the same-origin proxy."""
    mcp = "https://rag.example/mcp/"
    image_id = "page:0102eddc-35d6-4012-bef5-ee7280651ce1:1"
    assert (
        http_url_for_image_id(mcp, image_id)
        == "https://rag.example/images/0102eddc-35d6-4012-bef5-ee7280651ce1/"
        "page:0102eddc-35d6-4012-bef5-ee7280651ce1:1"
    )
    text = "Fetch via authenticated HTTP: https://rag.example/images/doc-1/page:doc-1:2"
    assert extract_image_http_url(text) == (
        "https://rag.example/images/doc-1/page:doc-1:2"
    )
    scrubbed = scrub_rag_image_urls(text)
    assert "rag.example/images/" not in scrubbed
    assert "/api/images?image_id=page%3Adoc-1%3A2" in scrubbed
    assert resolve_image_http_url(
        mcp_server_url=mcp,
        image_id=image_id,
        tool_text="Image exceeds MCP inline limit",
    ).endswith(f"/images/0102eddc-35d6-4012-bef5-ee7280651ce1/{image_id}")


def test_fetch_image_bytes_uses_bearer_and_hides_401(
    monkeypatch: MonkeyPatch,
) -> None:
    """A 401 from RAG becomes ImageFetchError without the upstream detail."""
    del monkeypatch

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.headers.get("Authorization") == "Bearer secret-token"
            if request.url.path.endswith("/missing"):
                return httpx.Response(
                    401,
                    json={"detail": "Missing or invalid bearer token"},
                    request=request,
                )
            return httpx.Response(
                200,
                content=b"png-bytes",
                headers={"content-type": "image/png"},
                request=request,
            )

    async def run() -> None:
        async with httpx.AsyncClient(transport=_Transport()) as client:
            raw, media = await fetch_image_bytes(
                "https://rag.example/images/doc/page:doc:1",
                token="secret-token",
                client=client,
            )
            assert raw == b"png-bytes"
            assert media == "image/png"
            try:
                await fetch_image_bytes(
                    "https://rag.example/images/doc/missing",
                    token="secret-token",
                    client=client,
                )
            except ImageFetchError as exc:
                assert "Missing or invalid bearer token" not in str(exc)
                assert exc.status_code == 401
            else:
                raise AssertionError("expected ImageFetchError")

    asyncio.run(run())


def test_resolve_prefers_absolute_hint_from_tool_text() -> None:
    """Oversized MCP replies carry the absolute URL the proxy should fetch."""
    url = resolve_image_http_url(
        mcp_server_url="https://rag.example/mcp/",
        image_id="abc123def4567890",
        tool_text=(
            "Image exceeds MCP inline limit (9000000 bytes). "
            "Fetch via authenticated HTTP: "
            "https://rag.example/images/doc-9/abc123def4567890"
        ),
    )
    assert url == "https://rag.example/images/doc-9/abc123def4567890"
