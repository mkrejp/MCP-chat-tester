"""Fact cards keep text, pictures, and GPS from RAG hits."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_chat.map_layers import map_catalog
from mcp_chat.mcp_client import ToolOutcome
from mcp_chat.places import places_from_outcomes

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture
    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from _pytest.monkeypatch import MonkeyPatch
    from pytest_mock.plugin import MockerFixture


def test_json_hit_keeps_coordinates_text_and_image(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
    caplog: LogCaptureFixture,
    request: FixtureRequest,
    mocker: MockerFixture,
) -> None:
    """A structured hit becomes one card and does not invent a second point."""
    del monkeypatch, capsys, caplog, request, mocker
    text = (
        '[{"doc_id":"doc-9","content":"Secese na namesti",'
        '"lat":50.0711,"lon":14.4512,"geo_source":"refined",'
        '"page_image_id":"page:doc-9:3","source_type":"pdf_chunk"}]'
    )
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    places = places_from_outcomes([outcome])
    assert len(places) == 1
    place = places[0].to_dict()
    assert place["lat"] == 50.0711
    assert place["lon"] == 14.4512
    assert place["geo_source"] == "refined"
    assert place["image_id"] == "page:doc-9:3"
    assert "Secese" in place["text"]
    assert place["doc_id"] == "doc-9"


def test_text_coordinates_and_rejected_ranges() -> None:
    """Labeled coordinates are read. Values outside the globe are dropped."""
    labeled = ToolOutcome(
        name="search_domain_knowledge",
        arguments={},
        text="## Vila\nSouřadnice: 50.07, 14.45\nPoznámka: roh",
    )
    broken = ToolOutcome(
        name="search_domain_knowledge",
        arguments={},
        text='[{"content":"bad","lat":120,"lon":14}]',
    )
    places = places_from_outcomes([labeled, broken])
    assert places[0].lat == 50.07
    assert places[0].lon == 14.45
    assert places[0].geo_source == "text"
    assert places[1].lat is None
    assert places[1].text == "bad"


def test_hit_url_is_not_kept_as_browser_image_id() -> None:
    """Auth-gated RAG URLs must not become img src values."""
    text = (
        '[{"doc_id":"doc-9","content":"see '
        'https://rag.example/images/doc-9/page:doc-9:3",'
        '"page_image_id":"https://rag.example/images/doc-9/page:doc-9:3"}]'
    )
    places = places_from_outcomes(
        [ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)]
    )
    assert places[0].image_id == ""
    assert "rag.example/images/" not in places[0].text
    assert "/api/images?image_id=" in places[0].text


def test_map_catalog_lists_geoserver_context_without_secrets() -> None:
    """Context layers point at the public Railway GeoServer."""
    catalog = map_catalog()
    names = [layer["wms_name"] for layer in catalog["layers"]]
    assert names == [
        "trails:obce",
        "trails:cast_obce",
        "trails:locality_links",
        "trails:layer_objects",
    ]
    assert catalog["wms_url"].startswith("https://geoserver-production-4d0d")
    assert "token" not in str(catalog).lower()
    assert any(layer["default_on"] for layer in catalog["layers"])
