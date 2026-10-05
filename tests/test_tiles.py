import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from flat_segments import cli, tiles
from flat_segments.detect import Segment, SegmentKind
from flat_segments.export import OSM_ATTRIBUTION, write_loops, write_segments
from flat_segments.loops import Loop, SportArea, build_tracks
from flat_segments.pipeline import loops_sibling, params_sidecar, run_publish
from tests.test_export import sample_segments
from tests.test_loops import stadium

needs_tippecanoe = pytest.mark.skipif(
    not tiles.tippecanoe_available(), reason="tippecanoe is not installed"
)


def labege_segments() -> list[Segment]:
    """Synthetic segments moved to Labège (Lambert-93)."""
    offset = np.array([581_000.0, 6_271_000.0])
    segments = sample_segments()
    return [replace(s, coords=s.coords + offset) for s in segments]


def test_tile_properties_have_no_lists_nor_nulls() -> None:
    segment = replace(labege_segments()[0], name=None, highways=("cycleway", "footway"))
    props = tiles.tile_properties(segment)
    assert "name" not in props
    assert props["highways"] == '["cycleway","footway"]'
    assert props["fits_targets_m"] == "[200,400,1000]"
    assert props["kind"] == "flat"
    assert all(not isinstance(v, (list, tuple, type(None))) for v in props.values())


def test_id_index_gives_the_midpoint_of_each_segment(tmp_path: Path) -> None:
    [segment] = labege_segments()
    other = replace(segment, id="climb-3fa2b1c9d0e4")
    (tmp_path / "ids").mkdir()
    (tmp_path / "ids" / "zz.json").write_text("{}")  # stale file of an older export
    assert tiles.write_id_index([segment, other], tmp_path / "ids") == 2
    key = tiles.index_key(segment.id)
    assert key == segment.id.split("-")[1][:2]
    entry = json.loads((tmp_path / "ids" / f"{key}.json").read_text())[segment.id]
    assert entry == pytest.approx([1.53, 43.53], abs=0.02)
    assert json.loads((tmp_path / "ids" / "3f.json").read_text()) == {"climb-3fa2b1c9d0e4": entry}
    assert not (tmp_path / "ids" / "zz.json").exists()


def test_tileset_metadata() -> None:
    segments = labege_segments()
    metadata = tiles.tileset_metadata(
        segments, generated_at=datetime(2026, 10, 1, tzinfo=UTC), params={"a": 1}
    )
    assert metadata["generated_at"] == "2026-10-01T00:00:00Z"
    assert metadata["sample"] is False
    assert metadata["attribution"][0] == OSM_ATTRIBUTION
    assert metadata["counts"] == {"flat": 1, "climb": 0, "loop": 0}
    west, south, east, north = metadata["bounds"]
    assert west < east
    assert south < north
    assert metadata["tiles"]["minzoom"] == 12
    assert metadata["tiles"]["url"] == "segments.pmtiles"
    assert metadata["tiles"]["index"] == "ids"
    assert metadata["params"] == {"a": 1}
    elsewhere = tiles.tileset_metadata(
        segments,
        tiles_url="https://pub-x.r2.dev/tiles/segments-1.pmtiles",
        index_url="https://pub-x.r2.dev/ids/1",
    )
    assert elsewhere["tiles"]["url"] == "https://pub-x.r2.dev/tiles/segments-1.pmtiles"
    assert elsewhere["tiles"]["index"] == "https://pub-x.r2.dev/ids/1"


@needs_tippecanoe
def test_tileset_holds_every_segment_with_its_properties(tmp_path: Path) -> None:
    import pyogrio  # type: ignore[import-untyped]

    segments = labege_segments()
    files = tiles.write_tileset(segments, tmp_path)
    assert files.pmtiles.stat().st_size > 0
    layers = {name for name, _ in pyogrio.list_layers(files.pmtiles)}
    assert layers == {"segments", "overview"}
    detail = pyogrio.read_dataframe(files.pmtiles, layer="segments")
    assert set(detail["id"]) == {s.id for s in segments}
    assert json.loads(detail["fits_targets_m"].iloc[0]) == [200, 400, 1000]
    overview = pyogrio.read_dataframe(files.pmtiles, layer="overview")
    assert set(overview.columns) - {"geometry", "mvt_id"} == {"id", "kind", "length_m"}
    assert json.loads(files.metadata.read_text())["counts"]["flat"] == 1


def write_run(directory: Path, segments: list[Segment], params: str = "x = 1\n") -> Path:
    path = directory / "segments.parquet"
    directory.mkdir(parents=True)
    write_segments(segments, path)
    params_sidecar(path).write_text(params)
    return path


def test_publish_refuses_mixed_parameters_and_duplicate_ids(tmp_path: Path) -> None:
    [segment] = labege_segments()
    a = write_run(tmp_path / "a", [segment])
    b = write_run(tmp_path / "b", [replace(segment, id="flat-000000000000")], "x = 2\n")
    with pytest.raises(ValueError, match="different parameters"):
        run_publish([a, b], tmp_path / "web")
    c = write_run(tmp_path / "c", [segment])
    with pytest.raises(ValueError, match="duplicate"):
        run_publish([a, c], tmp_path / "web")


@needs_tippecanoe
def test_export_pmtiles_command_merges_departments(tmp_path: Path) -> None:
    [segment] = labege_segments()
    climb = replace(
        segment, id="climb-000000000001", kind=SegmentKind.CLIMB, coords=segment.coords + 900.0
    )
    a = write_run(tmp_path / "31", [segment])
    b = write_run(tmp_path / "81", [climb])
    out = tmp_path / "web"
    result = CliRunner().invoke(cli.app, ["export-pmtiles", str(a), str(b), "--out-dir", str(out)])
    assert result.exit_code == 0, result.output
    metadata = json.loads((out / "segments.json").read_text())
    assert metadata["counts"] == {"flat": 1, "climb": 1, "loop": 0}
    assert metadata["params"] == {"x": 1}
    assert (out / "ids" / "00.json").exists()


def test_tippecanoe_version_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    assert tiles.parse_version("tippecanoe v2.79.0\n") == (2, 79, 0)
    assert tiles.parse_version("command not found") is None
    monkeypatch.setattr(tiles, "tippecanoe_version", lambda: (2, 49, 0))
    with pytest.raises(tiles.TippecanoeError, match=r"2\.49\.0 mixes up attribute values"):
        tiles.check_tippecanoe()
    assert not tiles.tippecanoe_available()
    monkeypatch.setattr(tiles, "tippecanoe_version", lambda: None)
    with pytest.raises(tiles.TippecanoeError, match="not found"):
        tiles.check_tippecanoe()


def test_tileset_mismatches_spot_mixed_up_values() -> None:
    expected = {
        "flat-a": {"id": "flat-a", "length_m": 271.8, "osm_way_ids": "[149966956]"},
        "flat-b": {"id": "flat-b", "length_m": 302.2, "osm_way_ids": "[1161127635]", "name": "Rue"},
        "flat-c": {"id": "flat-c", "length_m": 10.0, "osm_way_ids": "[1]"},
    }
    good = [
        {"id": "flat-a", "length_m": 271.8000000000001, "osm_way_ids": "[149966956]", "name": None},
        {"id": "flat-a", "length_m": 271.8, "osm_way_ids": "[149966956]", "name": float("nan")},
        {"id": "flat-b", "length_m": 302.2, "osm_way_ids": "[1161127635]", "name": "Rue"},
        {"id": "flat-c", "length_m": 10, "osm_way_ids": "[1]", "name": None},
    ]
    assert tiles.tileset_mismatches(expected, good) == []
    mixed = [dict(good[0], osm_way_ids="climb-d006353a8869"), good[2]]
    problems = tiles.tileset_mismatches(expected, [*mixed, {"id": "climb-x"}])
    assert problems[0] == "flat-a: osm_way_ids = 'climb-d006353a8869', expected '[149966956]'"
    assert problems[1] == "unknown feature id 'climb-x'"
    assert problems[-1] == "1 segments missing"


def labege_track(shift_m: float = 0.0) -> Loop:
    """A 400 m running track near the Labège segments."""
    [track] = build_tracks(
        [
            SportArea(
                "way/1",
                stadium((581_600.0 + shift_m, 6_271_600.0)),
                {"leisure": "track", "sport": "athletics", "name": "Stade", "access": "yes"},
            )
        ]
    )
    return track


@needs_tippecanoe
def test_loops_are_published_with_the_segments_next_to_them(tmp_path: Path) -> None:
    segments = labege_segments()
    path = write_run(tmp_path / "31", segments)
    track = labege_track()
    write_loops([track], loops_sibling(path))
    out = tmp_path / "web"
    count, files, _ = run_publish([path], out)  # the tiles are read back and checked
    assert count == len(segments) + 1
    metadata = json.loads(files.metadata.read_text())
    assert metadata["counts"]["loop"] == 1
    index = json.loads((files.index_dir / f"{tiles.index_key(track.id)}.json").read_text())
    assert track.id in index
    props = tiles.tile_properties(track)
    assert props["kind"] == "loop"
    assert (props["lap_m"], props["access"], props["indoor"]) == (400, "public", False)
    assert "opening_hours" not in props  # null


@needs_tippecanoe
def test_a_loop_keeps_its_published_id(tmp_path: Path) -> None:
    out = tmp_path / "web"
    v1 = write_run(tmp_path / "v1", labege_segments())
    old = labege_track()
    write_loops([old], loops_sibling(v1))
    run_publish([v1], out)
    v2 = write_run(tmp_path / "v2", labege_segments())
    new = labege_track(shift_m=6.0)  # redrawn in OSM: another id
    assert new.id != old.id
    write_loops([new], loops_sibling(v2))
    _, files, lineage = run_publish([v2], out, previous=out)
    assert lineage is not None
    index = json.loads((files.index_dir / f"{tiles.index_key(old.id)}.json").read_text())
    assert len(index[old.id]) == 2  # a live id, not a redirect
