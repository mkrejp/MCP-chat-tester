"""DeepL-backed batch translation with a process-local cache."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

SUPPORTED = frozenset({"en", "cs", "de", "fr", "it", "es"})
_DEEPL_LANG = {
    "en": "EN",
    "cs": "CS",
    "de": "DE",
    "fr": "FR",
    "it": "IT",
    "es": "ES",
}
MAX_BATCH = 50
MAX_CHARS = 8000

_cache: dict[tuple[str, str], str] = {}


class TranslateError(Exception):
    """Raised when DeepL cannot translate the batch."""


def deepl_api_url(api_key: str, override: str = "") -> str:
    """Pick the DeepL host. Free keys end with ``:fx``."""
    cleaned = override.strip().rstrip("/")
    if cleaned:
        return cleaned
    if api_key.strip().endswith(":fx"):
        return "https://api-free.deepl.com"
    return "https://api.deepl.com"


def clear_cache() -> None:
    """Drop cached translations. Used by tests."""
    _cache.clear()


def _cache_key(target: str, text: str) -> tuple[str, str]:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return target, digest


async def translate_texts(
    texts: list[str],
    target: str,
    *,
    api_key: str,
    api_url: str = "",
    client: httpx.AsyncClient | None = None,
) -> list[str]:
    """Translate ``texts`` into ``target`` via DeepL.

    Empty strings are returned unchanged. Cache hits skip the network.
    """
    lang = target.strip().lower()
    if lang not in SUPPORTED:
        raise TranslateError(f"Unsupported language: {target}")
    if not api_key.strip():
        raise TranslateError("DeepL API key is not configured.")
    if len(texts) > MAX_BATCH:
        raise TranslateError(f"At most {MAX_BATCH} texts per request.")
    for text in texts:
        if len(text) > MAX_CHARS:
            raise TranslateError(f"Each text may be at most {MAX_CHARS} characters.")

    results = list(texts)
    pending_indexes: list[int] = []
    pending_texts: list[str] = []
    for index, text in enumerate(texts):
        if not text:
            continue
        cached = _cache.get(_cache_key(lang, text))
        if cached is not None:
            results[index] = cached
            continue
        pending_indexes.append(index)
        pending_texts.append(text)

    if not pending_texts:
        return results

    payload: dict[str, Any] = {
        "text": pending_texts,
        "target_lang": _DEEPL_LANG[lang],
    }
    headers = {
        "Authorization": f"DeepL-Auth-Key {api_key.strip()}",
        "Content-Type": "application/json",
    }
    url = f"{deepl_api_url(api_key, api_url)}/v2/translate"
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await http.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise TranslateError(f"DeepL request failed: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code >= 400:
        detail = response.text.strip() or response.reason_phrase
        raise TranslateError(f"DeepL error {response.status_code}: {detail}")

    body = response.json()
    translations = body.get("translations")
    if not isinstance(translations, list) or len(translations) != len(pending_texts):
        raise TranslateError("DeepL returned an unexpected payload.")

    pairs = zip(pending_indexes, translations, pending_texts, strict=True)
    for index, item, source in pairs:
        if not isinstance(item, dict) or "text" not in item:
            raise TranslateError("DeepL returned an unexpected payload.")
        translated = str(item["text"])
        _cache[_cache_key(lang, source)] = translated
        results[index] = translated
    return results
