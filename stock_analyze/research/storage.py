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
        "code",
        "con_code",
        "list_date",
        "model_version",
        "source_date",
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
