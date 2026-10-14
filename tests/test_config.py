from pathlib import Path

import pytest

from flat_segments import config as cfg
from flat_segments.params import FlatParams, PipelineParams, ProfileParams

DEFAULT_TOML = Path(__file__).resolve().parents[1] / "configs" / "default.toml"


def test_default_file_is_in_sync_with_params() -> None:
    assert DEFAULT_TOML.read_text(encoding="utf-8") == cfg.params_to_toml(PipelineParams())
    assert cfg.load_params(DEFAULT_TOML) == PipelineParams()


def test_round_trip_of_modified_params() -> None:
    params = cfg.apply_overrides(
        PipelineParams(), ["flat.target_lengths_m=[300, 600]", "profile.step_m=2.5"]
    )
    assert params.detection.flat.target_lengths_m == (300.0, 600.0)
    assert params.profile.step_m == 2.5
    assert cfg.params_from_dict(cfg.params_to_dict(params)) == params


def test_partial_file_keeps_defaults(tmp_path: Path) -> None:
    path = tmp_path / "p.toml"
    path.write_text("[detection.flat]\nmax_local_grade_pct = 1.5\n")
    params = cfg.load_params(path)
    assert params.detection.flat.max_local_grade_pct == 1.5
    assert params.detection.flat.max_sinuosity == FlatParams().max_sinuosity
    assert params.profile == ProfileParams()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[profile]\nstep = 5\n", "unknown parameter.*profile.step"),
        ("[profile]\nstep_m = 'five'\n", "expected a number"),
        ("[profile]\nstep_m = true\n", "expected a number"),
        ("[detection.flat]\ntarget_lengths_m = 200\n", "list of numbers"),
        ("[profile]\nstep_m = -1\n", "must be positive"),
        ("profile = 3\n", "expected a table"),
        ("[profile\n", "p.toml"),
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "p.toml"
    path.write_text(content)
    with pytest.raises(cfg.ConfigError, match=message):
        cfg.load_params(path)


def test_overrides_accept_shortcuts_and_full_paths() -> None:
    params = cfg.apply_overrides(
        PipelineParams(),
        ["climb.min_mean_grade_pct=4", "detection.dedup.buffer_m=15", "flat.weights.lighting=0.2"],
    )
    assert params.detection.climb.min_mean_grade_pct == 4.0
    assert params.detection.dedup.buffer_m == 15.0
    assert params.detection.flat.weights.lighting == 0.2


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ("flat.max_local_grade_pct", "key=value"),
        ("flat.nope=1", "unknown parameter: detection.flat.nope"),
        ("nope.x=1", "unknown parameter group"),
        ("flat=1", "unknown parameter"),
        ("flat.max_sinuosity=[", "invalid value"),
        ("climb.min_mean_grade_pct=60", "min_mean_grade_pct <= max_mean_grade_pct"),
    ],
)
def test_invalid_overrides(override: str, message: str) -> None:
    with pytest.raises(cfg.ConfigError, match=message):
        cfg.apply_overrides(PipelineParams(), [override])


def test_params_validate_invariants() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        FlatParams(target_lengths_m=())
    with pytest.raises(ValueError, match="greater than 1"):
        FlatParams(max_sinuosity=1.0)
