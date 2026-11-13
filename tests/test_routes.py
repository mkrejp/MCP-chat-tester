"""HTTP routes keep the MCP token off the browser and require a session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from mcp_chat.app import create_app
from mcp_chat.config import Settings
from mcp_chat.mcp_client import InlineImage, Inspection, ToolInfo, ToolOutcome

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


class FakeMcp:
    """In-memory gateway used by the route tests."""

    def __init__(self) -> None:
        """Start connected with the two search tools the planner expects."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def inspect(self) -> Inspection:
        """Return a connected snapshot with no secret material."""
        return Inspection(
            status="connected",
            detail="",
            tools=(
                ToolInfo("search_domain_knowledge", "Search facts"),
                ToolInfo("ingest_text", "Write a document"),
            ),
            resources=(),
            prompts=(),
            server_url="https://rag.example/mcp/",
            token_configured=True,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        """Record the call and return one JSON hit or a tiny image."""
        self.calls.append((name, arguments))
        if name == "get_image":
            return ToolOutcome(
                name=name,
                arguments=arguments,
                text="",
                images=(InlineImage(mime_type="image/png", data_base64="aGk="),),
            )
        hit = (
            '[{"doc_id":"doc-1","content":"A fact","score":1,'
            '"page_image_id":"page:doc-1:1"}]'
        )
        return ToolOutcome(
            name=name,
            arguments=arguments,
            text=hit,
        )


def _settings() -> Settings:
    """Settings that do not point at the live RAG server."""
    return Settings(
        mcp_server_url="https://rag.example/mcp/",
        mcp_api_token="secret-token",
        host="127.0.0.1",
        port=8000,
        google_client_id="",
        google_client_secret="",
        google_allowed_emails=("reader@example.com",),
        session_secret="test-secret",
        public_base_url="http://127.0.0.1:8000",
    )


def test_health_is_public_and_chat_requires_a_session(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    caplog: LogCaptureFixture,
    request: FixtureRequest,
    mocker: MockerFixture,
) -> None:
    """Anonymous callers can probe health and cannot ask a question."""
    del monkeypatch, capsys, caplog, request, mocker
    client = TestClient(create_app(_settings(), mcp=FakeMcp()), follow_redirects=False)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 302
    assert client.get("/").headers["location"] == "/auth/login"
    denied = client.post("/api/chat", json={"message": "hello"})
    assert denied.status_code == 401
    assert client.get("/auth/login").status_code == 503


def test_signed_in_chat_searches_and_hides_the_token() -> None:
    """A session can search. The token is absent from inspection and the reply."""
    gateway = FakeMcp()
    client = TestClient(
        create_app(_settings(), mcp=gateway, forced_user="reader@example.com")
    )
    inspection = client.get("/api/inspection")
    assert inspection.status_code == 200
    body = inspection.json()
    assert body["status"] == "connected"
    assert "secret-token" not in inspection.text
    chat = client.post("/api/chat", json={"message": "Vršovice secession"})
    assert chat.status_code == 200
    payload = chat.json()
    assert payload["calls"][0]["tool"] == "search_domain_knowledge"
    assert gateway.calls[0][0] == "search_domain_knowledge"
    assert "ingest_text" not in {name for name, _args in gateway.calls}
    assert payload["images"] == [{"image_id": "page:doc-1:1"}]
    assert "secret-token" not in chat.text
    image = client.get("/api/images", params={"image_id": "page:doc-1:1"})
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == b"hi"
