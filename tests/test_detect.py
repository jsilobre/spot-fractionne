import math
from dataclasses import replace

import numpy as np
import pytest

from flat_segments import detect as dt
from flat_segments.network import EventKind, RoadClass, Stroke, StrokeEvent, StrokePart
from flat_segments.params import PipelineParams, ProfileParams
from flat_segments.profile import build_profile
from tests.helpers import piecewise, raw_profile, straight_stroke

PARAMS = PipelineParams()


def run(stroke: Stroke, z_raw: np.ndarray, source: str = "synthetic") -> list[dt.Segment]:
    profile = build_profile(stroke.coords, z_raw, PARAMS.profile, stroke.structures())
    return dt.detect_stroke(stroke, profile, PARAMS, source)


def of_kind(segments: list[dt.Segment], kind: dt.SegmentKind) -> list[dt.Segment]:
    return [s for s in segments if s.kind is kind]


# --- building blocks --------------------------------------------------------


def test_window_starts_always_include_the_last_window() -> None:
    np.testing.assert_array_equal(dt.window_starts(0, 11, 4, 2), [0, 2, 4, 6, 7])
    np.testing.assert_array_equal(dt.window_starts(0, 10, 4, 2), [0, 2, 4, 6])
    assert len(dt.window_starts(0, 3, 4, 1)) == 0


def test_merge_intervals() -> None:
    assert dt.merge_intervals([0, 5, 20, 8], [10, 12, 30, 15]) == [(0, 15), (20, 30)]
    assert dt.merge_intervals([0, 10], [10, 20]) == [(0, 20)]  # touching
    assert dt.merge_intervals([], []) == []


def test_longest_run_in_windows() -> None:
    mask = np.array([0, 1, 1, 1, 0, 0, 1, 1, 0, 0], bool)
    got = dt.longest_run_in_windows(mask, np.array([0, 2, 4, 8]), np.array([5, 7, 9, 10]))
    np.testing.assert_array_equal(got, [3, 2, 2, 0])


def test_summarize_parts_weights_by_length() -> None:
    parts = (
        StrokePart(1, 0, 300, "cycleway", RoadClass.PATH, surface="asphalt", lit="yes", name="VV"),
        StrokePart(2, 300, 400, "track", RoadClass.PATH, tracktype="grade2"),
        StrokePart(3, 400, 500, "residential", RoadClass.MINOR, structure="bridge", lit="no"),
    )
    summary = dt.summarize_parts(parts, 0, 400)
    assert summary.surface == "paved"
    assert summary.lit == "partial"
    assert summary.name == "VV"
    assert summary.highways == ("cycleway", "track")
    assert summary.way_ids == (1, 2)
    assert not summary.on_structure
    tail = dt.summarize_parts(parts, 350, 500)
    assert (tail.surface, tail.lit, tail.name, tail.on_structure) == ("paved", "no", None, True)


def test_crossings_component() -> None:
    assert dt.crossings_component(0, 500) == 1.0
    assert dt.crossings_component(1, 1000) == pytest.approx(0.5)


# --- flat segments ----------------------------------------------------------


def test_flat_kilometre_is_one_segment_fitting_all_targets() -> None:
    stroke = straight_stroke(1000)
    segments = run(stroke, raw_profile(stroke, piecewise((1000, 0.0))))
    [flat] = of_kind(segments, dt.SegmentKind.FLAT)
    assert flat.length_m == pytest.approx(1000)
    assert flat.fits_targets_m == (200, 400, 1000)
    assert flat.grade_max_pct == pytest.approx(0, abs=1e-9)
    assert flat.sinuosity == pytest.approx(1)
    assert (flat.surface, flat.lit, flat.n_crossings) == ("paved", "unknown", 0)
    assert flat.score > 90
    assert flat.id.startswith("flat-")
    assert not of_kind(segments, dt.SegmentKind.CLIMB)


def test_gentle_slope_is_flat_but_steeper_is_not() -> None:
    stroke = straight_stroke(1000)
    [flat] = run(stroke, raw_profile(stroke, piecewise((1000, 0.8))))
    assert flat.grade_mean_pct == pytest.approx(0.8)
    assert flat.elev_gain_m == pytest.approx(8)
    assert run(stroke, raw_profile(stroke, piecewise((1000, 1.5)))) == []


def test_bump_splits_flat_stretch_and_limits_targets() -> None:
    stroke = straight_stroke(1000)
    profile = piecewise((300, 0.0), (100, 5.0), (600, 0.0))
    first, second = sorted(
        of_kind(run(stroke, raw_profile(stroke, profile)), dt.SegmentKind.FLAT),
        key=lambda s: s.stroke_start_m,
    )
    assert first.stroke_start_m == 0
    assert 230 < first.stroke_end_m <= 300
    assert first.fits_targets_m == (200,)
    assert 400 <= second.stroke_start_m < 470
    assert second.stroke_end_m == pytest.approx(1000)
    assert second.fits_targets_m == (200, 400)


def test_zigzag_is_too_sinuous_but_a_wide_arc_is_fine() -> None:
    xs = np.arange(0, 1001, 25.0)
    zigzag = np.column_stack((xs, np.where(np.arange(len(xs)) % 2, 25.0, 0.0)))
    part = (StrokePart(1, 0, 2000, "path", RoadClass.PATH),)
    stroke = Stroke("s1", zigzag, part)
    assert (
        of_kind(run(stroke, raw_profile(stroke, piecewise((5000, 0)))), dt.SegmentKind.FLAT) == []
    )

    angles = np.linspace(0, 0.5, 100)  # 1 km of a 2 km radius circle
    arc = np.column_stack((2000 * np.sin(angles), 2000 * (1 - np.cos(angles))))
    stroke = Stroke("s2", arc, part)
    [flat] = run(stroke, raw_profile(stroke, piecewise((5000, 0))))
    assert flat.length_m == pytest.approx(1000, rel=1e-3)


def test_crossings_are_counted_and_penalised() -> None:
    events = (
        StrokeEvent(500, EventKind.CROSSING, 1),
        StrokeEvent(700, EventKind.JUNCTION, 2),
        StrokeEvent(0, EventKind.CROSSING, 3),  # on the boundary: not inside
    )
    plain = straight_stroke(1000)
    crossed = straight_stroke(1000, events=events)
    z = raw_profile(plain, piecewise((1000, 0)))
    [ref] = run(plain, z)
    [flat] = run(crossed, z)
    assert (flat.n_crossings, flat.n_junctions) == (1, 1)
    assert flat.score < ref.score


def test_bridge_is_interpolated_and_flagged() -> None:
    def valley(d: np.ndarray) -> np.ndarray:
        return 150.0 - 8.0 * ((d > 480) & (d < 520))

    parts = (
        StrokePart(1, 0, 470, "cycleway", RoadClass.PATH, surface="asphalt"),
        StrokePart(2, 470, 530, "cycleway", RoadClass.PATH, surface="asphalt", structure="bridge"),
        StrokePart(3, 530, 1000, "cycleway", RoadClass.PATH, surface="asphalt"),
    )
    with_bridge = straight_stroke(1000, parts=parts)
    [flat] = run(with_bridge, raw_profile(with_bridge, valley))
    assert flat.length_m == pytest.approx(1000)
    assert flat.on_structure
    assert flat.quality_flags == ("bridge_interpolated",)
    assert flat.osm_way_ids == (1, 2, 3)

    without = straight_stroke(1000)
    flats = of_kind(run(without, raw_profile(without, valley)), dt.SegmentKind.FLAT)
    assert len(flats) == 2
    assert all(s.length_m < 500 for s in flats)


# --- climbs -----------------------------------------------------------------


def test_regular_climb_is_detected_trimmed_and_oriented_uphill() -> None:
    stroke = straight_stroke(900)
    z = raw_profile(stroke, piecewise((300, 0), (300, 6), (300, 0)))
    [climb] = of_kind(run(stroke, z), dt.SegmentKind.CLIMB)
    assert climb.length_m == pytest.approx(300, abs=30)
    assert climb.grade_mean_pct == pytest.approx(6, abs=0.6)
    assert climb.elev_end_m > climb.elev_start_m
    assert climb.coords[0, 0] < climb.coords[-1, 0]
    assert climb.fits_targets_m == (100, 200)
    assert climb.score > 70


def test_steepest_grades_per_length() -> None:
    z = np.concatenate([np.arange(100) * 0.2, 19.8 + np.arange(1, 41) * 0.6])  # 4 % then 12 %
    grades = dt.steepest_grades(z, 5.0)
    assert grades["steep_100_pct"] == pytest.approx(12)
    assert grades["steep_200_pct"] == pytest.approx(12)
    assert grades["steep_400_pct"] == pytest.approx(8)
    assert grades["steep_800_pct"] is None
    assert grades["steep_1000_pct"] is None


def test_climb_with_a_steep_end_records_its_steepest_stretches() -> None:
    stroke = straight_stroke(1000)
    z = raw_profile(stroke, piecewise((200, 0), (400, 4), (200, 12), (200, 0)))
    [climb] = of_kind(run(stroke, z), dt.SegmentKind.CLIMB)
    assert climb.grade_mean_pct == pytest.approx(6.7, abs=0.7)
    assert climb.steep_100_pct == pytest.approx(12, abs=0.6)
    assert climb.steep_200_pct == pytest.approx(12, abs=1)
    assert climb.steep_800_pct is None
    flats = of_kind(run(stroke, z), dt.SegmentKind.FLAT)
    assert all(f.steep_100_pct is None for f in flats)


def test_long_climb_goes_over_a_replat_that_cuts_climbs() -> None:
    stroke = straight_stroke(2000)
    z = raw_profile(stroke, piecewise((200, 0), (700, 5), (100, 0), (700, 4), (300, 0)))
    segments = run(stroke, z)
    climbs = of_kind(segments, dt.SegmentKind.CLIMB)
    [long_climb] = of_kind(segments, dt.SegmentKind.LONG_CLIMB)
    assert len(climbs) == 2
    assert long_climb.length_m == pytest.approx(1500, abs=50)
    assert long_climb.grade_mean_pct == pytest.approx(4.2, abs=0.4)
    assert long_climb.id.startswith("long_climb-")
    assert long_climb.fits_targets_m == (1000, 1500)
    assert long_climb.steep_100_pct == pytest.approx(5, abs=0.5)


def test_short_climbs_are_not_long_climbs() -> None:
    stroke = straight_stroke(900)
    z = raw_profile(stroke, piecewise((300, 0), (300, 6), (300, 0)))
    assert of_kind(run(stroke, z), dt.SegmentKind.LONG_CLIMB) == []


def test_descending_stroke_gives_the_same_climb_reversed() -> None:
    stroke = straight_stroke(900)
    z = raw_profile(stroke, piecewise((300, 0), (300, -6), (300, 0)))
    [climb] = of_kind(run(stroke, z), dt.SegmentKind.CLIMB)
    assert climb.grade_mean_pct == pytest.approx(6, abs=0.6)
    assert climb.coords[0, 0] > climb.coords[-1, 0]  # starts at the bottom (east)
    assert climb.elev_end_m > climb.elev_start_m


@pytest.mark.parametrize("grade", [2.0, 20.0])
def test_too_gentle_or_too_steep_is_not_a_climb(grade: float) -> None:
    stroke = straight_stroke(900)
    z = raw_profile(stroke, piecewise((300, 0), (300, grade), (300, 0)))
    assert of_kind(run(stroke, z), dt.SegmentKind.CLIMB) == []


def test_plateau_splits_a_climb_in_two() -> None:
    stroke = straight_stroke(760)
    z = raw_profile(stroke, piecewise((200, 0), (150, 6), (60, 0), (150, 6), (200, 0)))
    climbs = of_kind(run(stroke, z), dt.SegmentKind.CLIMB)
    assert len(climbs) == 2
    assert all(150 <= c.length_m <= 180 for c in climbs)  # ramp + smoothing tails


# --- deduplication and ids --------------------------------------------------


def test_parallel_duplicates_keep_the_best_only() -> None:
    step = PARAMS.profile.step_m
    road = straight_stroke(1000, stroke_id="road", surface=None)
    sidewalk = straight_stroke(1000, stroke_id="walk", offset_y=5.0)
    far = straight_stroke(1000, stroke_id="far", offset_y=50.0)
    z = raw_profile(road, piecewise((1000, 0)))
    segments = [s for stroke in (road, sidewalk, far) for s in run(stroke, z)]
    kept = dt.deduplicate(segments, PARAMS.detection.dedup, step)
    assert sorted(s.stroke_id for s in kept) == ["far", "walk"]


def test_short_duplicate_does_not_remove_a_long_segment() -> None:
    step = PARAMS.profile.step_m
    long_road = straight_stroke(2000, stroke_id="road", surface=None)
    short_walk = straight_stroke(300, stroke_id="walk")
    segments = run(long_road, raw_profile(long_road, piecewise((2000, 0)))) + run(
        short_walk, raw_profile(short_walk, piecewise((300, 0)))
    )
    kept = dt.deduplicate(segments, PARAMS.detection.dedup, step)
    assert sorted(s.stroke_id for s in kept) == ["road", "walk"]


def test_assign_unique_ids_suffixes_collisions_by_score() -> None:
    stroke = straight_stroke(1000)
    [seg] = run(stroke, raw_profile(stroke, piecewise((1000, 0))))
    worse = replace(seg, score=seg.score - 10)
    ids = [s.id for s in dt.assign_unique_ids([worse, seg])]
    assert ids == [seg.id, f"{seg.id}-2"]


def test_detect_all_skips_strokes_without_profile() -> None:
    a = straight_stroke(1000, stroke_id="a")
    b = straight_stroke(1000, stroke_id="b", offset_y=500)
    z = raw_profile(a, piecewise((1000, 0)))
    segments = dt.detect_all([a, b], {"a": z}, PARAMS, "synthetic")
    assert [s.stroke_id for s in segments] == ["a"]
    assert segments[0].elevation_source == "synthetic"


def test_short_stroke_yields_nothing() -> None:
    stroke = straight_stroke(150)
    assert run(stroke, raw_profile(stroke, piecewise((150, 0)))) == []
    tiny = straight_stroke(3)
    params = ProfileParams()
    profile = build_profile(tiny.coords, raw_profile(tiny, piecewise((3, 0))), params)
    assert math.isclose(profile.length, 3)
    assert dt.detect_stroke(tiny, profile, PARAMS, "synthetic") == []


def test_split_at_long_runs() -> None:
    mask = np.array([0, 0, 1, 1, 1, 0, 0, 1, 0, 0, 1, 1, 1, 1], bool)
    assert dt.split_at_long_runs(0, 13, mask, 2) == [(0, 1), (5, 9)]
    assert dt.split_at_long_runs(0, 13, mask, 5) == [(0, 13)]
    assert dt.split_at_long_runs(3, 8, mask, 1) == [(5, 8)]
