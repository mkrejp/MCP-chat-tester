"""Tool planning stays on the read-only RAG surface."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_chat.interrogate import (
    collect_image_ids,
    plan_calls,
    render_reply,
    summarize_tool_text,
)
from mcp_chat.mcp_client import ToolOutcome

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture

_TOOLS = {
    "search_domain_knowledge",
    "search_images",
    "list_documents",
    "ingest_text",
    "update_document",
}


def test_plan_searches_knowledge_and_skips_writes(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    caplog: LogCaptureFixture,
    request: FixtureRequest,
    mocker: MockerFixture,
) -> None:
    """A normal question searches knowledge and never ingests."""
    del monkeypatch, capsys, caplog, request, mocker
    calls = plan_calls("What is known about Vršovice?", _TOOLS)
    assert [call.name for call in calls] == ["search_domain_knowledge"]
    assert calls[0].arguments["format"] == "json"
    assert "ingest_text" not in {call.name for call in calls}


def test_plan_adds_images_and_catalog() -> None:
    """Visual and catalog wording select those extra read-only tools."""
    calls = plan_calls("Show photos and list documents about the square", _TOOLS)
    names = [call.name for call in calls]
    assert names == ["list_documents", "search_domain_knowledge", "search_images"]


def test_summarize_json_hits_and_collect_page_images() -> None:
    """JSON hits become bullets, and page image ids are collected once."""
    text = (
        '[{"doc_id":"abc","content":"Secese na n\\u00e1m\\u011bst\\u00ed",'
        '"score":0.5,"page_image_id":"page:abc:2"}]'
    )
    summary = summarize_tool_text(text)
    assert "Secese" in summary
    assert "page:abc:2" not in summary or "abc" in summary
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    assert collect_image_ids([outcome]) == ["page:abc:2"]
    reply = render_reply("secese", [outcome])
    assert "search_domain_knowledge" in reply
    assert "data:image" not in reply
