"""Fill missing map coordinates from Czech place names.

GeoServer supplies ``obec_id`` / names. Coordinates come from Nominatim when
GeoServer geometries are integer-degree truncated (for example ``15, 50``).
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

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
_USER_AGENT = "mcp-chat-tester/1.0 (place resolve; contact: local-dev)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class Locality:
    """One stored municipality or municipal part."""

    name: str
    key: str
    lat: float
    lon: float
    obec_id: str = ""
    cast_obce_kod: str | None = None
    okres: str = ""
    kraj: str = ""
    level: str = "obec"
    geo_source: str = "gazetteer"

    def to_focus_dict(self) -> dict[str, Any]:
        """Serialize the map-focus place for the chat response."""
        return {
            "name": self.name,
            "name_norm": self.key,
            "obec_id": self.obec_id,
            "cast_obce_kod": self.cast_obce_kod,
            "okres": self.okres,
            "kraj": self.kraj,
            "level": self.level,
            "lat": self.lat,
            "lon": self.lon,
            "geo_source": self.geo_source,
        }


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
    Notes that already have coordinates are left unchanged unless those
    coordinates are coarse integer-degree pins.
    """
    localities = localities_for_question(question, lookup)
    if not localities:
        return [_drop_coarse_gps(place) for place in places]
    updated: list[Place] = []
    for place in places:
        cleaned = _drop_coarse_gps(place)
        if cleaned.lat is not None and cleaned.lon is not None:
            updated.append(cleaned)
            continue
        match = _named_in(f"{cleaned.title} {cleaned.text}", localities)
        if match is None and len(localities) == 1:
            match = localities[0]
        if match is None:
            updated.append(cleaned)
            continue
        updated.append(
            Place(
                id=cleaned.id,
                title=cleaned.title,
                text=cleaned.text,
                lat=match.lat,
                lon=match.lon,
                geo_source=match.geo_source or match.name,
                image_id=cleaned.image_id,
                doc_id=cleaned.doc_id,
                kind=cleaned.kind,
                url=cleaned.url,
            )
        )
    return updated


def localities_for_question(question: str, lookup: Any) -> list[Locality]:
    """Resolve place-like pieces of the question, longest phrases first."""
    return _localities_for_question(question, lookup)


def primary_locality(question: str, lookup: Any) -> Locality | None:
    """Return the single place a short question names, if unambiguous."""
    found = localities_for_question(question, lookup)
    if len(found) == 1:
        return found[0]
    return None


class GeoServerGazetteer:
    """Look up a place name in GeoServer, then geocode with Nominatim."""

    def __init__(self, wms_url: str = GEOSERVER_WMS) -> None:
        """Remember the WFS endpoint. Lookups are cached for one process."""
        self._url = wms_url
        self._cache: dict[str, Locality | None] = {}

    def lookup(self, name: str) -> Locality | None:
        """Return the stored point for ``name``, or None when it is unknown.

        Municipalities (``obce``) win over municipal parts so unique towns
        like Pardubice resolve cleanly, and Okrouhlice prefers the
        Havlíčkův Brod municipality over the Benešov part of the same name.
        """
        key = normalize_name(name)
        if key in self._cache:
            return self._cache[key]
        if not _NAME.fullmatch(key):
            self._cache[key] = None
            return None
        found = self._query("trails:obce", key, level="obec")
        if found is None:
            found = self._query("trails:cast_obce", key, level="cast_obce")
        self._cache[key] = found
        return found

    def _query(self, layer: str, key: str, *, level: str) -> Locality | None:
        """Read exact ``name_norm`` features and attach reliable GPS."""
        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
            "maxFeatures": "8",
            "srsName": "EPSG:4326",
            "CQL_FILTER": f"name_norm = '{key.replace(chr(39), chr(39) + chr(39))}'",
            "propertyName": (
                "obec_id,name,name_norm,okres,kraj"
                if level == "obec"
                else "cast_obce_kod,name,name_norm,obec_id,obec_name,okres,kraj"
            ),
        }
        try:
            response = httpx.get(self._url, params=params, timeout=8.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Locality lookup failed: %s", type(exc).__name__)
            return None
        features = payload.get("features") if isinstance(payload, dict) else None
        if not isinstance(features, list) or not features:
            return None
        if level == "obec" and len(features) != 1:
            logger.info("Ambiguous obce match for %s (%s)", key, len(features))
            return None
        chosen = features[0]
        if level == "cast_obce" and len(features) > 1:
            same_name = [
                item
                for item in features
                if normalize_name(
                    str((item.get("properties") or {}).get("obec_name") or "")
                )
                == key
            ]
            if len(same_name) == 1:
                chosen = same_name[0]
            elif len(features) != 1:
                logger.info(
                    "Ambiguous cast_obce match for %s (%s)", key, len(features)
                )
                return None
        return locality_from_feature(chosen, fallback_key=key, level=level)


def locality_from_feature(
    feature: dict[str, Any],
    *,
    fallback_key: str,
    level: str = "obec",
) -> Locality | None:
    """Build a locality from GeoServer properties plus Nominatim GPS."""
    raw_props = feature.get("properties")
    props = raw_props if isinstance(raw_props, dict) else {}
    name = str(props.get("name") or props.get("obec_name") or fallback_key).strip()
    obec_id = str(props.get("obec_id") or "").strip()
    cast_obce_kod = str(props.get("cast_obce_kod") or "").strip() or None
    okres = str(props.get("okres") or "").strip()
    kraj = str(props.get("kraj") or "").strip()
    lat, lon, source = _coords_for_place(
        feature,
        name=name or fallback_key,
        okres=okres,
        kraj=kraj,
    )
    if lat is None or lon is None:
        return None
    return Locality(
        name=name or fallback_key,
        key=fallback_key,
        lat=lat,
        lon=lon,
        obec_id=obec_id,
        cast_obce_kod=cast_obce_kod,
        okres=okres,
        kraj=kraj,
        level=level,
        geo_source=source,
    )


def _coords_for_place(
    feature: dict[str, Any],
    *,
    name: str,
    okres: str,
    kraj: str,
) -> tuple[float | None, float | None, str]:
    """Prefer Nominatim; GeoServer geometries are often degree-truncated."""
    nom = _nominatim_point(name=name, okres=okres, kraj=kraj)
    if nom is not None:
        return nom[0], nom[1], "nominatim"
    pair = _pair_from_geometry(feature.get("geometry"))
    if pair is None:
        pair = _pair_from_bbox(feature.get("bbox"))
    if pair is not None and not _looks_coarse_degree(pair[0], pair[1]):
        return pair[0], pair[1], "geoserver"
    return None, None, ""


def _nominatim_point(
    *,
    name: str,
    okres: str,
    kraj: str,
) -> tuple[float, float] | None:
    """Geocode a Czech place. Prefer okres-qualified queries."""
    queries = [
        ", ".join(part for part in (name, okres, "Czechia") if part),
        ", ".join(part for part in (name, kraj, "Czechia") if part),
        f"{name}, Czechia",
    ]
    seen: set[str] = set()
    for query in queries:
        if query in seen:
            continue
        seen.add(query)
        params = {
            "q": query,
            "format": "json",
            "limit": "1",
            "countrycodes": "cz",
        }
        url = f"{NOMINATIM_URL}?{urlencode(params)}"
        try:
            response = httpx.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "cs,en"},
                timeout=8.0,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Nominatim failed for %s: %s", query, type(exc).__name__)
            continue
        if not isinstance(payload, list) or not payload:
            continue
        item = payload[0]
        if not isinstance(item, dict):
            continue
        lat = _as_float(item.get("lat"))
        lon = _as_float(item.get("lon"))
        if lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if _looks_integer_degree(lat, lon):
            continue
        return lat, lon
    return None


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


def _drop_coarse_gps(place: Place) -> Place:
    """Drop integer-degree pins that land on the wrong Czech town."""
    if place.lat is None or place.lon is None:
        return place
    if not _looks_integer_degree(place.lat, place.lon):
        return place
    return Place(
        id=place.id,
        title=place.title,
        text=place.text,
        lat=None,
        lon=None,
        geo_source="",
        image_id=place.image_id,
        doc_id=place.doc_id,
        kind=place.kind,
        url=place.url,
    )


def _looks_integer_degree(lat: float, lon: float) -> bool:
    """Reject coarse degree-rounded pins such as GeoServer's broken 15,50."""
    return _looks_coarse_degree(lat, lon)


def _looks_coarse_degree(lat: float, lon: float) -> bool:
    """Reject pins quantized to whole or half degrees."""
    return _is_half_or_whole(lat) or _is_half_or_whole(lon)


def _is_half_or_whole(value: float) -> bool:
    """Return whether ``value`` sits on a 0.5-degree grid."""
    scaled = abs(float(value)) * 2
    return abs(scaled - round(scaled)) < 1e-9


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
