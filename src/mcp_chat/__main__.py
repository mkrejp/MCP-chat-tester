"""Run the chat server."""

from __future__ import annotations

import uvicorn

from mcp_chat.app import create_app
from mcp_chat.config import load_settings


def main() -> None:
    """Start uvicorn with the process environment."""
    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
