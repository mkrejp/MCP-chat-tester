"""HTTP routes keep the MCP token off the browser and require a session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
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

    def __init__(self, *, oversized: bool = False) -> None:
        """Start connected with the two search tools the planner expects."""
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.oversized = oversized

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
            if self.oversized:
                image_id = str(arguments.get("image_id") or "")
                return ToolOutcome(
                    name=name,
                    arguments=arguments,
                    text=(
                        "Image exceeds MCP inline limit (9000000 bytes). "
                        "Fetch via authenticated HTTP: "
                        f"https://rag.example/images/doc-1/{image_id}"
                    ),
                    images=(),
                )
            return ToolOutcome(
                name=name,
                arguments=arguments,
                text="",
                images=(InlineImage(mime_type="image/png", data_base64="aGk="),),
            )
        hit = (
            '[{"doc_id":"doc-1","content":"A fact","score":1,'
            '"page_image_id":"page:doc-1:1",'
            '"url":"https://rag.example/images/doc-1/page:doc-1:1"}]'
        )
        return ToolOutcome(
            name=name,
            arguments=arguments,
            text=hit,
        )


def _settings(**overrides: Any) -> Settings:
    """Settings that do not point at the live RAG server."""
    values: dict[str, Any] = {
        "mcp_server_url": "https://rag.example/mcp/",
        "mcp_api_token": "secret-token",
        "host": "127.0.0.1",
        "port": 8000,
        "google_client_id": "",
        "google_client_secret": "",
        "google_allowed_emails": ("reader@example.com",),
        "session_secret": "test-secret",
        "public_base_url": "http://127.0.0.1:8000",
        "deepl_api_key": "",
        "deepl_api_url": "",
    }
    values.update(overrides)
    return Settings(**values)


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
    assert "rag.example/images/" not in chat.text
    assert "/api/images?image_id=" in chat.text
    image = client.get("/api/images", params={"image_id": "page:doc-1:1"})
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/png")
    assert image.content == b"hi"


def test_oversized_image_is_fetched_with_bearer(
    monkeypatch: MonkeyPatch,
) -> None:
    """When MCP returns a URL hint, this app fetches bytes with the token."""
    gateway = FakeMcp(oversized=True)
    seen: dict[str, str] = {}

    class _Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            seen["authorization"] = request.headers.get("Authorization", "")
            seen["url"] = str(request.url)
            return httpx.Response(
                200,
                content=b"big-png",
                headers={"content-type": "image/png"},
                request=request,
            )

    real_client = httpx.AsyncClient

    def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs = dict(kwargs)
        kwargs["transport"] = _Transport()
        return real_client(*args, **kwargs)

    monkeypatch.setattr("mcp_chat.image_proxy.httpx.AsyncClient", fake_async_client)
    client = TestClient(
        create_app(_settings(), mcp=gateway, forced_user="reader@example.com")
    )
    image = client.get("/api/images", params={"image_id": "page:doc-1:1"})
    assert image.status_code == 200
    assert image.content == b"big-png"
    assert seen["authorization"] == "Bearer secret-token"
    assert "Missing or invalid bearer token" not in image.text
    assert seen["url"].endswith("/images/doc-1/page:doc-1:1")


def test_translate_requires_session_and_deepl_key() -> None:
    """Anonymous callers are blocked; signed-in callers need DeepL configured."""
    anonymous = TestClient(
        create_app(_settings(), mcp=FakeMcp()), follow_redirects=False
    )
    denied = anonymous.post("/api/translate", json={"target": "cs", "texts": ["Hello"]})
    assert denied.status_code == 401

    client = TestClient(
        create_app(_settings(), mcp=FakeMcp(), forced_user="reader@example.com")
    )
    missing = client.post("/api/translate", json={"target": "cs", "texts": ["Hello"]})
    assert missing.status_code == 503
    assert "DeepL" in missing.json()["detail"]


def test_translate_rejects_unsupported_language() -> None:
    """Only the six UI languages are accepted."""
    client = TestClient(
        create_app(
            _settings(deepl_api_key="test-key"),
            mcp=FakeMcp(),
            forced_user="reader@example.com",
        )
    )
    response = client.post("/api/translate", json={"target": "pl", "texts": ["Hello"]})
    assert response.status_code == 400


def test_translate_returns_deepl_batch(monkeypatch: MonkeyPatch) -> None:
    """A configured key returns translations and a second call can use the cache."""
    from mcp_chat import translate as translate_mod

    translate_mod.clear_cache()
    calls = {"n": 0}

    async def fake_translate(
        texts: list[str],
        target: str,
        *,
        api_key: str,
        api_url: str = "",
        client: Any = None,
    ) -> list[str]:
        del target, api_key, api_url, client
        calls["n"] += 1
        return [f"<{text}>" for text in texts]

    monkeypatch.setattr("mcp_chat.app.translate_texts", fake_translate)
    client = TestClient(
        create_app(
            _settings(deepl_api_key="test-key:fx"),
            mcp=FakeMcp(),
            forced_user="reader@example.com",
        )
    )
    first = client.post("/api/translate", json={"target": "de", "texts": ["Hello", ""]})
    assert first.status_code == 200
    assert first.json() == {"translations": ["<Hello>", "<>"]}
    assert calls["n"] == 1
