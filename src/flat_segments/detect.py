"""Detection of flat segments and climbs along strokes.

Pipeline for one stroke (docs/algorithm.md sections 7 to 12):

1. evaluate sliding windows of the shortest target length on the profile;
2. keep windows meeting the flat / climb criteria;
3. merge overlapping valid windows into maximal stretches;
4. compute attributes and a score for each stretch.

Segments from all strokes are then deduplicated (sidewalk + road, parallel
paths) and given unique stable ids.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from flat_segments.geometry import (
    BoolArray,
    FloatArray,
    IntArray,
    bbox,
    point_polyline_distances,
    resample,
    sinuosity,
    stable_id,
    substring,
)
from flat_segments.network import EventKind, Stroke, StrokePart
from flat_segments.osm import lit_category, surface_category
from flat_segments.params import (
    ClimbParams,
    DedupParams,
    FlatParams,
    PipelineParams,
)
from flat_segments.profile import Profile, build_profile, elevation_gain_loss, true_runs


class SegmentKind(StrEnum):
    """Type of segment."""

    FLAT = "flat"
    CLIMB = "climb"
    LONG_CLIMB = "long_climb"


@dataclass(frozen=True, slots=True, eq=False)
class Segment:
    """A detected segment. Fields mirror docs/data-model.md (table ``segments``)."""

    id: str
    kind: SegmentKind
    coords: FloatArray
    length_m: float
    elev_start_m: float
    elev_end_m: float
    elev_gain_m: float
    elev_loss_m: float
    grade_mean_pct: float
    grade_max_pct: float
    sinuosity: float
    n_crossings: int
    n_junctions: int
    surface: str
    lit: str
    name: str | None
    highways: tuple[str, ...]
    osm_way_ids: tuple[int, ...]
    on_structure: bool
    quality_flags: tuple[str, ...]
    fits_targets_m: tuple[int, ...]
    score: float
    elevation_source: str
    stroke_id: str
    stroke_start_m: float
    stroke_end_m: float
    # Climbs only: steepest mean grade over a stretch of each STEEP_LENGTHS_M
    # (None for flats, or when the climb is shorter).
    steep_100_pct: float | None = None
    steep_200_pct: float | None = None
    steep_400_pct: float | None = None
    steep_800_pct: float | None = None
    steep_1000_pct: float | None = None


#: Lengths of the steepest stretches recorded on climbs (the minimum lengths of
#: the web page), so that a long climb with a steep end passes a steep filter.
STEEP_LENGTHS_M: Final = (100, 200, 400, 800, 1000)


def steepest_grades(z: FloatArray, step: float) -> dict[str, float | None]:
    """Steepest mean grade over each of ``STEEP_LENGTHS_M``, on a climbing profile.

    ``z`` is sampled every ``step`` metres in the climbing direction. A length
    longer than the profile gets None.
    """
    grades: dict[str, float | None] = {}
    for length in STEEP_LENGTHS_M:
        n = samples_for(length, step)
        value = None
        if step > 0 and len(z) > n:
            value = float(np.max(z[n:] - z[:-n])) * 100.0 / (n * step)
        grades[f"steep_{length}_pct"] = value
    return grades


# --- sliding windows --------------------------------------------------------


@dataclass(frozen=True, slots=True, eq=False)
class Windows:
    """Statistics of windows ``[start, end]`` (sample indices, inclusive)."""

    start: IntArray
    end: IntArray
    mean_grade_pct: FloatArray
    max_abs_grade_pct: FloatArray
    sinuosity: FloatArray
    has_nan: BoolArray

    def __len__(self) -> int:
        return len(self.start)


def samples_for(length_m: float, step: float) -> int:
    """Number of grid intervals covering ``length_m``."""
    return max(1, round(length_m / step))


def window_starts(lo: int, hi: int, n: int, stride: int) -> IntArray:
    """Start indices of windows of ``n`` intervals within ``[lo, hi]``.

    The last possible window (ending exactly at ``hi``) is always included.
    """
    if hi - lo < n:
        return np.zeros(0, dtype=np.intp)
    starts = np.arange(lo, hi - n + 1, max(1, stride), dtype=np.intp)
    if starts[-1] != hi - n:
        starts = np.append(starts, hi - n)
    return starts


def sliding_windows(
    profile: Profile, n: int, stride: int, lo: int = 0, hi: int | None = None
) -> Windows:
    """Evaluate all windows of ``n`` intervals within samples ``[lo, hi]``."""
    hi = len(profile.distances) - 1 if hi is None else hi
    starts = window_starts(lo, hi, n, stride)
    ends = starts + n
    d, z, xy = profile.distances, profile.z, profile.xy
    if len(starts) == 0:
        empty = np.zeros(0)
        return Windows(starts, ends, empty, empty, empty, np.zeros(0, bool))
    span = d[ends] - d[starts]
    chord = np.hypot(*(xy[ends] - xy[starts]).T)
    with np.errstate(divide="ignore"):
        sinuosities = np.where(chord > 0, span / np.where(chord > 0, chord, 1.0), np.inf)
    return Windows(
        start=starts,
        end=ends,
        mean_grade_pct=100.0 * (z[ends] - z[starts]) / span,
        max_abs_grade_pct=sliding_window_view(np.abs(profile.grade_pct), n + 1)[starts].max(axis=1),
        sinuosity=sinuosities,
        has_nan=sliding_window_view(np.isnan(z), n + 1)[starts].any(axis=1),
    )


def longest_run_in_windows(mask: BoolArray, starts: IntArray, stops: IntArray) -> IntArray:
    """Length (in samples) of the longest ``True`` run inside each ``[start, stop)``."""
    longest = np.zeros(len(starts), dtype=np.intp)
    for run_start, run_stop in true_runs(mask):
        overlap = np.minimum(stops, run_stop) - np.maximum(starts, run_start)
        np.maximum(longest, overlap, out=longest)
    return longest


def flat_mask(windows: Windows, params: FlatParams) -> BoolArray:
    """Windows meeting the flat criteria (docs/algorithm.md section 7.1)."""
    valid: BoolArray = (
        ~windows.has_nan
        & (np.abs(windows.mean_grade_pct) <= params.max_mean_grade_pct)
        & (windows.max_abs_grade_pct <= params.max_local_grade_pct)
        & (windows.sinuosity <= params.max_sinuosity)
    )
    return valid


def climb_mask(windows: Windows, profile: Profile, params: ClimbParams, sign: int) -> BoolArray:
    """Windows meeting the climb criteria in direction ``sign`` (section 7.2)."""
    with np.errstate(invalid="ignore"):
        flat = ~(sign * profile.grade_pct >= params.min_local_grade_pct)
    longest_m = longest_run_in_windows(flat, windows.start, windows.end + 1) * profile.step
    mean = sign * windows.mean_grade_pct
    valid: BoolArray = (
        ~windows.has_nan
        & (mean >= params.min_mean_grade_pct)
        & (mean <= params.max_mean_grade_pct)
        & (longest_m <= params.max_flat_stretch_m + 1e-9)
        & (windows.sinuosity <= params.max_sinuosity)
    )
    return valid


def merge_intervals(starts: Iterable[int], ends: Iterable[int]) -> list[tuple[int, int]]:
    """Union of closed integer intervals; touching intervals are merged."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(zip(starts, ends, strict=True)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((int(start), int(end)))
    return merged


def split_at_long_runs(
    lo: int, hi: int, mask: BoolArray, max_samples: int
) -> list[tuple[int, int]]:
    """Split ``[lo, hi]`` around ``True`` runs longer than ``max_samples``.

    Merged climb windows can meet inside a plateau; splitting there enforces
    the "no flat stretch longer than ``max_flat_stretch_m``" rule on the
    final stretch.
    """
    pieces = []
    cursor = lo
    for run_start, run_stop in true_runs(mask[lo : hi + 1]):
        if run_stop - run_start > max_samples:
            if lo + run_start - 1 > cursor:
                pieces.append((cursor, lo + run_start - 1))
            cursor = lo + run_stop
    if cursor < hi:
        pieces.append((cursor, hi))
    return pieces


# --- attributes -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartSummary:
    """OSM attributes of a stretch, aggregated by length (section 9)."""

    surface: str
    lit: str
    name: str | None
    highways: tuple[str, ...]
    way_ids: tuple[int, ...]
    on_structure: bool


def _dominant(weights: Mapping[str, float]) -> str:
    return max(sorted(weights), key=lambda key: weights[key])


def summarize_parts(parts: Sequence[StrokePart], start: float, end: float) -> PartSummary:
    """Aggregate the attributes of the parts overlapping ``[start, end]``."""
    surfaces: defaultdict[str, float] = defaultdict(float)
    lights: defaultdict[str, float] = defaultdict(float)
    names: defaultdict[str, float] = defaultdict(float)
    highways: defaultdict[str, float] = defaultdict(float)
    way_ids: list[int] = []
    on_structure = False
    for part in parts:
        overlap = min(part.end_m, end) - max(part.start_m, start)
        if overlap <= 0:
            continue
        surfaces[surface_category(part.surface, part.tracktype, part.road_class)] += overlap
        lights[lit_category(part.lit)] += overlap
        highways[part.highway] += overlap
        if part.name:
            names[part.name] += overlap
        if part.way_id not in way_ids:
            way_ids.append(part.way_id)
        on_structure |= part.structure is not None
    total = sum(surfaces.values()) or 1.0
    yes, no = lights.get("yes", 0.0) / total, lights.get("no", 0.0) / total
    if yes >= 0.9:
        lit = "yes"
    elif yes > 0:
        lit = "partial"
    elif no >= 0.5:
        lit = "no"
    else:
        lit = "unknown"
    name = _dominant(names) if names and max(names.values()) / total >= 0.5 else None
    return PartSummary(
        surface=_dominant(surfaces) if surfaces else "unknown",
        lit=lit,
        name=name,
        highways=tuple(sorted(highways, key=lambda h: (-highways[h], h))),
        way_ids=tuple(way_ids),
        on_structure=on_structure,
    )


# --- scoring ----------------------------------------------------------------

SURFACE_SCORES: Final[Mapping[str, float]] = {
    "paved": 1.0,
    "compacted": 0.8,
    "gravel": 0.6,
    "unknown": 0.5,
    "cobbles": 0.4,
    "unpaved": 0.4,
}
LIT_SCORES: Final[Mapping[str, float]] = {"yes": 1.0, "partial": 0.5, "unknown": 0.3, "no": 0.0}


def _clip01(value: float) -> float:
    return min(1.0, max(0.0, value)) if math.isfinite(value) else 0.0


def crossings_component(n_crossings: int, length_m: float) -> float:
    """``1 / (1 + crossings per km)``."""
    return 1.0 / (1.0 + n_crossings / max(length_m / 1000.0, 1e-9))


def straightness_component(sinuosity_value: float, max_sinuosity: float) -> float:
    """1 for a straight line, 0 at ``max_sinuosity`` and beyond."""
    return _clip01(1.0 - (sinuosity_value - 1.0) / (max_sinuosity - 1.0))


def _weighted(components: Mapping[str, float], weights: Mapping[str, float]) -> float:
    total = sum(weights.values())
    return 100.0 * sum(weights[k] * _clip01(components[k]) for k in weights) / total


def score_flat(
    *,
    grade_mean_pct: float,
    grade_max_pct: float,
    sinuosity_value: float,
    n_crossings: int,
    length_m: float,
    surface: str,
    lit: str,
    n_fits: int,
    params: FlatParams,
) -> float:
    """Score of a flat segment in [0, 100] (docs/algorithm.md section 10)."""
    w = params.weights
    components = {
        "flatness": 1.0
        - 0.5 * abs(grade_mean_pct) / params.max_mean_grade_pct
        - 0.5 * grade_max_pct / params.max_local_grade_pct,
        "straightness": straightness_component(sinuosity_value, params.max_sinuosity),
        "crossings": crossings_component(n_crossings, length_m),
        "surface": SURFACE_SCORES.get(surface, 0.5),
        "lighting": LIT_SCORES.get(lit, 0.3),
        "length": n_fits / len(params.target_lengths_m),
    }
    weights = {
        "flatness": w.flatness,
        "straightness": w.straightness,
        "crossings": w.crossings,
        "surface": w.surface,
        "lighting": w.lighting,
        "length": w.length,
    }
    return _weighted(components, weights)


def score_climb(
    *,
    grade_mean_pct: float,
    grade_std_pct: float,
    sinuosity_value: float,
    n_crossings: int,
    length_m: float,
    surface: str,
    lit: str,
    n_fits: int,
    params: ClimbParams,
) -> float:
    """Score of a climb in [0, 100] (docs/algorithm.md section 10)."""
    w = params.weights
    components = {
        "regularity": 1.0 - grade_std_pct / grade_mean_pct if grade_mean_pct > 0 else 0.0,
        "crossings": crossings_component(n_crossings, length_m),
        "straightness": straightness_component(sinuosity_value, params.max_sinuosity),
        "surface": SURFACE_SCORES.get(surface, 0.5),
        "lighting": LIT_SCORES.get(lit, 0.3),
        "length": n_fits / len(params.target_lengths_m),
    }
    weights = {
        "regularity": w.regularity,
        "crossings": w.crossings,
        "straightness": w.straightness,
        "surface": w.surface,
        "lighting": w.lighting,
        "length": w.length,
    }
    return _weighted(components, weights)


# --- segment construction ---------------------------------------------------

MaskFn = Callable[[Windows], BoolArray]


def _fits_targets(
    profile: Profile, i0: int, i1: int, targets: Sequence[float], mask_fn: MaskFn
) -> tuple[int, ...]:
    """Target lengths for which a valid window exists inside ``[i0, i1]``."""
    fits = []
    for target in sorted(targets):
        windows = sliding_windows(profile, samples_for(target, profile.step), 1, i0, i1)
        if len(windows) and mask_fn(windows).any():
            fits.append(round(target))
    return tuple(fits)


def _make_segment(
    kind: SegmentKind,
    stroke: Stroke,
    profile: Profile,
    i0: int,
    i1: int,
    sign: int,
    fits: tuple[int, ...],
    params: PipelineParams,
    elevation_source: str,
) -> Segment:
    """Build a segment from samples ``[i0, i1]``, oriented by ``sign``."""
    start, end = float(profile.distances[i0]), float(profile.distances[i1])
    coords = substring(stroke.coords, start, end)
    z = profile.z[i0 : i1 + 1]
    grade = profile.grade_pct[i0 : i1 + 1]
    summary = summarize_parts(stroke.parts, start, end)
    way_ids = summary.way_ids
    if sign < 0:
        coords, z, grade, way_ids = coords[::-1], z[::-1], -grade[::-1], way_ids[::-1]
    length = end - start
    gain, loss = elevation_gain_loss(z, params.profile.gain_hysteresis_m)
    grade_mean = 100.0 * float(z[-1] - z[0]) / length
    grade_max = float(np.abs(grade).max())
    sinuosity_value = sinuosity(coords)
    inside = [ev for ev in stroke.events if start < ev.offset_m < end]
    n_crossings = sum(ev.kind is EventKind.CROSSING for ev in inside)
    n_junctions = sum(ev.kind is EventKind.JUNCTION for ev in inside)
    flags = []
    if profile.bridge_mask[i0 : i1 + 1].any():
        flags.append("bridge_interpolated")
    if profile.tunnel_mask[i0 : i1 + 1].any():
        flags.append("tunnel_interpolated")
    if profile.gap_mask[i0 : i1 + 1].any():
        flags.append("gap_filled")
    detection = params.detection
    if kind is SegmentKind.FLAT:
        score = score_flat(
            grade_mean_pct=grade_mean,
            grade_max_pct=grade_max,
            sinuosity_value=sinuosity_value,
            n_crossings=n_crossings,
            length_m=length,
            surface=summary.surface,
            lit=summary.lit,
            n_fits=len(fits),
            params=detection.flat,
        )
    else:
        climb = detection.climb if kind is SegmentKind.CLIMB else detection.long_climb
        score = score_climb(
            grade_mean_pct=grade_mean,
            grade_std_pct=float(grade.std()),
            sinuosity_value=sinuosity_value,
            n_crossings=n_crossings,
            length_m=length,
            surface=summary.surface,
            lit=summary.lit,
            n_fits=len(fits),
            params=climb,
        )
    return Segment(
        id=stable_id(kind.value, coords),
        kind=kind,
        coords=coords,
        length_m=length,
        elev_start_m=float(z[0]),
        elev_end_m=float(z[-1]),
        elev_gain_m=gain,
        elev_loss_m=loss,
        grade_mean_pct=grade_mean,
        grade_max_pct=grade_max,
        sinuosity=sinuosity_value,
        n_crossings=n_crossings,
        n_junctions=n_junctions,
        surface=summary.surface,
        lit=summary.lit,
        name=summary.name,
        highways=summary.highways,
        osm_way_ids=way_ids,
        on_structure=summary.on_structure,
        quality_flags=tuple(flags),
        fits_targets_m=fits,
        score=score,
        elevation_source=elevation_source,
        stroke_id=stroke.id,
        stroke_start_m=start,
        stroke_end_m=end,
        **(steepest_grades(z, profile.step) if kind is not SegmentKind.FLAT else {}),
    )


def detect_flats(
    stroke: Stroke, profile: Profile, params: PipelineParams, elevation_source: str
) -> list[Segment]:
    """Maximal flat stretches of a stroke (sections 7.1 and 8)."""
    flat = params.detection.flat
    step = profile.step
    if step <= 0:
        return []
    n0 = samples_for(min(flat.target_lengths_m), step)
    stride = samples_for(params.detection.window_step_m, step)
    windows = sliding_windows(profile, n0, stride)
    valid = flat_mask(windows, flat)
    segments = []
    for i0, i1 in merge_intervals(windows.start[valid], windows.end[valid]):
        fits = _fits_targets(profile, i0, i1, flat.target_lengths_m, lambda w: flat_mask(w, flat))
        segments.append(
            _make_segment(
                SegmentKind.FLAT, stroke, profile, i0, i1, 1, fits, params, elevation_source
            )
        )
    return segments


def detect_climbs(
    stroke: Stroke,
    profile: Profile,
    params: PipelineParams,
    elevation_source: str,
    kind: SegmentKind = SegmentKind.CLIMB,
) -> list[Segment]:
    """Maximal climbs (or long climbs) of a stroke in both directions (sections 7.2, 7.3, 8)."""
    climb = params.detection.climb if kind is SegmentKind.CLIMB else params.detection.long_climb
    step = profile.step
    if step <= 0:
        return []
    n0 = samples_for(min(climb.target_lengths_m), step)
    stride = samples_for(params.detection.window_step_m, step)
    windows = sliding_windows(profile, n0, stride)
    segments = []
    for sign in (1, -1):

        def mask_fn(w: Windows, sign: int = sign) -> BoolArray:
            return climb_mask(w, profile, climb, sign)

        valid = mask_fn(windows)
        directional = sign * profile.grade_pct
        with np.errstate(invalid="ignore"):
            flat = ~(directional >= climb.min_local_grade_pct)
        max_flat = math.floor(climb.max_flat_stretch_m / step + 1e-9)
        merged = merge_intervals(windows.start[valid], windows.end[valid])
        for start, stop in (p for m in merged for p in split_at_long_runs(*m, flat, max_flat)):
            i0, i1 = start, stop
            while i0 < i1 and flat[i0]:
                i0 += 1
            while i1 > i0 and flat[i1]:
                i1 -= 1
            if i1 - i0 < n0:
                continue
            fits = _fits_targets(profile, i0, i1, climb.target_lengths_m, mask_fn)
            segments.append(
                _make_segment(kind, stroke, profile, i0, i1, sign, fits, params, elevation_source)
            )
    return segments


# --- deduplication and ids --------------------------------------------------


def overlap_ratio(candidate: FloatArray, other: FloatArray, buffer_m: float, step: float) -> float:
    """Share of ``candidate`` lying within ``buffer_m`` of ``other``."""
    _, points = resample(candidate, step)
    return float((point_polyline_distances(points, other) <= buffer_m).mean())


def deduplicate(segments: Sequence[Segment], params: DedupParams, step: float) -> list[Segment]:
    """Drop segments mostly covered by a better one of the same kind (section 11)."""
    kept: list[Segment] = []
    for kind in SegmentKind:
        ordered = sorted(
            (s for s in segments if s.kind is kind), key=lambda s: (-s.score, s.stroke_id)
        )
        boxes = np.zeros((len(ordered), 4))
        kept_of_kind: list[Segment] = []
        for candidate in ordered:
            min_x, min_y, max_x, max_y = bbox(candidate.coords)
            box = boxes[: len(kept_of_kind)]
            near = np.flatnonzero(
                (box[:, 0] <= max_x + params.buffer_m)
                & (box[:, 2] >= min_x - params.buffer_m)
                & (box[:, 1] <= max_y + params.buffer_m)
                & (box[:, 3] >= min_y - params.buffer_m)
            )
            if any(
                overlap_ratio(candidate.coords, kept_of_kind[k].coords, params.buffer_m, step)
                > params.max_overlap
                for k in near
            ):
                continue
            boxes[len(kept_of_kind)] = (min_x, min_y, max_x, max_y)
            kept_of_kind.append(candidate)
        kept.extend(kept_of_kind)
    return kept


def assign_unique_ids(segments: Sequence[Segment]) -> list[Segment]:
    """Suffix colliding ids with ``-2``, ``-3``... by decreasing score."""
    seen: Counter[str] = Counter()
    result = []
    for segment in sorted(segments, key=lambda s: (-s.score, s.id)):
        seen[segment.id] += 1
        count = seen[segment.id]
        result.append(segment if count == 1 else replace(segment, id=f"{segment.id}-{count}"))
    return result


def detect_stroke(
    stroke: Stroke, profile: Profile, params: PipelineParams, elevation_source: str
) -> list[Segment]:
    """All flat segments, climbs and long climbs of one stroke (before deduplication)."""
    return (
        detect_flats(stroke, profile, params, elevation_source)
        + detect_climbs(stroke, profile, params, elevation_source)
        + detect_climbs(stroke, profile, params, elevation_source, SegmentKind.LONG_CLIMB)
    )


def detect_all(
    strokes: Iterable[Stroke],
    z_raw_by_stroke: Mapping[str, FloatArray],
    params: PipelineParams | None = None,
    elevation_source: str = "rge_alti_1m",
    source_by_stroke: Mapping[str, str] | None = None,
) -> list[Segment]:
    """Run detection on every stroke that has a profile, then deduplicate.

    ``source_by_stroke`` overrides ``elevation_source`` for some strokes.

    Returns:
        Segments sorted by kind, then decreasing score.
    """
    params = params or PipelineParams()
    segments: list[Segment] = []
    for stroke in strokes:
        z_raw = z_raw_by_stroke.get(stroke.id)
        if z_raw is None:
            continue
        profile = build_profile(stroke.coords, z_raw, params.profile, stroke.structures())
        source = (source_by_stroke or {}).get(stroke.id, elevation_source)
        segments.extend(detect_stroke(stroke, profile, params, source))
    unique = deduplicate(segments, params.detection.dedup, params.profile.step_m)
    return sorted(assign_unique_ids(unique), key=lambda s: (s.kind.value, -s.score, s.id))
