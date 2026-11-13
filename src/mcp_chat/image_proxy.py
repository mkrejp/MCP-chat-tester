"""Resolve MCP image ids through this app, never through the browser's bearer."""

from __future__ import annotations

import logging
import re
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger("mcp_chat.images")

# Keep under the RAG HTTP ceiling (50 MiB) while allowing MCP-oversized pages.
_MAX_PROXY_BYTES = 25 * 1024 * 1024

_ABSOLUTE_IMAGE = re.compile(
    r"https?://[^\s\"'<>]+/images/([^/\s\"'<>]+)/([^/\s\"'<>]+)",
    re.IGNORECASE,
)
_RELATIVE_IMAGE = re.compile(
    r"(?<![\w])/images/([^/\s\"'<>]+)/([^/\s\"'<>]+)",
    re.IGNORECASE,
)
_PAGE_IMAGE_ID = re.compile(r"^page:([0-9a-fA-F-]{8,}):(\d+)$")


class ImageFetchError(Exception):
    """Raised when the authenticated RAG image HTTP fetch fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Store a browser-safe message and optional upstream status."""
        super().__init__(message)
        self.status_code = status_code


def rag_http_base(mcp_server_url: str) -> str:
    """Strip a trailing ``/mcp`` path from the Streamable HTTP endpoint."""
    parsed = urlparse(mcp_server_url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[: -len("/mcp")]
    elif path == "mcp":
        path = ""
    cleaned = parsed._replace(path=path or "", params="", query="", fragment="")
    return cleaned.geturl().rstrip("/")


def http_url_for_image_id(mcp_server_url: str, image_id: str) -> str | None:
    """Build ``/images/{doc_id}/{image_id}`` for page ids. Hex ids need a hint."""
    cleaned = image_id.strip()
    match = _PAGE_IMAGE_ID.match(cleaned)
    if match is None:
        return None
    doc_id = match.group(1)
    base = rag_http_base(mcp_server_url)
    if not base:
        return None
    return f"{base}/images/{doc_id}/{quote(cleaned, safe=':')}"


def extract_image_http_url(text: str) -> str | None:
    """Return the first absolute or root-relative RAG image URL in ``text``."""
    absolute = _ABSOLUTE_IMAGE.search(text or "")
    if absolute:
        return absolute.group(0).rstrip(").,;")
    relative = _RELATIVE_IMAGE.search(text or "")
    if relative:
        return relative.group(0).rstrip(").,;")
    return None


def image_id_from_rag_url(url: str) -> str | None:
    """Pull the image id path segment from a RAG ``/images/{doc}/{id}`` URL."""
    absolute = _ABSOLUTE_IMAGE.search(url or "")
    if absolute:
        return absolute.group(2)
    relative = _RELATIVE_IMAGE.search(url or "")
    if relative:
        return relative.group(2)
    return None


def is_rag_image_url(url: str) -> bool:
    """Return whether ``url`` is an auth-gated RAG image HTTP path."""
    return image_id_from_rag_url(url) is not None


def resolve_image_http_url(
    *,
    mcp_server_url: str,
    image_id: str,
    tool_text: str = "",
) -> str | None:
    """Prefer an absolute URL from tool text; else derive one for page ids."""
    from_text = extract_image_http_url(tool_text)
    if from_text and from_text.startswith("http"):
        return from_text
    built = http_url_for_image_id(mcp_server_url, image_id)
    if built:
        return built
    if from_text and from_text.startswith("/"):
        base = rag_http_base(mcp_server_url)
        return f"{base}{from_text}" if base else None
    return None


def proxy_path_for_image_id(image_id: str) -> str:
    """Return the same-origin image path the browser is allowed to open."""
    return f"/api/images?image_id={quote(image_id.strip(), safe='')}"


def scrub_rag_image_urls(text: str) -> str:
    """Replace auth-gated RAG image URLs with same-origin proxy links."""

    def _abs(match: re.Match[str]) -> str:
        image_id = match.group(2)
        return proxy_path_for_image_id(image_id)

    def _rel(match: re.Match[str]) -> str:
        image_id = match.group(2)
        return proxy_path_for_image_id(image_id)

    cleaned = _ABSOLUTE_IMAGE.sub(_abs, text or "")
    return _RELATIVE_IMAGE.sub(_rel, cleaned)


async def fetch_image_bytes(
    url: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """GET ``url`` with the MCP bearer token and return bytes plus media type."""
    if not token:
        raise ImageFetchError("MCP_API_TOKEN is not set.")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ImageFetchError("Image URL is not absolute.")
    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(30.0, read=120.0)
    http = client or httpx.AsyncClient(timeout=timeout)
    owns_client = client is None
    try:
        response = await http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Authenticated image fetch failed: %s", type(exc).__name__)
        raise ImageFetchError("Image fetch failed.") from exc
    finally:
        if owns_client:
            await http.aclose()
    if response.status_code in {401, 403}:
        # Never surface the upstream "Missing or invalid bearer token" string.
        raise ImageFetchError("Image fetch was not authorized.", response.status_code)
    if response.status_code == 404:
        raise ImageFetchError("Image not found.", 404)
    if response.status_code >= 400:
        raise ImageFetchError(
            f"Image fetch failed with HTTP {response.status_code}.",
            response.status_code,
        )
    raw = response.content
    if len(raw) > _MAX_PROXY_BYTES:
        raise ImageFetchError("Image is too large.", 413)
    media = response.headers.get("content-type") or "application/octet-stream"
    media = media.split(";", 1)[0].strip() or "application/octet-stream"
    return raw, media
