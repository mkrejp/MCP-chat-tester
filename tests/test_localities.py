"""Questions that name a stored municipality pin notes that have no GPS."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from mcp_chat.app import create_app
from mcp_chat.config import Settings
from mcp_chat.localities import Locality, attach_localities, locality_from_feature
from mcp_chat.mcp_client import Inspection, ToolInfo, ToolOutcome
from mcp_chat.places import places_from_outcomes


def _krouna() -> Locality:
    """The stored Krouna point used by the tests."""
    return Locality(name="Krouna", key="krouna", lat=50.0, lon=16.0)


def _lookup(name: str) -> Locality | None:
    """Resolve only the Krouna key."""
    if name == "krouna":
        return _krouna()
    return None


def test_question_locality_fills_notes_without_coordinates() -> None:
    """A Krouna question pins every note that did not bring its own GPS."""
    text = (
        '[{"parent_document":"volne-smery.pdf","content":"A chapel interior",'
        '"lat":null,"lon":null,"page_image_id":"page:doc:237"},'
        '{"parent_document":"kept.pdf","content":"Already located",'
        '"lat":49.1,"lon":15.2,"geo_source":"refined"}]'
    )
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    places = attach_localities(places_from_outcomes([outcome]), "Krouna", _lookup)
    assert places[0].lat == 50.0
    assert places[0].lon == 16.0
    assert places[0].geo_source == "Krouna"
    assert places[0].id == "1"
    assert places[1].lat == 49.1
    assert places[1].geo_source == "refined"


def test_two_places_do_not_guess_when_the_note_names_neither() -> None:
    """An ambiguous question leaves a note unpinned unless it names a place."""
    praha = Locality(name="Praha", key="praha", lat=50.0, lon=14.0)

    def lookup(name: str) -> Locality | None:
        if name == "krouna":
            return _krouna()
        if name == "praha":
            return praha
        return None

    text = '[{"content":"A studio interior","parent_document":"studio.pdf"}]'
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    places = attach_localities(
        places_from_outcomes([outcome]), "Krouna and Praha", lookup
    )
    assert places[0].lat is None


def test_feature_point_is_read_as_latitude_longitude() -> None:
    """GeoJSON positions are longitude, latitude."""
    feature = {
        "properties": {"name": "Krouna"},
        "geometry": {"type": "Point", "coordinates": [16, 50]},
    }
    locality = locality_from_feature(feature, fallback_key="krouna")
    assert locality is not None
    assert locality.lat == 50
    assert locality.lon == 16
    assert locality.name == "Krouna"


class _Gateway:
    """Returns one hit with an image and no coordinates."""

    async def inspect(self) -> Inspection:
        return Inspection(
            status="connected",
            detail="",
            tools=(ToolInfo("search_domain_knowledge", "Search"),),
            server_url="https://rag.example/mcp/",
            token_configured=True,
        )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
        del name, arguments
        return ToolOutcome(
            name="search_domain_knowledge",
            arguments={},
            text=json.dumps(
                [
                    {
                        "parent_document": "volne-smery.pdf",
                        "content": "Page without a coordinate",
                        "lat": None,
                        "page_image_id": "page:doc:237",
                    }
                ]
            ),
        )


def test_chat_uses_the_locality_lookup() -> None:
    """The chat response includes the stored point for a Krouna question."""
    settings = Settings(
        mcp_server_url="https://rag.example/mcp/",
        mcp_api_token="secret-token",
        host="127.0.0.1",
        port=8000,
        google_client_id="",
        google_client_secret="",
        google_allowed_emails=("reader@example.com",),
        session_secret="test-secret",
        public_base_url="http://127.0.0.1:8000",
    )
    client = TestClient(
        create_app(
            settings,
            mcp=_Gateway(),
            forced_user="reader@example.com",
            locality_lookup=_lookup,
        )
    )
    response = client.post("/api/chat", json={"message": "Krouna"})
    assert response.status_code == 200
    place = response.json()["places"][0]
    assert place["lat"] == 50.0
    assert place["lon"] == 16.0
    assert place["geo_source"] == "Krouna"
    assert "secret-token" not in response.text
