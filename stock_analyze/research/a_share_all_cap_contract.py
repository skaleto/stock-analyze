"""Immutable loader for the research-only A-share all-cap contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, NoReturn

from ..config import load_config


_FROZEN_SIZE_BOUNDARIES = (300, 800, 1800, 3800)
_FROZEN_HOLDOUT_POLICY = (
    "open_once_after_data_code_and_development_gates"
)
_MINIMUM_STORAGE_FREE_FRACTION = 0.15


@dataclass(frozen=True)
class SleeveContract:
    name: str
    rank_min: int
    rank_max: int | None
    benchmark: str
    capital_weight: float


@dataclass(frozen=True)
class AllCapContract:
    campaign_id: str
    development_start: date
    development_end: date
    holdout_start: date
    holdout_end: date
    holdout_policy: str
    size_boundaries: tuple[int, int, int, int]
    boundary_buffer_fraction: float
    sleeves: tuple[SleeveContract, ...]
    raw: Mapping[str, Any]


def _invalid(field: str) -> NoReturn:
    raise ValueError(f"all_cap_contract:{field}")


def _require_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(field)
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(field)
    return value.strip()


def _require_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid(field)
    return value


def _require_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _invalid(field)
    parsed = float(value)
    if not math.isfinite(parsed):
        _invalid(field)
    return parsed


def _require_date(value: object) -> date:
    if not isinstance(value, str):
        _invalid("windows")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _invalid("windows")
    if parsed.isoformat() != value:
        _invalid("windows")
    return parsed


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def parse_all_cap_contract(payload: Mapping[str, Any]) -> AllCapContract:
    """Parse and fail closed on changes to the frozen research contract."""

    source = _require_mapping(payload, "root")
    if source.get("research_only") is not True:
        _invalid("research_only")

    windows = _require_mapping(source.get("windows"), "windows")
    development_start = _require_date(windows.get("development_start"))
    development_end = _require_date(windows.get("development_end"))
    holdout_start = _require_date(windows.get("holdout_start"))
    holdout_end = _require_date(windows.get("holdout_end"))
    if not (
        development_start <= development_end
        < holdout_start <= holdout_end
    ):
        _invalid("windows")

    holdout_policy = _require_string(
        windows.get("holdout_policy"),
        "holdout_policy",
    )
    if holdout_policy != _FROZEN_HOLDOUT_POLICY:
        _invalid("holdout_policy")

    universe = _require_mapping(source.get("universe"), "universe")
    raw_boundaries = universe.get("size_rank_boundaries")
    if not isinstance(raw_boundaries, (list, tuple)):
        _invalid("size_boundaries")
    boundaries = tuple(
        _require_int(value, "size_boundaries")
        for value in raw_boundaries
    )
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        _invalid("size_boundaries")
    if boundaries != _FROZEN_SIZE_BOUNDARIES:
        _invalid("size_boundaries")

    boundary_buffer_fraction = _require_float(
        universe.get("boundary_buffer_fraction"),
        "boundary_buffer_fraction",
    )

    raw_sleeves = _require_mapping(source.get("sleeves"), "sleeves")
    sleeves: list[SleeveContract] = []
    for raw_name, raw_sleeve in raw_sleeves.items():
        name = _require_string(raw_name, "sleeves")
        sleeve = _require_mapping(raw_sleeve, "sleeves")
        rank_min = _require_int(sleeve.get("rank_min"), "sleeve_boundaries")
        raw_rank_max = sleeve.get("rank_max")
        rank_max = (
            None
            if raw_rank_max is None
            else _require_int(raw_rank_max, "sleeve_boundaries")
        )
        benchmark = _require_string(sleeve.get("benchmark"), "benchmark")
        capital_weight = _require_float(
            sleeve.get("capital_weight"),
            "capital_weights",
        )
        if capital_weight < 0.0:
            _invalid("capital_weights")
        sleeves.append(
            SleeveContract(
                name=name,
                rank_min=rank_min,
                rank_max=rank_max,
                benchmark=benchmark,
                capital_weight=capital_weight,
            )
        )

    sleeves.sort(key=lambda item: item.rank_min)
    if len(sleeves) != len(boundaries):
        _invalid("sleeve_boundaries")
    for index, (sleeve, boundary) in enumerate(zip(sleeves, boundaries)):
        expected_min = 1 if index == 0 else boundaries[index - 1] + 1
        if sleeve.rank_min != expected_min or sleeve.rank_max != boundary:
            _invalid("sleeve_boundaries")

    if not math.isclose(
        math.fsum(sleeve.capital_weight for sleeve in sleeves),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _invalid("capital_weights")

    storage = _require_mapping(source.get("storage"), "storage")
    storage_free_fraction = _require_float(
        storage.get("minimum_filesystem_free_fraction_after_publish"),
        "storage_free_space",
    )
    if not _MINIMUM_STORAGE_FREE_FRACTION <= storage_free_fraction <= 1.0:
        _invalid("storage_free_space")

    return AllCapContract(
        campaign_id=_require_string(source.get("campaign_id"), "campaign_id"),
        development_start=development_start,
        development_end=development_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        holdout_policy=holdout_policy,
        size_boundaries=_FROZEN_SIZE_BOUNDARIES,
        boundary_buffer_fraction=boundary_buffer_fraction,
        sleeves=tuple(sleeves),
        raw=_freeze(source),
    )


def load_all_cap_contract(path: str | Path) -> AllCapContract:
    return parse_all_cap_contract(load_config(path))
