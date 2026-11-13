"""Turn MCP search hits into cards with text, an image, and optional GPS."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from mcp_chat.image_proxy import image_id_from_rag_url
from mcp_chat.mcp_client import ToolOutcome
from mcp_chat.present import public_url, readable_text, readable_title

_LAT_LON = re.compile(
    r"lat=([+-]?\d+(?:\.\d+)?)\s+lon=([+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_LABELED_PAIR = re.compile(
    r"(?i)(?:souřadnice|souradnice)\s*:\s*"
    r"([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)"
)
_MAX_PLACES = 12


@dataclass(frozen=True)
class Place:
    """One fact the page can show beside the map."""

    id: str
    title: str
    text: str
    lat: float | None
    lon: float | None
    geo_source: str
    image_id: str
    doc_id: str
    kind: str
    url: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize the card for the chat response."""
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text[:700],
            "lat": self.lat,
            "lon": self.lon,
            "geo_source": self.geo_source,
            "image_id": self.image_id,
            "doc_id": self.doc_id,
            "kind": self.kind,
            "url": self.url,
        }


def places_from_outcomes(outcomes: list[ToolOutcome]) -> list[Place]:
    """Collect fact cards from JSON hits and from coordinate lines in text."""
    places: list[Place] = []
    seen: set[tuple[str, str, str, str]] = set()
    for outcome in outcomes:
        if outcome.is_error:
            continue
        for place in _places_in_text(outcome.text):
            key = (
                place.doc_id,
                place.image_id,
                "" if place.lat is None else f"{place.lat:.5f}",
                place.title[:80],
            )
            if key in seen:
                continue
            seen.add(key)
            places.append(place)
            if len(places) >= _MAX_PLACES:
                return _with_ids(places)
    return _with_ids(places)


def _places_in_text(text: str) -> list[Place]:
    """Parse a tool body that is either a JSON hit list or plain text."""
    hits = _parse_hits(text)
    if hits is not None:
        return [_place_from_hit(hit) for hit in hits if _has_body(hit)]
    stripped = text.strip()
    if not stripped:
        return []
    lat, lon, source = _coords_from_text(stripped)
    return [
        Place(
            id="",
            title=_title_from_text(stripped),
            text=readable_text(stripped)[:700],
            lat=lat,
            lon=lon,
            geo_source=source,
            image_id=_first_image_id(stripped),
            doc_id="",
            kind="passage",
            url="",
        )
    ]


def _place_from_hit(hit: dict[str, Any]) -> Place:
    """Read one JSON search hit, preferring structured lat/lon."""
    content = str(hit.get("content") or hit.get("text") or "").strip()
    lat, lon, source = _coords_from_hit(hit)
    if not _valid(lat, lon):
        lat, lon, source = _coords_from_text(content)
    image_id, url = _browser_image_fields(hit)
    kind = str(hit.get("source_type") or hit.get("kind") or "hit")
    return Place(
        id="",
        title=readable_title(hit),
        text=readable_text(content)[:700],
        lat=lat,
        lon=lon,
        geo_source=source if lat is not None else "",
        image_id=image_id,
        doc_id=str(hit.get("doc_id") or ""),
        kind=kind,
        url=url,
    )


def _browser_image_fields(hit: dict[str, Any]) -> tuple[str, str]:
    """Return a proxyable image id and a non-auth browser URL."""
    image_id = str(hit.get("page_image_id") or hit.get("image_id") or "").strip()
    raw_url = str(hit.get("url") or hit.get("source_url") or "").strip()
    from_url = image_id_from_rag_url(raw_url) or image_id_from_rag_url(image_id)
    if from_url and (
        not image_id
        or image_id.startswith("http://")
        or image_id.startswith("https://")
        or image_id.startswith("/images/")
    ):
        image_id = from_url
    if image_id.startswith("http://") or image_id.startswith("https://"):
        image_id = image_id_from_rag_url(image_id) or ""
    if image_id.startswith("/images/"):
        image_id = image_id.rstrip("/").rsplit("/", 1)[-1]
    # Auth-gated RAG image URLs are never "Open source" hrefs.
    return image_id, public_url(raw_url)


def _coords_from_hit(
    hit: dict[str, Any],
) -> tuple[float | None, float | None, str]:
    """Read top-level coordinates, then refined, original, or a GeoJSON point."""
    lat = _as_float(hit.get("lat"))
    if lat is None:
        lat = _as_float(hit.get("latitude"))
    lon = _as_float(hit.get("lon"))
    if lon is None:
        lon = _as_float(hit.get("longitude"))
    source = str(hit.get("geo_source") or "")
    if _valid(lat, lon):
        return lat, lon, source or "original"
    for key, label in (("geo_refined", "refined"), ("geo_original", "original")):
        nested = hit.get(key)
        if not isinstance(nested, dict):
            continue
        nested_lat = _as_float(nested.get("lat"))
        nested_lon = _as_float(nested.get("lon"))
        if _valid(nested_lat, nested_lon):
            return nested_lat, nested_lon, label
    geom = hit.get("geom")
    if isinstance(geom, dict) and str(geom.get("type") or "") == "Point":
        coords = geom.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            geom_lon = _as_float(coords[0])
            geom_lat = _as_float(coords[1])
            if _valid(geom_lat, geom_lon):
                return geom_lat, geom_lon, "geom"
    return None, None, ""


def _coords_from_text(text: str) -> tuple[float | None, float | None, str]:
    """Find ``lat= lon=`` or a Czech coordinate label."""
    match = _LAT_LON.search(text) or _LABELED_PAIR.search(text)
    if match is None:
        return None, None, ""
    lat = _as_float(match.group(1))
    lon = _as_float(match.group(2))
    if not _valid(lat, lon):
        return None, None, ""
    source = "header" if _LAT_LON.search(text) else "text"
    return lat, lon, source


def _parse_hits(text: str) -> list[dict[str, Any]] | None:
    """Return hit objects when ``text`` is a JSON array."""
    stripped = text.strip()
    if not stripped.startswith("["):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    return [item for item in payload if isinstance(item, dict)]


def _has_body(hit: dict[str, Any]) -> bool:
    """Skip empty objects that would render as blank cards."""
    return bool(
        hit.get("content")
        or hit.get("text")
        or hit.get("title")
        or hit.get("page_image_id")
        or hit.get("lat") is not None
    )


def _as_float(value: object) -> float | None:
    """Parse a coordinate. Booleans and blanks are not numbers."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _valid(lat: float | None, lon: float | None) -> bool:
    """Return whether the pair is inside the geographic range."""
    if lat is None or lon is None:
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _title_from_text(text: str) -> str:
    """Use the first short line as a card title."""
    line = text.splitlines()[0].strip("# ").strip()
    return line[:80] or "Passage"


def _first_image_id(text: str) -> str:
    """Return the first page-image id mentioned in plain text."""
    match = re.search(r"page:[0-9a-fA-F-]{8,}:\d+", text)
    return match.group(0) if match else ""


def _with_ids(places: list[Place]) -> list[Place]:
    """Assign stable ids after deduplication."""
    numbered: list[Place] = []
    for index, place in enumerate(places, start=1):
        numbered.append(
            Place(
                id=str(index),
                title=place.title,
                text=place.text,
                lat=place.lat,
                lon=place.lon,
                geo_source=place.geo_source,
                image_id=place.image_id,
                doc_id=place.doc_id,
                kind=place.kind,
                url=place.url,
            )
        )
    return numbered
