"""Reading copy keeps links and drops encoded document ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_chat.present import public_url, readable_text, readable_title

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def test_readable_text_strips_ids_inside_prose(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    caplog: LogCaptureFixture,
    request: FixtureRequest,
    mocker: MockerFixture,
) -> None:
    """A UUID in a sentence is removed. The URL that contains one is kept."""
    del monkeypatch, capsys, caplog, request, mocker
    doc_id = "6210cafc-b01e-49ce-bbcf-4faed684d330"
    raw = (
        f"Studio note {doc_id} page:{doc_id}:2 "
        f"https://example.com/a/{doc_id}/photo image abcdabcdabcdabcd."
    )
    cleaned = readable_text(raw)
    assert doc_id not in cleaned.replace(f"https://example.com/a/{doc_id}/photo", "")
    assert f"https://example.com/a/{doc_id}/photo" in cleaned
    assert "abcdabcdabcdabcd" not in cleaned
    assert "Studio note" in cleaned


def test_title_and_public_url_ignore_bare_ids() -> None:
    """The visible name is the document, and only http(s) URLs pass through."""
    doc_id = "6210cafc-b01e-49ce-bbcf-4faed684d330"
    titled = readable_title({"doc_id": doc_id, "filename": "Muchova.pdf"})
    assert titled == "Muchova.pdf"
    assert readable_title({"doc_id": doc_id}) == "Finding"
    assert (
        public_url(f"https://example.com/{doc_id}") == f"https://example.com/{doc_id}"
    )
    assert public_url("javascript:alert(1)") == ""
    assert public_url(f"https://rag.example/images/{doc_id}/page:{doc_id}:1") == ""


def test_readable_text_rewrites_rag_image_urls() -> None:
    """Auth-gated RAG image links become same-origin proxy paths."""
    raw = "See https://rag.example/images/doc-1/page:doc-1:2 for the plate."
    cleaned = readable_text(raw)
    assert "rag.example/images/" not in cleaned
    assert "/api/images?image_id=page%3Adoc-1%3A2" in cleaned
