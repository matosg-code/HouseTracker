"""Electric utility territory lookup from the California Energy Commission's
"Electric Load Serving Entities" layer.

Only publicly owned utilities (MID, TID) are stored; anything outside their
polygons is PG&E territory here. Territory means the utility *may* serve the
address, not that the meter is on it: inside MID's boundary many homes are
still PG&E customers, and only the seller or a bill can settle that.

Usage:
    python -m tracker.utility --refresh   # re-download polygons from the CEC
"""
from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

AREAS_FILE = Path("data/utility_areas.geojson")
DEFAULT_UTILITY = "PG&E"
CEC_LAYER = ("https://services3.arcgis.com/bWPjFyq029ChCGur/arcgis/rest/services/"
             "ElectricLoadServingEntities_IOU_POU/FeatureServer/0/query")
MERCED_BBOX = "-121.3,36.7,-120.0,37.7"

LABELS = {"MeID": "MID", "TID": "TID"}


def _point_in_ring(x: float, y: float, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _point_in_geometry(x: float, y: float, geom: dict) -> bool:
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        if _point_in_ring(x, y, poly[0]) and not any(_point_in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


@lru_cache(maxsize=1)
def load_areas(path: str = str(AREAS_FILE)) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return json.load(f).get("features", [])


def lookup(latitude, longitude, areas: list[dict] | None = None) -> str | None:
    """Return the utility label for a point, or None if coordinates are missing."""
    try:
        y, x = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None
    areas = load_areas() if areas is None else areas
    for feature in areas:
        if _point_in_geometry(x, y, feature["geometry"]):
            acronym = feature["properties"].get("Acronym", "")
            return LABELS.get(acronym, acronym)
    return DEFAULT_UTILITY


def refresh(path: Path = AREAS_FILE) -> int:
    import requests

    resp = requests.get(CEC_LAYER, params={
        "f": "geojson", "where": "Acronym IN ('MeID','TID')", "geometry": MERCED_BBOX,
        "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "Acronym,Utility,Type,URL", "outSR": 4326, "returnGeometry": "true", "geometryPrecision": 5,
    }, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    load_areas.cache_clear()
    return len(data.get("features", []))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh:
        n = refresh()
        print(f"Saved {n} utility polygons to {AREAS_FILE}")
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
