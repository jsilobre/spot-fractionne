"""File storage of pipeline tables and GeoJSON export for the web.

* ``strokes``  -> GeoParquet (Lambert-93), ``parts`` / ``events`` as JSON text;
* ``profiles`` -> Parquet, one ``list<float64>`` of raw elevations per stroke;
* ``segments`` -> GeoParquet (Lambert-93) and GeoJSON (WGS84, public fields);
* ``loops``    -> GeoParquet (Lambert-93).

Schemas are documented in docs/data-model.md.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np

from flat_segments.detect import Segment, SegmentKind
from flat_segments.geometry import FloatArray, Projector, make_projector
from flat_segments.loops import Loop
from flat_segments.network import EventKind, RoadClass, Stroke, StrokeEvent, StrokePart
from flat_segments.params import WEB_CRS, WORK_CRS

SCHEMA_VERSION: Final = "0.1"

OSM_ATTRIBUTION: Final = "© les contributeurs d'OpenStreetMap (ODbL)"
#: Attribution line of each elevation source (docs/data-sources.md).
SOURCE_ATTRIBUTION: Final = {
    "lidar_hd": "IGN – MNT LiDAR HD (Licence Ouverte 2.0)",  # noqa: RUF001 (French typography)
    "rge_alti_1m": "IGN – RGE ALTI® (Licence Ouverte 2.0)",  # noqa: RUF001
    "rge_alti_wms": "IGN – RGE ALTI® (Licence Ouverte 2.0)",  # noqa: RUF001
}


def attribution_for(segments: Sequence[Segment]) -> list[str]:
    """Attribution lines for segments: OSM, then each elevation source used."""
    lines = [OSM_ATTRIBUTION]
    for source in sorted({s.elevation_source for s in segments}):
        line = SOURCE_ATTRIBUTION.get(source)
        if line is not None and line not in lines:
            lines.append(line)
    return lines


#: Public segment fields, in export order (docs/data-model.md).
PUBLIC_FIELDS: Final = (
    "id",
    "kind",
    "length_m",
    "elev_start_m",
    "elev_end_m",
    "elev_gain_m",
    "elev_loss_m",
    "grade_mean_pct",
    "grade_max_pct",
    "sinuosity",
    "n_crossings",
    "n_junctions",
    "surface",
    "lit",
    "name",
    "highways",
    "osm_way_ids",
    "on_structure",
    "quality_flags",
    "fits_targets_m",
    "score",
    "elevation_source",
)
INTERNAL_FIELDS: Final = ("stroke_id", "stroke_start_m", "stroke_end_m")

#: Decimal places of numeric fields in the GeoJSON export.
ROUNDING: Final[Mapping[str, int]] = {
    "length_m": 1,
    "elev_start_m": 1,
    "elev_end_m": 1,
    "elev_gain_m": 1,
    "elev_loss_m": 1,
    "grade_mean_pct": 2,
    "grade_max_pct": 2,
    "sinuosity": 2,
    "score": 1,
}
COORD_DECIMALS: Final = 6

_TUPLE_FIELDS: Final = ("highways", "osm_way_ids", "quality_flags", "fits_targets_m")


# --- GeoJSON ----------------------------------------------------------------


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def segment_properties(segment: Segment) -> dict[str, Any]:
    """Public properties of a segment, rounded for the web."""
    props: dict[str, Any] = {}
    for name in PUBLIC_FIELDS:
        value = getattr(segment, name)
        if name in ROUNDING and isinstance(value, float) and math.isfinite(value):
            value = round(value, ROUNDING[name]) + 0.0  # + 0.0 turns -0.0 into 0.0
        props[name] = _json_value(value.value if name == "kind" else value)
    return props


def segments_to_geojson(
    segments: Sequence[Segment],
    to_wgs84: Projector | None = None,
    *,
    sample: bool = False,
    generated_at: datetime | None = None,
    attribution: Sequence[str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a GeoJSON FeatureCollection (RFC 7946) with a ``metadata`` member.

    Args:
        segments: Segments in Lambert-93.
        to_wgs84: Projection to WGS84; defaults to Lambert-93 -> WGS84.
        sample: Marks the data as fictitious (the web page shows a banner).
        generated_at: Export timestamp (defaults to now, UTC).
        attribution: Attribution lines shown on the map; by default OSM and
            the elevation sources of the segments (:func:`attribution_for`).
        params: Detection parameters (nested mapping) recorded in the metadata.
    """
    to_wgs84 = to_wgs84 or make_projector(WORK_CRS, WEB_CRS)
    generated_at = generated_at or datetime.now(UTC)
    features = []
    for segment in segments:
        lonlat = np.round(to_wgs84(segment.coords), COORD_DECIMALS)
        features.append(
            {
                "type": "Feature",
                "id": segment.id,
                "geometry": {"type": "LineString", "coordinates": lonlat.tolist()},
                "properties": segment_properties(segment),
            }
        )
    return {
        "type": "FeatureCollection",
        "metadata": {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sample": sample,
            "attribution": list(
                attribution if attribution is not None else attribution_for(segments)
            ),
            **({"params": dict(params)} if params is not None else {}),
        },
        "features": features,
    }


def write_geojson(collection: Mapping[str, Any], path: Path) -> None:
    """Write a GeoJSON document (compact, UTF-8)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(collection, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


# --- GeoParquet: segments ---------------------------------------------------


def write_segments(segments: Sequence[Segment], path: Path) -> None:
    """Write segments to GeoParquet (Lambert-93), internal fields included."""
    import geopandas as gpd
    from shapely import LineString

    rows = []
    for segment in segments:
        row = {name: getattr(segment, name) for name in (*PUBLIC_FIELDS, *INTERNAL_FIELDS)}
        row["kind"] = segment.kind.value
        for name in _TUPLE_FIELDS:
            row[name] = list(row[name])
        rows.append(row)
    geometry = [LineString(s.coords) for s in segments]
    frame = gpd.GeoDataFrame(
        rows, columns=[*PUBLIC_FIELDS, *INTERNAL_FIELDS], geometry=geometry, crs=WORK_CRS
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)


def read_segments(path: Path) -> list[Segment]:
    """Read segments written by :func:`write_segments`."""
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    segments = []
    for record, geom in zip(
        frame.drop(columns="geometry").to_dict("records"), frame.geometry, strict=True
    ):
        values = {k: _from_parquet(v) for k, v in record.items()}
        for name in _TUPLE_FIELDS:
            values[name] = tuple(values[name])
        values["kind"] = SegmentKind(values["kind"])
        segments.append(Segment(coords=np.asarray(geom.coords, dtype=np.float64), **values))
    return segments


def _from_parquet(value: Any) -> Any:
    """Convert numpy scalars / arrays read back from Parquet to Python values."""
    if isinstance(value, np.ndarray):
        return [_from_parquet(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


# --- GeoParquet: loops -----------------------------------------------------

#: Loop fields, in storage order (docs/data-model.md, table ``loops``).
LOOP_FIELDS: Final = (
    "id",
    "loop_type",
    "length_m",
    "lap_m",
    "name",
    "surface",
    "lit",
    "access",
    "opening_hours",
    "indoor",
    "osm_id",
)


def write_loops(loops: Sequence[Loop], path: Path) -> None:
    """Write loops to GeoParquet (Lambert-93), as closed LineStrings."""
    import geopandas as gpd
    from shapely import LineString

    rows = [{name: getattr(loop, name) for name in LOOP_FIELDS} for loop in loops]
    frame = gpd.GeoDataFrame(
        rows,
        columns=list(LOOP_FIELDS),
        geometry=[LineString(x.coords) for x in loops],
        crs=WORK_CRS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)


def read_loops(path: Path) -> list[Loop]:
    """Read loops written by :func:`write_loops`."""
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    return [
        Loop(
            coords=np.asarray(geom.coords, dtype=np.float64),
            **{k: _from_parquet(v) for k, v in record.items()},
        )
        for record, geom in zip(
            frame.drop(columns="geometry").to_dict("records"), frame.geometry, strict=True
        )
    ]


# --- GeoParquet: strokes ----------------------------------------------------


def write_strokes(strokes: Sequence[Stroke], path: Path) -> None:
    """Write strokes to GeoParquet (Lambert-93), parts and events as JSON."""
    import geopandas as gpd
    from shapely import LineString

    frame = gpd.GeoDataFrame(
        {
            "stroke_id": [s.id for s in strokes],
            "length_m": [s.length_m for s in strokes],
            "is_ring": [s.is_ring for s in strokes],
            "parts": [json.dumps([dataclasses.asdict(p) for p in s.parts]) for s in strokes],
            "events": [json.dumps([dataclasses.asdict(e) for e in s.events]) for s in strokes],
        },
        geometry=[LineString(s.coords) for s in strokes],
        crs=WORK_CRS,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path)


def read_strokes(path: Path) -> list[Stroke]:
    """Read strokes written by :func:`write_strokes`."""
    import geopandas as gpd

    frame = gpd.read_parquet(path)
    strokes = []
    for stroke_id, is_ring, parts, events, geom in zip(
        frame["stroke_id"],
        frame["is_ring"],
        frame["parts"],
        frame["events"],
        frame.geometry,
        strict=True,
    ):
        strokes.append(
            Stroke(
                id=str(stroke_id),
                coords=np.asarray(geom.coords, dtype=np.float64),
                parts=tuple(
                    StrokePart(**{**p, "road_class": RoadClass(p["road_class"])})
                    for p in json.loads(parts)
                ),
                events=tuple(
                    StrokeEvent(e["offset_m"], EventKind(e["kind"]), e["node_id"])
                    for e in json.loads(events)
                ),
                is_ring=bool(is_ring),
            )
        )
    return strokes


# --- Parquet: profiles ------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class ProfileTable:
    """Raw elevation samples of every stroke (table ``profiles``).

    ``elevation_source`` is the source of most strokes; ``source_by_stroke``
    names the others (strokes sampled on the fallback DEM).
    """

    z_raw: dict[str, FloatArray]
    step_m: float
    elevation_source: str
    source_by_stroke: dict[str, str] = field(default_factory=dict)

    def source_of(self, stroke_id: str) -> str:
        """Elevation source of a stroke."""
        return self.source_by_stroke.get(stroke_id, self.elevation_source)


def write_profiles(table: ProfileTable, path: Path) -> None:
    """Write raw profiles to Parquet."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    ids = list(table.z_raw)
    arrow = pa.table(
        {
            "stroke_id": pa.array(ids, pa.string()),
            "step_m": pa.array([table.step_m] * len(ids), pa.float64()),
            "z_raw": pa.array([table.z_raw[i].tolist() for i in ids], pa.list_(pa.float64())),
            "elevation_source": pa.array([table.source_of(i) for i in ids], pa.string()),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(arrow, path)


def read_profiles(path: Path) -> ProfileTable:
    """Read raw profiles written by :func:`write_profiles`.

    Raises:
        ValueError: If the file mixes several steps.
    """
    import pyarrow.parquet as pq

    columns = pq.read_table(path).to_pydict()
    steps = set(columns["step_m"])
    if len(steps) > 1:
        raise ValueError(f"{path}: mixed steps {steps}")
    z_raw = {
        stroke_id: np.asarray(values, dtype=np.float64)
        for stroke_id, values in zip(columns["stroke_id"], columns["z_raw"], strict=True)
    }
    sources = Counter(columns["elevation_source"])
    main = str(sources.most_common(1)[0][0]) if sources else "unknown"
    return ProfileTable(
        z_raw=z_raw,
        step_m=float(steps.pop()) if steps else 0.0,
        elevation_source=main,
        source_by_stroke={
            stroke_id: str(source)
            for stroke_id, source in zip(
                columns["stroke_id"], columns["elevation_source"], strict=True
            )
            if source != main
        },
    )
