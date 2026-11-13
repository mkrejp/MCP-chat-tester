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


def test_reading_copy_drops_document_ids_and_keeps_links() -> None:
    """Encoded document ids leave the note. A web address stays readable."""
    doc_id = "6210cafc-b01e-49ce-bbcf-4faed684d330"
    text = (
        '[{"doc_id":"' + doc_id + '",'
        '"parent_document":"Vila Hradecinska.pdf",'
        '"content":"Secese na namesti. See https://example.com/vila '
        "and page:" + doc_id + ":3 doc_id=" + doc_id + ' lat=50.07 lon=14.45",'
        '"url":"https://example.com/images/' + doc_id + '/abcdabcdabcdabcd",'
        '"lat":50.07,"lon":14.45,"geo_source":"refined"}]'
    )
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    place = places_from_outcomes([outcome])[0].to_dict()
    assert place["title"] == "Vila Hradecinska.pdf"
    assert doc_id not in place["text"]
    assert "page:" not in place["text"]
    assert "https://example.com/vila" in place["text"]
    # Auth-gated /images/... links are not browser "Open source" targets.
    assert place["url"] == ""
    assert place["image_id"] == "abcdabcdabcdabcd"
    assert place["lat"] == 50.07


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


def test_nested_refined_and_geom_coordinates() -> None:
    """A null top-level lat still uses geo_refined, then a GeoJSON point."""
    refined = ToolOutcome(
        name="search_domain_knowledge",
        arguments={},
        text=(
            '[{"content":"Krouna chapel","lat":null,"lon":null,'
            '"geo_refined":{"lat":49.77,"lon":16.03}}]'
        ),
    )
    geom = ToolOutcome(
        name="search_domain_knowledge",
        arguments={},
        text=(
            '[{"content":"point","geom":{"type":"Point","coordinates":[16.1,49.8]}}]'
        ),
    )
    places = places_from_outcomes([refined, geom])
    assert places[0].lat == 49.77
    assert places[0].lon == 16.03
    assert places[0].geo_source == "refined"
    assert places[1].lat == 49.8
    assert places[1].lon == 16.1
    assert places[1].geo_source == "geom"


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
    stored = next(
        layer for layer in catalog["layers"] if layer["id"] == "locality_links"
    )
    assert stored["default_on"] is False
