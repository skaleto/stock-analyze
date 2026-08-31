"""Verified total-return ETF history for permanent-portfolio research."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Protocol

import pandas as pd

from .contract import canonical_hash


REQUIRED_COLUMNS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "adj_factor",
    "adjusted_close",
    "is_open",
)
ACCOUNTING_COLUMN = "distribution_cash_per_share"
DISTRIBUTION_ERROR_COLUMN = "distribution_reference_error"
DISTRIBUTION_REFERENCE_TOLERANCE = 0.005
PRICE_COLUMNS = ("open", "high", "low", "close", "adjusted_close")


class PermanentPortfolioDataProvider(Protocol):
    def fund_daily(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def fund_adj(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def trade_cal(
        self,
        *,
        exchange: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...

    def suspend_d(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame: ...


def _date_key(value: object) -> str:
    key = str(value).replace("-", "")[:8]
    if len(key) != 8 or not key.isdigit():
        raise ValueError(f"permanent_portfolio_date:{value}")
    return key


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_total_return_frame(
    daily: pd.DataFrame,
    adjustment: pd.DataFrame,
    *,
    require_distribution_reference: bool = False,
) -> pd.DataFrame:
    left = daily.copy()
    right = adjustment.copy()
    for frame in (left, right):
        frame["ts_code"] = frame["ts_code"].astype(str)
        frame["trade_date"] = frame["trade_date"].map(_date_key)
    if left.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("permanent_portfolio_daily_duplicate")
    if right.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("permanent_portfolio_adjustment_duplicate")
    merged = left.merge(
        right[["ts_code", "trade_date", "adj_factor"]],
        on=["ts_code", "trade_date"],
        how="left",
        validate="one_to_one",
    )
    merged["adj_factor"] = pd.to_numeric(
        merged["adj_factor"],
        errors="coerce",
    )
    merged = merged.sort_values(["ts_code", "trade_date"]).reset_index(
        drop=True
    )
    if merged["adj_factor"].isna().any() or (merged["adj_factor"] <= 0).any():
        raise ValueError("permanent_portfolio_market_adjustment")
    previous_factor = merged.groupby("ts_code", sort=False)[
        "adj_factor"
    ].shift(1)
    previous_close = merged.groupby("ts_code", sort=False)["close"].shift(1)
    factor_change = merged["adj_factor"] / previous_factor
    if factor_change.dropna().lt(1.0 - 1e-10).any():
        raise ValueError("permanent_portfolio_distribution_factor")
    distributions = previous_close * (
        1.0 - previous_factor / merged["adj_factor"]
    )
    merged[ACCOUNTING_COLUMN] = distributions.where(
        factor_change.gt(1.0 + 1e-10),
        0.0,
    ).fillna(0.0)
    changed = factor_change.gt(1.0 + 1e-10)
    if "pre_close" in merged.columns:
        pre_close = pd.to_numeric(merged["pre_close"], errors="coerce")
        implied_reference = previous_close - merged[ACCOUNTING_COLUMN]
        reference_error = (implied_reference - pre_close).abs()
        merged[DISTRIBUTION_ERROR_COLUMN] = reference_error.where(
            changed,
            0.0,
        )
        if (
            merged.loc[changed, DISTRIBUTION_ERROR_COLUMN].isna().any()
            or merged.loc[
                changed, DISTRIBUTION_ERROR_COLUMN
            ].gt(DISTRIBUTION_REFERENCE_TOLERANCE + 1e-12).any()
        ):
            raise ValueError("permanent_portfolio_distribution_reference")
    else:
        merged[DISTRIBUTION_ERROR_COLUMN] = 0.0
        if require_distribution_reference and changed.any():
            raise ValueError("permanent_portfolio_distribution_reference")
    if (
        merged[ACCOUNTING_COLUMN].lt(-1e-10).any()
        or ~merged[ACCOUNTING_COLUMN].map(math.isfinite).all()
    ):
        raise ValueError("permanent_portfolio_distribution_amount")
    merged["adjusted_close"] = (
        pd.to_numeric(merged["close"], errors="coerce")
        * merged["adj_factor"]
    )
    return merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def _verified_zero_trade_dates(
    total_return: pd.DataFrame,
    missing_dates: set[str],
) -> set[str]:
    if not missing_dates or "pre_close" not in total_return.columns:
        return set()
    observed = total_return.sort_values("trade_date").reset_index(drop=True)
    dates = observed["trade_date"].astype(str)
    verified: set[str] = set()
    for trade_date in missing_dates:
        previous = observed.loc[dates.lt(trade_date)].tail(1)
        following = observed.loc[dates.gt(trade_date)].head(1)
        if previous.empty or following.empty:
            continue
        previous_close = pd.to_numeric(previous["close"], errors="coerce").iloc[0]
        following_pre_close = pd.to_numeric(
            following["pre_close"],
            errors="coerce",
        ).iloc[0]
        if (
            pd.notna(previous_close)
            and pd.notna(following_pre_close)
            and math.isclose(
                float(previous_close),
                float(following_pre_close),
                rel_tol=1e-9,
                abs_tol=1e-8,
            )
        ):
            verified.add(trade_date)
    return verified


def validate_market_frame(
    frame: pd.DataFrame,
    *,
    expected_codes: Iterable[str],
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(
            f"permanent_portfolio_market_schema:{','.join(missing)}"
        )
    columns = list(REQUIRED_COLUMNS)
    if ACCOUNTING_COLUMN in frame.columns:
        columns.append(ACCOUNTING_COLUMN)
    if DISTRIBUTION_ERROR_COLUMN in frame.columns:
        columns.append(DISTRIBUTION_ERROR_COLUMN)
    clean = frame.loc[:, columns].copy()
    clean["ts_code"] = clean["ts_code"].astype(str)
    clean["trade_date"] = clean["trade_date"].map(_date_key)
    if clean.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("permanent_portfolio_market_duplicate")
    observed = set(clean["ts_code"])
    expected = {str(value) for value in expected_codes}
    if observed != expected:
        raise ValueError("permanent_portfolio_market_codes")
    for column in (*PRICE_COLUMNS, "vol", "amount", "adj_factor"):
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
    if ACCOUNTING_COLUMN in clean.columns:
        clean[ACCOUNTING_COLUMN] = pd.to_numeric(
            clean[ACCOUNTING_COLUMN], errors="coerce"
        )
        if (
            clean[ACCOUNTING_COLUMN].isna().any()
            or (clean[ACCOUNTING_COLUMN] < -1e-10).any()
        ):
            raise ValueError("permanent_portfolio_distribution_amount")
    if DISTRIBUTION_ERROR_COLUMN in clean.columns:
        clean[DISTRIBUTION_ERROR_COLUMN] = pd.to_numeric(
            clean[DISTRIBUTION_ERROR_COLUMN], errors="coerce"
        )
        if (
            clean[DISTRIBUTION_ERROR_COLUMN].isna().any()
            or (clean[DISTRIBUTION_ERROR_COLUMN] < 0).any()
            or clean[DISTRIBUTION_ERROR_COLUMN].gt(
                DISTRIBUTION_REFERENCE_TOLERANCE + 1e-12
            ).any()
        ):
            raise ValueError("permanent_portfolio_distribution_reference")
    clean["is_open"] = clean["is_open"].astype(bool)
    if clean[["close", "adjusted_close", "adj_factor"]].isna().any().any():
        raise ValueError("permanent_portfolio_market_adjustment")
    open_rows = clean["is_open"]
    if clean.loc[open_rows, PRICE_COLUMNS].isna().any().any():
        raise ValueError("permanent_portfolio_market_price")
    if (clean.loc[open_rows, PRICE_COLUMNS] <= 0).any().any():
        raise ValueError("permanent_portfolio_market_price")
    if (clean["adj_factor"] <= 0).any():
        raise ValueError("permanent_portfolio_market_adjustment")
    if start_date is not None and clean["trade_date"].min() < _date_key(start_date):
        raise ValueError("permanent_portfolio_market_range")
    if end_date is not None and clean["trade_date"].max() > _date_key(end_date):
        raise ValueError("permanent_portfolio_market_range")
    return clean.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)


def write_market_publication(
    root: str | Path,
    frame: pd.DataFrame,
    *,
    source_start: str,
    end_date: str,
    source_manifest_sha256: str | None = None,
) -> dict[str, object]:
    destination_root = Path(root)
    destination_root.mkdir(parents=True, exist_ok=True)
    validated = validate_market_frame(
        frame,
        expected_codes=set(frame["ts_code"].astype(str)),
        start_date=source_start,
        end_date=end_date,
    )
    staging = Path(
        tempfile.mkdtemp(
            prefix=".permanent-portfolio-",
            dir=destination_root,
        )
    )
    try:
        data_path = staging / "market.parquet"
        validated.to_parquet(data_path, index=False)
        data_sha256 = _file_sha256(data_path)
        publication_id = f"{_date_key(end_date)}-{data_sha256[:16]}"
        schema_version = 2 if ACCOUNTING_COLUMN in validated.columns else 1
        manifest: dict[str, object] = {
            "schema_version": schema_version,
            "status": "complete",
            "publication_id": publication_id,
            "source_start": _date_key(source_start),
            "end_date": _date_key(end_date),
            "codes": sorted(validated["ts_code"].unique().tolist()),
            "rows": int(len(validated)),
            "data_file": "market.parquet",
            "data_sha256": data_sha256,
        }
        if source_manifest_sha256 is not None:
            source_sha256 = str(source_manifest_sha256).lower()
            if len(source_sha256) != 64 or any(
                value not in "0123456789abcdef" for value in source_sha256
            ):
                raise ValueError("permanent_portfolio_source_manifest")
            manifest["source_manifest_sha256"] = source_sha256
        if schema_version == 2:
            distributions = validated.loc[
                validated[ACCOUNTING_COLUMN].gt(0),
                ["ts_code", "trade_date", ACCOUNTING_COLUMN],
            ]
            manifest["accounting_version"] = "cash_distributions_v2"
            manifest["distribution_count"] = int(len(distributions))
            manifest["distribution_cash_per_share_sum"] = float(
                distributions[ACCOUNTING_COLUMN].sum()
            )
            manifest["distribution_reference_tolerance"] = (
                DISTRIBUTION_REFERENCE_TOLERANCE
            )
            manifest["distribution_reference_max_error"] = float(
                validated.get(
                    DISTRIBUTION_ERROR_COLUMN,
                    pd.Series([0.0]),
                ).max()
            )
            evidence: dict[str, object] = {}
            for code in sorted(validated["ts_code"].unique()):
                code_rows = distributions.loc[
                    distributions["ts_code"].eq(code)
                ].sort_values("trade_date")
                records = [
                    {
                        "trade_date": str(row.trade_date),
                        "cash_per_share": float(
                            row.distribution_cash_per_share
                        ),
                    }
                    for row in code_rows.itertuples(index=False)
                ]
                evidence[str(code)] = {
                    "count": len(records),
                    "cash_per_share_sum": float(
                        code_rows[ACCOUNTING_COLUMN].sum()
                    ),
                    "first_date": (
                        records[0]["trade_date"] if records else None
                    ),
                    "last_date": (
                        records[-1]["trade_date"] if records else None
                    ),
                    "records_sha256": canonical_hash({"records": records}),
                }
            manifest["distribution_evidence"] = evidence
        manifest["manifest_sha256"] = canonical_hash(manifest)
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        final = destination_root / publication_id
        if final.exists():
            _, existing = load_market_publication(final)
            if existing != manifest:
                raise ValueError("permanent_portfolio_publication_conflict")
            return existing
        os.replace(staging, final)
        latest_tmp = destination_root / ".latest.json.tmp"
        latest_tmp.write_text(
            json.dumps(
                {
                    "publication_id": publication_id,
                    "manifest_sha256": manifest["manifest_sha256"],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(latest_tmp, destination_root / "latest.json")
        return manifest
    finally:
        if staging and staging.exists():
            shutil.rmtree(staging)


def load_market_publication(
    publication_dir: str | Path,
) -> tuple[pd.DataFrame, dict[str, object]]:
    root = Path(publication_dir)
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("permanent_portfolio_manifest_json") from exc
    if not isinstance(manifest, dict):
        raise ValueError("permanent_portfolio_manifest_json")
    unsigned = dict(manifest)
    recorded = unsigned.pop("manifest_sha256", None)
    if not isinstance(recorded, str) or recorded != canonical_hash(unsigned):
        raise ValueError("permanent_portfolio_manifest_checksum")
    data_path = root / str(manifest.get("data_file") or "")
    if (
        not data_path.is_file()
        or _file_sha256(data_path) != manifest.get("data_sha256")
    ):
        raise ValueError("permanent_portfolio_data_checksum")
    frame = pd.read_parquet(data_path)
    frame["ts_code"] = frame["ts_code"].astype(str)
    frame["trade_date"] = frame["trade_date"].astype(str)
    verified = validate_market_frame(
        frame,
        expected_codes=manifest.get("codes") or (),
        start_date=str(manifest["source_start"]),
        end_date=str(manifest["end_date"]),
    )
    return verified, manifest


def materialize_market_data(
    *,
    provider: PermanentPortfolioDataProvider,
    codes: Iterable[str],
    source_start: str,
    end_date: str,
    output_root: str | Path,
    holdout_start: str | None = None,
    source_manifest_sha256: str | None = None,
) -> dict[str, object]:
    start_key = _date_key(source_start)
    end_key = _date_key(end_date)
    calendar = provider.trade_cal(
        exchange="SSE",
        start_date=start_key,
        end_date=end_key,
    ).copy()
    calendar["trade_date"] = calendar["cal_date"].map(_date_key)
    open_dates = calendar.loc[
        pd.to_numeric(calendar["is_open"], errors="coerce").eq(1),
        ["trade_date"],
    ].drop_duplicates().sort_values("trade_date").reset_index(drop=True)
    if open_dates.empty:
        raise ValueError("permanent_portfolio_market_calendar")
    collected: list[pd.DataFrame] = []
    normalized_codes = tuple(str(code) for code in codes)
    for code in normalized_codes:
        daily = provider.fund_daily(
            ts_code=code,
            start_date=start_key,
            end_date=end_key,
        )
        adjustment = provider.fund_adj(
            ts_code=code,
            start_date=start_key,
            end_date=end_key,
        )
        total_return = build_total_return_frame(
            daily,
            adjustment,
            require_distribution_reference=True,
        )
        if total_return.empty:
            raise ValueError(f"permanent_portfolio_missing_open_date:{code}:all")
        first_observed = str(total_return["trade_date"].min())
        required_dates = set(
            open_dates.loc[
                open_dates["trade_date"].ge(first_observed),
                "trade_date",
            ].astype(str)
        )
        observed_dates = set(total_return["trade_date"].astype(str))
        suspension_dates: set[str] = set()
        suspend_method = getattr(provider, "suspend_d", None)
        if callable(suspend_method):
            suspensions = suspend_method(
                ts_code=code,
                start_date=first_observed,
                end_date=end_key,
            ).copy()
            if not suspensions.empty:
                required_columns = {
                    "trade_date",
                    "suspend_timing",
                    "suspend_type",
                }
                if not required_columns.issubset(suspensions.columns):
                    raise ValueError(
                        f"permanent_portfolio_suspension_schema:{code}"
                    )
                timing = (
                    suspensions["suspend_timing"]
                    .astype(str)
                    .str.replace(" ", "", regex=False)
                )
                kind = suspensions["suspend_type"].astype(str).str.strip()
                full_day = kind.eq("S") & (
                    timing.isin({"", "09:30-15:00", "9:30-15:00"})
                    | timing.str.contains("全天", regex=False)
                )
                suspension_dates = set(
                    suspensions.loc[full_day, "trade_date"].map(_date_key)
                )
        unobserved_dates = required_dates - observed_dates
        zero_trade_dates = _verified_zero_trade_dates(
            total_return,
            unobserved_dates,
        )
        missing_dates = sorted(
            unobserved_dates - suspension_dates - zero_trade_dates
        )
        if missing_dates:
            raise ValueError(
                f"permanent_portfolio_missing_open_date:{code}:{missing_dates[0]}"
            )
        code_dates = open_dates.loc[
            open_dates["trade_date"].ge(first_observed)
        ].copy()
        merged = code_dates.merge(
            total_return,
            on="trade_date",
            how="left",
            validate="one_to_one",
        )
        merged["ts_code"] = merged["ts_code"].fillna(code).astype(str)
        observed = merged["close"].notna()
        merged["is_open"] = observed
        merged = merged.sort_values("trade_date").reset_index(drop=True)
        for column in ("close", "adjusted_close", "adj_factor"):
            merged[column] = pd.to_numeric(
                merged[column],
                errors="coerce",
            ).ffill()
        merged[ACCOUNTING_COLUMN] = pd.to_numeric(
            merged.get(ACCOUNTING_COLUMN), errors="coerce"
        ).fillna(0.0)
        merged[DISTRIBUTION_ERROR_COLUMN] = pd.to_numeric(
            merged.get(DISTRIBUTION_ERROR_COLUMN), errors="coerce"
        ).fillna(0.0)
        collected.append(merged)
    frame = pd.concat(collected, ignore_index=True)
    validated = validate_market_frame(
        frame,
        expected_codes=set(normalized_codes),
        start_date=start_key,
        end_date=end_key,
    )
    if holdout_start is not None:
        return write_partitioned_market_publication(
            output_root,
            validated,
            source_start=start_key,
            end_date=end_key,
            holdout_start=holdout_start,
            source_manifest_sha256=source_manifest_sha256,
        )
    return write_market_publication(
        output_root,
        validated,
        source_start=start_key,
        end_date=end_key,
        source_manifest_sha256=source_manifest_sha256,
    )


def write_partitioned_market_publication(
    root: str | Path,
    frame: pd.DataFrame,
    *,
    source_start: str,
    end_date: str,
    holdout_start: str,
    source_manifest_sha256: str | None = None,
) -> dict[str, object]:
    destination = Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    holdout_key = _date_key(holdout_start)
    dates = frame["trade_date"].map(_date_key)
    development_frame = frame.loc[dates.lt(holdout_key)].copy()
    warmup_start = (
        pd.Timestamp(holdout_key) - pd.DateOffset(months=13)
    ).strftime("%Y%m%d")
    holdout_frame = frame.loc[dates.ge(warmup_start)].copy()
    if development_frame.empty or not dates.ge(holdout_key).any():
        raise ValueError("permanent_portfolio_market_partitions")
    development = write_market_publication(
        destination / "development",
        development_frame,
        source_start=source_start,
        end_date=str(development_frame["trade_date"].max()),
        source_manifest_sha256=source_manifest_sha256,
    )
    holdout = write_market_publication(
        destination / "holdout",
        holdout_frame,
        source_start=str(holdout_frame["trade_date"].min()),
        end_date=end_date,
        source_manifest_sha256=source_manifest_sha256,
    )
    schema_version = max(
        int(development.get("schema_version") or 1),
        int(holdout.get("schema_version") or 1),
    )
    manifest: dict[str, object] = {
        "schema_version": schema_version,
        "status": "complete",
        "holdout_start": holdout_key,
        "development": development,
        "holdout": holdout,
    }
    if schema_version == 2:
        manifest["accounting_version"] = "cash_distributions_v2"
    if source_manifest_sha256 is not None:
        manifest["source_manifest_sha256"] = str(
            source_manifest_sha256
        ).lower()
    manifest["manifest_sha256"] = canonical_hash(manifest)
    latest_tmp = destination / ".latest.json.tmp"
    latest_tmp.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(latest_tmp, destination / "latest.json")
    return manifest
