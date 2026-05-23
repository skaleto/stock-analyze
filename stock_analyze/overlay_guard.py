"""Overlay guard for LLM-direct strategy evolution.

The guard validates the **shape** of an agent overlay before it lands on
disk. Strategy quality (returns / IR / style drift / overfitting) is **not**
judged here — that responsibility lives with the LLM agent itself, by
explicit human authorisation 2026-05-23.

Six checks:

| Check                                | Exception                    |
|--------------------------------------|------------------------------|
| Top-level keys outside whitelist     | OverlayUnknownTopLevelKey    |
| Baseline-locked field touched        | OverlayBaselineLocked        |
| Unknown factor name                  | OverlayUnknownFactor         |
| Factor weight outside `[0, 1]`       | OverlayInvalidWeight         |
| Mapping cannot be parsed             | OverlayInvalidYAML           |
| Generic structural / type mismatch   | OverlaySchemaError           |

Call :func:`validate` with the in-memory overlay dict. On success it
returns ``None``. On failure it raises one of the six exceptions above
with the offending field path baked into the message.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import competition


# Whitelist of overlay-permitted factor names. Kept in sync with
# `proposal_judge.KNOWN_FACTORS` (which this module replaces) and with the
# factor columns produced by ``data_provider``. Until the codebase grows a
# canonical ``data_provider.AVAILABLE_FACTORS`` constant, we keep the
# whitelist here as the single source of truth.
AVAILABLE_FACTORS: frozenset[str] = frozenset(
    {
        "pe",
        "pb",
        "roe",
        "gross_margin",
        "debt_ratio",
        "net_profit_growth",
        "momentum_20",
        "momentum_60",
        "low_volatility_60",
        "dividend_yield",
    }
)

# Top-level keys permitted in an agent overlay. Anything else raises
# ``OverlayUnknownTopLevelKey``. Mirrors ``competition.OVERLAY_ALLOWED_TOP_LEVEL``
# but is duplicated here so the guard is self-contained even when called
# without a baseline.
ALLOWED_TOP_LEVEL: frozenset[str] = competition.OVERLAY_ALLOWED_TOP_LEVEL


class OverlayGuardError(RuntimeError):
    """Base class for all overlay-guard violations."""


class OverlaySchemaError(OverlayGuardError):
    """Generic schema mismatch (wrong type, missing required nested field)."""


class OverlayInvalidYAML(OverlayGuardError):
    """Overlay file is not valid JSON/YAML and cannot be parsed."""


class OverlayUnknownTopLevelKey(OverlayGuardError):
    """Overlay declares a top-level key outside the allowed whitelist."""


class OverlayBaselineLocked(OverlayGuardError):
    """Overlay tries to override a baseline-locked field."""

    def __init__(self, field: str, baseline_value: Any, overlay_value: Any) -> None:
        super().__init__(
            f"overlay_baseline_locked:{field} "
            f"(baseline={baseline_value!r}, overlay={overlay_value!r})"
        )
        self.field = field
        self.baseline_value = baseline_value
        self.overlay_value = overlay_value


class OverlayUnknownFactor(OverlayGuardError):
    """Overlay references a factor name not in the whitelist."""

    def __init__(self, name: str, whitelist: frozenset[str]) -> None:
        super().__init__(
            f"overlay_unknown_factor:{name} "
            f"(whitelist={sorted(whitelist)})"
        )
        self.name = name
        self.whitelist = whitelist


class OverlayInvalidWeight(OverlayGuardError):
    """Factor weight is not a finite number in `[0, 1]`."""

    def __init__(self, name: str, weight: Any) -> None:
        super().__init__(
            f"overlay_invalid_weight:factors.{name}.weight "
            f"(value={weight!r}; expected float in [0, 1])"
        )
        self.name = name
        self.weight = weight


def validate(
    agent_id: str,
    overlay: dict[str, Any] | str | Path,
    repo_root: str | Path | None = None,
    baseline: dict[str, Any] | None = None,
) -> None:
    """Run all guard checks on the given overlay.

    Parameters
    ----------
    agent_id:
        Identity recorded on errors (the file's ``agent_id`` key is
        independently verified to match).
    overlay:
        Either an already-parsed dict, a path to a JSON/YAML file, or a
        raw JSON/YAML string. Path / string inputs go through
        :func:`_parse_overlay` so file-only callers don't have to re-implement
        the parser.
    repo_root:
        Repo root used to locate ``configs/competition.yaml`` if ``baseline``
        is not provided. Defaults to ``Path.cwd()``.
    baseline:
        Optional pre-loaded baseline mapping (skip disk read).

    Raises
    ------
    OverlayInvalidYAML, OverlaySchemaError, OverlayUnknownTopLevelKey,
    OverlayBaselineLocked, OverlayUnknownFactor, OverlayInvalidWeight
        One of the six guard exceptions on any violation.

    Returns
    -------
    None
        On success the function returns ``None``. No disk I/O is performed
        beyond the optional baseline read.
    """

    parsed = _parse_overlay(overlay)
    _validate_top_level(parsed)
    _validate_agent_id(parsed, agent_id)
    _validate_baseline_locks(parsed, repo_root=repo_root, baseline=baseline)
    _validate_factors(parsed)


# ---------------------------------------------------------------------------
# Internals


def _parse_overlay(overlay: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(overlay, dict):
        return overlay
    if isinstance(overlay, (str, Path)):
        path = Path(overlay)
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = str(overlay)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError:
                raise OverlayInvalidYAML(
                    f"overlay_invalid_yaml:{exc.msg} "
                    "(file is not valid JSON; install PyYAML for non-JSON YAML)"
                ) from exc
            try:
                parsed = yaml.safe_load(text)
            except Exception as yaml_exc:  # noqa: BLE001
                raise OverlayInvalidYAML(
                    f"overlay_invalid_yaml:{yaml_exc}"
                ) from yaml_exc
        if not isinstance(parsed, dict):
            raise OverlaySchemaError(
                "overlay_schema_error:top_level_must_be_mapping"
            )
        return parsed
    raise OverlaySchemaError(
        f"overlay_schema_error:unsupported_overlay_type:{type(overlay).__name__}"
    )


def _validate_top_level(overlay: dict[str, Any]) -> None:
    extras = sorted(set(overlay.keys()) - ALLOWED_TOP_LEVEL)
    if extras:
        raise OverlayUnknownTopLevelKey(
            f"overlay_unknown_top_level_key:{extras[0]} "
            f"(allowed={sorted(ALLOWED_TOP_LEVEL)})"
        )


def _validate_agent_id(overlay: dict[str, Any], agent_id: str) -> None:
    declared = overlay.get("agent_id")
    if declared is not None and declared != agent_id:
        raise OverlaySchemaError(
            f"overlay_schema_error:agent_id_mismatch "
            f"(declared={declared!r}, expected={agent_id!r})"
        )


def _validate_baseline_locks(
    overlay: dict[str, Any],
    repo_root: str | Path | None,
    baseline: dict[str, Any] | None,
) -> None:
    """Delegate baseline-lock checks to ``competition._validate_locked_paths``.

    Wraps the existing ``CompetitionBaselineLocked`` into our
    ``OverlayBaselineLocked`` so callers only have to catch one family.
    """

    root = Path(repo_root) if repo_root else Path.cwd()
    if baseline is None:
        baseline = competition.load_baseline(root)
    try:
        competition._validate_locked_paths(baseline, overlay)
    except competition.CompetitionBaselineLocked as exc:
        raise OverlayBaselineLocked(
            field=exc.field,
            baseline_value=exc.baseline_value,
            overlay_value=exc.overlay_value,
        ) from exc


def _validate_factors(overlay: dict[str, Any]) -> None:
    factors = overlay.get("factors")
    if factors is None:
        return
    if not isinstance(factors, dict):
        raise OverlaySchemaError(
            "overlay_schema_error:factors_must_be_mapping"
        )
    for name, spec in factors.items():
        if name not in AVAILABLE_FACTORS:
            raise OverlayUnknownFactor(name=name, whitelist=AVAILABLE_FACTORS)
        if not isinstance(spec, dict):
            raise OverlaySchemaError(
                f"overlay_schema_error:factors.{name}_must_be_mapping"
            )
        if "weight" in spec:
            weight = spec.get("weight")
            if not _is_number(weight):
                raise OverlayInvalidWeight(name=name, weight=weight)
            value = float(weight)
            if value < 0.0 or value > 1.0:
                raise OverlayInvalidWeight(name=name, weight=weight)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))
