"""Training-only signed IC stability selection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SignedICConfig:
    minimum_coverage: float = 0.70
    minimum_abs_mean_ic: float = 0.01
    minimum_monthly_positive_rate: float = 0.55
    minimum_subperiod_agreement: float = 2 / 3
    bootstrap_samples: int = 1000
    bootstrap_block_length: int = 20
    fdr_q: float = 0.10
    redundancy_threshold: float = 0.80
    max_features: int = 8
    max_per_family: int = 2


@dataclass(frozen=True)
class FeatureICAudit:
    feature: str
    family: str
    coverage: float
    mean_ic: float
    direction: int
    signed_mean_ic: float
    bootstrap_lower: float
    bootstrap_upper: float
    p_value: float
    fdr_passed: bool
    monthly_positive_rate: float
    subperiod_agreement: float
    eligible: bool
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SignedICSelection:
    selected_features: tuple[str, ...]
    directions: Mapping[str, int]
    weights: Mapping[str, float]
    audits: tuple[FeatureICAudit, ...]

    def transform(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy()
        for feature in self.selected_features:
            result[feature] = (
                pd.to_numeric(result[feature], errors="coerce")
                * int(self.directions[feature])
            )
        return result


def _daily_ic(frame: pd.DataFrame, feature: str) -> pd.DataFrame:
    rows = []
    for day, group in frame.groupby("trade_date", sort=True):
        x = pd.to_numeric(group[feature], errors="coerce")
        y = pd.to_numeric(group["excess_return"], errors="coerce")
        valid = x.notna() & y.notna()
        if int(valid.sum()) < 3 or x.loc[valid].nunique() < 2 or y.loc[valid].nunique() < 2:
            continue
        value = x.loc[valid].corr(y.loc[valid], method="spearman")
        if pd.notna(value):
            rows.append({"trade_date": str(day), "ic": float(value)})
    return pd.DataFrame(rows)


def _block_bootstrap(
    values: np.ndarray,
    *,
    samples: int,
    block_length: int,
    seed: int,
) -> tuple[float, float, float]:
    if values.size < 2:
        return 0.0, 0.0, 1.0
    rng = np.random.default_rng(seed)
    block = max(1, min(int(block_length), values.size))
    means = np.empty(int(samples), dtype=float)
    starts_high = max(1, values.size - block + 1)
    count = int(np.ceil(values.size / block))
    for sample in range(int(samples)):
        starts = rng.integers(0, starts_high, size=count)
        draw = np.concatenate([values[start:start + block] for start in starts])
        means[sample] = float(np.mean(draw[: values.size]))
    return (
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
        float((np.sum(means <= 0.0) + 1) / (len(means) + 1)),
    )


def _bh_passes(p_values: Mapping[str, float], q: float) -> set[str]:
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    cutoff = -1
    total = len(ordered)
    for rank, (_, value) in enumerate(ordered, start=1):
        if float(value) <= float(q) * rank / max(total, 1):
            cutoff = rank
    return {name for name, _ in ordered[:cutoff]} if cutoff > 0 else set()


def _initial_audit(
    frame: pd.DataFrame,
    feature: str,
    family: str,
    config: SignedICConfig,
    seed: int,
) -> FeatureICAudit:
    values = pd.to_numeric(frame[feature], errors="coerce")
    coverage = float(values.notna().mean())
    daily = _daily_ic(frame, feature)
    mean_ic = float(daily["ic"].mean()) if not daily.empty else 0.0
    direction = 1 if mean_ic >= 0.0 else -1
    signed = daily["ic"].to_numpy(dtype=float) * direction if not daily.empty else np.asarray([], dtype=float)
    lower, upper, p_value = _block_bootstrap(
        signed,
        samples=config.bootstrap_samples,
        block_length=config.bootstrap_block_length,
        seed=seed,
    )
    if daily.empty:
        monthly_rate = 0.0
        agreement = 0.0
    else:
        months = daily["trade_date"].str.slice(0, 6)
        monthly = daily["ic"].mul(direction).groupby(months).mean()
        monthly_rate = float(monthly.gt(0.0).mean())
        chunks = np.array_split(signed, 3)
        agreement = float(np.mean([
            bool(len(chunk)) and float(np.mean(chunk)) > 0.0 for chunk in chunks
        ]))
    reasons = []
    if coverage < config.minimum_coverage:
        reasons.append("low_coverage")
    if abs(mean_ic) < config.minimum_abs_mean_ic:
        reasons.append("weak_mean_ic")
    if lower <= 0.0:
        reasons.append("bootstrap_lower_nonpositive")
    if monthly_rate < config.minimum_monthly_positive_rate:
        reasons.append("monthly_sign_unstable")
    if agreement < config.minimum_subperiod_agreement:
        reasons.append("subperiod_sign_unstable")
    return FeatureICAudit(
        feature=feature,
        family=family,
        coverage=coverage,
        mean_ic=mean_ic,
        direction=direction,
        signed_mean_ic=abs(mean_ic),
        bootstrap_lower=lower,
        bootstrap_upper=upper,
        p_value=p_value,
        fdr_passed=False,
        monthly_positive_rate=monthly_rate,
        subperiod_agreement=agreement,
        eligible=False,
        rejection_reasons=tuple(reasons),
    )


def select_signed_ic_features(
    train: pd.DataFrame,
    *,
    candidate_features: Sequence[str],
    feature_families: Mapping[str, str],
    config: SignedICConfig | None = None,
    seed: int,
) -> SignedICSelection:
    """Select stable signed factors from training data only."""

    resolved = config or SignedICConfig()
    required = {"trade_date", "code", "excess_return"}
    missing = sorted(required.difference(train.columns))
    if missing:
        raise ValueError(f"signed_ic_columns:{','.join(missing)}")
    initial = []
    for number, feature in enumerate(candidate_features):
        if feature not in train.columns:
            continue
        initial.append(_initial_audit(
            train,
            feature,
            str(feature_families.get(feature) or "unclassified"),
            resolved,
            int(seed) + number,
        ))
    p_values = {
        item.feature: item.p_value
        for item in initial
        if not item.rejection_reasons
    }
    fdr_passed = _bh_passes(p_values, resolved.fdr_q)
    audits = []
    for item in initial:
        reasons = list(item.rejection_reasons)
        passed = item.feature in fdr_passed
        if not passed:
            reasons.append("fdr_rejected")
        audits.append(replace(
            item,
            fdr_passed=passed,
            eligible=not reasons,
            rejection_reasons=tuple(dict.fromkeys(reasons)),
        ))
    eligible = sorted(
        (item for item in audits if item.eligible),
        key=lambda item: (-item.bootstrap_lower, -item.signed_mean_ic, item.feature),
    )
    selected: list[FeatureICAudit] = []
    family_counts: dict[str, int] = {}
    signed_frame = pd.DataFrame(index=train.index)
    for item in eligible:
        if family_counts.get(item.family, 0) >= resolved.max_per_family:
            audits[audits.index(item)] = replace(
                item, eligible=False, rejection_reasons=("family_cap",)
            )
            continue
        candidate = pd.to_numeric(train[item.feature], errors="coerce") * item.direction
        redundant_with = None
        for kept in selected:
            correlation = candidate.corr(
                pd.to_numeric(train[kept.feature], errors="coerce") * kept.direction,
                method="spearman",
            )
            if pd.notna(correlation) and abs(float(correlation)) > resolved.redundancy_threshold:
                redundant_with = kept.feature
                break
        if redundant_with is not None:
            audits[audits.index(item)] = replace(
                item,
                eligible=False,
                rejection_reasons=(f"redundant_with:{redundant_with}",),
            )
            continue
        selected.append(item)
        signed_frame[item.feature] = candidate
        family_counts[item.family] = family_counts.get(item.family, 0) + 1
        if len(selected) >= resolved.max_features:
            break
    raw_weights = {
        item.feature: max(item.signed_mean_ic, 1e-12)
        for item in selected
    }
    total = sum(raw_weights.values())
    weights = {
        feature: value / total for feature, value in raw_weights.items()
    } if total > 0.0 else {}
    return SignedICSelection(
        selected_features=tuple(item.feature for item in selected),
        directions={item.feature: item.direction for item in selected},
        weights=weights,
        audits=tuple(audits),
    )


__all__ = [
    "FeatureICAudit",
    "SignedICConfig",
    "SignedICSelection",
    "select_signed_ic_features",
]
