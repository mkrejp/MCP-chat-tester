"""DeepL translate helper caches and validates batches."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from mcp_chat.translate import (
    TranslateError,
    clear_cache,
    deepl_api_url,
    translate_texts,
)


class _FakeTransport(httpx.AsyncBaseTransport):
    """Record DeepL calls and return canned translations."""

    def __init__(self, translations: list[str]) -> None:
        self.translations = list(translations)
        self.calls = 0
        self.bodies: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        data = json.loads(request.content.decode("utf-8"))
        self.bodies.append(data)
        count = len(data.get("text") or [])
        batch = self.translations[:count]
        return httpx.Response(
            200,
            json={
                "translations": [
                    {"detected_source_language": "EN", "text": text} for text in batch
                ]
            },
            request=request,
        )


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    clear_cache()


def test_deepl_api_url_picks_free_host() -> None:
    assert deepl_api_url("abc:fx") == "https://api-free.deepl.com"
    assert deepl_api_url("abc") == "https://api.deepl.com"
    assert deepl_api_url("abc", "https://example.test/") == "https://example.test"


def test_translate_texts_batches_and_caches() -> None:
    transport = _FakeTransport(["Ahoj", "světe"])

    async def run() -> tuple[list[str], list[str]]:
        async with httpx.AsyncClient(transport=transport) as client:
            first = await translate_texts(
                ["Hello", "world"],
                "cs",
                api_key="test-key:fx",
                client=client,
            )
            second = await translate_texts(
                ["Hello", "world"],
                "cs",
                api_key="test-key:fx",
                client=client,
            )
        return first, second

    first, second = asyncio.run(run())
    assert first == ["Ahoj", "světe"]
    assert second == ["Ahoj", "světe"]
    assert transport.calls == 1
    assert transport.bodies[0]["target_lang"] == "CS"
    assert transport.bodies[0]["text"] == ["Hello", "world"]


def test_translate_texts_rejects_missing_key() -> None:
    with pytest.raises(TranslateError, match="not configured"):
        asyncio.run(translate_texts(["Hello"], "de", api_key=""))


def test_translate_texts_keeps_empty_strings() -> None:
    transport = _FakeTransport(["Hallo"])

    async def run() -> list[str]:
        async with httpx.AsyncClient(transport=transport) as client:
            return await translate_texts(
                ["", "Hi", ""],
                "de",
                api_key="key",
                client=client,
            )

    assert asyncio.run(run()) == ["", "Hallo", ""]
    assert transport.bodies[0]["text"] == ["Hi"]
