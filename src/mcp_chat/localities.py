"""Fill missing map coordinates from stored municipality points."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_chat.map_layers import GEOSERVER_WMS
from mcp_chat.places import Place

logger = logging.getLogger("mcp_chat.localities")

_STOP = frozenset(
    {
        "about",
        "and",
        "from",
        "jak",
        "kde",
        "pro",
        "the",
        "what",
        "where",
    }
)
_NAME = re.compile(r"^[a-z0-9]+(?: [a-z0-9]+)*$")
_LAYERS = ("trails:cast_obce", "trails:obce")


@dataclass(frozen=True)
class Locality:
    """One stored municipality or municipal part."""

    name: str
    key: str
    lat: float
    lon: float


def normalize_name(value: str) -> str:
    """Fold diacritics and punctuation so ``Krouna`` matches ``krouna``."""
    folded = unicodedata.normalize("NFKD", value or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def attach_localities(
    places: list[Place],
    question: str,
    lookup: Any,
) -> list[Place]:
    """Give notes without GPS the locality named in the question.

    A note that names one of several matched places uses that place. When the
    question names exactly one stored locality, every unlocated note uses it.
    Notes that already have coordinates are left unchanged.
    """
    localities = _localities_for_question(question, lookup)
    if not localities:
        return places
    updated: list[Place] = []
    for place in places:
        if place.lat is not None and place.lon is not None:
            updated.append(place)
            continue
        match = _named_in(f"{place.title} {place.text}", localities)
        if match is None and len(localities) == 1:
            match = localities[0]
        if match is None:
            updated.append(place)
            continue
        updated.append(
            Place(
                id=place.id,
                title=place.title,
                text=place.text,
                lat=match.lat,
                lon=match.lon,
                geo_source=match.name,
                image_id=place.image_id,
                doc_id=place.doc_id,
                kind=place.kind,
                url=place.url,
            )
        )
    return updated


class GeoServerGazetteer:
    """Look up a place name in the public trails GeoServer."""

    def __init__(self, wms_url: str = GEOSERVER_WMS) -> None:
        """Remember the WFS endpoint. Lookups are cached for one process."""
        self._url = wms_url
        self._cache: dict[str, Locality | None] = {}

    def lookup(self, name: str) -> Locality | None:
        """Return the stored point for ``name``, or None when it is unknown."""
        key = normalize_name(name)
        if key in self._cache:
            return self._cache[key]
        if not _NAME.fullmatch(key):
            self._cache[key] = None
            return None
        found: Locality | None = None
        for layer in _LAYERS:
            found = self._query(layer, key)
            if found is not None:
                break
        self._cache[key] = found
        return found

    def _query(self, layer: str, key: str) -> Locality | None:
        """Read one exact ``name_norm`` feature. Failures yield no point."""
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
            "maxFeatures": "1",
            "srsName": "EPSG:4326",
            "cql_filter": f"name_norm = '{key}'",
        }
        try:
            response = httpx.get(self._url, params=params, timeout=8.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Locality lookup failed: %s", type(exc).__name__)
            return None
        features = payload.get("features") if isinstance(payload, dict) else None
        if not features:
            return None
        return locality_from_feature(features[0], fallback_key=key)


def locality_from_feature(
    feature: dict[str, Any],
    *,
    fallback_key: str,
) -> Locality | None:
    """Read a display name and a WGS-84 point from one GeoJSON feature."""
    raw_props = feature.get("properties")
    props = raw_props if isinstance(raw_props, dict) else {}
    name = str(props.get("name") or props.get("obec_name") or fallback_key).strip()
    pair = _pair_from_geometry(feature.get("geometry"))
    if pair is None:
        pair = _pair_from_bbox(feature.get("bbox"))
    if pair is None:
        return None
    lat, lon = pair
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return Locality(name=name or fallback_key, key=fallback_key, lat=lat, lon=lon)


def _localities_for_question(question: str, lookup: Any) -> list[Locality]:
    """Resolve place-like pieces of the question, longest phrases first."""
    words = [word for word in normalize_name(question).split() if word]
    if not words:
        return []
    candidates: list[str] = []
    if len(words) <= 4:
        candidates.append(" ".join(words))
    for size in (2, 1):
        for index in range(len(words) - size + 1):
            piece = " ".join(words[index : index + size])
            if size == 1 and (len(piece) < 4 or piece in _STOP):
                continue
            candidates.append(piece)
    found: list[Locality] = []
    seen: set[str] = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        if len(seen) > 6:
            break
        try:
            hit = lookup(name)
        except Exception as exc:
            logger.warning("Locality lookup failed: %s", type(exc).__name__)
            continue
        if isinstance(hit, Locality) and hit.key not in {item.key for item in found}:
            found.append(hit)
    return found


def _named_in(text: str, localities: list[Locality]) -> Locality | None:
    """Return the longest locality whose name appears as a whole word."""
    folded = normalize_name(text)
    best: Locality | None = None
    for locality in localities:
        if re.search(rf"(?<![a-z0-9]){re.escape(locality.key)}(?![a-z0-9])", folded):
            if best is None or len(locality.key) > len(best.key):
                best = locality
    return best


def _pair_from_geometry(geometry: object) -> tuple[float, float] | None:
    """Return lat, lon from a GeoJSON geometry. Coordinates are lon, lat."""
    if not isinstance(geometry, dict):
        return None
    return _first_pair(geometry.get("coordinates"))


def _pair_from_bbox(bbox: object) -> tuple[float, float] | None:
    """Return the center of a GeoJSON bbox (min lon, min lat, max lon, max lat)."""
    if not isinstance(bbox, list) or len(bbox) != 4:
        return None
    numbers = [_as_float(item) for item in bbox]
    if any(item is None for item in numbers):
        return None
    min_lon, min_lat, max_lon, max_lat = numbers
    assert min_lon is not None and min_lat is not None
    assert max_lon is not None and max_lat is not None
    return (min_lat + max_lat) / 2, (min_lon + max_lon) / 2


def _first_pair(value: object) -> tuple[float, float] | None:
    """Walk nested coordinates until the first lon/lat pair."""
    if isinstance(value, list) and len(value) >= 2 and not isinstance(value[0], list):
        lon = _as_float(value[0])
        lat = _as_float(value[1])
        if lon is None or lat is None:
            return None
        return lat, lon
    if isinstance(value, list):
        for item in value:
            found = _first_pair(item)
            if found is not None:
                return found
    return None


def _as_float(value: object) -> float | None:
    """Parse a coordinate number."""
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
