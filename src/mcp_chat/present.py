"""Reading copy for RAG hits, without internal document identifiers."""

from __future__ import annotations

import re
from typing import Any

_URL = re.compile(r"https?://[^\s<>\"']+")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PAGE_ID = re.compile(
    r"\bpage:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:\d+\b",
    re.IGNORECASE,
)
_IMAGE_ID = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{16}(?![0-9a-fA-F])")
_MACHINE = re.compile(
    r"\b(?:doc_id|page_image_id|image_id|source_type|page_number|"
    r"geo_source|lat|lon|verification_status|chunk_sha256)\s*=\s*\S+",
    re.IGNORECASE,
)


def readable_text(value: str) -> str:
    """Remove document ids and machine tags. Keep web addresses intact.

    Coordinates are parsed from the raw hit before this runs, so dropping
    ``lat=`` / ``lon=`` here only affects what a person reads.
    """
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0).rstrip(".,;)"))
        return f"\x00{len(protected) - 1}\x00"

    text = _URL.sub(_hold, value or "")
    text = _PAGE_ID.sub(" ", text)
    text = _MACHINE.sub(" ", text)
    text = _UUID.sub(" ", text)
    text = _IMAGE_ID.sub(" ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip(" \t\n-:")
    for index, url in enumerate(protected):
        text = text.replace(f"\x00{index}\x00", url)
    return text


def readable_title(hit: dict[str, Any]) -> str:
    """Prefer a human document name over an encoded id."""
    for key in ("title", "caption", "parent_document", "filename", "name"):
        cleaned = readable_text(str(hit.get(key) or ""))
        if cleaned and _UUID.fullmatch(cleaned) is None:
            return cleaned[:120]
    return "Finding"


def public_url(value: object) -> str:
    """Return an http(s) URL, or an empty string."""
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return ""
