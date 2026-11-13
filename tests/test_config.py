"""Settings load from the environment and never require a secret file."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mcp_chat.config import load_settings

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def test_load_settings_reads_shared_credential_names(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    capsys: CaptureFixture[str],
    caplog: LogCaptureFixture,
    request: FixtureRequest,
    mocker: MockerFixture,
) -> None:
    """Google and MCP values use the fastmcp-rag-timer-frontend names."""
    del capsys, caplog, request, mocker
    monkeypatch.delenv("MCP_API_TOKEN", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_ALLOWED_EMAILS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MCP_API_TOKEN=server-token",
                "GOOGLE_CLIENT_ID=client-id",
                "GOOGLE_CLIENT_SECRET=client-secret",
                "GOOGLE_ALLOWED_EMAILS=One@Example.com, two@example.com",
                "PUBLIC_BASE_URL=http://127.0.0.1:8000",
            ]
        ),
        encoding="utf-8",
    )
    settings = load_settings(env_file)
    assert settings.mcp_api_token == "server-token"
    assert settings.google_client_id == "client-id"
    assert settings.google_client_secret == "client-secret"
    assert settings.google_allowed_emails == ("one@example.com", "two@example.com")
    assert settings.token_configured is True
    assert settings.google_configured is True
    assert settings.mcp_server_url.endswith("/mcp/")


def test_process_environment_wins_over_file(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A filled process environment is not overwritten by the env file."""
    monkeypatch.setenv("MCP_API_TOKEN", "from-process")
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_API_TOKEN=from-file\n", encoding="utf-8")
    settings = load_settings(env_file)
    assert settings.mcp_api_token == "from-process"
