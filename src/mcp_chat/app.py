"""HTTP application: Google wall, inspection panel, and chat."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mcp_chat.auth import install_auth
from mcp_chat.config import Settings, load_settings
from mcp_chat.image_proxy import (
    ImageFetchError,
    fetch_image_bytes,
    resolve_image_http_url,
    scrub_rag_image_urls,
)
from mcp_chat.interrogate import collect_image_ids, plan_calls, render_reply
from mcp_chat.localities import GeoServerGazetteer, attach_localities
from mcp_chat.map_layers import map_catalog
from mcp_chat.mcp_client import (
    HttpMcpClient,
    Inspection,
    McpCallError,
    McpGateway,
    MissingTokenError,
    ToolOutcome,
)
from mcp_chat.places import places_from_outcomes

_WEB = Path(__file__).resolve().parent / "web"
_STATIC = _WEB / "static"
_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class ChatRequest(BaseModel):
    """One user turn."""

    message: str = Field(min_length=1, max_length=4000)


def create_app(
    settings: Settings | None = None,
    mcp: McpGateway | None = None,
    forced_user: str | None = None,
    locality_lookup: Any | None = None,
) -> FastAPI:
    """Build the FastAPI app. Tests pass a fake gateway and a forced user."""
    resolved = settings or load_settings()
    gateway = mcp or HttpMcpClient(resolved)
    app = FastAPI(title="MCP chat tester", docs_url=None, redoc_url=None)
    app.state.settings = resolved
    app.state.mcp = gateway
    app.state.locality_lookup = locality_lookup or GeoServerGazetteer().lookup
    install_auth(app, resolved, forced_user)
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe. It does not touch Google or MCP."""
        return {"status": "ok"}

    @app.get("/")
    async def index() -> FileResponse:
        """Serve the chat page to a signed-in browser."""
        return FileResponse(_WEB / "index.html")

    @app.get("/api/me")
    async def me(request: Request) -> dict[str, str]:
        """Return the signed-in email. The session cookie is the credential."""
        user = request.session.get("user") or {}
        return {"email": str(user.get("email") or "")}

    @app.get("/api/map/layers")
    async def map_layers() -> dict[str, Any]:
        """Describe base tiles, GeoServer context, and the answer overlay."""
        return map_catalog()

    @app.get("/api/inspection")
    async def inspection() -> dict[str, Any]:
        """List MCP capabilities. The bearer token is not part of the payload."""
        snapshot = await gateway.inspect()
        return _inspection_payload(snapshot)

    @app.post("/api/chat")
    async def chat(body: ChatRequest) -> dict[str, Any]:
        """Search the knowledge server and return a reply plus image ids."""
        try:
            snapshot = await gateway.inspect()
        except MissingTokenError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if snapshot.status != "connected":
            raise HTTPException(
                status_code=503,
                detail=snapshot.detail or "MCP server is not connected.",
            )
        names = {tool.name for tool in snapshot.tools}
        planned = plan_calls(body.message, names)
        outcomes: list[ToolOutcome] = []
        calls: list[dict[str, Any]] = []
        for item in planned:
            try:
                outcome = await gateway.call_tool(item.name, item.arguments)
            except MissingTokenError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except McpCallError as exc:
                outcome = ToolOutcome(
                    name=item.name,
                    arguments=item.arguments,
                    text=str(exc),
                    is_error=True,
                )
            outcomes.append(outcome)
            calls.append(
                {
                    "tool": outcome.name,
                    "arguments": outcome.arguments,
                    "ok": not outcome.is_error,
                    "preview": scrub_rag_image_urls(outcome.text[:1200]),
                }
            )
        places = [
            place.to_dict()
            for place in attach_localities(
                places_from_outcomes(outcomes),
                body.message,
                app.state.locality_lookup,
            )
        ]
        return {
            "reply": render_reply(body.message, outcomes),
            "calls": calls,
            "places": places,
            "images": [
                {"image_id": image_id} for image_id in collect_image_ids(outcomes)
            ],
        }

    @app.get("/api/images")
    async def image(image_id: str) -> Response:
        """Proxy one MCP image. The browser does not call the RAG host itself."""
        cleaned = image_id.strip()
        if not cleaned or len(cleaned) > 200:
            raise HTTPException(status_code=400, detail="Invalid image id.")
        try:
            outcome = await gateway.call_tool("get_image", {"image_id": cleaned})
        except MissingTokenError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except McpCallError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if outcome.images:
            picture = outcome.images[0]
            raw = _decode_image(picture.data_base64)
            if len(raw) > _MAX_IMAGE_BYTES:
                raise HTTPException(status_code=413, detail="Image is too large.")
            return Response(
                content=raw,
                media_type=picture.mime_type or "application/octet-stream",
            )
        return await _image_via_authenticated_http(
            settings=resolved,
            image_id=cleaned,
            tool_text=outcome.text,
        )

    return app


def _inspection_payload(snapshot: Inspection) -> dict[str, Any]:
    """Shape the sidebar JSON. URLs are included; secrets are not."""
    return {
        "status": snapshot.status,
        "detail": snapshot.detail,
        "server_url": snapshot.server_url,
        "token_configured": snapshot.token_configured,
        "tools": [
            {"name": tool.name, "description": tool.description}
            for tool in snapshot.tools
        ],
        "resources": [
            {"name": item.name, "description": item.description}
            for item in snapshot.resources
        ],
        "prompts": [
            {"name": item.name, "description": item.description}
            for item in snapshot.prompts
        ],
    }


def _decode_image(data_base64: str) -> bytes:
    """Decode an MCP image block. Invalid data becomes an empty payload."""
    try:
        return base64.b64decode(data_base64, validate=False)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail="Image payload was not valid."
        ) from exc


async def _image_via_authenticated_http(
    *,
    settings: Settings,
    image_id: str,
    tool_text: str,
) -> Response:
    """Fetch an oversized or URL-only image using the process bearer token."""
    url = resolve_image_http_url(
        mcp_server_url=settings.mcp_server_url,
        image_id=image_id,
        tool_text=tool_text,
    )
    if not url:
        raise HTTPException(status_code=404, detail="Image not found.")
    if not settings.token_configured:
        raise HTTPException(status_code=503, detail="MCP_API_TOKEN is not set.")
    try:
        raw, media_type = await fetch_image_bytes(url, token=settings.mcp_api_token)
    except ImageFetchError as exc:
        status = 404 if exc.status_code == 404 else 502
        if exc.status_code == 413:
            status = 413
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return Response(content=raw, media_type=media_type)


app = create_app()
