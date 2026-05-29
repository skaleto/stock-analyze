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
| Factor direction outside `high/low`  | OverlayInvalidDirection      |
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
CLASSIC_FACTORS: frozenset[str] = frozenset(
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

# Per-market factor whitelists. The 'a_share' set is the union of
# A-share's classic factors + the sentiment alt-factors introduced by
# OpenSpec change ``add-llm-sentiment-alpha-factor``. Note that
# ``validate_factor_name`` still enforces the cross-agent prefix rule
# via ``AGENT_ALT_FACTOR_PATTERN`` — appearing in the whitelist is not
# sufficient on its own to bypass the transparency boundary.
AVAILABLE_FACTORS_BY_MARKET: dict[str, set[str]] = {
    "a_share": {
        # Classic per-stock factors
        "pe", "pb", "roe", "gross_margin", "debt_ratio",
        "net_profit_growth", "momentum_20", "momentum_60",
        "low_volatility_60", "dividend_yield",
        # Broadcast alt-factors (one per agent — overlay_guard's
        # cross-agent rule still rejects mismatched prefixes).
        "claude_market_sentiment_1w",
        "codex_market_sentiment_1w",
        # Phase 3 per-stock sector-sentiment alt-factors (industry-level).
        "claude_sector_sentiment",
        "codex_sector_sentiment",
    },
    "hk": {
        # v1 factor set (Phase 2): 6 factors derivable from yfinance.info
        # + price history. ROE / gross_margin / debt_ratio /
        # net_profit_growth are deferred to v2 (require quarterly_financials
        # DataFrame parsing). Sentiment alt-factors also deferred to v2.
        "pe", "pb", "momentum_20", "momentum_60",
        "low_volatility_60", "dividend_yield",
    },
    "us": {
        # v1 factor set (Phase 3): same 6-factor set as HK.
        "pe", "pb", "momentum_20", "momentum_60",
        "low_volatility_60", "dividend_yield",
    },
}

# Backwards-compat alias for code paths that still reference the old
# flat ``AVAILABLE_FACTORS`` name. New code uses
# ``AVAILABLE_FACTORS_BY_MARKET[market]``.
AVAILABLE_FACTORS = AVAILABLE_FACTORS_BY_MARKET["a_share"]

# Agent-specific alt-factor naming convention: ``<agent_id>_market_sentiment_1w``
# (and future ``<agent_id>_*`` factors). The agent prefix in the factor name
# MUST match the calling agent_id — claude cannot reference codex's
# sentiment factor (transparency rule, see CLAUDE.md §7.1).
import re as _re_module  # local alias to avoid shadowing
# Matches both the broadcast market factor (Phase 1) and the per-stock
# sector-sentiment factor (Phase 3). The captured agent prefix drives the
# cross-agent rule: claude cannot reference codex_* and vice versa.
AGENT_ALT_FACTOR_PATTERN = _re_module.compile(
    r"^(claude|codex)_(market_sentiment_1w|sector_sentiment)$"
)

# Top-level keys permitted in an agent overlay. Anything else raises
# ``OverlayUnknownTopLevelKey``. Mirrors ``competition.OVERLAY_ALLOWED_TOP_LEVEL``
# but is duplicated here so the guard is self-contained even when called
# without a baseline.
ALLOWED_TOP_LEVEL: frozenset[str] = competition.OVERLAY_ALLOWED_TOP_LEVEL


class OverlayGuardError(RuntimeError):
    """Base class for all overlay-guard violations."""


class OverlayUnknownMarket(OverlayGuardError):
    """Overlay validation requested for a market not in
    ``AVAILABLE_FACTORS_BY_MARKET``.
    """

    def __init__(self, market: str, known_markets: list[str]) -> None:
        super().__init__(
            f"overlay_unknown_market:{market!r} "
            f"(known_markets={known_markets})"
        )
        self.market = market
        self.known_markets = known_markets


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


class OverlayCrossAgentFactor(OverlayGuardError):
    """Overlay references an agent-prefixed alt-factor whose agent_id differs.

    e.g. ``claude.yaml`` referencing ``codex_market_sentiment_1w``. Each
    agent's alt-factor data is private to that agent (transparency rule,
    CLAUDE.md §7.1).
    """

    def __init__(self, name: str, agent_id: str) -> None:
        super().__init__(
            f"overlay_cross_agent_factor:{name} "
            f"(agent_id={agent_id!r} cannot reference another agent's alt-factor)"
        )
        self.name = name
        self.agent_id = agent_id


def validate_factor_name(
    name: str,
    agent_id: str,
    *,
    factors_whitelist: set[str] | frozenset[str] | None = None,
) -> None:
    """Raise the appropriate exception if ``name`` is unsupported for ``agent_id``.

    - Classic factor (in ``CLASSIC_FACTORS``)  -> ok
    - ``<agent_id>_*`` matching the alt-factor pattern  -> ok
    - ``<other>_*`` matching the pattern  -> raise OverlayCrossAgentFactor
    - Anything else  -> raise OverlayUnknownFactor

    When ``factors_whitelist`` is provided, names not in that set are
    rejected (modulo the alt-factor cross-agent rule, which is enforced
    via ``AGENT_ALT_FACTOR_PATTERN`` so a whitelisted but
    wrong-agent-prefixed alt-factor still raises
    ``OverlayCrossAgentFactor``). When ``factors_whitelist`` is ``None``,
    falls back to ``CLASSIC_FACTORS`` for backwards compatibility.
    """
    whitelist = factors_whitelist if factors_whitelist is not None else CLASSIC_FACTORS
    if name in whitelist:
        # Could be a classic factor or a whitelisted alt-factor; in the
        # latter case the cross-agent prefix rule below still applies.
        match = AGENT_ALT_FACTOR_PATTERN.match(name)
        if match and match.group(1) != agent_id:
            raise OverlayCrossAgentFactor(name=name, agent_id=agent_id)
        return
    # Not in whitelist: alt-factor naming may still be admitted via the
    # cross-agent pattern (matching legacy behaviour when the default
    # ``CLASSIC_FACTORS`` whitelist is in effect and the sentiment factors
    # are not listed there).
    match = AGENT_ALT_FACTOR_PATTERN.match(name)
    if not match:
        raise OverlayUnknownFactor(name=name, whitelist=frozenset(whitelist))
    factor_agent = match.group(1)
    if factor_agent != agent_id:
        raise OverlayCrossAgentFactor(name=name, agent_id=agent_id)


class OverlayInvalidWeight(OverlayGuardError):
    """Factor weight is not a finite number in `[0, 1]`."""

    def __init__(self, name: str, weight: Any) -> None:
        super().__init__(
            f"overlay_invalid_weight:factors.{name}.weight "
            f"(value={weight!r}; expected float in [0, 1])"
        )
        self.name = name
        self.weight = weight


class OverlayInvalidDirection(OverlayGuardError):
    """Factor direction is not one of the two supported values."""

    def __init__(self, name: str, direction: Any) -> None:
        super().__init__(
            f"overlay_invalid_direction:factors.{name}.direction "
            f"(value={direction!r}; expected 'high' or 'low')"
        )
        self.name = name
        self.direction = direction


def validate(
    agent_id: str,
    overlay: dict[str, Any] | str | Path,
    repo_root: str | Path | None = None,
    baseline: dict[str, Any] | None = None,
    *,
    market: str = "a_share",
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
        Repo root used to locate ``configs/competition_<market>.yaml`` if
        ``baseline`` is not provided. Defaults to ``Path.cwd()``.
    baseline:
        Optional pre-loaded baseline mapping (skip disk read).
    market:
        Selects the per-market factor whitelist
        (``AVAILABLE_FACTORS_BY_MARKET[market]``). Keyword-only; defaults
        to ``"a_share"`` so existing call sites continue to work
        unchanged. Unknown markets raise ``OverlayUnknownMarket``.

    Raises
    ------
    OverlayInvalidYAML, OverlaySchemaError, OverlayUnknownTopLevelKey,
    OverlayBaselineLocked, OverlayUnknownFactor, OverlayInvalidWeight,
    OverlayUnknownMarket
        One of the guard exceptions on any violation.

    Returns
    -------
    None
        On success the function returns ``None``. No disk I/O is performed
        beyond the optional baseline read.
    """

    factors_whitelist = AVAILABLE_FACTORS_BY_MARKET.get(market)
    if factors_whitelist is None:
        raise OverlayUnknownMarket(
            market=market,
            known_markets=sorted(AVAILABLE_FACTORS_BY_MARKET.keys()),
        )

    parsed = _parse_overlay(overlay)
    _validate_top_level(parsed)
    _validate_agent_id(parsed, agent_id)
    _validate_baseline_locks(parsed, repo_root=repo_root, baseline=baseline)
    _validate_factors(parsed, agent_id=agent_id, factors_whitelist=factors_whitelist)


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


def _validate_factors(
    overlay: dict[str, Any],
    *,
    agent_id: str,
    factors_whitelist: set[str] | frozenset[str] | None = None,
) -> None:
    factors = overlay.get("factors")
    if factors is None:
        return
    if not isinstance(factors, dict):
        raise OverlaySchemaError(
            "overlay_schema_error:factors_must_be_mapping"
        )
    for name, spec in factors.items():
        validate_factor_name(
            name,
            agent_id=agent_id,
            factors_whitelist=factors_whitelist,
        )  # Tasks 6/7: alt-factor + cross-agent + per-market whitelist
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
        if "direction" in spec:
            direction = spec.get("direction")
            if direction not in {"high", "low"}:
                raise OverlayInvalidDirection(name=name, direction=direction)


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))
