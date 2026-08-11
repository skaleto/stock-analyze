"""Atomic stores for immutable research snapshots and model metadata."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd


TEXT_IDENTIFIER_COLUMNS = frozenset(
    {
        "ann_date",
        "benchmark_code",
        "account_id",
        "attribution_contract_version",
        "code",
        "con_code",
        "list_date",
        "model_version",
        "holding_episode_id",
        "source_date",
        "strategy_id",
        "trade_date",
        "ts_code",
    }
)


class ResearchStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def feature_snapshot_path(self, market: str, as_of: str) -> Path:
        safe_date = str(as_of).replace("-", "")
        return self.root / "features" / market / f"{safe_date}.parquet"

    def write_feature_snapshot(
        self,
        market: str,
        as_of: str,
        frame: pd.DataFrame,
    ) -> Path:
        return self.write_parquet_atomic(
            self.feature_snapshot_path(market, as_of),
            self._normalize_identifiers(frame),
        )

    def read_feature_snapshot(self, market: str, as_of: str) -> pd.DataFrame:
        frame = pd.read_parquet(self.feature_snapshot_path(market, as_of))
        return self._normalize_identifiers(frame)

    def label_snapshot_path(self, market: str, as_of: str) -> Path:
        safe_date = str(as_of).replace("-", "")
        return self.root / "labels" / market / f"{safe_date}.parquet"

    def write_label_snapshot(
        self,
        market: str,
        as_of: str,
        frame: pd.DataFrame,
    ) -> Path:
        return self.write_parquet_atomic(
            self.label_snapshot_path(market, as_of),
            self._normalize_identifiers(frame),
        )

    def read_label_snapshot(self, market: str, as_of: str) -> pd.DataFrame:
        frame = pd.read_parquet(self.label_snapshot_path(market, as_of))
        return self._normalize_identifiers(frame)

    def attribution_snapshot_path(
        self,
        market: str,
        as_of: str,
        strategy_id: str,
        account_id: str,
    ) -> Path:
        safe_date = str(as_of).replace("-", "")
        safe_strategy = self._safe_partition(strategy_id, "strategy_id")
        safe_account = self._safe_partition(account_id, "account_id")
        return (
            self.root
            / "attributions"
            / str(market)
            / f"{safe_date}__{safe_strategy}__{safe_account}.parquet"
        )

    def write_attribution_snapshot(
        self,
        market: str,
        as_of: str,
        strategy_id: str,
        account_id: str,
        frame: pd.DataFrame,
    ) -> Path:
        return self.write_parquet_atomic(
            self.attribution_snapshot_path(
                market,
                as_of,
                strategy_id,
                account_id,
            ),
            self._normalize_identifiers(frame),
        )

    def read_attribution_snapshot(
        self,
        market: str,
        as_of: str,
        strategy_id: str,
        account_id: str,
    ) -> pd.DataFrame:
        frame = pd.read_parquet(
            self.attribution_snapshot_path(
                market,
                as_of,
                strategy_id,
                account_id,
            )
        )
        return self._normalize_identifiers(frame)

    def latest_common_snapshot_date(self, market: str, *, as_of: str) -> str:
        cutoff = str(as_of).replace("-", "")[:8]
        feature_dates = self._snapshot_dates("features", market, cutoff=cutoff)
        label_dates = self._snapshot_dates("labels", market, cutoff=cutoff)
        common_dates = feature_dates.intersection(label_dates)
        if not common_dates:
            raise FileNotFoundError(
                f"research_snapshot_missing:{market}:as_of={cutoff}"
            )
        return max(common_dates)

    def prune_dated_artifacts(
        self,
        market: str,
        *,
        categories: tuple[str, ...],
        keep_recent: int = 3,
        keep_monthly: int = 3,
    ) -> int:
        removed = 0
        for category in categories:
            directory = self.root / category / market
            paths = {
                path.stem: path
                for path in directory.glob("*.parquet")
                if len(path.stem) == 8 and path.stem.isdigit()
            }
            dates = sorted(paths)
            if not dates:
                continue
            recent = set(dates[-max(0, keep_recent):]) if keep_recent else set()
            current_month = dates[-1][:6]
            monthly_checkpoints: dict[str, str] = {}
            for snapshot_date in dates:
                month = snapshot_date[:6]
                if month == current_month:
                    continue
                monthly_checkpoints[month] = snapshot_date
            retained_months = sorted(monthly_checkpoints)[-max(0, keep_monthly):]
            retained = recent.union(
                monthly_checkpoints[month] for month in retained_months
            )
            for snapshot_date, path in paths.items():
                if snapshot_date not in retained:
                    path.unlink(missing_ok=True)
                    removed += 1
        return removed

    def _snapshot_dates(
        self,
        category: str,
        market: str,
        *,
        cutoff: str | None = None,
    ) -> set[str]:
        dates = {
            path.stem
            for path in (self.root / category / market).glob("*.parquet")
            if len(path.stem) == 8 and path.stem.isdigit()
        }
        if cutoff is not None:
            dates = {snapshot_date for snapshot_date in dates if snapshot_date <= cutoff}
        return dates

    def write_parquet_atomic(self, path: str | Path, frame: pd.DataFrame) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".parquet",
                delete=False,
            ) as handle:
                tmp_name = handle.name
            frame.to_parquet(tmp_name, index=False)
            os.replace(tmp_name, destination)
            return destination
        finally:
            if tmp_name:
                Path(tmp_name).unlink(missing_ok=True)

    @staticmethod
    def _normalize_identifiers(frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        for column in TEXT_IDENTIFIER_COLUMNS.intersection(normalized.columns):
            normalized[column] = normalized[column].astype("string")
        return normalized

    @staticmethod
    def _safe_partition(raw: str, name: str) -> str:
        value = str(raw).strip()
        if not value:
            raise ValueError(f"research_{name}_required")
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in value):
            raise ValueError(f"research_{name}_invalid:{value}")
        return value
