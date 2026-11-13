"""Streamable HTTP MCP client. The bearer token never leaves this process."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, ImageContent, TextContent

from mcp_chat.config import Settings

logger = logging.getLogger("mcp_chat.mcp")


class MissingTokenError(Exception):
    """Raised when a call is attempted without ``MCP_API_TOKEN``."""


class McpCallError(Exception):
    """Raised when the MCP transport fails before a tool result arrives."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Store a caller-safe message and the HTTP status when one exists."""
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ToolInfo:
    """One tool advertised by the server."""

    name: str
    description: str


@dataclass(frozen=True)
class NamedInfo:
    """A resource or prompt name the inspection panel can show."""

    name: str
    description: str


@dataclass(frozen=True)
class InlineImage:
    """An image block returned by a tool, without the raw token nearby."""

    mime_type: str
    data_base64: str


@dataclass(frozen=True)
class ToolOutcome:
    """Text and images from one tool call."""

    name: str
    arguments: dict[str, Any]
    text: str
    images: tuple[InlineImage, ...] = ()
    is_error: bool = False


@dataclass(frozen=True)
class Inspection:
    """Connection snapshot for the sidebar."""

    status: str
    detail: str
    tools: tuple[ToolInfo, ...] = ()
    resources: tuple[NamedInfo, ...] = ()
    prompts: tuple[NamedInfo, ...] = ()
    server_url: str = ""
    token_configured: bool = False


class McpGateway(Protocol):
    """The chat routes depend on this surface, not on HTTP details."""

    async def inspect(self) -> Inspection:
        """List tools, resources, and prompts."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Call one tool and return its text and images."""


@dataclass
class HttpMcpClient:
    """Open one stateless Streamable HTTP session per operation."""

    settings: Settings
    _tools: tuple[ToolInfo, ...] = field(default_factory=tuple, init=False)
    _inspected: bool = field(default=False, init=False)

    async def inspect(self) -> Inspection:
        """Connect and list capabilities. Failures stay on this side of the API."""
        if not self.settings.token_configured:
            return Inspection(
                status="not_configured",
                detail="MCP_API_TOKEN is not set.",
                server_url=self.settings.mcp_server_url,
                token_configured=False,
            )
        try:
            tools, resources, prompts = await self._list_capabilities()
        except McpCallError as exc:
            status = "auth_error" if exc.status_code in {401, 403} else "offline"
            return Inspection(
                status=status,
                detail=str(exc),
                server_url=self.settings.mcp_server_url,
                token_configured=True,
            )
        self._tools = tools
        self._inspected = True
        return Inspection(
            status="connected",
            detail="",
            tools=tools,
            resources=resources,
            prompts=prompts,
            server_url=self.settings.mcp_server_url,
            token_configured=True,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Call ``name`` and return its text and inline images."""
        result = await self._invoke(name, arguments)
        return outcome_from_result(name, arguments, result)

    def known_tool_names(self) -> set[str]:
        """Return tool names from the last successful inspection."""
        return {tool.name for tool in self._tools}

    async def _list_capabilities(
        self,
    ) -> tuple[tuple[ToolInfo, ...], tuple[NamedInfo, ...], tuple[NamedInfo, ...]]:
        """Initialize one session and read tools, resources, and prompts."""

        async def _read(
            session: ClientSession,
        ) -> tuple[
            tuple[ToolInfo, ...],
            tuple[NamedInfo, ...],
            tuple[NamedInfo, ...],
        ]:
            tool_result = await session.list_tools()
            tools = tuple(
                ToolInfo(name=item.name, description=(item.description or "").strip())
                for item in tool_result.tools
            )
            resources: tuple[NamedInfo, ...] = ()
            prompts: tuple[NamedInfo, ...] = ()
            try:
                resource_result = await session.list_resources()
                resources = tuple(
                    NamedInfo(
                        name=str(item.uri),
                        description=(item.name or item.description or "").strip(),
                    )
                    for item in resource_result.resources
                )
            except Exception:
                logger.info("MCP server did not list resources")
            try:
                prompt_result = await session.list_prompts()
                prompts = tuple(
                    NamedInfo(
                        name=item.name,
                        description=(item.description or "").strip(),
                    )
                    for item in prompt_result.prompts
                )
            except Exception:
                logger.info("MCP server did not list prompts")
            return tools, resources, prompts

        return await self._with_session(_read)

    async def _invoke(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call ``name``. The bearer header is attached only on this client."""

        async def _call(session: ClientSession) -> CallToolResult:
            logger.info("MCP tool %s argument_count=%s", name, len(arguments))
            return await session.call_tool(name, arguments)

        return await self._with_session(_call)

    async def _with_session(self, action: Any) -> Any:
        """Run ``action`` inside a new Streamable HTTP session."""
        token = self.settings.mcp_api_token
        if not token:
            raise MissingTokenError("MCP_API_TOKEN is not set.")
        timeout = httpx.Timeout(30.0, read=120.0)
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout
            ) as http_client:
                async with streamable_http_client(
                    self.settings.mcp_server_url,
                    http_client=http_client,
                    terminate_on_close=False,
                ) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return await action(session)
        except MissingTokenError:
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("MCP HTTP status %s", status)
            raise McpCallError(f"MCP server returned HTTP {status}.", status) from exc
        except McpCallError:
            raise
        except Exception as exc:
            logger.warning("MCP call failed: %s", type(exc).__name__)
            raise McpCallError("MCP call failed.") from exc


def outcome_from_result(
    name: str,
    arguments: dict[str, Any],
    result: CallToolResult,
) -> ToolOutcome:
    """Convert a protocol result into text plus inline images.

    Base64 image bytes are kept on the image objects and left out of ``text``.
    """
    parts: list[str] = []
    images: list[InlineImage] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ImageContent):
            images.append(InlineImage(mime_type=block.mimeType, data_base64=block.data))
    text = "\n".join(parts).strip()
    if result.isError and not text.startswith("Error"):
        text = f"Error {text or 'tool failed'}"
    return ToolOutcome(
        name=name,
        arguments=arguments,
        text=text,
        images=tuple(images),
        is_error=bool(result.isError) or text.startswith("Error"),
    )
