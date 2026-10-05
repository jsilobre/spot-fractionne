"""French départements: outlines and per-département processing (phase 2.1).

Outlines come from IGN Admin Express (COG CARTO edition, Licence Ouverte 2.0),
served by the Géoplateforme WFS. A département is processed on its outline
grown by a margin, so that ways crossing the border are not cut; a segment
then belongs to the département that contains its midpoint
(docs/phase-2/2.1-departement.md).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from urllib.parse import urlencode

import numpy as np
import shapely
from shapely.geometry import LineString, shape
from shapely.geometry.base import BaseGeometry

from flat_segments.download import DownloadError, Opener, fetch_bytes, urlopen
from flat_segments.geometry import FloatArray, make_projector
from flat_segments.params import WEB_CRS, WORK_CRS

DEPARTMENTS_WFS_URL: Final = "https://data.geopf.fr/wfs/ows"
DEPARTMENTS_LAYER: Final = "ADMINEXPRESS-COG-CARTO.LATEST:departement"
#: Margin around a département outline, in metres (docs/phase-2/2.1-departement.md).
BORDER_MARGIN_M: Final = 2000.0


@dataclass(frozen=True)
class Department:
    """A département outline in WGS84 (lon, lat)."""

    code: str
    name: str
    outline: BaseGeometry

    def outline_l93(self) -> BaseGeometry:
        """Outline in Lambert-93."""
        return transform_geometry(self.outline, WEB_CRS, WORK_CRS)

    def work_area_l93(self, margin_m: float = BORDER_MARGIN_M) -> BaseGeometry:
        """Outline grown by ``margin_m``, in Lambert-93."""
        return self.outline_l93().buffer(margin_m)


def transform_geometry(geometry: BaseGeometry, src: str, dst: str) -> BaseGeometry:
    """Reproject a shapely geometry."""
    project = make_projector(src, dst)
    return shapely.transform(geometry, lambda xy: project(np.asarray(xy, dtype=np.float64)))


def departments_url(base_url: str = DEPARTMENTS_WFS_URL, layer: str = DEPARTMENTS_LAYER) -> str:
    """WFS 2.0 GetFeature URL returning every département as GeoJSON."""
    query = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": layer,
        "OUTPUTFORMAT": "application/json",
        "PROPERTYNAME": "code_insee,nom_officiel,geometrie",
    }
    return f"{base_url}?{urlencode(query)}"


def _check_collection(collection: Any) -> None:
    """Validate the WFS answer: features with a code, a name and a WGS84 outline.

    Raises:
        DownloadError: If the answer is not the expected GeoJSON.
    """
    features = collection.get("features") if isinstance(collection, dict) else None
    if not features:
        raise DownloadError("no département in the WFS answer")
    for feature in features:
        props = feature.get("properties") or {}
        if not props.get("code_insee") or feature.get("geometry") is None:
            raise DownloadError(f"incomplete département feature: {props}")
        min_x, min_y, max_x, max_y = shape(feature["geometry"]).bounds
        if not (-180 <= min_x <= max_x <= 180 and -90 <= min_y <= max_y <= 90):
            raise DownloadError(f"département {props['code_insee']} is not in lon/lat")


def download_departments(dest: Path, opener: Opener = urlopen) -> Path:
    """Download all département outlines to a GeoJSON file.

    Raises:
        DownloadError: If the service fails or answers something unexpected.
    """
    data = fetch_bytes(departments_url(), opener)
    try:
        collection = json.loads(data)
    except json.JSONDecodeError as error:
        raise DownloadError(f"WFS answer is not JSON: {data[:200]!r}") from error
    _check_collection(collection)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(collection, separators=(",", ":")), encoding="utf-8")
    return dest


def load_department(path: Path, code: str) -> Department:
    """Read one département from a file written by :func:`download_departments`.

    Raises:
        KeyError: If the code is unknown.
    """
    collection = json.loads(path.read_text(encoding="utf-8"))
    for feature in collection["features"]:
        props = feature["properties"]
        if props["code_insee"] == code:
            return Department(code, props.get("nom_officiel", code), shape(feature["geometry"]))
    raise KeyError(f"unknown département {code!r} in {path}")


def department_codes(path: Path, *, overseas: bool = False) -> list[str]:
    """INSEE codes of a file written by :func:`download_departments`, sorted.

    Overseas départements (codes 971 to 976) are left out unless asked for.
    """
    collection = json.loads(path.read_text(encoding="utf-8"))
    codes = sorted(f["properties"]["code_insee"] for f in collection["features"])
    return [c for c in codes if overseas or not c.startswith("97")]


def load_departments(path: Path, codes: Sequence[str]) -> list[Department]:
    """Read several départements, in the order of ``codes``.

    Raises:
        KeyError: If a code is unknown.
    """
    collection = json.loads(path.read_text(encoding="utf-8"))
    by_code = {f["properties"]["code_insee"]: f for f in collection["features"]}
    departments = []
    for code in codes:
        if code not in by_code:
            raise KeyError(f"unknown département {code!r} in {path}")
        feature = by_code[code]
        name = feature["properties"].get("nom_officiel", code)
        departments.append(Department(code, name, shape(feature["geometry"])))
    return departments


class HasCoords(Protocol):
    """A line in Lambert-93: a segment or a loop."""

    @property
    def coords(self) -> FloatArray:
        """``(N, 2)`` vertices."""
        ...


def owned_segments[T: HasCoords](segments: Sequence[T], outline_l93: BaseGeometry) -> list[T]:
    """Segments (or loops) whose midpoint lies in the outline (Lambert-93).

    Each segment belongs to exactly one département, even when it crosses the
    border: it is kept whole by the département that contains its midpoint.
    """
    if not segments:
        return []
    midpoints = np.array(
        [LineString(s.coords).interpolate(0.5, normalized=True).coords[0] for s in segments]
    )
    inside = shapely.contains_xy(outline_l93, midpoints[:, 0], midpoints[:, 1])
    return [s for s, keep in zip(segments, inside, strict=True) if keep]
