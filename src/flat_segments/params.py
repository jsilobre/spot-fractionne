"""Algorithm parameters and their default values.

Every default here is documented in ``docs/algorithm.md`` (section 13); keep
both in sync. All parameter objects are frozen dataclasses so that a run is
fully described by one immutable :class:`PipelineParams` value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Pilot area (Labège / Caraman), WGS84 ``(min_lon, min_lat, max_lon, max_lat)``.
PILOT_BBOX_WGS84: tuple[float, float, float, float] = (1.48, 43.48, 1.80, 43.59)

#: CRS used for every computation in the pipeline (Lambert-93, metres).
WORK_CRS = "EPSG:2154"

#: CRS of the web export (GeoJSON, RFC 7946).
WEB_CRS = "EPSG:4326"


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _check_targets(group: str, targets: tuple[float, ...]) -> None:
    _check(len(targets) > 0, f"{group}.target_lengths_m must not be empty")
    _check(all(t > 0 for t in targets), f"{group}.target_lengths_m must be positive")


@dataclass(frozen=True, slots=True)
class NetworkParams:
    """Parameters for chaining OSM ways into strokes (algorithm.md section 2).

    Attributes:
        max_deflection_deg: Maximum deviation from a straight line for a stroke
            to continue through a junction of degree >= 3.
        bearing_probe_m: Distance along an edge used to measure its bearing
            when leaving a node.
    """

    max_deflection_deg: float = 35.0
    bearing_probe_m: float = 15.0

    def __post_init__(self) -> None:
        _check(
            0 <= self.max_deflection_deg <= 180, "network.max_deflection_deg must be in [0, 180]"
        )
        _check(self.bearing_probe_m > 0, "network.bearing_probe_m must be positive")


@dataclass(frozen=True, slots=True)
class ProfileParams:
    """Parameters for elevation sampling and profile smoothing (sections 3-6).

    Attributes:
        step_m: Requested sampling step along strokes. The effective step is
            ``length / ceil(length / step_m)``.
        lateral_offset_m: When > 0, also sample at +/- this distance across
            the line and keep the median of the three values. 0 disables it.
        structure_margin_m: Margin added on both sides of bridges and tunnels
            before interpolating the elevation across them.
        max_gap_fill_m: Longest DEM nodata gap that is linearly interpolated.
        median_window_m: Width of the rolling median (spike removal).
        gaussian_sigma_m: Standard deviation of the Gaussian smoothing.
        grade_base_m: Baseline of the centred difference used for local grade.
        gain_hysteresis_m: Hysteresis threshold for cumulative gain / loss.
    """

    step_m: float = 5.0
    lateral_offset_m: float = 0.0
    structure_margin_m: float = 5.0
    max_gap_fill_m: float = 20.0
    median_window_m: float = 15.0
    gaussian_sigma_m: float = 10.0
    grade_base_m: float = 20.0
    gain_hysteresis_m: float = 0.5

    def __post_init__(self) -> None:
        for name in ("step_m", "grade_base_m"):
            _check(getattr(self, name) > 0, f"profile.{name} must be positive")
        for name in (
            "lateral_offset_m",
            "structure_margin_m",
            "max_gap_fill_m",
            "median_window_m",
            "gaussian_sigma_m",
            "gain_hysteresis_m",
        ):
            _check(getattr(self, name) >= 0, f"profile.{name} must not be negative")


@dataclass(frozen=True, slots=True)
class FlatScoreWeights:
    """Weights of the flat-segment score components (section 10)."""

    flatness: float = 0.35
    straightness: float = 0.20
    crossings: float = 0.25
    surface: float = 0.10
    lighting: float = 0.05
    length: float = 0.05


@dataclass(frozen=True, slots=True)
class ClimbScoreWeights:
    """Weights of the climb score components (section 10)."""

    regularity: float = 0.35
    crossings: float = 0.30
    straightness: float = 0.10
    surface: float = 0.15
    lighting: float = 0.05
    length: float = 0.05


@dataclass(frozen=True, slots=True)
class FlatParams:
    """Criteria for flat segments (section 7.1).

    Attributes:
        target_lengths_m: Target lengths; the shortest is the detection window.
        max_mean_grade_pct: Maximum absolute mean grade of a window.
        max_local_grade_pct: Maximum absolute local grade inside a window.
        max_sinuosity: Maximum length / chord ratio of a window.
        weights: Score weights.
    """

    target_lengths_m: tuple[float, ...] = (200.0, 400.0, 1000.0)
    max_mean_grade_pct: float = 1.0
    max_local_grade_pct: float = 2.0
    max_sinuosity: float = 1.2
    weights: FlatScoreWeights = field(default_factory=FlatScoreWeights)

    def __post_init__(self) -> None:
        _check_targets("flat", self.target_lengths_m)
        _check(self.max_mean_grade_pct > 0, "flat.max_mean_grade_pct must be positive")
        _check(self.max_local_grade_pct > 0, "flat.max_local_grade_pct must be positive")
        _check(self.max_sinuosity > 1, "flat.max_sinuosity must be greater than 1")


@dataclass(frozen=True, slots=True)
class ClimbParams:
    """Criteria for climbs (section 7.2).

    Attributes:
        target_lengths_m: Target lengths; the shortest is the detection window.
        min_mean_grade_pct: Minimum mean grade (in the climbing direction).
        max_mean_grade_pct: Maximum mean grade (in the climbing direction).
        min_local_grade_pct: Below this local grade, a sample counts as flat.
        max_flat_stretch_m: Longest flat stretch tolerated inside a window.
        max_sinuosity: Maximum length / chord ratio of a window.
        weights: Score weights.
    """

    target_lengths_m: tuple[float, ...] = (100.0, 200.0, 400.0, 800.0)
    min_mean_grade_pct: float = 3.0
    max_mean_grade_pct: float = 15.0
    min_local_grade_pct: float = 1.0
    max_flat_stretch_m: float = 20.0
    max_sinuosity: float = 1.5
    weights: ClimbScoreWeights = field(default_factory=ClimbScoreWeights)

    def __post_init__(self) -> None:
        _check_targets("climb", self.target_lengths_m)
        _check(
            0 < self.min_mean_grade_pct <= self.max_mean_grade_pct,
            "climb grades must satisfy 0 < min_mean_grade_pct <= max_mean_grade_pct",
        )
        _check(self.max_flat_stretch_m >= 0, "climb.max_flat_stretch_m must not be negative")
        _check(self.max_sinuosity > 1, "climb.max_sinuosity must be greater than 1")


def _long_climb_params() -> ClimbParams:
    """Long climbs (section 7.3): replats and bends tolerated, 1 km and more."""
    return ClimbParams(
        target_lengths_m=(1000.0, 1500.0, 2000.0, 3000.0),
        min_mean_grade_pct=2.5,
        max_flat_stretch_m=150.0,
        max_sinuosity=3.0,
    )


@dataclass(frozen=True, slots=True)
class DedupParams:
    """Cross-stroke deduplication (section 11).

    Attributes:
        buffer_m: Distance under which a point is considered covered.
        max_overlap: Covered fraction above which a candidate is dropped.
    """

    buffer_m: float = 20.0
    max_overlap: float = 0.5

    def __post_init__(self) -> None:
        _check(self.buffer_m >= 0, "dedup.buffer_m must not be negative")
        _check(0 <= self.max_overlap <= 1, "dedup.max_overlap must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class DetectionParams:
    """Sliding-window detection parameters (sections 7-11)."""

    window_step_m: float = 10.0
    flat: FlatParams = field(default_factory=FlatParams)
    climb: ClimbParams = field(default_factory=ClimbParams)
    long_climb: ClimbParams = field(default_factory=_long_climb_params)
    dedup: DedupParams = field(default_factory=DedupParams)

    def __post_init__(self) -> None:
        _check(self.window_step_m > 0, "detection.window_step_m must be positive")


@dataclass(frozen=True, slots=True)
class PipelineParams:
    """All parameters of a pipeline run."""

    network: NetworkParams = field(default_factory=NetworkParams)
    profile: ProfileParams = field(default_factory=ProfileParams)
    detection: DetectionParams = field(default_factory=DetectionParams)
