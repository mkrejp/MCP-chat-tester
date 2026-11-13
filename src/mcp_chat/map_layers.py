"""Map layers. Answer points come from RAG hits. Context comes from GeoServer."""

from __future__ import annotations

from typing import Any

GEOSERVER_WMS = "https://geoserver-production-4d0d.up.railway.app/geoserver/ows"

# Public WMS layers on the Railway GeoServer (workspace ``trails``).
# They are context. The points for one answer are not a WMS layer; the page
# draws those from ``lat`` / ``lon`` on the RAG hits.
_LAYERS: tuple[dict[str, Any], ...] = (
    {
        "id": "obce",
        "title": "Municipalities",
        "wms_name": "trails:obce",
        "description": "Czech municipality polygons.",
        "default_on": False,
    },
    {
        "id": "cast_obce",
        "title": "Municipal parts",
        "wms_name": "trails:cast_obce",
        "description": "Parts of municipalities, drawn at the parent point.",
        "default_on": False,
    },
    {
        "id": "locality_links",
        "title": "Stored localities",
        "wms_name": "trails:locality_links",
        "description": "GPS points already stored for locality mentions.",
        "default_on": False,
    },
    {
        "id": "layer_objects",
        "title": "Catalog geometries",
        "wms_name": "trails:layer_objects",
        "description": "Trail catalog geometries.",
        "default_on": False,
    },
)


def map_catalog() -> dict[str, Any]:
    """Return the WMS endpoint and the toggleable context layers.

    The browser requests tiles directly. This payload has no credentials.
    """
    return {
        "wms_url": GEOSERVER_WMS,
        "wms_version": "1.1.1",
        "base": {
            "title": "OpenStreetMap",
            "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "attribution": "© OpenStreetMap",
        },
        "answer_layer": {
            "id": "answer",
            "title": "This search",
            "description": "Numbered GPS points for the notes in the current search.",
        },
        "layers": list(_LAYERS),
    }
