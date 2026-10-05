from pathlib import Path

import numpy as np
import pytest
from pyproj import Transformer
from typer.testing import CliRunner

from flat_segments import cli, loops
from flat_segments.export import read_loops, write_loops
from flat_segments.geometry import FloatArray, polyline_length
from flat_segments.loops import SportArea, build_tracks
from flat_segments.osm import iter_sport_areas
from flat_segments.params import WEB_CRS, WORK_CRS
from flat_segments.pipeline import read_loops_from_osm

STRAIGHT_M = 84.39  # straights of a standard 400 m track (lane 1)


def stadium(
    center: tuple[float, float] = (575_000.0, 6_275_000.0),
    radius: float = 36.80,
    start_deg: float = 0.0,
    clockwise: bool = False,
) -> FloatArray:
    """Closed outline of a running track: two straights and two half circles."""
    cx, cy = center
    half = STRAIGHT_M / 2
    points = []
    for x0, first in ((half, -90), (-half, 90)):  # east bend, then west bend
        angles = np.radians(np.linspace(first, first + 180, 31))
        points += [(cx + x0 + radius * np.cos(a), cy + radius * np.sin(a)) for a in angles]
    ring = np.array(points)
    shift = int(len(ring) * start_deg / 360)
    ring = np.roll(ring, -shift, axis=0)
    if clockwise:
        ring = ring[::-1]
    return np.vstack([ring, ring[:1]])


def square(center: tuple[float, float], side: float) -> FloatArray:
    """Closed square ring."""
    cx, cy = center
    h = side / 2
    corners = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
    return np.array([*corners, corners[0]])


def area(osm_id: str, ring: FloatArray, **tags: str) -> SportArea:
    return SportArea(osm_id, ring, tags)


ATHLETICS = {"leisure": "track", "sport": "athletics"}


def test_stadium_helper_is_a_400_m_lap() -> None:
    assert polyline_length(stadium()) == pytest.approx(400, abs=1)


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        (ATHLETICS, True),
        ({"leisure": "track", "sport": "running;soccer"}, True),
        ({"leisure": "track", "surface": "tartan"}, True),
        ({"leisure": "track"}, False),
        ({"leisure": "track", "sport": "horse_racing"}, False),
        ({"leisure": "track", "sport": "cycling", "surface": "tartan"}, False),
        ({"leisure": "pitch", "sport": "athletics"}, False),
    ],
)
def test_is_running_track(tags: dict[str, str], expected: bool) -> None:
    assert loops.is_running_track(tags) is expected


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"access": "yes"}, "public"),
        ({"access": "private"}, "restricted"),
        ({"access": "private", "foot": "permissive"}, "public"),
        ({"access": "customers"}, "restricted"),
        ({"amenity": "school"}, "restricted"),
        ({"amenity": "school", "access": "yes"}, "public"),
        ({"amenity": "university"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_access_category(tags: dict[str, str], expected: str) -> None:
    assert loops.access_category(tags) == expected


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (398.0, 400.0),
        (460.0, 400.0),  # outer edge of an 8-lane track
        (205.0, 200.0),
        (335.0, 1000 / 3),
        (600.0, None),
        (1200.0, None),
    ],
)
def test_lap_length(length: float, expected: float | None) -> None:
    assert loops.lap_length(length) == (pytest.approx(expected) if expected else None)


def test_loop_id_ignores_where_the_ring_starts_and_its_direction() -> None:
    reference = loops.loop_id(stadium())
    assert reference.startswith("loop-")
    assert loops.loop_id(stadium(start_deg=120)) == reference
    assert loops.loop_id(stadium(clockwise=True)) == reference
    assert loops.loop_id(stadium(center=(575_100.0, 6_275_000.0))) != reference


def test_ring_helpers() -> None:
    ring = square((10.0, 20.0), 4.0)
    assert loops.ring_area(ring) == pytest.approx(16)
    assert loops.ring_centroid(ring) == pytest.approx([10, 20])
    assert loops.point_in_ring(np.array([10.0, 20.0]), ring)
    assert not loops.point_in_ring(np.array([13.0, 20.0]), ring)


def test_a_track_inherits_the_access_and_name_of_its_facility() -> None:
    center = (575_000.0, 6_275_000.0)
    [track] = build_tracks(
        [
            area("way/1", stadium(center), **ATHLETICS, surface="tartan"),
            area("relation/2", square(center, 300), leisure="stadium", name="Stade", access="no"),
            area("way/3", square(center, 2000), leisure="sports_centre", access="yes"),
        ]
    )
    assert track.kind == "loop"
    assert track.loop_type == "track"
    assert track.lap_m == 400
    assert track.length_m == pytest.approx(400, abs=1)
    assert (track.name, track.access, track.surface, track.indoor) == (
        "Stade",
        "restricted",  # the smallest facility around it
        "paved",
        False,
    )
    assert track.osm_id == "way/1"


def test_track_tags_win_over_the_facility() -> None:
    center = (575_000.0, 6_275_000.0)
    [track] = build_tracks(
        [
            area("way/1", stadium(center), **ATHLETICS, name="Piste", access="yes"),
            area("way/2", square(center, 300), amenity="school", name="Collège"),
        ]
    )
    assert (track.name, track.access) == ("Piste", "public")


def test_school_tracks_are_restricted_and_hall_tracks_indoor() -> None:
    school, hall = (575_000.0, 6_275_000.0), (576_000.0, 6_275_000.0)
    by_id = {
        t.osm_id: t
        for t in build_tracks(
            [
                area("way/1", stadium(school), **ATHLETICS),
                area("way/2", square(school, 300), amenity="school"),
                area("way/3", stadium(hall, radius=13.0), **ATHLETICS),
                area("way/4", square(hall, 300), leisure="sports_hall"),
            ]
        )
    }
    assert by_id["way/1"].access == "restricted"
    assert not by_id["way/1"].indoor
    assert by_id["way/3"].indoor
    assert by_id["way/3"].lap_m == 250
    assert by_id["way/3"].access == "unknown"


def test_a_track_mapped_twice_gives_one_loop() -> None:
    center = (575_000.0, 6_275_000.0)
    tracks = build_tracks(
        [
            area("way/1", stadium(center, radius=46.0), **ATHLETICS),  # outer edge, ~458 m
            area("way/2", stadium(center), **ATHLETICS),  # running line, 400 m
            area("way/3", stadium((575_500.0, 6_275_000.0)), **ATHLETICS),
        ]
    )
    assert [t.osm_id for t in sorted(tracks, key=lambda t: t.osm_id)] == ["way/2", "way/3"]


def test_the_running_line_of_a_ring_shaped_area_is_its_inner_edge() -> None:
    center = (575_000.0, 6_275_000.0)
    outline, inner = stadium(center, radius=46.0), stadium(center)
    [track] = build_tracks([SportArea("relation/1", outline, ATHLETICS, inner)])
    np.testing.assert_array_equal(track.coords, inner)
    assert track.lap_m == 400
    assert track.id == loops.loop_id(inner)


def test_thin_areas_without_inner_ring_are_sprint_straights() -> None:
    strip = np.array([(0.0, 0.0), (120.0, 0.0), (120.0, 8.0), (0.0, 8.0), (0.0, 0.0)])
    assert loops.compactness(strip) < loops.MIN_COMPACTNESS < loops.compactness(stadium())
    assert not build_tracks([area("way/1", strip, **ATHLETICS)])  # 256 m, like a 250 m lap


def test_other_areas_and_odd_lengths_are_ignored() -> None:
    assert not build_tracks(
        [
            area("way/1", stadium(), leisure="track", sport="horse_racing"),
            area("way/2", square((0.0, 0.0), 10), **ATHLETICS),  # 40 m
            area("way/3", square((0.0, 0.0), 300), leisure="stadium"),
        ]
    )
    [trail] = build_tracks(
        [area("way/4", square((0.0, 0.0), 300), leisure="track", sport="running")]
    )
    assert trail.lap_m is None
    assert trail.length_m == pytest.approx(1200)


def test_loops_round_trip(tmp_path: Path) -> None:
    center = (575_000.0, 6_275_000.0)
    written = build_tracks(
        [
            area("way/1", stadium(center), **ATHLETICS, opening_hours="Mo-Fr 08:00-20:00"),
            area("way/2", square((0.0, 0.0), 300), leisure="track", sport="running", lit="yes"),
        ]
    )
    path = tmp_path / "loops.parquet"
    write_loops(written, path)
    read = read_loops(path)
    for a, b in zip(written, read, strict=True):
        assert {f: getattr(a, f) for f in loops.Loop.__slots__ if f != "coords"} == {
            f: getattr(b, f) for f in loops.Loop.__slots__ if f != "coords"
        }
        np.testing.assert_allclose(a.coords, b.coords)
    write_loops([], tmp_path / "empty.parquet")
    assert read_loops(tmp_path / "empty.parquet") == []


# --- reading OSM ------------------------------------------------------------

TO_WGS84 = Transformer.from_crs(WORK_CRS, WEB_CRS, always_xy=True)


def osm_xml() -> str:
    """A track (closed way) in a stadium (multipolygon), a ring-shaped track
    (multipolygon with an inner ring) far away, and a cycleway."""
    nodes, ways = [], []
    refs: dict[str, list[int]] = {}
    shapes = {
        "track": stadium()[:-1],
        "stadium": square((575_000.0, 6_275_000.0), 300)[:-1],
        "far": stadium((600_000.0, 6_300_000.0), radius=46.0)[:-1],
        "far_inner": stadium((600_000.0, 6_300_000.0))[:-1],
    }
    node_id = 1
    for name, ring in shapes.items():
        refs[name] = []
        for x, y in ring:
            lon, lat = TO_WGS84.transform(x, y)
            nodes.append(f'<node id="{node_id}" lat="{lat:.7f}" lon="{lon:.7f}" version="1"/>')
            refs[name].append(node_id)
            node_id += 1

    def nds(name: str) -> str:
        ids = [*refs[name], refs[name][0]]
        return "".join(f'<nd ref="{i}"/>' for i in ids)

    ways.append(
        f'<way id="10" version="1">{nds("track")}<tag k="leisure" v="track"/>'
        '<tag k="sport" v="athletics"/><tag k="surface" v="tartan"/></way>'
    )
    ways.append(f'<way id="11" version="1">{nds("stadium")}</way>')
    ways.append(f'<way id="12" version="1">{nds("far")}</way>')
    ways.insert(0, f'<way id="9" version="1">{nds("far_inner")}</way>')  # ids in order
    ways.append(
        '<way id="13" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="footway"/></way>'
    )
    relation = (
        '<relation id="20" version="1"><member type="way" ref="11" role="outer"/>'
        '<tag k="type" v="multipolygon"/><tag k="leisure" v="stadium"/>'
        '<tag k="name" v="Stade municipal"/><tag k="access" v="yes"/></relation>'
        '<relation id="21" version="1"><member type="way" ref="12" role="outer"/>'
        '<member type="way" ref="9" role="inner"/><tag k="type" v="multipolygon"/>'
        '<tag k="leisure" v="track"/><tag k="sport" v="athletics"/></relation>'
    )
    return (
        f'<?xml version="1.0"?><osm version="0.6">{"".join(nodes)}{"".join(ways)}{relation}</osm>'
    )


def bbox_around(x: float, y: float, margin: float = 500.0) -> tuple[float, float, float, float]:
    min_lon, min_lat = TO_WGS84.transform(x - margin, y - margin)
    max_lon, max_lat = TO_WGS84.transform(x + margin, y + margin)
    return min_lon, min_lat, max_lon, max_lat


def test_iter_sport_areas_reads_closed_ways_and_multipolygons(tmp_path: Path) -> None:
    path = tmp_path / "area.osm"
    path.write_text(osm_xml())
    found = {a.osm_id: a for a in iter_sport_areas(path)}
    assert set(found) == {"way/10", "relation/20", "relation/21"}
    assert found["relation/20"].tags == {
        "leisure": "stadium",
        "name": "Stade municipal",
        "access": "yes",
    }
    [ring] = found["way/10"].rings
    assert ring.shape[1] == 2
    assert (ring[0] == ring[-1]).all()
    assert found["way/10"].holes == [None]
    [hole] = found["relation/21"].holes
    assert hole is not None
    assert len(hole) == len(stadium())  # the inner ring, not the outer one
    near = {a.osm_id for a in iter_sport_areas(path, bbox_around(575_000.0, 6_275_000.0))}
    assert near == {"way/10", "relation/20"}


def test_loops_command(tmp_path: Path) -> None:
    path = tmp_path / "area.osm"
    path.write_text(osm_xml())
    out = tmp_path / "loops.parquet"
    bbox = ",".join(str(v) for v in bbox_around(575_000.0, 6_275_000.0))
    result = CliRunner().invoke(
        cli.app, ["loops", "--pbf", str(path), "--bbox", bbox, "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert "2 sports areas -> 1 tracks" in result.output
    [track] = read_loops(out)
    assert (track.name, track.access, track.lap_m) == ("Stade municipal", "public", 400)


def test_a_ring_shaped_track_runs_along_its_inner_edge(tmp_path: Path) -> None:
    path = tmp_path / "area.osm"
    path.write_text(osm_xml())
    _, [track] = read_loops_from_osm(path, bbox_around(600_000.0, 6_300_000.0))
    assert track.osm_id == "relation/21"
    assert track.length_m == pytest.approx(400, abs=1)  # the outline is about 458 m
    assert track.lap_m == 400
