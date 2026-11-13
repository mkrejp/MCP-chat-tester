"""Choose read-only MCP tools for a question and turn hits into a reply."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from mcp_chat.mcp_client import ToolOutcome

READ_ONLY_TOOLS = frozenset(
    {
        "search_domain_knowledge",
        "search_images",
        "list_documents",
        "get_document_text",
        "get_document_images",
        "get_image",
        "get_page_highlights",
        "get_page_image",
        "get_document_chunks",
        "list_links",
        "link_stats",
        "list_image_records",
        "image_record_stats",
        "list_geo_refinements",
    }
)

_VISUAL_WORDS = (
    "image",
    "photo",
    "picture",
    "map",
    "figure",
    "plan",
    "obraz",
    "foto",
    "fotog",
    "mapa",
    "plán",
    "výkres",
)
_CATALOG_WORDS = (
    "catalog",
    "catalogue",
    "which documents",
    "list documents",
    "seznam dokument",
    "jaké dokument",
    "jake dokument",
)
_IMAGE_ID = re.compile(
    r"page:[0-9a-fA-F-]{8,}:\d+|(?<![0-9a-fA-F])[0-9a-fA-F]{16}(?![0-9a-fA-F])"
)


@dataclass(frozen=True)
class PlannedCall:
    """One read-only tool call the chat is allowed to make."""

    name: str
    arguments: dict[str, Any]


def plan_calls(message: str, available: set[str]) -> list[PlannedCall]:
    """Pick read-only RAG tools for ``message``.

    Write tools such as ``ingest_text`` are never selected, even when the
    server advertises them.
    """
    text = " ".join(message.split())
    lowered = text.lower()
    allowed = available & READ_ONLY_TOOLS
    calls: list[PlannedCall] = []
    if any(word in lowered for word in _CATALOG_WORDS) and "list_documents" in allowed:
        calls.append(PlannedCall("list_documents", {"since": ""}))
    if "search_domain_knowledge" in allowed and text:
        calls.append(
            PlannedCall(
                "search_domain_knowledge",
                {
                    "query": text,
                    "limit": 5,
                    "format": "json",
                    "include_figures": True,
                },
            )
        )
    if (
        any(word in lowered for word in _VISUAL_WORDS)
        and "search_images" in allowed
        and text
    ):
        calls.append(
            PlannedCall(
                "search_images",
                {
                    "query": text,
                    "limit": 4,
                    "hybrid": False,
                    "format": "json",
                },
            )
        )
    return calls


def render_reply(question: str, outcomes: list[ToolOutcome]) -> str:
    """Build a markdown reply from tool text. Image bytes are not included."""
    if not outcomes:
        return (
            "No read-only search tool is available on this MCP server, "
            "so I did not guess at an answer."
        )
    sections: list[str] = [f"I asked the knowledge server about “{_plain(question)}”."]
    for outcome in outcomes:
        sections.append(f"### {outcome.name}")
        if outcome.is_error:
            sections.append(_plain(outcome.text) or "The tool reported an error.")
            continue
        sections.append(summarize_tool_text(outcome.text))
    return "\n\n".join(sections).strip()


def summarize_tool_text(text: str) -> str:
    """Prefer structured hits. Fall back to trimmed plain text."""
    hits = _parse_hits(text)
    if hits is None:
        body = text.strip() or "The tool returned no text."
        return body[:4000]
    if not hits:
        return "The tool returned no matching hits."
    lines: list[str] = []
    for hit in hits[:5]:
        content = _plain(str(hit.get("content") or hit.get("text") or "")).strip()
        content = content[:500]
        title = _plain(
            str(hit.get("title") or hit.get("doc_id") or hit.get("source") or "hit")
        )
        score = hit.get("score")
        score_bit = f" (score {score})" if isinstance(score, int | float) else ""
        page = hit.get("page") or hit.get("page_number")
        page_bit = f", page {page}" if page else ""
        lines.append(f"- **{title}**{page_bit}{score_bit}: {content}")
    return "\n".join(lines)


def collect_image_ids(outcomes: list[ToolOutcome]) -> list[str]:
    """Collect page and figure ids the UI can load through the image proxy."""
    found: list[str] = []
    seen: set[str] = set()
    for outcome in outcomes:
        for match in _IMAGE_ID.findall(outcome.text):
            if match not in seen:
                seen.add(match)
                found.append(match)
        for hit in _parse_hits(outcome.text) or []:
            for key in ("page_image_id", "image_id"):
                value = str(hit.get(key) or "").strip()
                if value and value not in seen:
                    seen.add(value)
                    found.append(value)
    return found[:8]


def _parse_hits(text: str) -> list[dict[str, Any]] | None:
    """Return a list of hit objects when ``text`` is a JSON array."""
    stripped = text.strip()
    if not stripped.startswith("["):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]


def _plain(value: str) -> str:
    """Drop characters that would break a one-line markdown label."""
    return value.replace("\n", " ").replace("`", "'")
