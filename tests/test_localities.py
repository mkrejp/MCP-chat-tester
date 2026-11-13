"""Questions that name a stored municipality pin notes that have no GPS."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from mcp_chat.app import create_app
from mcp_chat.config import Settings
from mcp_chat.interrogate import plan_calls
from mcp_chat.localities import Locality, attach_localities, locality_from_feature
from mcp_chat.mcp_client import Inspection, ToolInfo, ToolOutcome
from mcp_chat.places import places_from_outcomes


def _pardubice() -> Locality:
    """A unique municipality with Nominatim-quality coordinates."""
    return Locality(
        name="Pardubice",
        key="pardubice",
        lat=50.0385812,
        lon=15.7791356,
        obec_id="555134",
        okres="Pardubice",
        kraj="Pardubický kraj",
        level="obec",
        geo_source="nominatim",
    )


def _lookup(name: str) -> Locality | None:
    """Resolve only the Pardubice key."""
    if name == "pardubice":
        return _pardubice()
    return None


def test_question_locality_fills_notes_without_coordinates() -> None:
    """A Pardubice question pins every note that did not bring its own GPS."""
    text = (
        '[{"parent_document":"volne-smery.pdf","content":"A chapel interior",'
        '"lat":null,"lon":null,"page_image_id":"page:doc:237"},'
        '{"parent_document":"kept.pdf","content":"Already located",'
        '"lat":49.1,"lon":15.2,"geo_source":"refined"}]'
    )
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    places = attach_localities(places_from_outcomes([outcome]), "Pardubice", _lookup)
    assert places[0].lat == pytest.approx(50.0385812)
    assert places[0].lon == pytest.approx(15.7791356)
    assert places[0].geo_source == "nominatim"
    assert places[0].id == "1"
    assert places[1].lat == 49.1
    assert places[1].geo_source == "refined"


def test_coarse_integer_gps_is_replaced_by_locality() -> None:
    """Integer-degree pins are dropped so the village point can replace them."""
    text = (
        '[{"parent_document":"bad.pdf","content":"Okrouhlice fact",'
        '"lat":15,"lon":50,"geo_source":"original"}]'
    )
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    okrouhlice = Locality(
        name="Okrouhlice",
        key="okrouhlice",
        lat=49.6301144,
        lon=15.4823662,
        obec_id="569186",
        geo_source="nominatim",
    )

    def lookup(name: str) -> Locality | None:
        return okrouhlice if name == "okrouhlice" else None

    places = attach_localities(places_from_outcomes([outcome]), "Okrouhlice", lookup)
    assert places[0].lat == pytest.approx(49.6301144)
    assert places[0].lon == pytest.approx(15.4823662)


def test_two_places_do_not_guess_when_the_note_names_neither() -> None:
    """An ambiguous question leaves a note unpinned unless it names a place."""
    praha = Locality(name="Praha", key="praha", lat=50.0755, lon=14.4378)

    def lookup(name: str) -> Locality | None:
        if name == "pardubice":
            return _pardubice()
        if name == "praha":
            return praha
        return None

    text = '[{"content":"A studio interior","parent_document":"studio.pdf"}]'
    outcome = ToolOutcome(name="search_domain_knowledge", arguments={}, text=text)
    places = attach_localities(
        places_from_outcomes([outcome]), "Pardubice and Praha", lookup
    )
    assert places[0].lat is None


def test_feature_without_geom_uses_nominatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GeoServer properties alone are enough when Nominatim supplies GPS."""

    def fake_nominatim(
        *,
        name: str,
        okres: str,
        kraj: str,
    ) -> tuple[float, float] | None:
        del okres, kraj
        assert name == "Pardubice"
        return 50.0385812, 15.7791356

    monkeypatch.setattr("mcp_chat.localities._nominatim_point", fake_nominatim)
    feature = {
        "properties": {
            "name": "Pardubice",
            "obec_id": "555134",
            "okres": "Pardubice",
            "kraj": "Pardubický kraj",
        }
    }
    locality = locality_from_feature(feature, fallback_key="pardubice", level="obec")
    assert locality is not None
    assert locality.obec_id == "555134"
    assert locality.lat == pytest.approx(50.0385812)
    assert locality.geo_source == "nominatim"


def test_plan_adds_filtered_search_and_links_for_place() -> None:
    """Resolved places raise the search limit and request locality links."""
    calls = plan_calls(
        "Pardubice",
        {"search_domain_knowledge", "list_links", "ingest_text"},
        place=_pardubice(),
    )
    assert [call.name for call in calls] == [
        "search_domain_knowledge",
        "search_domain_knowledge",
        "list_links",
    ]
    assert calls[0].arguments["limit"] == 12
    assert calls[1].arguments["obec_id"] == "555134"
    assert calls[2].arguments["obec_id"] == "555134"


class _Gateway:
    """Returns one hit with an image and no coordinates."""

    def __init__(self) -> None:
        """Track tool calls for assertions."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def inspect(self) -> Inspection:
        return Inspection(
            status="connected",
            detail="",
            tools=(
                ToolInfo("search_domain_knowledge", "Search"),
                ToolInfo("list_links", "Links"),
            ),
            server_url="https://rag.example/mcp/",
            token_configured=True,
        )

    async def call_tool(self, name: str, arguments: dict[str, object]) -> ToolOutcome:
        self.calls.append((name, arguments))
        if name == "list_links":
            return ToolOutcome(
                name=name,
                arguments=arguments,
                text='{"links":[],"returned":0,"has_more":false}',
            )
        return ToolOutcome(
            name="search_domain_knowledge",
            arguments=arguments,
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
    """The chat response includes the stored point for a Pardubice question."""
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
    gateway = _Gateway()
    client = TestClient(
        create_app(
            settings,
            mcp=gateway,
            forced_user="reader@example.com",
            locality_lookup=_lookup,
        )
    )
    response = client.post("/api/chat", json={"message": "Pardubice"})
    assert response.status_code == 200
    body = response.json()
    place = body["places"][0]
    assert place["lat"] == pytest.approx(50.0385812)
    assert place["lon"] == pytest.approx(15.7791356)
    assert body["focus"]["obec_id"] == "555134"
    assert gateway.calls[0][1]["limit"] == 12
    assert gateway.calls[1][1]["obec_id"] == "555134"
    assert "secret-token" not in response.text
