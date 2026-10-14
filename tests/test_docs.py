"""Keep docs/algorithm.md (parameter table) in sync with params.py."""

import re
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from flat_segments.params import PipelineParams

ALGORITHM_MD = Path(__file__).resolve().parents[1] / "docs" / "algorithm.md"
ROW = re.compile(r"^\| `(?P<name>[a-z_]+\.[a-z_]+)` \| (?P<value>[^|]+) \|", re.MULTILINE)
NUMBER = re.compile(r"\d+(?:,\d+)?")  # French decimal comma


def documented_parameters() -> dict[str, tuple[float, ...]]:
    table = ALGORITHM_MD.read_text(encoding="utf-8").split("## 13.")[1].split("\n## ")[0]
    return {
        m["name"]: tuple(float(n.replace(",", ".")) for n in NUMBER.findall(m["value"]))
        for m in ROW.finditer(table)
    }


def parameter_groups() -> dict[str, Any]:
    params = PipelineParams()
    return {
        "network": params.network,
        "profile": params.profile,
        "detection": params.detection,
        "flat": params.detection.flat,
        "climb": params.detection.climb,
        "long_climb": params.detection.long_climb,
        "dedup": params.detection.dedup,
    }


def actual_value(name: str) -> tuple[float, ...]:
    group, field = name.split(".")
    value = getattr(parameter_groups()[group], field)
    return tuple(float(v) for v in value) if isinstance(value, tuple) else (float(value),)


DOCUMENTED = documented_parameters()


def test_every_numeric_parameter_is_documented() -> None:
    expected = {
        f"{group}.{f.name}"
        for group, holder in parameter_groups().items()
        for f in fields(holder)
        if isinstance(getattr(holder, f.name), (int, float, tuple))
    }
    assert expected == set(DOCUMENTED)


@pytest.mark.parametrize("name", sorted(DOCUMENTED))
def test_documented_default_matches_code(name: str) -> None:
    assert DOCUMENTED[name] == pytest.approx(actual_value(name))
