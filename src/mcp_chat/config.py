"""Environment configuration. Secret values stay in the process environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MCP_SERVER_URL = "https://rag-timer-00001-production.up.railway.app/mcp/"


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from the environment.

    ``GOOGLE_CLIENT_ID``, ``GOOGLE_CLIENT_SECRET``, and ``MCP_API_TOKEN`` are
    the same variable names used by fastmcp-rag-timer-frontend. This process
    is the only place those values are read.
    """

    mcp_server_url: str
    mcp_api_token: str
    host: str
    port: int
    google_client_id: str
    google_client_secret: str
    google_allowed_emails: tuple[str, ...]
    session_secret: str
    public_base_url: str

    @property
    def token_configured(self) -> bool:
        """Return whether the MCP bearer token is present."""
        return bool(self.mcp_api_token)

    @property
    def google_configured(self) -> bool:
        """Return whether both Google OAuth client values are present."""
        return bool(self.google_client_id and self.google_client_secret)


def load_settings(env_file: Path | None = None) -> Settings:
    """Load settings from the environment, filling gaps from a local env file.

    Existing process variables win over the file. The file is optional and is
    ignored when it is absent.
    """
    _load_dotenv(env_file or Path(".env"))
    emails = tuple(
        item.strip().lower()
        for item in os.environ.get("GOOGLE_ALLOWED_EMAILS", "").split(",")
        if item.strip()
    )
    return Settings(
        mcp_server_url=os.environ.get("MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL).strip()
        or DEFAULT_MCP_SERVER_URL,
        mcp_api_token=os.environ.get("MCP_API_TOKEN", "").strip(),
        host=os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        google_allowed_emails=emails,
        session_secret=os.environ.get("SESSION_SECRET", "").strip()
        or "dev-session-change-me",
        public_base_url=os.environ.get(
            "PUBLIC_BASE_URL", "http://127.0.0.1:8000"
        ).strip()
        or "http://127.0.0.1:8000",
    )


def _load_dotenv(path: Path) -> None:
    """Set missing environment variables from a simple KEY=VALUE file."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
