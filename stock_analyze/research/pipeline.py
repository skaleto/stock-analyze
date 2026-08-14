"""End-to-end research preparation, study, training, and prediction workflows."""

from __future__ import annotations

import gc
import json
import hashlib
import re
import tempfile
from dataclasses import asdict, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import competition
from ..model_iteration import (
    ensure_iteration_candidate,
    iteration_portfolio_dir,
    iteration_prediction_path,
)
from ..intelligence.factors import attach_event_features
from ..intelligence.lifecycle import model_iteration_features
from ..utils import write_dataframe_csv_atomic, write_text_atomic
from .activation import (
    ModelRegistry,
    ShadowCycleTracker,
    activation_evidence_from_metrics,
    evaluate_role_activation,
    select_registry_model,
)
from .account_features import (
    account_feature_contract,
    alpha158_lite_feature_columns,
    build_account_feature_view,
    build_alpha158_lite_feature_view,
)
from .classical_specs import (
    a_share_h3_specs,
    a_share_h20_specs,
    qdii_h5_specs,
    qdii_h10_specs,
)
from .classical_tournament import run_classical_tournament as execute_classical_tournament
from .cross_sectional_candidate import evaluate_cross_sectional_candidate
from .event_study import build_event_study_from_parquet
from .events import write_events_incremental
from .feature_registry import (
    DEFAULT_REGISTRY,
    DEFAULT_REGISTRY_HASH,
    INTELLIGENCE_FEATURES,
    MACRO_FEATURES,
)
from .forward_evidence import load_forward_portfolio_evidence
from .drift import DriftLifecycle, DriftObservation
from .governance import (
    TrialRegistry,
    build_aligned_trial_return_matrix,
    deflated_sharpe_probability,
    probability_of_backtest_overfit,
)
from .lineage import ResearchLineageStore
from .labels import LABEL_CONTRACT_VERSION, build_forward_labels
from .models import (
    TRAINING_PROTOCOL_VERSION,
    load_model_bundle,
    save_model_bundle,
    train_model_bundle,
)
from .moneyflow import (
    MONEYFLOW_FEATURE_COLUMNS,
    attach_moneyflow_point_in_time_features,
    load_moneyflow_cache,
)
from .portfolio_replay import SIMULATOR_VERSION
from .prediction import generate_predictions
from .regime import classify_regimes
from .source_features import (
    SourceCollection,
    add_industry_features,
    attach_daily_basic_point_in_time_features,
    attach_industry_membership,
    attach_point_in_time_features,
    attach_qdii_point_in_time_features,
    build_fundamental_history,
    build_regime_components,
    build_source_features,
)
from .storage import ResearchStore
from .technical_features import compute_technical_features
from .unified_arena import build_unified_arena_report
from .tabular_ranker import (
    evaluate_regime_tabular_candidate,
    load_tabular_ranker_config,
)
from .tabular_forward import (
    freeze_tabular_forward_model,
    observe_tabular_forward_model,
    tabular_forward_model_root,
)
from .trial_ledger import DEFAULT_CLASSICAL_TRIAL_SPECS, TrialLedger
from .universe import attach_point_in_time_universe


class ResearchPipeline:
    _FEATURE_BATCH_SIZE = 32
    _A_SHARE_ENRICH_BATCH_SIZE = 64
    _A_SHARE_PREP_SOURCE_NAMES = {
        "daily_basic", "fina_indicator", "income", "cashflow",
        "balancesheet", "fina_mainbz", "index_member_all", "index_weight",
        "moneyflow", "margin_detail",
    }
    _REGIME_SOURCE_NAMES = {
        "cn_pmi", "cn_m", "cn_cpi", "cn_ppi", "shibor", "us_tycr",
        "index_global", "fx_daily",
    }
    _TABULAR_FEATURE_SOURCE_COLUMNS = (
        "code", "trade_date", "open", "high", "low", "close", "volume",
        "amount", "amount_unit", "turnover_rate", "industry", "industry_l2",
        "total_mv", "account_id", "research_scope", "roe", "roic",
        "gross_margin", "net_profit_margin", "debt_ratio", "revenue_growth",
        "profit_growth", "cash_conversion", "accrual_ratio",
        "free_cashflow_to_assets", "gross_profit_to_assets", "pe_ttm", "pb",
        "industry_relative_momentum_20", "industry_breadth", "industry_cycle_score",
        *MONEYFLOW_FEATURE_COLUMNS,
    )
    _TABULAR_LABEL_COLUMNS = (
        "code", "trade_date", "horizon", "entry_date", "entry_price",
        "entry_high", "entry_low", "entry_close", "entry_volume",
        "entry_return_from_prev_close", "entry_one_price_limit_up",
        "entry_one_price_limit_down", "entry_buy_allowed", "entry_sell_allowed",
        "label_end_date", "absolute_return", "benchmark_entry_price",
        "benchmark_exit_price", "benchmark_return", "excess_return",
        "account_id", "research_scope", "benchmark_code",
    )

    def __init__(
        self,
        repo_root: str | Path,
        *,
        market: str,
        agent: str,
        as_of: str | None = None,
        offline: bool = False,
        max_full_history_instruments: int | None = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.market = market
        self.agent = agent
        self.as_of = as_of or date.today().isoformat()
        self.offline = offline
        self.max_full_history_instruments = (
            None
            if max_full_history_instruments is None
            else max(1, int(max_full_history_instruments))
        )
        self.research_root = self.repo_root / "data" / "research"
        self.store = ResearchStore(self.research_root)
        self._persisted_source_frames_cache: dict[str, pd.DataFrame] | None = None

    @property
    def run_key(self) -> str:
        return self.as_of.replace("-", "")

    def _cache_dir(self) -> Path:
        if self.market == "cn_qdii_etf":
            return self.repo_root / "data" / "cn_qdii_etf" / "shared" / "cache"
        return self.repo_root / "data" / "shared" / "cache"

    def _assert_a_share_materialization_ready(self, run_key: str | None = None) -> None:
        if self.market != "a_share":
            return
        target = run_key or self.run_key
        market_root = self.research_root / "raw" / "a_share"
        markers = (
            market_root / target / ".materialization_in_progress",
            market_root / f".materialization_in_progress.{target}",
        )
        if any(marker.exists() for marker in markers):
            raise ValueError(f"a_share_materialization_in_progress:{target}")

    def _a_share_materialization_manifest(
        self, run_key: str | None = None
    ) -> dict[str, object] | None:
        if self.market != "a_share":
            return None
        target = run_key or self.run_key
        self._assert_a_share_materialization_ready(target)
        path = (
            self.research_root / "raw" / "a_share" / target
            / "materialization_manifest.json"
        )
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("a_share_materialization_manifest_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("a_share_materialization_manifest_invalid")
        if payload.get("schema_version") != "a-share-materialization-v1":
            raise ValueError("a_share_materialization_manifest_invalid")
        if payload.get("status") != "complete" or str(payload.get("as_of")) != target:
            raise ValueError("a_share_materialization_manifest_incomplete")
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            raise ValueError("a_share_materialization_outputs_missing")
        canonical = json.dumps(
            outputs,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if payload.get("output_digest") != hashlib.sha256(canonical).hexdigest():
            raise ValueError("a_share_materialization_output_digest_mismatch")
        for relative, record in outputs.items():
            if not isinstance(relative, str) or not isinstance(record, dict):
                raise ValueError("a_share_materialization_outputs_invalid")
            if record.get("path") != relative:
                raise ValueError("a_share_materialization_output_path_mismatch")
        declared_stems = {Path(relative).stem for relative in outputs}
        cache_root = self.repo_root / "data" / "shared" / "backtest_cache"
        populated_statements = {
            endpoint
            for endpoint in ("income", "balancesheet", "cashflow")
            if any((cache_root / endpoint).glob("*.csv"))
        }
        missing_statements = sorted(populated_statements - declared_stems)
        if missing_statements:
            raise ValueError(
                "a_share_materialization_stale:missing_endpoint="
                + ",".join(missing_statements)
            )
        return payload

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_materialized_frame(
        frame: pd.DataFrame,
        record: dict[str, object],
        *,
        name: str,
    ) -> None:
        if len(frame) != int(record.get("rows") or 0):
            raise ValueError(f"a_share_materialized_output_rows_mismatch:{name}")
        date_column = next(
            (
                column
                for column in ("trade_date", "ann_date", "cal_date", "start_date")
                if column in frame.columns
            ),
            None,
        )
        observed_min: str | None = None
        observed_max: str | None = None
        if date_column is not None and not frame.empty:
            dates = (
                frame[date_column]
                .astype("string")
                .str.replace("-", "", regex=False)
                .replace("", pd.NA)
                .dropna()
            )
            if not dates.empty:
                observed_min = str(dates.min())
                observed_max = str(dates.max())
        if observed_min != record.get("min_date") or observed_max != record.get("max_date"):
            raise ValueError(f"a_share_materialized_output_date_mismatch:{name}")

    def _history_files(self) -> list[Path]:
        manifest = self._a_share_materialization_manifest()
        if manifest is not None:
            outputs = manifest.get("outputs")
            if not isinstance(outputs, dict):
                raise ValueError("a_share_materialization_outputs_missing")
            cache_root = self._cache_dir().absolute()
            resolved_cache_root = cache_root.resolve()
            declared: list[Path] = []
            for record in outputs.values():
                if not isinstance(record, dict):
                    continue
                relative = record.get("path")
                if not isinstance(relative, str):
                    continue
                candidate = (self.repo_root / relative).absolute()
                try:
                    candidate.resolve().relative_to(resolved_cache_root)
                except ValueError:
                    continue
                if not re.fullmatch(
                    rf"history_\d{{6}}_{re.escape(self.run_key)}_\d+\.csv",
                    candidate.name,
                ):
                    continue
                if not candidate.is_file():
                    raise ValueError(
                        f"a_share_materialized_history_missing:{candidate.name}"
                    )
                expected_hash = record.get("sha256")
                if not isinstance(expected_hash, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", expected_hash
                ):
                    raise ValueError(
                        f"a_share_materialized_history_hash_missing:{candidate.name}"
                    )
                if self._file_sha256(candidate) != expected_hash:
                    raise ValueError(
                        f"a_share_materialized_history_hash_mismatch:{candidate.name}"
                    )
                history_dates = pd.read_csv(
                    candidate,
                    usecols=["trade_date"],
                    dtype={"trade_date": str},
                )
                self._validate_materialized_frame(
                    history_dates, record, name=candidate.name
                )
                declared.append(candidate)
            if not declared:
                raise ValueError("a_share_materialization_history_outputs_missing")
            return sorted(set(declared))
        pattern = "fund_daily_*.csv" if self.market == "cn_qdii_etf" else "history_*.csv"
        candidates = sorted(self._cache_dir().glob(pattern))
        latest_by_code: dict[str, tuple[tuple[int, str], Path]] = {}
        for path in candidates:
            if self.market == "a_share":
                match = re.fullmatch(r"history_(\d{6})_(\d{8})_(\d+)\.csv", path.name)
                if not match or match.group(2) > self.run_key:
                    continue
                code = match.group(1)
                score = (int(match.group(3)), match.group(2))
            else:
                match = re.fullmatch(r"fund_daily_(\d{6})_[A-Z]+_(\d{8})\.csv", path.name)
                if not match or match.group(2) > self.run_key:
                    continue
                code = match.group(1)
                score = (0, match.group(2))
            current = latest_by_code.get(code)
            if current is None or score > current[0]:
                latest_by_code[code] = (score, path)
        return sorted(item[1] for item in latest_by_code.values())

    def _normalize_history(self, path: Path) -> pd.DataFrame:
        frame = pd.read_csv(
            path,
            dtype={
                "ts_code": str,
                "trade_date": str,
                "日期": str,
                "name": str,
                "industry": str,
                "list_date": str,
                "delist_date": str,
                "security_status": str,
                "status_source": str,
                "st_source": str,
                "suspension_source": str,
                "suspension_status_source": str,
                "suspend_timing": str,
                "suspend_type": str,
                "tushare_suspend_timing": str,
                "tushare_suspend_type": str,
            },
        )
        match = re.search(r"(?:fund_daily|history)_(\d{6})", path.name)
        code = match.group(1) if match else ""
        aliases = {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
            "vol": "volume",
        }
        if "volume" in frame.columns and "vol" in frame.columns:
            frame = frame.drop(columns="vol")
        normalized = frame.rename(columns=aliases).copy()
        normalized["code"] = code
        normalized["trade_date"] = normalized["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
        for column in (
            "open", "high", "low", "close", "volume", "amount",
            "turnover_rate", "adj_factor",
        ):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if self.market == "cn_qdii_etf":
            from ..markets.cn_qdii_etf.units import canonicalize_tushare_amount

            normalized = canonicalize_tushare_amount(normalized)
        keep = [
            column
            for column in (
                "code", "trade_date", "open", "high", "low", "close", "volume",
                "amount", "amount_yuan", "amount_thousand_yuan", "amount_unit",
                "turnover_rate", "adj_factor",
                "name", "industry", "list_date", "delist_date",
                "security_status", "is_st", "is_suspended",
                "is_tradable", "status_conflict", "baostock_tradestatus",
                "status_source", "st_source", "suspension_source",
                "suspension_status_source", "suspension_conflict",
                "tushare_suspend_event", "tushare_suspend_timing",
                "tushare_suspend_type", "suspend_timing", "suspend_type",
                "name_source", "list_status_source",
            )
            if column in normalized.columns
        ]
        return normalized[keep]

    def _compute_technical_history(self, history: pd.DataFrame) -> pd.DataFrame:
        technical_input = history.copy()
        price_columns = ("open", "high", "low", "close")
        raw_prices = technical_input.loc[:, price_columns].apply(
            pd.to_numeric, errors="coerce"
        ).reset_index(drop=True)
        if self.market == "a_share" and "adj_factor" in technical_input.columns:
            factor = pd.to_numeric(
                technical_input["adj_factor"], errors="coerce"
            ).where(lambda values: values.gt(0))
            for column in price_columns:
                technical_input[column] = (
                    pd.to_numeric(technical_input[column], errors="coerce") * factor
                )
        featured = compute_technical_features(technical_input)
        # Execution and labels require the actual traded prices. Only derived
        # technical features use the corporate-action-adjusted series.
        for column in price_columns:
            featured[column] = raw_prices[column]
        return featured.drop(columns="adj_factor", errors="ignore")

    @staticmethod
    def _compact_numeric_features(frame: pd.DataFrame, *, copy: bool = True) -> pd.DataFrame:
        compact = frame.copy() if copy else frame
        for column in compact.columns:
            dtype = compact[column].dtype
            if pd.api.types.is_float_dtype(dtype):
                compact[column] = pd.to_numeric(
                    compact[column], errors="coerce"
                ).astype(np.float32)
            elif pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
                compact[column] = compact[column].astype("string[pyarrow]")
        return compact

    def prepare_data(self, *, force: bool = False) -> dict[str, Any]:
        if (
            self.market == "a_share"
            and not self.offline
            and self._a_share_materialization_manifest() is not None
        ):
            raise ValueError("a_share_materialized_online_prepare_forbidden")
        destination = self.store.feature_snapshot_path(self.market, self.as_of)
        if destination.exists() and not force:
            cached = self.store.read_feature_snapshot(self.market, self.as_of)
            manifest = self._write_feature_registry_manifest(cached, destination)
            return {
                "status": "cached",
                "rows": len(cached),
                "path": str(destination),
                "feature_registry_hash": DEFAULT_REGISTRY_HASH,
                "feature_manifest": str(manifest),
            }
        history_files = self._history_files()
        if not history_files:
            raise FileNotFoundError(f"research_history_cache_missing:{self._cache_dir()}")
        all_codes = [
            match.group(1)
            for path in history_files
            if (match := re.search(r"(?:fund_daily|history)_(\d{6})", path.name))
        ]
        full_history_codes = self._full_history_codes(all_codes)
        featured = self._build_history_features(history_files, full_history_codes)
        source_count = 0
        source_frames: dict[str, pd.DataFrame] = {}
        if not self.offline:
            available_codes = sorted(featured["code"].dropna().astype(str).unique())
            persisted_sources = self._load_persisted_source_frames(
                names=(
                    self._A_SHARE_PREP_SOURCE_NAMES
                    if self.market == "a_share"
                    else None
                )
            )
            source_codes = self._research_source_codes(available_codes, full_history_codes)
            sources = self._collect_sources(source_codes)
            source_frames = self._merge_source_frame_maps(persisted_sources, sources.frames)
            self._persisted_source_frames_cache = source_frames
            raw_root = self.research_root / "raw" / self.market / self.run_key
            for name, frame in source_frames.items():
                if frame.empty:
                    continue
                self.store.write_parquet_atomic(raw_root / f"{name}.parquet", frame)
                source_count += 1
            if not sources.health.empty:
                self.store.write_parquet_atomic(raw_root / "source_health.parquet", sources.health)
            if source_frames:
                write_text_atomic(
                    raw_root / "snapshot_manifest.json",
                    json.dumps(
                        {
                            "schema_version": 1,
                            "mode": "cumulative",
                            "as_of": self.as_of,
                            "sources": sorted(
                                name for name, frame in source_frames.items()
                                if isinstance(frame, pd.DataFrame) and not frame.empty
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        else:
            source_frames = self._load_persisted_source_frames(
                names=(
                    self._A_SHARE_PREP_SOURCE_NAMES
                    if self.market == "a_share"
                    else None
                )
            )
            source_count = sum(not frame.empty for frame in source_frames.values())
        universe = None
        if self.market == "a_share":
            universe = attach_point_in_time_universe(
                featured,
                repo_root=self.repo_root,
                market=self.market,
                accounts=self._baseline_accounts(),
                as_of=self.as_of,
                index_weights=source_frames.get("index_weight"),
            )
            if (
                self._a_share_materialization_manifest() is not None
                and not bool(universe.metadata.get("unbiased_universe"))
            ):
                reasons = ",".join(universe.metadata.get("quality_reasons") or [])
                raise ValueError(
                    f"a_share_materialized_universe_unavailable:{reasons}"
                )
            featured = universe.frame
        if source_frames:
            if self.market == "a_share":
                with tempfile.TemporaryDirectory(
                    dir=self.research_root,
                    prefix=".a-share-enriched-batches-",
                ) as temporary_dir:
                    batch_root = Path(temporary_dir)
                    self._write_a_share_enriched_feature_batches(
                        featured,
                        source_frames,
                        batch_root,
                    )
                    source_frames.clear()
                    del featured
                    gc.collect()
                    featured = pd.read_parquet(
                        batch_root, dtype_backend="pyarrow"
                    )
                featured = self._compact_numeric_features(featured, copy=False)
            else:
                featured = attach_qdii_point_in_time_features(featured, source_frames)
                regime_sources = self._load_regime_source_frames()
                regime_sources.update(source_frames)
                market_context = build_regime_components(
                    regime_sources, featured["trade_date"].drop_duplicates()
                )
                featured = featured.merge(market_context, on="trade_date", how="left")
                featured = self._attach_latest_source_features(
                    featured, build_source_features(source_frames)
                )
        if self.market == "cn_qdii_etf":
            featured = self._attach_qdii_metadata(featured)
        if universe is None:
            universe = attach_point_in_time_universe(
                featured,
                repo_root=self.repo_root,
                market=self.market,
                accounts=self._baseline_accounts(),
                as_of=self.as_of,
            )
            featured = universe.frame
        featured = add_industry_features(featured)
        featured = attach_event_features(
            featured,
            self.repo_root / "data" / "shared" / "intelligence",
            market=self.market,
            as_of=self.as_of,
            availability_policy="research",
            copy=False,
        )
        featured["feature_observed_at"] = self.as_of
        featured = self._compact_numeric_features(featured, copy=False)
        self.store.write_feature_snapshot(self.market, self.as_of, featured)
        manifest = self._write_feature_registry_manifest(featured, destination)
        return {
            "status": "built",
            "rows": len(featured),
            "instruments": int(featured["code"].nunique()),
            "path": str(destination),
            "offline": self.offline,
            "sources": source_count,
            "full_history_instruments": len(full_history_codes) if self.market == "a_share" else int(featured["code"].nunique()),
            "feature_registry_hash": DEFAULT_REGISTRY_HASH,
            "feature_manifest": str(manifest),
            "universe": universe.metadata,
        }

    def _build_history_features(
        self,
        history_files: list[Path],
        full_history_codes: set[str],
    ) -> pd.DataFrame:
        self.research_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=self.research_root,
            prefix=f".{self.market}-feature-batches-",
        ) as temporary_dir:
            batch_root = Path(temporary_dir)
            pending: list[pd.DataFrame] = []
            batch_count = 0

            def flush() -> None:
                nonlocal batch_count
                if not pending:
                    return
                batch = pd.concat(pending, ignore_index=True)
                pending.clear()
                batch.to_parquet(batch_root / f"batch-{batch_count:04d}.parquet", index=False)
                batch_count += 1

            for path in history_files:
                history = self._normalize_history(path)
                history = history.loc[history["trade_date"] <= self.run_key]
                if history.empty:
                    continue
                part = self._compact_numeric_features(
                    self._compute_technical_history(history), copy=False
                )
                code = str(part.iloc[-1]["code"])
                keep_full = self.market != "a_share" or code in full_history_codes
                part["history_role"] = "full" if keep_full else "latest_only"
                pending.append(part if keep_full else part.tail(1))
                if len(pending) >= self._FEATURE_BATCH_SIZE:
                    flush()
            flush()
            if batch_count == 0:
                raise FileNotFoundError(f"research_history_cache_empty:{self._cache_dir()}")
            return self._compact_numeric_features(
                pd.read_parquet(batch_root, dtype_backend="pyarrow"), copy=False
            )

    def _write_a_share_enriched_feature_batches(
        self,
        features: pd.DataFrame,
        source_frames: dict[str, pd.DataFrame],
        batch_root: Path,
        *,
        batch_size: int | None = None,
    ) -> int:
        if features.empty:
            return 0
        batch_root.mkdir(parents=True, exist_ok=True)
        size = max(1, int(batch_size or self._A_SHARE_ENRICH_BATCH_SIZE))
        fundamental_history = build_fundamental_history(source_frames)
        regime_sources = self._load_regime_source_frames()
        regime_sources.update(source_frames)
        market_context = build_regime_components(
            regime_sources, features["trade_date"].drop_duplicates()
        )
        source_features = build_source_features(source_frames)
        codes = (
            features["code"]
            .dropna()
            .astype("string")
            .drop_duplicates()
            .tolist()
        )
        feature_codes = features["code"].astype("string")
        count = 0
        for offset in range(0, len(codes), size):
            selected_codes = set(codes[offset:offset + size])
            part = features.loc[feature_codes.isin(selected_codes)].copy()
            part = attach_point_in_time_features(part, fundamental_history)
            part = attach_daily_basic_point_in_time_features(
                part,
                source_frames.get("daily_basic", pd.DataFrame()),
            )
            part = attach_industry_membership(
                part,
                source_frames.get("index_member_all", pd.DataFrame()),
            )
            part = self._attach_a_share_industry_fallback(part)
            part = part.merge(market_context, on="trade_date", how="left")
            historical_moneyflow = load_moneyflow_cache(
                self.repo_root,
                codes=selected_codes,
                start_date=str(part["trade_date"].min()),
                end_date=str(part["trade_date"].max()),
            )
            recent_moneyflow = source_frames.get("moneyflow", pd.DataFrame())
            if not recent_moneyflow.empty and "ts_code" in recent_moneyflow.columns:
                source_codes = self._research_code(recent_moneyflow["ts_code"])
                recent_moneyflow = recent_moneyflow.loc[
                    source_codes.isin(selected_codes)
                ].copy()
            moneyflow = self._merge_source_versions(
                [historical_moneyflow, recent_moneyflow]
            )
            part = attach_moneyflow_point_in_time_features(part, moneyflow)
            part = self._attach_latest_source_features(part, source_features)
            part = self._compact_numeric_features(part, copy=False)
            self.store.write_parquet_atomic(
                batch_root / f"batch-{count:04d}.parquet",
                part,
            )
            count += 1
            del part
        return count

    @staticmethod
    def _attach_latest_source_features(
        featured: pd.DataFrame,
        source_features: pd.DataFrame,
    ) -> pd.DataFrame:
        if source_features.empty:
            return featured
        indexed = source_features.set_index("code")
        latest_indices = (
            featured.sort_values("trade_date")
            .groupby("code")
            .tail(1)
            .index
        )
        for column in indexed.columns.difference(["ts_code"]):
            mapped = featured.loc[latest_indices, "code"].map(indexed[column])
            mapped = pd.Series(
                pd.to_numeric(mapped, errors="coerce").to_numpy(
                    dtype=float, na_value=np.nan
                ),
                index=mapped.index,
            )
            if column not in featured.columns:
                featured[column] = np.nan
            current = featured.loc[latest_indices, column]
            featured.loc[latest_indices, column] = current.where(
                current.notna(), mapped
            ).to_numpy()
        return featured

    @staticmethod
    def _write_feature_registry_manifest(frame: pd.DataFrame, destination: Path) -> Path:
        definitions = {item.name: asdict(item) for item in DEFAULT_REGISTRY}
        excluded = {
            "open", "high", "low", "close", "volume", "amount",
            "amount_yuan", "amount_thousand_yuan",
            "code", "trade_date", "feature_observed_at",
        }
        numeric_features = sorted(
            column for column in frame.select_dtypes(include=[np.number]).columns
            if column not in excluded
        )
        present = sorted(set(numeric_features).intersection(definitions))
        manifest = {
            "registry_hash": DEFAULT_REGISTRY_HASH,
            "registry_version": "research-feature-registry-v2",
            "registered_features": sorted(definitions),
            "present_registered_features": present,
            "present_unregistered_numeric_features": sorted(set(numeric_features).difference(definitions)),
            "definitions": definitions,
            "universe": {
                "quality": (
                    str(frame["universe_quality"].iloc[0])
                    if "universe_quality" in frame.columns and not frame.empty
                    else "unavailable"
                ),
                "unbiased_universe": bool(
                    "unbiased_universe" in frame.columns
                    and not frame.empty
                    and frame["unbiased_universe"].fillna(False).astype(bool).all()
                ),
                "contract_version": (
                    str(frame["universe_contract_version"].iloc[0])
                    if "universe_contract_version" in frame.columns and not frame.empty
                    else None
                ),
                "membership_source": (
                    str(frame["membership_source"].iloc[0])
                    if "membership_source" in frame.columns and not frame.empty
                    else None
                ),
            },
        }
        path = destination.with_suffix(".metadata.json")
        write_text_atomic(path, json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _full_history_codes(self, all_codes: list[str]) -> set[str]:
        if self.market != "a_share":
            return set(all_codes)
        available = set(all_codes)
        materialized = self._materialized_full_history_codes(available)
        if materialized is not None:
            return materialized
        if self.max_full_history_instruments is None:
            return available
        priority: list[str] = []
        market_root = self.repo_root / "data" / "a_share"
        if market_root.exists():
            for data_dir in sorted(path for path in market_root.iterdir() if path.is_dir()):
                positions = data_dir / "positions.csv"
                if positions.exists():
                    try:
                        frame = pd.read_csv(positions, dtype={"code": str})
                        priority.extend(frame.get("code", pd.Series(dtype=str)).dropna().astype(str).str.split(".").str[0])
                    except (OSError, pd.errors.ParserError):
                        pass
                pending = data_dir / "pending_orders.json"
                if pending.exists():
                    try:
                        payload = json.loads(pending.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        payload = []
                    for batch in payload if isinstance(payload, list) else []:
                        orders = batch.get("orders") if isinstance(batch, dict) else None
                        for order in orders if isinstance(orders, list) else ([batch] if isinstance(batch, dict) else []):
                            code = str(order.get("code") or "").split(".")[0]
                            if code:
                                priority.append(code)
        financials = self._load_persisted_source_frames().get("fina_indicator", pd.DataFrame())
        prioritized: list[str] = []
        if not financials.empty and "ts_code" in financials.columns:
            financial_codes = financials["ts_code"].dropna().astype(str).str.split(".").str[0]
            prioritized.extend(code for code in financial_codes if code in available and code not in prioritized)
        prioritized.extend(code for code in priority if code in available and code not in prioritized)
        remaining = sorted(
            available.difference(prioritized),
            key=lambda code: hashlib.sha256(f"a-share-research-v1|{code}".encode("utf-8")).hexdigest(),
        )
        ordered = [*prioritized, *remaining]
        return set(ordered[: self.max_full_history_instruments])

    def _materialized_full_history_codes(self, available: set[str]) -> set[str] | None:
        payload = self._a_share_materialization_manifest()
        if payload is None:
            return None
        raw_codes = payload.get("historical_union_codes")
        if not isinstance(raw_codes, list):
            raise ValueError("a_share_materialization_union_missing")
        codes = {
            str(code).split(".")[0].zfill(6)
            for code in raw_codes
            if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", str(code))
        }
        if len(codes) != int(payload.get("historical_union_count") or -1):
            raise ValueError("a_share_materialization_union_count_mismatch")
        missing = codes.difference(available)
        if missing:
            raise ValueError(
                f"a_share_materialized_history_missing:{','.join(sorted(missing)[:10])}"
            )
        return codes

    def _research_source_codes(
        self,
        available_codes: list[str],
        full_history_codes: set[str],
    ) -> list[str]:
        available = set(available_codes)
        financials = self._load_persisted_source_frames().get("fina_indicator", pd.DataFrame())
        covered = set()
        if not financials.empty and "ts_code" in financials.columns:
            covered = set(
                financials["ts_code"].dropna().astype(str).str.split(".").str[0]
            )
        preferred = sorted(available.intersection(full_history_codes))
        remaining = sorted(available.difference(full_history_codes))
        return [
            *[code for code in preferred if code not in covered],
            *[code for code in remaining if code not in covered],
            *[code for code in preferred if code in covered],
            *[code for code in remaining if code in covered],
        ]

    def _collect_sources(self, codes: list[str]) -> SourceCollection:
        if self.market == "cn_qdii_etf":
            from ..markets.cn_qdii_etf.data_provider import make_provider

            provider = make_provider(
                cache_dir=self._cache_dir(),
                offline=False,
                as_of=self.as_of,
            )
            return provider.collect_research_sources(codes)

        from ..markets.a_share.data_provider import make_provider, ts_code_for_stock
        from ..markets.a_share.market_data import collect_research_sources

        provider = make_provider(
            cache_dir=self._cache_dir(),
            offline=False,
            as_of=self.as_of,
        )
        if not hasattr(provider, "pro"):
            return SourceCollection(
                frames={},
                health=pd.DataFrame([{"source": "tushare", "failed": True, "error": "source_unavailable"}]),
            )

        class SafeProProxy:
            def __getattr__(self, endpoint: str):
                def call(**kwargs):
                    return provider._safe_pro_call(
                        f"research_{endpoint}",
                        lambda: getattr(provider.pro, endpoint)(**kwargs),
                    )

                return call

        return collect_research_sources(
            SafeProProxy(),
            as_of=self.as_of,
            codes=[ts_code_for_stock(code) for code in codes[:40]],
            benchmark_codes=self._benchmark_codes(),
        )

    def _baseline_accounts(self) -> list[dict[str, Any]]:
        try:
            accounts = competition.load_baseline(self.repo_root, self.market).get("accounts") or []
        except (FileNotFoundError, ValueError):
            accounts = []
        if accounts:
            return [dict(account) for account in accounts]
        if self.market == "a_share":
            return [
                {"id": "hs300", "scope": "hs300", "benchmark": "000300", "cash": 500_000},
                {"id": "zz500", "scope": "zz500", "benchmark": "000905", "cash": 500_000},
            ]
        return [
            {"id": "us_exposure", "scope": "us_exposure", "benchmark": "513100.SH", "cash": 500_000},
            {"id": "hk_exposure", "scope": "hk_exposure", "benchmark": "159920.SZ", "cash": 500_000},
        ]

    def _prediction_universe(
        self,
        features: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Revalidate cached features against the configured point-in-time universe."""

        accounts = self._baseline_accounts()
        account_to_scope = {
            str(account.get("id")): str(account.get("scope") or account.get("id"))
            for account in accounts
        }
        required = {
            "account_id",
            "research_scope",
            "universe_contract_version",
            "unbiased_universe",
        }
        current_contract = required.issubset(features.columns) and bool(
            features["universe_contract_version"]
            .astype("string")
            .eq("pit-universe-v1")
            .all()
        )
        if current_contract:
            scoped = features.copy()
            source = "cached_point_in_time_contract"
            universe_metadata: dict[str, Any] = {
                "quality": "available",
                "unbiased_universe": bool(
                    scoped["unbiased_universe"].fillna(False).all()
                ),
                "universe_contract_version": "pit-universe-v1",
            }
        else:
            universe = attach_point_in_time_universe(
                features,
                repo_root=self.repo_root,
                market=self.market,
                accounts=accounts,
                as_of=self.as_of,
            )
            scoped = universe.frame
            source = "revalidated_point_in_time_universe"
            universe_metadata = dict(universe.metadata)

        if not bool(universe_metadata.get("unbiased_universe")):
            reasons = ",".join(universe_metadata.get("quality_reasons") or [])
            raise ValueError(f"prediction_universe_unavailable:{self.market}:{reasons}")
        input_rows = int(len(features))
        if scoped.empty:
            raise ValueError(f"prediction_universe_empty:{self.market}")
        account_ids = scoped["account_id"].astype("string")
        research_scopes = scoped["research_scope"].astype("string")
        valid = pd.Series(False, index=scoped.index)
        for account_id, scope in account_to_scope.items():
            valid |= account_ids.eq(account_id) & research_scopes.eq(scope)
        scoped = scoped.loc[valid].copy()
        if scoped.empty:
            raise ValueError(f"prediction_universe_scope_mismatch:{self.market}")
        return scoped, {
            **universe_metadata,
            "source": source,
            "input_rows": input_rows,
            "eligible_rows": int(len(scoped)),
            "rejected_rows": int(input_rows - len(scoped)),
            "accounts": sorted(account_to_scope),
        }

    def _research_portfolio_contract(
        self,
        account_scope: str | None = None,
    ) -> dict[str, Any]:
        execution_policy = (
            {
                "version": "cost-aware-aim-v1",
                "rank_buffer_pct": 0.50,
                "minimum_target_change": 0.01,
                "partial_adjustment_rate": 0.35,
                "max_daily_turnover": 0.10,
                "cost_safety_multiple": 1.50,
                "alpha_persistence": 1.0,
            }
            if self.market == "a_share"
            else {
                "version": "cost-aware-aim-v1",
                "rank_buffer_pct": 0.80,
                "minimum_target_change": 0.02,
                "partial_adjustment_rate": 0.25,
                "max_daily_turnover": 0.08,
                "cost_safety_multiple": 2.00,
                "alpha_persistence": 1.0,
            }
        )
        try:
            baseline = competition.load_baseline(self.repo_root, self.market)
        except (FileNotFoundError, ValueError):
            baseline = {}
        if baseline:
            accounts = [dict(account) for account in baseline.get("accounts") or []]
            if account_scope:
                accounts = [
                    account
                    for account in accounts
                    if str(account.get("scope") or account.get("id")) == str(account_scope)
                ]
                if not accounts:
                    raise ValueError(f"research_account_scope_unknown:{account_scope}")
            return {
                "accounts": accounts,
                "trading": dict(baseline.get("trading") or {}),
                "schedule": dict(baseline.get("schedule") or {}),
                "execution_policy": execution_policy,
            }
        accounts = self._baseline_accounts()
        if account_scope:
            accounts = [
                account
                for account in accounts
                if str(account.get("scope") or account.get("id")) == str(account_scope)
            ]
            if not accounts:
                raise ValueError(f"research_account_scope_unknown:{account_scope}")
        return {
            "accounts": accounts,
            "trading": (
                {
                    "lot_size": 100, "commission_rate": 0.0003,
                    "min_commission": 5.0, "stamp_tax_rate": 0.0005,
                    "slippage_rate": 0.0005, "max_single_weight": 0.05,
                }
                if self.market == "a_share"
                else {
                    "lot_size_default": 100, "commission_rate": 0.0003,
                    "stamp_tax_rate": 0.0, "slippage_bps": 5.0,
                    "settlement_days": 1, "max_single_weight": 0.20,
                }
            ),
            "schedule": {"execution": "next_trading_day_open"},
            "execution_policy": execution_policy,
        }

    def _benchmark_codes(self) -> list[str]:
        return [
            str(account.get("benchmark") or "").split(".")[0].zfill(6)
            for account in self._baseline_accounts()
            if account.get("benchmark")
        ]

    def _benchmark_history(
        self,
        features: pd.DataFrame,
        *,
        account: dict[str, Any] | None = None,
    ) -> tuple[pd.DataFrame, float]:
        accounts = [account] if account is not None else self._baseline_accounts()
        total_cash = sum(float(account.get("cash") or 0.0) for account in accounts) or float(len(accounts) or 1)
        raw_frames = self._load_persisted_source_frames()
        opens: list[pd.Series] = []
        closes: list[pd.Series] = []
        weights: list[float] = []
        codes: list[str] = []
        for account in accounts:
            code = str(account.get("benchmark") or "").split(".")[0].zfill(6)
            frame = raw_frames.get(f"benchmark_{code}", pd.DataFrame()).copy()
            if frame.empty and self.market == "cn_qdii_etf":
                frame = features.loc[features["code"].astype("string").str.zfill(6).eq(code)].copy()
            if frame.empty or not {"trade_date", "open", "close"}.issubset(frame.columns):
                continue
            frame["trade_date"] = frame["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
            frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame = frame.dropna(subset=["trade_date", "open", "close"]).sort_values("trade_date").drop_duplicates("trade_date", keep="last")
            if frame.empty or float(frame.iloc[0]["close"]) <= 0:
                continue
            scale = float(frame.iloc[0]["close"])
            indexed = frame.set_index("trade_date")
            opens.append(indexed["open"] / scale)
            closes.append(indexed["close"] / scale)
            weights.append(float(account.get("cash") or 1.0) / total_cash)
            codes.append(code)
        if not closes:
            raise ValueError(f"research_benchmark_missing:{self.market}")
        open_panel = pd.concat(opens, axis=1)
        close_panel = pd.concat(closes, axis=1)
        open_panel.columns = codes
        close_panel.columns = codes
        weight_series = pd.Series(weights, index=codes, dtype=float)
        available = open_panel.notna() & close_panel.notna()
        available_weight = available.mul(weight_series, axis=1).sum(axis=1)
        composite_open = open_panel.mul(weight_series, axis=1).sum(axis=1, min_count=1) / available_weight.replace(0.0, np.nan)
        composite_close = close_panel.mul(weight_series, axis=1).sum(axis=1, min_count=1) / available_weight.replace(0.0, np.nan)
        benchmark = pd.concat(
            [composite_open.rename("open"), composite_close.rename("close")],
            axis=1,
        ).dropna().reset_index().rename(columns={"index": "trade_date"})
        feature_dates = set(features["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8])
        benchmark_dates = set(benchmark["trade_date"].astype(str))
        coverage = len(feature_dates.intersection(benchmark_dates)) / max(len(feature_dates), 1)
        benchmark["benchmark_code"] = "composite:" + "|".join(codes)
        return benchmark, float(coverage)

    def _load_persisted_source_frames(
        self,
        names: set[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        requested = set(names) if names is not None else None
        if self._persisted_source_frames_cache is not None:
            if requested is None:
                return self._persisted_source_frames_cache
            if requested.issubset(self._persisted_source_frames_cache):
                return {
                    name: self._persisted_source_frames_cache[name]
                    for name in requested
                }
        market_root = self.research_root / "raw" / self.market
        if not market_root.exists():
            return {}
        if self.market == "a_share":
            parent_markers = [
                marker
                for marker in market_root.glob(".materialization_in_progress.*")
                if marker.name.rsplit(".", 1)[-1] <= self.run_key
            ]
            if parent_markers:
                raise ValueError("a_share_materialization_in_progress")
        runs = sorted(
            path for path in market_root.iterdir()
            if path.is_dir() and path.name.isdigit() and path.name <= self.run_key
        )
        if not runs:
            return {}
        if self.market == "a_share":
            for run in runs:
                if (run / ".materialization_in_progress").exists():
                    raise ValueError(
                        f"a_share_materialization_in_progress:{run.name}"
                    )
        for index in range(len(runs) - 1, -1, -1):
            manifest_path = runs[index] / "snapshot_manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                isinstance(manifest, dict)
                and manifest.get("schema_version") == 1
                and manifest.get("mode") == "cumulative"
            ):
                runs = runs[index:]
                break
        versions: dict[str, list[pd.DataFrame]] = {}
        for run in runs:
            materialization = (
                self._a_share_materialization_manifest(run.name)
                if self.market == "a_share"
                else None
            )
            if materialization is None:
                paths = [
                    path
                    for path in run.glob("*.parquet")
                    if path.stem != "source_health"
                ]
                records: dict[Path, dict[str, object]] = {}
            else:
                records = {}
                for record in materialization["outputs"].values():
                    relative = str(record["path"])
                    path = (self.repo_root / relative).absolute()
                    if path.suffix != ".parquet" or path.parent.resolve() != run.resolve():
                        continue
                    records[path] = record
                paths = sorted(records)
                actual = {path.absolute() for path in run.glob("*.parquet")}
                declared = set(paths)
                extra = sorted(path.name for path in actual - declared)
                if extra:
                    raise ValueError(
                        f"a_share_materialization_undeclared_raw_output:{','.join(extra)}"
                    )
                missing = sorted(path.name for path in declared - actual)
                if missing:
                    raise ValueError(
                        f"a_share_materialized_output_missing:{','.join(missing)}"
                    )
            for path in paths:
                if materialization is None:
                    if requested is not None and path.stem not in requested:
                        continue
                    frame = pd.read_parquet(path, dtype_backend="pyarrow")
                else:
                    record = records[path]
                    expected_hash = record.get("sha256")
                    if not isinstance(expected_hash, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", expected_hash
                    ):
                        raise ValueError(
                            f"a_share_materialized_output_hash_missing:{path.name}"
                        )
                    if self._file_sha256(path) != expected_hash:
                        raise ValueError(
                            f"a_share_materialized_output_hash_mismatch:{path.name}"
                        )
                    parquet_file = pq.ParquetFile(path)
                    if parquet_file.metadata.num_rows != int(
                        record.get("rows") or 0
                    ):
                        raise ValueError(
                            f"a_share_materialized_output_rows_mismatch:{path.name}"
                        )
                    if requested is not None and path.stem not in requested:
                        date_column = next(
                            (
                                column
                                for column in (
                                    "trade_date", "ann_date", "cal_date",
                                    "start_date",
                                )
                                if column in parquet_file.schema.names
                            ),
                            None,
                        )
                        audit_columns = [date_column] if date_column else []
                        audit_frame = parquet_file.read(
                            columns=audit_columns
                        ).to_pandas(types_mapper=pd.ArrowDtype)
                        self._validate_materialized_frame(
                            audit_frame, record, name=path.name
                        )
                        continue
                    frame = pd.read_parquet(path, dtype_backend="pyarrow")
                    self._validate_materialized_frame(frame, record, name=path.name)
                versions.setdefault(path.stem, []).append(frame)
        frames = {
            name: self._merge_source_versions(items)
            for name, items in versions.items()
        }
        if requested is None:
            self._persisted_source_frames_cache = frames
        return frames

    @classmethod
    def _merge_source_frame_maps(
        cls,
        *maps: dict[str, pd.DataFrame],
    ) -> dict[str, pd.DataFrame]:
        names = set().union(*(mapping.keys() for mapping in maps))
        return {
            name: cls._merge_source_versions(
                [mapping[name] for mapping in maps if name in mapping]
            )
            for name in names
        }

    @staticmethod
    def _merge_source_versions(frames: list[pd.DataFrame]) -> pd.DataFrame:
        non_empty = [frame for frame in frames if isinstance(frame, pd.DataFrame) and not frame.empty]
        if not non_empty:
            return pd.DataFrame()
        combined = pd.concat(non_empty, ignore_index=True, sort=False)
        if "observed_at" in combined.columns:
            combined = combined.sort_values("observed_at", kind="stable")
        identity_candidates = (
            "ts_code", "code", "index_code", "con_code", "trade_date",
            "ann_date", "nav_date", "end_date", "source_date", "date",
            "month", "in_date", "out_date", "bz_item", "series",
        )
        identity = [column for column in identity_candidates if column in combined.columns]
        if identity:
            combined = combined.drop_duplicates(identity, keep="last")
        else:
            value_columns = [
                column for column in combined.columns
                if column not in {"source", "observed_at"}
            ]
            if value_columns:
                combined = combined.drop_duplicates(value_columns, keep="last")
        return combined.reset_index(drop=True)

    def _attach_a_share_industry_fallback(self, features: pd.DataFrame) -> pd.DataFrame:
        cache = self._cache_dir()
        materialized = (
            self.research_root / "raw" / "a_share" / self.run_key
            / "stock_basic.parquet"
        )
        source = "tushare_stock_basic_snapshot"
        if materialized.exists():
            basic = pd.read_parquet(materialized)
        else:
            candidates = sorted(
                path for path in cache.glob("stock_basic_*.csv")
                if path.stem.rsplit("_", 1)[-1].isdigit()
                and path.stem.rsplit("_", 1)[-1] <= self.run_key
            )
            if not candidates:
                return features
            basic = pd.read_csv(candidates[-1], dtype={"code": str, "ts_code": str})
            source = "tushare_stock_basic_cache"
        if "code" not in basic.columns and "ts_code" in basic.columns:
            basic["code"] = basic["ts_code"].astype("string").str.split(".").str[0]
        if not {"code", "industry"}.issubset(basic.columns):
            return features
        basic["code"] = basic["code"].astype("string").str.zfill(6)
        mapping = basic.drop_duplicates("code", keep="last").set_index("code")["industry"]
        result = features.copy()
        if "industry" not in result.columns:
            result["industry"] = "unclassified"
        if "industry_l2" not in result.columns:
            result["industry_l2"] = "unclassified"
        if "industry_source" not in result.columns:
            result["industry_source"] = "unclassified"
        fallback = result["code"].astype("string").str.zfill(6).map(mapping)
        missing = result["industry"].isna() | result["industry"].eq("unclassified")
        result.loc[missing, "industry"] = fallback.loc[missing].fillna("unclassified")
        result.loc[missing & fallback.notna(), "industry_l2"] = fallback.loc[missing & fallback.notna()]
        result.loc[missing & fallback.notna(), "industry_source"] = source
        return result

    def _artifact_path(self, name: str) -> Path:
        return self.research_root / name / self.market / f"{self.run_key}.parquet"

    def _attach_qdii_metadata(self, features: pd.DataFrame) -> pd.DataFrame:
        from ..markets.cn_qdii_etf.research_catalog import build_research_catalog
        from ..markets.cn_qdii_etf.universe import build_catalog_candidates

        metadata_rows: list[dict[str, Any]] = []
        cache = self._cache_dir()
        basic_paths = [path for name in ("fund_basic_E_v2.csv", "fund_basic_E.csv") if (path := cache / name).exists()]
        if basic_paths:
            basic = pd.read_csv(basic_paths[0], dtype={"ts_code": str, "list_date": str, "delist_date": str})
            metadata_rows.extend(build_catalog_candidates(basic, as_of=self.as_of))
            metadata_rows.extend(build_research_catalog(basic, as_of=self.as_of).to_dict(orient="records"))
        shadow_root = self.repo_root / "data" / "cn_qdii_etf" / "research" / "shadow"
        catalogs = sorted(shadow_root.glob("*/catalog.csv")) if shadow_root.exists() else []
        if catalogs:
            metadata_rows.extend(pd.read_csv(catalogs[-1], dtype={"code": str}).to_dict(orient="records"))
        snapshot_path = self.repo_root / "data" / "cn_qdii_etf" / self.agent / "selection_snapshot.json"
        if snapshot_path.exists():
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                snapshot = {}
            for scope, block in (snapshot.get("scopes") or {}).items():
                if not isinstance(block, dict):
                    continue
                for row in [*(block.get("ranked") or []), *(block.get("selected") or [])]:
                    if isinstance(row, dict):
                        metadata_rows.append({**row, "research_scope": scope, "scope": scope})
        if not metadata_rows:
            result = features.copy()
            result["industry"] = "unclassified"
            result["industry_l2"] = "unclassified"
            return result
        metadata = pd.DataFrame(metadata_rows)
        metadata["code"] = metadata["code"].astype("string").str.split(".").str[0].str.zfill(6)
        for column in ("sector", "theme", "research_scope", "scope", "index_key", "asset_class", "country"):
            if column not in metadata.columns:
                metadata[column] = pd.NA
        metadata["industry"] = metadata["sector"].fillna(metadata["theme"]).fillna(metadata["research_scope"]).fillna(metadata["scope"])
        metadata["industry_l2"] = metadata["index_key"].fillna(metadata["research_scope"]).fillna(metadata["scope"])
        metadata = metadata.drop_duplicates("code", keep="first").set_index("code")
        result = features.copy()
        for column in ("industry", "industry_l2", "research_scope", "index_key", "asset_class", "country", "theme"):
            result[column] = result["code"].astype("string").map(metadata[column] if column in metadata.columns else pd.Series(dtype="string"))
        result["industry"] = result["industry"].fillna("unclassified")
        result["industry_l2"] = result["industry_l2"].fillna("unclassified")
        return result

    def _load_regime_source_frames(self) -> dict[str, pd.DataFrame]:
        frames: dict[str, pd.DataFrame] = {}
        markets = (self.market, "cn_qdii_etf" if self.market == "a_share" else "a_share")
        for market in markets:
            market_root = self.research_root / "raw" / market
            if not market_root.exists():
                continue
            runs = sorted(
                path for path in market_root.iterdir()
                if path.is_dir() and path.name.isdigit() and path.name <= self.run_key
            )
            if not runs:
                continue
            for source in self._REGIME_SOURCE_NAMES:
                path = next(
                    (run / f"{source}.parquet" for run in reversed(runs) if (run / f"{source}.parquet").exists()),
                    None,
                )
                if source not in frames and path is not None:
                    frames[source] = pd.read_parquet(path)
        return frames

    def _current_regime_context(self) -> tuple[str, float]:
        path = self._artifact_path("regimes")
        if not path.exists():
            return "unknown", 0.5
        regimes = pd.read_parquet(path)
        if regimes.empty or "composite_regime" not in regimes.columns:
            return "unknown", 0.5
        if "scope" in regimes.columns and regimes["scope"].eq("market").any():
            regimes = regimes.loc[regimes["scope"].eq("market")]
        if "trade_date" in regimes.columns:
            regimes = regimes.loc[regimes["trade_date"].astype("string") <= self.run_key].sort_values("trade_date")
        if regimes.empty:
            return "unknown", 0.5
        latest = regimes.iloc[-1]
        regime = str(latest.get("composite_regime") or "unknown")
        if regime == "unknown":
            return regime, 0.5
        recent = regimes.tail(10)["composite_regime"].astype("string")
        consistency = float(recent.eq(regime).mean())
        coverage = float(pd.to_numeric(pd.Series([latest.get("regime_coverage")]), errors="coerce").fillna(0.0).iloc[0])
        stability = float(np.clip(0.7 * consistency + 0.3 * coverage, 0.0, 1.0))
        return regime, stability

    @staticmethod
    def _score_regime_daily(frame: pd.DataFrame) -> pd.DataFrame:
        scored = frame.sort_values("trade_date").copy()
        scored["trend_score"] = np.tanh(pd.to_numeric(scored["momentum_20"], errors="coerce").fillna(0.0) * 8.0)
        volatility = pd.to_numeric(scored["realized_volatility_20"], errors="coerce")
        center = volatility.expanding(min_periods=10).median()
        scale = volatility.expanding(min_periods=10).std().replace(0.0, np.nan)
        scored["volatility_score"] = (volatility - center) / scale
        scored["liquidity_score"] = np.tanh(
            (pd.to_numeric(scored["volume_ratio_5_20"], errors="coerce").fillna(1.0) - 1.0) * 2.0
        )
        return scored

    def run_research(self) -> dict[str, Any]:
        features = self.store.read_feature_snapshot(self.market, self.as_of)
        features_rows = len(features)
        price_columns = [
            column for column in ("code", "trade_date", "open", "high", "low", "close")
            if column in features.columns
        ]
        scoped = "account_id" in features.columns and not features["account_id"].eq("unscoped").all()
        label_parts: list[pd.DataFrame] = []
        benchmark_coverages: list[float] = []
        for account in self._baseline_accounts() if scoped else [None]:
            account_features = (
                features.loc[features["account_id"].eq(str(account.get("id")))].copy()
                if account is not None
                else features
            )
            if account_features.empty:
                continue
            benchmark, coverage = self._benchmark_history(account_features, account=account)
            if coverage < 0.95:
                account_name = str(account.get("id")) if account is not None else "composite"
                raise ValueError(f"research_benchmark_coverage:{account_name}:{coverage:.4f}")
            part = build_forward_labels(
                account_features[price_columns],
                benchmark=benchmark,
                require_benchmark=True,
            )
            if account is not None:
                part["account_id"] = str(account.get("id"))
                part["research_scope"] = str(account.get("scope") or account.get("id"))
                part["benchmark_code"] = str(account.get("benchmark") or "")
            for column in (
                "universe_quality", "unbiased_universe",
                "universe_contract_version", "membership_source",
            ):
                if column in account_features.columns:
                    part[column] = account_features[column].iloc[0]
            label_parts.append(part)
            benchmark_coverages.append(float(coverage))
        if not label_parts:
            raise ValueError(f"research_label_scope_missing:{self.market}")
        labels = pd.concat(label_parts, ignore_index=True, sort=False)
        benchmark_coverage = min(benchmark_coverages)
        label_parts.clear()
        del label_parts, part, account_features, benchmark
        labels_rows = len(labels)
        self.store.write_label_snapshot(self.market, self.as_of, labels)
        del labels, price_columns
        gc.collect()

        market_daily = features.groupby("trade_date", as_index=False).agg(
            momentum_20=("momentum_20", "median"),
            realized_volatility_20=("realized_volatility_20", "median"),
            volume_ratio_5_20=("volume_ratio_5_20", "median"),
        )
        market_daily = self._score_regime_daily(market_daily)
        market_daily["scope"] = "market"
        regime_components = build_regime_components(
            self._load_regime_source_frames(),
            market_daily["trade_date"],
        )
        market_daily = market_daily.merge(regime_components, on="trade_date", how="left")
        regime_inputs = [market_daily]
        if "industry" in features.columns:
            industry_source = features.loc[
                features["industry"].notna() & features["industry"].ne("unclassified")
            ]
            if not industry_source.empty:
                industry_daily = industry_source.groupby(["industry", "trade_date"], as_index=False).agg(
                    momentum_20=("momentum_20", "median"),
                    realized_volatility_20=("realized_volatility_20", "median"),
                    volume_ratio_5_20=("volume_ratio_5_20", "median"),
                    instruments=("code", "nunique"),
                )
                industry_daily = industry_daily.loc[industry_daily["instruments"] >= 2]
                scored_industries = [
                    self._score_regime_daily(group).assign(scope=f"industry:{industry}")
                    for industry, group in industry_daily.groupby("industry", sort=False)
                ]
                if scored_industries:
                    industry_regimes = pd.concat(scored_industries, ignore_index=True)
                    industry_regimes = industry_regimes.merge(regime_components, on="trade_date", how="left")
                    regime_inputs.append(industry_regimes)
        regimes = classify_regimes(pd.concat(regime_inputs, ignore_index=True, sort=False))
        self.store.write_parquet_atomic(self._artifact_path("regimes"), regimes)

        market_regimes = regimes.loc[regimes["scope"].eq("market")]
        regime_by_date = dict(zip(
            market_regimes["trade_date"].astype(str),
            market_regimes["composite_regime"].astype(str),
        ))
        events_path = self._artifact_path("events")
        events_rows = write_events_incremental(
            features,
            market=self.market,
            destination=events_path,
            regime_by_date=regime_by_date,
        )
        industry_source = industry_daily = scored_industries = industry_regimes = None
        market_daily = regime_inputs = regime_components = market_regimes = None
        del features
        gc.collect()
        if events_rows:
            event_study = build_event_study_from_parquet(
                events_path,
                self.store.label_snapshot_path(self.market, self.as_of),
            )
        else:
            event_study = pd.DataFrame()
        gc.collect()
        self.store.write_parquet_atomic(self._artifact_path("event_studies"), event_study)
        pruned_artifacts = self.store.prune_dated_artifacts(
            self.market,
            categories=("features", "labels", "events", "regimes", "event_studies"),
            keep_recent=3,
            keep_monthly=3,
        )
        return {
            "status": "complete",
            "stages": ["features", "labels", "events", "regimes", "event_study"],
            "features_rows": features_rows,
            "labels_rows": labels_rows,
            "benchmark_coverage": round(benchmark_coverage, 4),
            "events_rows": events_rows,
            "regime_rows": len(regimes),
            "event_study_rows": len(event_study),
            "pruned_artifacts": pruned_artifacts,
        }

    def _model_root(
        self,
        horizon: int,
        account_scope: str | None = None,
    ) -> Path:
        if account_scope:
            return self.store.model_root(self.market, account_scope, horizon)
        return self.research_root / "models" / self.market / str(horizon)

    def train_models(self, *, account_scope: str | None = None) -> dict[str, Any]:
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        features = self.store.read_feature_snapshot(self.market, snapshot_date)
        labels = self.store.read_label_snapshot(self.market, snapshot_date)
        if account_scope is None:
            scope_column = (
                "research_scope"
                if "research_scope" in features.columns
                else "account_id" if "account_id" in features.columns else ""
            )
            scopes = (
                sorted(
                    str(value).strip()
                    for value in features[scope_column].dropna().astype(str).unique()
                    if str(value).strip()
                )
                if scope_column else []
            )
            if scopes:
                scoped_results = [
                    self.train_models(account_scope=scope)
                    for scope in scopes
                ]
                trained = [
                    item
                    for result in scoped_results
                    for item in result.get("trained") or []
                ]
                failures = [
                    item
                    for result in scoped_results
                    for item in result.get("failures") or []
                ]
                return {
                    "status": "complete" if trained else "failed",
                    "snapshot_date": snapshot_date,
                    "account_scopes": scopes,
                    "trained": trained,
                    "failures": failures,
                }
        normalized_scope = str(account_scope or "").strip()
        if normalized_scope:
            feature_scope_column = (
                "research_scope"
                if "research_scope" in features.columns
                else "account_id" if "account_id" in features.columns else ""
            )
            if not feature_scope_column:
                raise ValueError("model_scope_missing")
            features = features.loc[
                features[feature_scope_column].astype(str).eq(normalized_scope)
            ].copy()
            label_scope_column = (
                "research_scope"
                if "research_scope" in labels.columns
                else "account_id" if "account_id" in labels.columns else ""
            )
            if label_scope_column:
                labels = labels.loc[
                    labels[label_scope_column].astype(str).eq(normalized_scope)
                ].copy()
            if features.empty or labels.empty:
                raise ValueError(f"model_scope_data_missing:{normalized_scope}")
            features = build_account_feature_view(
                features,
                account_scope=normalized_scope,
            )
        excluded = {
            "code", "trade_date", "open", "high", "low", "close", "volume", "amount",
            "horizon", "label", "label_end_date", "absolute_return", "benchmark_return",
            "entry_date", "entry_price", "label_contract_version",
            "excess_return", "threshold", "max_favorable_excursion", "max_adverse_excursion",
        }
        numeric = [
            column
            for column in features.select_dtypes(include=[np.number]).columns
            if column not in excluded
        ]
        registered = {
            item.name for item in DEFAULT_REGISTRY if self.market in item.markets
        }
        regime_only = {item.name for item in MACRO_FEATURES}
        feature_columns = [
            column for column in numeric
            if column in registered
            and column not in regime_only
            and features[column].notna().mean() >= 0.55
        ]
        if normalized_scope:
            feature_contract = account_feature_contract(
                self.market,
                normalized_scope,
                3,
            )
            allowed = set(feature_contract.allowed_features)
            feature_columns = [
                column
                for column in feature_columns
                if column in allowed
                and features[column].notna().mean() >= feature_contract.minimum_coverage
            ]
        else:
            feature_contract = None
        intelligence_columns = {item.name for item in INTELLIGENCE_FEATURES}
        permitted_intelligence = model_iteration_features(
            self.repo_root / "configs" / "intelligence_factors.json"
        )
        feature_columns = [
            column for column in feature_columns
            if column not in intelligence_columns or column in permitted_intelligence
        ]
        trained: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for horizon in (3, 5, 10, 20):
            try:
                horizon_labels = labels.loc[labels["horizon"].eq(horizon)]
                if horizon_labels.empty:
                    raise ValueError(f"model_horizon_data_missing:{horizon}")
                join_columns = ["code", "trade_date"]
                if "account_id" in features.columns and "account_id" in horizon_labels.columns:
                    join_columns.append("account_id")
                dataset = features.merge(
                    horizon_labels,
                    on=join_columns,
                    how="inner",
                    suffixes=("", "_label"),
                )
                if dataset.empty:
                    raise ValueError(f"model_horizon_data_missing:{horizon}")
                model_root = self._model_root(horizon, normalized_scope or None)
                portfolio_contract = self._research_portfolio_contract(
                    normalized_scope or None
                )
                contract_hash = hashlib.sha256(
                    json.dumps(portfolio_contract, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16]
                trial_family_id = ":".join((
                    self.market,
                    normalized_scope or "legacy_market_wide",
                    str(horizon),
                    TRAINING_PROTOCOL_VERSION,
                    DEFAULT_REGISTRY_HASH,
                    LABEL_CONTRACT_VERSION,
                    SIMULATOR_VERSION,
                    contract_hash,
                ))
                trial_ledger_key = hashlib.sha256(
                    trial_family_id.encode("utf-8")
                ).hexdigest()[:16]
                trial_ledger = TrialLedger(
                    model_root / "trial_ledgers" / f"{trial_ledger_key}.json"
                )
                declaration = trial_ledger.declare(
                    family_id=trial_family_id,
                    specs=DEFAULT_CLASSICAL_TRIAL_SPECS,
                    objective="exact_net_active_return",
                )
                bundle = train_model_bundle(
                    dataset,
                    feature_columns=feature_columns,
                    horizon=horizon,
                    portfolio_contract=portfolio_contract,
                    account_scope=normalized_scope,
                    feature_selection_policy=(
                        {
                            "max_features": feature_contract.max_features,
                            "max_per_family": feature_contract.max_per_family,
                            "min_coverage": feature_contract.minimum_coverage,
                            "min_stability": 0.75,
                        }
                        if feature_contract is not None else None
                    ),
                    trial_declaration_id=str(declaration["declaration_id"]),
                )
                trial_registry = TrialRegistry(model_root / "trials.jsonl")
                metrics = bundle.metrics
                period_dates = list(metrics.get("portfolio_period_return_dates") or [])
                period_returns = list(metrics.get("portfolio_period_returns") or [])
                trial = trial_registry.record({
                    "trial_id": f"{self.market}:{normalized_scope or 'legacy_market_wide'}:{horizon}:{self.run_key}:{bundle.model_version}",
                    "experiment_id": trial_family_id,
                    "trial_family_id": trial_family_id,
                    "model_version": bundle.model_version,
                    "as_of": self.as_of,
                    "snapshot_date": snapshot_date,
                    "market": self.market,
                    "account_scope": normalized_scope,
                    "horizon": horizon,
                    "protocol": metrics.get("training_protocol_version", "unknown"),
                    "sharpe": float(metrics.get("portfolio_sharpe", 0.0)),
                    "rank_ic": float(metrics.get("rank_ic", 0.0)),
                    "period_returns": period_returns,
                    "oos_returns": [
                        {"date": str(day), "return": float(value)}
                        for day, value in zip(period_dates, period_returns)
                    ],
                    "data_fingerprint": metrics.get("data_fingerprint"),
                    "feature_registry_hash": metrics.get("feature_registry_hash"),
                    "selected_features": list(metrics.get("selected_features") or []),
                })
                trial_results = [
                    dict(item)
                    for item in metrics.get("predeclared_trial_results") or []
                    if isinstance(item, dict)
                ]
                trial_ledger.finalize(
                    run_id=f"{self.run_key}:{bundle.model_version}",
                    declaration_id=str(declaration["declaration_id"]),
                    results=trial_results,
                )
                aligned_trials = [
                    {
                        "trial_id": str(item.get("spec_id") or ""),
                        "oos_returns": list(item.get("oos_returns") or []),
                    }
                    for item in trial_results
                    if item.get("spec_id") and item.get("oos_returns")
                ]
                trial_sharpes = [
                    float(item.get("sharpe", 0.0)) for item in trial_results
                ]
                try:
                    trial_returns = build_aligned_trial_return_matrix(aligned_trials)
                    pbo_alignment_status = "aligned"
                except ValueError as exc:
                    trial_returns = pd.DataFrame()
                    pbo_alignment_status = str(exc)
                valid_trial_count = int(len(trial_returns.columns))
                trial_evidence_status = (
                    "available"
                    if pbo_alignment_status == "aligned" and valid_trial_count >= 4
                    else "insufficient_evidence"
                )
                governance = {
                    "trial_number": int(trial.get("trial_number", len(trial_registry.read()))),
                    "protocol_trial_number": int(trial.get("protocol_trial_number", 1)),
                    "deflated_sharpe_probability": deflated_sharpe_probability(
                        observed_sharpe=float(metrics.get("portfolio_sharpe", 0.0)),
                        trial_sharpes=trial_sharpes,
                        observations=max(len(trial_returns.index), 2),
                        periods_per_year=252.0,
                    ),
                    "probability_of_backtest_overfit": probability_of_backtest_overfit(
                        trial_returns
                    ),
                    "pbo_trial_count": valid_trial_count,
                    "valid_trial_count": valid_trial_count,
                    "declared_trial_count": len(declaration.get("specs") or []),
                    "trial_evidence_status": trial_evidence_status,
                    "trial_declaration_id": declaration["declaration_id"],
                    "trial_family_id": trial_family_id,
                    "pbo_alignment_status": pbo_alignment_status,
                }
                metrics["governance"] = governance
                ResearchLineageStore(
                    self.repo_root / "data" / "shared" / "research_lineage.sqlite3"
                ).append_experiment_trials({**trial, "governance": governance})
                artifact = model_root / f"{self.run_key}-{bundle.model_version}.joblib"
                save_model_bundle(bundle, artifact)
                registry = ModelRegistry(model_root / "registry.json")
                state = registry._read()
                model = state.setdefault("models", {}).setdefault(
                    bundle.model_version,
                    {"status": "research", "gate_history": []},
                )
                model["artifact"] = str(artifact)
                model["account_scope"] = normalized_scope
                model["governance"] = governance
                model.setdefault("registered_at", datetime.now(timezone.utc).isoformat())
                registry._write(state)
                gate = None
                role_gates = {}
                if model.get("status", "research") == "research":
                    role_gates = evaluate_role_activation(
                        activation_evidence_from_metrics(metrics),
                        current_status="research",
                        target_status="shadow",
                    )
                    for role, role_gate in role_gates.items():
                        state = registry.record_role_gate(bundle.model_version, role, role_gate)
                    state = registry.finalize_research_evaluation(bundle.model_version)
                    gate = role_gates["ranker"]
                trained.append({
                    "horizon": horizon,
                    "account_scope": normalized_scope,
                    "model_version": bundle.model_version,
                    "artifact": str(artifact),
                    "status": state["models"][bundle.model_version]["status"],
                    "gate_passed": gate.passed if gate is not None else None,
                    "gate_reasons": list(gate.reasons) if gate is not None else [],
                    "role_gates": {
                        role: {"passed": report.passed, "reasons": list(report.reasons)}
                        for role, report in role_gates.items()
                    },
                })
            except Exception as exc:  # noqa: BLE001 - one horizon must not erase others
                failures.append({
                    "horizon": horizon,
                    "account_scope": normalized_scope,
                    "error": str(exc)[:200],
                })
        return {
            "status": "complete" if trained else "failed",
            "snapshot_date": snapshot_date,
            "trained": trained,
            "failures": failures,
        }

    def run_classical_tournament(
        self,
        *,
        account_scope: str | None = None,
        horizon: int | None = None,
    ) -> dict[str, Any]:
        target_horizon = int(
            horizon if horizon is not None
            else 3 if self.market == "a_share" else 10
        )
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        features = self.store.read_feature_snapshot(self.market, snapshot_date)
        labels = self.store.read_label_snapshot(self.market, snapshot_date)
        scope_column = (
            "research_scope"
            if "research_scope" in features.columns
            else "account_id" if "account_id" in features.columns else ""
        )
        if not scope_column:
            raise ValueError("tournament_scope_missing")
        if account_scope is None:
            scopes = sorted(
                str(value).strip()
                for value in features[scope_column].dropna().astype(str).unique()
                if str(value).strip()
            )
            results = [
                self.run_classical_tournament(
                    account_scope=scope,
                    horizon=target_horizon,
                )
                for scope in scopes
            ]
            return {
                "status": (
                    "shadow_available"
                    if any(item.get("status") == "shadow_available" for item in results)
                    else "no_pass"
                ),
                "snapshot_date": snapshot_date,
                "market": self.market,
                "horizon": target_horizon,
                "account_scopes": scopes,
                "results": results,
            }
        normalized_scope = str(account_scope).strip()
        scoped_features = features.loc[
            features[scope_column].astype(str).eq(normalized_scope)
        ].copy()
        label_scope_column = (
            "research_scope"
            if "research_scope" in labels.columns
            else "account_id" if "account_id" in labels.columns else ""
        )
        scoped_labels = labels.copy()
        if label_scope_column:
            scoped_labels = scoped_labels.loc[
                scoped_labels[label_scope_column].astype(str).eq(normalized_scope)
            ].copy()
        scoped_labels = scoped_labels.loc[
            pd.to_numeric(scoped_labels["horizon"], errors="coerce").eq(target_horizon)
        ].copy()
        if scoped_features.empty or scoped_labels.empty:
            raise ValueError(f"tournament_scope_data_missing:{normalized_scope}")
        scoped_features = build_account_feature_view(
            scoped_features,
            account_scope=normalized_scope,
        )
        join_columns = ["code", "trade_date"]
        if "account_id" in scoped_features.columns and "account_id" in scoped_labels.columns:
            join_columns.append("account_id")
        dataset = scoped_features.merge(
            scoped_labels,
            on=join_columns,
            how="inner",
            suffixes=("", "_label"),
        )
        contract = account_feature_contract(
            self.market,
            normalized_scope,
            target_horizon,
        )
        feature_columns = tuple(
            column
            for column in contract.allowed_features
            if column in dataset.columns
            and pd.to_numeric(dataset[column], errors="coerce").notna().mean()
            >= contract.minimum_coverage
        )
        if self.market == "a_share" and target_horizon == 3:
            declared_specs = a_share_h3_specs(normalized_scope)
        elif self.market == "a_share" and target_horizon == 20:
            declared_specs = a_share_h20_specs(normalized_scope)
        elif self.market == "cn_qdii_etf" and target_horizon == 5:
            declared_specs = qdii_h5_specs(normalized_scope)
        elif self.market == "cn_qdii_etf" and target_horizon == 10:
            declared_specs = qdii_h10_specs(normalized_scope)
        else:
            raise ValueError(
                f"tournament_horizon_not_predeclared:{self.market}:{target_horizon}"
            )
        if any(int(spec.horizon) != target_horizon for spec in declared_specs):
            raise ValueError(
                f"tournament_horizon_not_predeclared:{self.market}:{target_horizon}"
            )
        return execute_classical_tournament(
            self.repo_root,
            market=self.market,
            account_scope=normalized_scope,
            horizon=target_horizon,
            as_of=self.as_of,
            dataset=dataset,
            feature_columns=feature_columns,
            portfolio_contract=self._research_portfolio_contract(normalized_scope),
            specs=declared_specs,
        )

    def run_unified_model_arena(
        self,
        *,
        horizon: int | None = None,
    ) -> dict[str, Any]:
        target_horizon = int(
            horizon
            if horizon is not None
            else 20 if self.market == "a_share" else 5
        )
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        if self.market == "a_share":
            manifest = self._a_share_materialization_manifest(snapshot_date)
            if manifest is None:
                raise ValueError(
                    "unified_arena_a_share_materialization_required:"
                    f"{snapshot_date}"
                )
            features = self.store.read_feature_snapshot(
                self.market,
                snapshot_date,
            )
            dates = pd.to_datetime(
                features["trade_date"]
                .astype("string")
                .str.replace("-", "", regex=False),
                format="%Y%m%d",
                errors="coerce",
            ).dropna()
            history_days = (
                int((dates.max() - dates.min()).days)
                if len(dates) > 1 else 0
            )
            unbiased_coverage = float(
                features.get(
                    "unbiased_universe",
                    pd.Series(False, index=features.index),
                )
                .fillna(False)
                .astype(bool)
                .mean()
            )
            if history_days < int(365.25 * 8):
                raise ValueError(
                    "unified_arena_a_share_history_incomplete:"
                    f"days={history_days}"
                )
            if unbiased_coverage < 0.95:
                raise ValueError(
                    "unified_arena_a_share_universe_incomplete:"
                    f"coverage={unbiased_coverage:.4f}"
                )

        tournament = self.run_classical_tournament(
            account_scope=None,
            horizon=target_horizon,
        )
        reports = list(tournament.get("results") or [tournament])
        config_suffix = (
            "a_share" if self.market == "a_share" else "cn_qdii_etf"
        )
        overlays = {
            "defensive": json.loads(
                (
                    self.repo_root
                    / "configs" / "agents"
                    / f"claude_{config_suffix}.yaml"
                ).read_text(encoding="utf-8")
            ),
            "trend": json.loads(
                (
                    self.repo_root
                    / "configs" / "agents"
                    / f"codex_{config_suffix}.yaml"
                ).read_text(encoding="utf-8")
            ),
        }
        baseline = json.loads(
            (
                self.repo_root
                / "configs"
                / f"competition_{config_suffix}.yaml"
            ).read_text(encoding="utf-8")
        )
        return build_unified_arena_report(
            self.repo_root,
            market=self.market,
            horizon=target_horizon,
            as_of=snapshot_date,
            tournament_reports=reports,
            overlays=overlays,
            baseline=baseline,
        )

    def run_cross_sectional_alpha_repair(
        self,
        *,
        account_scope: str | None = None,
        horizon: int = 20,
    ) -> dict[str, Any]:
        """Evaluate the frozen H20 objective ablation without opening final data."""

        if self.market != "a_share" or int(horizon) != 20:
            raise ValueError(
                f"cross_sectional_repair_not_predeclared:{self.market}:{int(horizon)}"
            )
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        features = self.store.read_feature_snapshot(self.market, snapshot_date)
        labels = self.store.read_label_snapshot(self.market, snapshot_date)
        scope_column = (
            "research_scope"
            if "research_scope" in features.columns
            else "account_id" if "account_id" in features.columns else ""
        )
        if not scope_column:
            raise ValueError("cross_sectional_repair_scope_missing")
        if account_scope is None:
            scopes = sorted(
                str(value).strip()
                for value in features[scope_column].dropna().astype(str).unique()
                if str(value).strip()
            )
            results = [
                self.run_cross_sectional_alpha_repair(
                    account_scope=scope,
                    horizon=int(horizon),
                )
                for scope in scopes
            ]
            return {
                "status": (
                    "development_pass"
                    if any(item.get("status") == "development_pass" for item in results)
                    else "research"
                ),
                "snapshot_date": snapshot_date,
                "market": self.market,
                "horizon": int(horizon),
                "account_scopes": scopes,
                "results": results,
            }

        normalized_scope = str(account_scope).strip()
        scoped_features = features.loc[
            features[scope_column].astype(str).eq(normalized_scope)
        ].copy()
        label_scope_column = (
            "research_scope"
            if "research_scope" in labels.columns
            else "account_id" if "account_id" in labels.columns else ""
        )
        scoped_labels = labels.copy()
        if label_scope_column:
            scoped_labels = scoped_labels.loc[
                scoped_labels[label_scope_column].astype(str).eq(normalized_scope)
            ].copy()
        scoped_labels = scoped_labels.loc[
            pd.to_numeric(scoped_labels["horizon"], errors="coerce").eq(int(horizon))
        ].copy()
        if scoped_features.empty or scoped_labels.empty:
            raise ValueError(
                f"cross_sectional_repair_scope_data_missing:{normalized_scope}"
            )
        scoped_features = build_account_feature_view(
            scoped_features,
            account_scope=normalized_scope,
        )
        join_columns = ["code", "trade_date"]
        if "account_id" in scoped_features.columns and "account_id" in scoped_labels.columns:
            join_columns.append("account_id")
        dataset = scoped_features.merge(
            scoped_labels,
            on=join_columns,
            how="inner",
            suffixes=("", "_label"),
        )
        spec = a_share_h20_specs(normalized_scope)[0]
        feature_contract = account_feature_contract(
            self.market,
            normalized_scope,
            int(horizon),
        )
        feature_columns = tuple(
            column
            for column in spec.feature_allowlist
            if column in feature_contract.allowed_features
            and column in dataset.columns
            and pd.to_numeric(dataset[column], errors="coerce").notna().mean()
            >= feature_contract.minimum_coverage
        )
        manifest_path = (
            self.repo_root
            / "data" / "research" / "models" / self.market
            / normalized_scope / str(int(horizon))
            / "tournaments" / str(snapshot_date).replace("-", "")
            / "evaluation_manifest.json"
        )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload = manifest["payload"]
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"cross_sectional_repair_manifest_missing:{manifest_path}"
            ) from exc
        return evaluate_cross_sectional_candidate(
            self.repo_root,
            market=self.market,
            account_scope=normalized_scope,
            as_of=snapshot_date,
            dataset=dataset,
            feature_columns=feature_columns,
            portfolio_contract=self._research_portfolio_contract(normalized_scope),
            model_spec=spec,
            development_start=str(payload["development_start"]),
            development_end=str(payload["development_end"]),
            observed_final_start=str(payload["final_start"]),
            observed_final_end=str(payload["final_end"]),
        )

    @staticmethod
    def _read_parquet_subset(
        path: Path,
        *,
        columns: tuple[str, ...],
        filters: list[tuple[str, str, object]] | None = None,
    ) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(path)
        available = set(pq.ParquetFile(path).schema_arrow.names)
        selected = [column for column in columns if column in available]
        if not selected:
            raise ValueError(f"research_parquet_columns_missing:{path}")
        effective_filters = [
            item for item in (filters or []) if item[0] in available
        ]
        return pd.read_parquet(
            path,
            columns=selected,
            filters=effective_filters or None,
        )

    @staticmethod
    def _research_code(values: pd.Series) -> pd.Series:
        return (
            values.astype("string")
            .str.split(".").str[0]
            .str.zfill(6)
        )

    def _load_regime_tabular_dataset(
        self,
        *,
        snapshot_date: str,
        config: dict[str, Any],
    ) -> tuple[pd.DataFrame, tuple[str, ...]]:
        """Build the frozen development panel from raw execution and adjusted prices."""

        scope = str(config["account_scope"])
        horizon = int(config["horizon"])
        development = config["development"]
        start = str(development["start"]).replace("-", "")[:8]
        end = str(development["end"]).replace("-", "")[:8]
        research_root = self.research_root
        feature_path = research_root / "features" / self.market / f"{snapshot_date}.parquet"
        label_path = research_root / "labels" / self.market / f"{snapshot_date}.parquet"
        raw_root = research_root / "raw" / self.market / str(snapshot_date).replace("-", "")[:8]
        features = self._read_parquet_subset(
            feature_path,
            columns=self._TABULAR_FEATURE_SOURCE_COLUMNS,
            filters=[
                ("research_scope", "==", scope),
                ("trade_date", "<=", end),
            ],
        )
        if features.empty:
            raise ValueError(f"regime_tabular_features_missing:{scope}")
        features["code"] = self._research_code(features["code"])
        features["trade_date"] = features["trade_date"].astype("string").str[:8]
        if "amount_unit" not in features.columns:
            features["amount_unit"] = "yuan"
        feature_set = str(config.get("feature_set") or "alpha158_lite_v1")
        if feature_set == "alpha158_lite_moneyflow_v2":
            missing_moneyflow = set(MONEYFLOW_FEATURE_COLUMNS).difference(features.columns)
            if missing_moneyflow:
                raise ValueError(
                    "regime_tabular_moneyflow_features_missing:"
                    + ",".join(sorted(missing_moneyflow))
                )
            coverage = float(
                pd.to_numeric(features["moneyflow_observed"], errors="coerce")
                .fillna(0.0)
                .gt(0.0)
                .mean()
            )
            required_coverage = float(
                (config.get("training") or {}).get(
                    "minimum_moneyflow_coverage", 0.80
                )
            )
            if coverage < required_coverage:
                raise ValueError(
                    "regime_tabular_moneyflow_coverage:"
                    f"{coverage:.6f}<{required_coverage:.6f}"
                )

        adjustments = self._read_parquet_subset(
            raw_root / "adj_factor.parquet",
            columns=("ts_code", "trade_date", "adj_factor"),
            filters=[("trade_date", "<=", end)],
        )
        adjustments["code"] = self._research_code(adjustments["ts_code"])
        adjustments["trade_date"] = adjustments["trade_date"].astype("string").str[:8]
        adjustments = adjustments.loc[
            adjustments["code"].isin(features["code"].dropna().unique()),
            ["code", "trade_date", "adj_factor"],
        ].drop_duplicates(["code", "trade_date"], keep="last")
        features = features.merge(
            adjustments,
            on=["code", "trade_date"],
            how="left",
            validate="one_to_one",
        )
        adjustment_coverage = float(
            pd.to_numeric(features["adj_factor"], errors="coerce").gt(0.0).mean()
        )
        if adjustment_coverage < 0.995:
            raise ValueError(
                f"regime_tabular_adj_factor_coverage:{adjustment_coverage:.6f}"
            )
        features = features.loc[
            pd.to_numeric(features["adj_factor"], errors="coerce").gt(0.0)
        ].copy()

        benchmark = self._read_parquet_subset(
            raw_root / "benchmark_000905.parquet",
            columns=("trade_date", "close"),
            filters=[("trade_date", "<=", end)],
        ).rename(columns={"close": "benchmark_close"})
        benchmark["trade_date"] = benchmark["trade_date"].astype("string").str[:8]
        benchmark = benchmark.drop_duplicates("trade_date", keep="last")
        features = features.merge(
            benchmark,
            on="trade_date",
            how="left",
            validate="many_to_one",
        )
        benchmark_coverage = float(
            pd.to_numeric(features["benchmark_close"], errors="coerce").notna().mean()
        )
        if benchmark_coverage < 0.995:
            raise ValueError(
                f"regime_tabular_benchmark_coverage:{benchmark_coverage:.6f}"
            )
        index_weights = self._read_parquet_subset(
            raw_root / "index_weight.parquet",
            columns=("index_code", "con_code", "trade_date", "weight"),
            filters=[
                ("index_code", "==", "000905.SH"),
                ("trade_date", "<=", end),
            ],
        )
        index_weights["code"] = self._research_code(index_weights["con_code"])
        index_weights["trade_date"] = index_weights["trade_date"].astype("string").str[:8]
        index_weights["benchmark_weight"] = pd.to_numeric(
            index_weights["weight"], errors="coerce"
        ) / 100.0
        index_weights = index_weights.loc[
            index_weights["code"].isin(features["code"].dropna().unique()),
            ["code", "trade_date", "benchmark_weight"],
        ].drop_duplicates(["code", "trade_date"], keep="last")
        features["code"] = features["code"].astype(str)
        index_weights["code"] = index_weights["code"].astype(str)
        features["_benchmark_weight_date"] = pd.to_numeric(
            features["trade_date"], errors="raise"
        ).astype(np.int64)
        index_weights["_benchmark_weight_date"] = pd.to_numeric(
            index_weights["trade_date"], errors="raise"
        ).astype(np.int64)
        feature_order = list(features.columns)
        features = pd.merge_asof(
            features.sort_values(["_benchmark_weight_date", "code"], kind="stable"),
            index_weights.drop(columns="trade_date").sort_values(
                ["_benchmark_weight_date", "code"], kind="stable"
            ),
            on="_benchmark_weight_date",
            by="code",
            direction="backward",
        ).loc[:, [*feature_order, "benchmark_weight"]]
        features = features.drop(columns="_benchmark_weight_date")
        weight_coverage = float(
            pd.to_numeric(features["benchmark_weight"], errors="coerce").notna().mean()
        )
        if weight_coverage < 0.95:
            raise ValueError(
                f"regime_tabular_benchmark_weight_coverage:{weight_coverage:.6f}"
            )
        featured = self._compute_technical_history(features)
        del features, adjustments, benchmark, index_weights
        gc.collect()
        featured = build_alpha158_lite_feature_view(
            featured,
            account_scope=scope,
        )
        feature_columns = alpha158_lite_feature_columns(
            featured,
            feature_set=feature_set,
        )

        labels = self._read_parquet_subset(
            label_path,
            columns=self._TABULAR_LABEL_COLUMNS,
            filters=[
                ("research_scope", "==", scope),
                ("horizon", "==", horizon),
                ("trade_date", ">=", start),
                ("trade_date", "<=", end),
            ],
        )
        labels["code"] = self._research_code(labels["code"])
        labels["trade_date"] = labels["trade_date"].astype("string").str[:8]
        labels = labels.loc[
            pd.to_numeric(labels["horizon"], errors="coerce").eq(horizon)
            & labels["trade_date"].between(start, end)
        ].copy()
        join_columns = ["code", "trade_date"]
        if "account_id" in featured.columns and "account_id" in labels.columns:
            join_columns.append("account_id")
        dataset = featured.merge(
            labels,
            on=join_columns,
            how="inner",
            suffixes=("", "_label"),
            validate="one_to_one",
        )
        del featured, labels
        gc.collect()
        required_columns = {
            *feature_columns,
            *(str(column) for column in config.get("controls") or ()),
            "code", "trade_date", "horizon", "label_end_date", "excess_return",
            "account_id", "research_scope", "industry", "total_mv",
            "realized_volatility_20", "avg_amount_20", "close", "return_1",
            "benchmark_weight",
            *self._TABULAR_LABEL_COLUMNS,
        }
        dataset = dataset.loc[:, [
            column for column in dataset.columns if column in required_columns
        ]].sort_values(["trade_date", "code"], kind="stable").reset_index(drop=True)
        if dataset.empty:
            raise ValueError(f"regime_tabular_dataset_missing:{scope}:{horizon}")
        if dataset.duplicated(join_columns).any():
            raise ValueError("regime_tabular_dataset_duplicate")
        return dataset, feature_columns

    def run_regime_tabular_alpha(
        self,
        *,
        config_path: str | Path = "configs/research/classical_model.yaml",
    ) -> dict[str, Any]:
        """Evaluate one frozen tabular candidate without changing formal strategy state."""

        path = Path(config_path)
        if not path.is_absolute():
            path = self.repo_root / path
        config = load_tabular_ranker_config(path)
        if str(config["market"]) != self.market:
            raise ValueError(
                f"regime_tabular_market_mismatch:{self.market}:{config['market']}"
            )
        if self.market != "a_share" or str(config["account_scope"]) != "zz500":
            raise ValueError("regime_tabular_hypothesis_not_predeclared")
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        dataset, feature_columns = self._load_regime_tabular_dataset(
            snapshot_date=snapshot_date,
            config=config,
        )
        result = evaluate_regime_tabular_candidate(
            self.repo_root,
            dataset=dataset,
            feature_columns=feature_columns,
            config=config,
            portfolio_contract=self._research_portfolio_contract("zz500"),
            as_of=snapshot_date,
        )
        result["snapshot_date"] = snapshot_date
        result["dataset_rows"] = int(len(dataset))
        result["dataset_start"] = str(dataset["trade_date"].min())
        result["dataset_end"] = str(dataset["trade_date"].max())
        result["formal_order_source"] = False
        return result

    def freeze_regime_tabular_forward(
        self,
        *,
        config_path: str | Path = "configs/research/classical_model.yaml",
        source_report: str | Path,
        observation_start: str,
    ) -> dict[str, Any]:
        """Freeze the declared best candidate for future-only observation."""

        path = Path(config_path)
        if not path.is_absolute():
            path = self.repo_root / path
        report_path = Path(source_report)
        if not report_path.is_absolute():
            report_path = self.repo_root / report_path
        config = load_tabular_ranker_config(path)
        if self.market != "a_share" or str(config["account_scope"]) != "zz500":
            raise ValueError("regime_tabular_forward_hypothesis_not_predeclared")
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        dataset, feature_columns = self._load_regime_tabular_dataset(
            snapshot_date=snapshot_date,
            config=config,
        )
        result = freeze_tabular_forward_model(
            self.repo_root,
            dataset=dataset,
            feature_columns=feature_columns,
            config=config,
            observation_start=observation_start,
            source_report=report_path,
        )
        return {**result, "snapshot_date": snapshot_date}

    @staticmethod
    def _forward_adjustment_history(
        cache_root: Path,
        *,
        codes: Iterable[str],
        start: str,
        end: str,
    ) -> pd.DataFrame:
        parts: list[pd.DataFrame] = []
        for code in sorted({str(value).zfill(6) for value in codes}):
            candidates = (
                cache_root / "adj_factor" / f"{code}.SH.csv",
                cache_root / "adj_factor" / f"{code}.SZ.csv",
            )
            source = next((candidate for candidate in candidates if candidate.exists()), None)
            if source is None:
                continue
            try:
                frame = pd.read_csv(
                    source,
                    usecols=["ts_code", "trade_date", "adj_factor"],
                    dtype={"ts_code": str, "trade_date": str},
                )
            except (OSError, ValueError, pd.errors.ParserError):
                continue
            frame["trade_date"] = frame["trade_date"].astype(str).str[:8]
            frame = frame.loc[frame["trade_date"].le(end)].copy()
            if frame.empty:
                continue
            before = frame.loc[frame["trade_date"].lt(start)].tail(1)
            within = frame.loc[frame["trade_date"].ge(start)]
            parts.append(pd.concat([before, within], ignore_index=True))
        if not parts:
            return pd.DataFrame(columns=["code", "trade_date", "adj_factor"])
        adjustments = pd.concat(parts, ignore_index=True, sort=False)
        adjustments["code"] = ResearchPipeline._research_code(
            adjustments["ts_code"]
        )
        return adjustments.loc[:, ["code", "trade_date", "adj_factor"]].drop_duplicates(
            ["code", "trade_date"], keep="last"
        )

    @staticmethod
    def _merge_forward_adjustments(
        features: pd.DataFrame,
        adjustments: pd.DataFrame,
    ) -> pd.DataFrame:
        """Attach the latest adjustment known on or before each feature date."""

        left = features.drop(columns=["adj_factor"], errors="ignore").copy()
        left["code"] = left["code"].astype(str)
        left["_forward_trade_key"] = pd.to_numeric(
            left["trade_date"], errors="coerce"
        )
        if left["_forward_trade_key"].isna().any():
            raise ValueError("regime_tabular_forward_feature_date_invalid")
        left["_forward_trade_key"] = left["_forward_trade_key"].astype(
            np.int64
        )
        left["_forward_row_order"] = np.arange(len(left), dtype=np.int64)

        right = adjustments.loc[
            :, ["code", "trade_date", "adj_factor"]
        ].copy()
        right["code"] = right["code"].astype(str)
        right["_forward_trade_key"] = pd.to_numeric(
            right["trade_date"], errors="coerce"
        )
        right["adj_factor"] = pd.to_numeric(
            right["adj_factor"], errors="coerce"
        )
        right = right.dropna(
            subset=["_forward_trade_key", "adj_factor"]
        ).drop_duplicates(
            ["code", "_forward_trade_key"], keep="last"
        )
        if right.empty:
            left["adj_factor"] = np.nan
            return left.drop(
                columns=["_forward_trade_key", "_forward_row_order"]
            )
        right["_forward_trade_key"] = right["_forward_trade_key"].astype(
            np.int64
        )

        merged = pd.merge_asof(
            left.sort_values(
                ["_forward_trade_key", "code"], kind="stable"
            ),
            right.loc[:, ["code", "_forward_trade_key", "adj_factor"]].sort_values(
                ["_forward_trade_key", "code"], kind="stable"
            ),
            on="_forward_trade_key",
            by="code",
            direction="backward",
            allow_exact_matches=True,
        )
        return merged.sort_values("_forward_row_order", kind="stable").drop(
            columns=["_forward_trade_key", "_forward_row_order"]
        ).reset_index(drop=True)

    def _load_regime_tabular_forward_features(
        self,
        *,
        snapshot_date: str,
        config: dict[str, Any],
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Rebuild the current Alpha158-lite view with adjusted price history."""

        end = str(snapshot_date).replace("-", "")[:8]
        start = (pd.Timestamp(end) - pd.Timedelta(days=550)).strftime("%Y%m%d")
        scope = str(config["account_scope"])
        source_columns = tuple(dict.fromkeys((
            *self._TABULAR_FEATURE_SOURCE_COLUMNS,
            "name", "benchmark_code", "benchmark_weight",
        )))
        features = self._read_parquet_subset(
            self.store.feature_snapshot_path(self.market, snapshot_date),
            columns=source_columns,
            filters=[
                ("research_scope", "==", scope),
                ("trade_date", ">=", start),
                ("trade_date", "<=", end),
            ],
        )
        if features.empty:
            raise ValueError("regime_tabular_forward_features_missing")
        features["code"] = self._research_code(features["code"])
        features["trade_date"] = features["trade_date"].astype("string").str[:8]
        if "amount_unit" not in features.columns:
            features["amount_unit"] = "yuan"

        raw_root = self.research_root / "raw" / self.market / end
        raw_adjustment_path = raw_root / "adj_factor.parquet"
        if raw_adjustment_path.exists():
            adjustments = self._read_parquet_subset(
                raw_adjustment_path,
                columns=("ts_code", "trade_date", "adj_factor"),
                filters=[("trade_date", "<=", end)],
            )
            adjustments["code"] = self._research_code(adjustments["ts_code"])
            adjustments["trade_date"] = adjustments["trade_date"].astype(
                "string"
            ).str[:8]
            adjustments = adjustments.loc[
                adjustments["code"].isin(features["code"].dropna().unique()),
                ["code", "trade_date", "adj_factor"],
            ]
        else:
            adjustments = self._forward_adjustment_history(
                self.repo_root / "data" / "shared" / "backtest_cache",
                codes=features["code"].dropna().astype(str).unique(),
                start=start,
                end=end,
            )
        features = self._merge_forward_adjustments(features, adjustments)
        features = features.sort_values(
            ["code", "trade_date"], kind="stable"
        ).reset_index(drop=True)
        latest = features.loc[features["trade_date"].eq(end)]
        adjustment_coverage = float(
            pd.to_numeric(latest["adj_factor"], errors="coerce").gt(0.0).mean()
        )
        if adjustment_coverage < 0.98:
            raise ValueError(
                f"regime_tabular_forward_adj_factor_coverage:{adjustment_coverage:.6f}"
            )

        benchmark_path = raw_root / "benchmark_000905.parquet"
        if benchmark_path.exists():
            benchmark = self._read_parquet_subset(
                benchmark_path,
                columns=("trade_date", "open", "close"),
                filters=[
                    ("trade_date", ">=", start),
                    ("trade_date", "<=", end),
                ],
            )
        else:
            fallback = (
                self.repo_root
                / "data" / "shared" / "backtest_cache"
                / "benchmark_daily" / "000905.csv"
            )
            benchmark = pd.read_csv(
                fallback,
                usecols=["trade_date", "open", "close"],
                dtype={"trade_date": str},
            )
            benchmark = benchmark.loc[
                benchmark["trade_date"].astype(str).between(start, end)
            ].copy()
        benchmark["trade_date"] = benchmark["trade_date"].astype("string").str[:8]
        benchmark = benchmark.drop_duplicates("trade_date", keep="last")
        features = features.merge(
            benchmark.loc[:, ["trade_date", "close"]].rename(
                columns={"close": "benchmark_close"}
            ),
            on="trade_date",
            how="left",
            validate="many_to_one",
        )
        featured = self._compute_technical_history(features)
        featured = build_alpha158_lite_feature_view(
            featured,
            account_scope=scope,
        )
        return featured, benchmark

    def run_regime_tabular_forward(
        self,
        *,
        config_path: str | Path = "configs/research/classical_model.yaml",
    ) -> dict[str, Any]:
        """Score the latest immutable snapshot with the frozen observer."""

        path = Path(config_path)
        if not path.is_absolute():
            path = self.repo_root / path
        config = load_tabular_ranker_config(path)
        if self.market != "a_share" or str(config["account_scope"]) != "zz500":
            raise ValueError("regime_tabular_forward_hypothesis_not_predeclared")
        cutoff = self.run_key
        feature_dates = sorted(
            candidate.stem
            for candidate in (
                self.research_root / "features" / self.market
            ).glob("*.parquet")
            if candidate.stem.isdigit() and candidate.stem <= cutoff
        )
        if not feature_dates:
            raise ValueError("regime_tabular_forward_snapshot_missing")
        snapshot_date = feature_dates[-1]
        featured, benchmark = self._load_regime_tabular_forward_features(
            snapshot_date=snapshot_date,
            config=config,
        )
        label_dates = sorted(
            candidate.stem
            for candidate in (
                self.research_root / "labels" / self.market
            ).glob("*.parquet")
            if candidate.stem.isdigit() and candidate.stem <= snapshot_date
        )
        labels = (
            self.store.read_label_snapshot(self.market, label_dates[-1])
            if label_dates else pd.DataFrame()
        )
        model_root = tabular_forward_model_root(
            self.repo_root,
            market=self.market,
            account_scope=str(config["account_scope"]),
            config_hash=str(config["config_hash"]),
        )
        result = observe_tabular_forward_model(
            self.repo_root,
            model_root=model_root,
            featured=featured,
            labels=labels,
            benchmark=benchmark,
            config=config,
            portfolio_contract=self._research_portfolio_contract("zz500"),
        )
        return {**result, "snapshot_date": snapshot_date}

    def _resolve_model(
        self,
        horizon: int,
        account_scope: str | None = None,
    ) -> tuple[Path, str]:
        artifact, statuses = self._resolve_model_roles(horizon, account_scope)
        return artifact, str(statuses.get("ranker", "research"))

    def _resolve_model_roles(
        self,
        horizon: int,
        account_scope: str | None = None,
    ) -> tuple[Path, dict[str, str]]:
        artifact, statuses, _ = self._resolve_model_roles_with_provenance(
            horizon,
            account_scope,
        )
        return artifact, statuses

    def _resolve_model_roles_with_provenance(
        self,
        horizon: int,
        account_scope: str | None = None,
    ) -> tuple[Path, dict[str, str], dict[str, str]]:
        def selected(
            scope: str | None,
        ) -> tuple[Path, dict[str, str]] | None:
            model_root = self._model_root(horizon, scope)
            registry_path = model_root / "registry.json"
            if not registry_path.exists():
                return None
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            resolved = select_registry_model(state, role="ranker")
            if resolved is None:
                return None
            _, metadata = resolved
            fallback = str(metadata.get("status", "research"))
            statuses = {
                role: str(
                    (metadata.get("role_status") or {}).get(role, fallback)
                )
                for role in ("classifier", "ranker", "portfolio")
            }
            return Path(metadata["artifact"]), statuses

        scoped = selected(account_scope)
        if scoped is not None:
            artifact, statuses = scoped
            artifact_exists = (
                artifact.exists()
                or (not artifact.is_absolute() and (self.repo_root / artifact).exists())
            )
            if account_scope and not artifact_exists:
                market = selected(None)
                if market is not None:
                    market_artifact, market_statuses = market
                    market_exists = (
                        market_artifact.exists()
                        or (
                            not market_artifact.is_absolute()
                            and (self.repo_root / market_artifact).exists()
                        )
                    )
                    if market_exists:
                        return market_artifact, market_statuses, {
                            "requested_scope": str(account_scope),
                            "selected_scope": "",
                            "resolution": "market_fallback",
                            "fallback_reason": "scoped_artifact_missing",
                        }
            return artifact, statuses, {
                "requested_scope": str(account_scope or ""),
                "selected_scope": str(account_scope or ""),
                "resolution": "scoped" if account_scope else "market",
                "fallback_reason": "",
            }

        if account_scope:
            market = selected(None)
            if market is not None:
                artifact, statuses = market
                return artifact, statuses, {
                    "requested_scope": str(account_scope),
                    "selected_scope": "",
                    "resolution": "market_fallback",
                    "fallback_reason": "scoped_model_unavailable",
                }

        model_root = self._model_root(horizon, account_scope)
        return model_root / "missing.joblib", {
            "classifier": "research",
            "ranker": "research",
            "portfolio": "research",
        }, {
            "requested_scope": str(account_scope or ""),
            "selected_scope": str(account_scope or ""),
            "resolution": "missing",
            "fallback_reason": "registered_model_unavailable",
        }

    def backfill_prediction_accuracy(self) -> dict[str, Any]:
        label_paths = sorted(
            path
            for path in (self.research_root / "labels" / self.market).glob("*.parquet")
            if path.stem.isdigit() and path.stem <= self.run_key
        )
        prediction_dir = self.repo_root / "data" / self.market / self.agent / "predictions"
        prediction_paths = sorted(
            path
            for path in prediction_dir.glob("*.parquet")
            if path.stem.isdigit() and path.stem <= self.run_key
        )
        if not label_paths or not prediction_paths:
            return {"status": "unavailable", "evaluated": 0}
        labels = pd.read_parquet(label_paths[-1])
        required_labels = {"code", "trade_date", "horizon", "label", "excess_return"}
        if required_labels.difference(labels.columns):
            raise ValueError("prediction_accuracy_label_schema")
        prediction_parts: list[pd.DataFrame] = []
        for path in prediction_paths[-120:]:
            frame = pd.read_parquet(path)
            if "as_of" not in frame.columns:
                frame = frame.assign(as_of=path.stem)
            prediction_parts.append(frame)
        predictions = pd.concat(prediction_parts, ignore_index=True, sort=False)
        required_predictions = {
            "as_of", "code", "horizon", "p_down", "p_flat", "p_up",
        }
        if required_predictions.difference(predictions.columns):
            raise ValueError("prediction_accuracy_prediction_schema")
        predictions["trade_date"] = (
            predictions["as_of"].astype("string").str.replace("-", "", regex=False).str[:8]
        )
        predictions["code"] = predictions["code"].astype("string").str.split(".").str[0].str.zfill(6)
        labels = labels.copy()
        labels["code"] = labels["code"].astype("string").str.split(".").str[0].str.zfill(6)
        labels["trade_date"] = labels["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
        if "label_end_date" in labels.columns:
            labels = labels.loc[
                labels["label_end_date"].astype("string").str.replace("-", "", regex=False).str[:8] <= self.run_key
            ]
        joined = predictions.merge(
            labels[["code", "trade_date", "horizon", "label", "excess_return"]],
            on=["code", "trade_date", "horizon"],
            how="inner",
            validate="many_to_one",
        )
        if joined.empty:
            return {"status": "current", "evaluated": 0}
        probability_columns = ["p_down", "p_flat", "p_up"]
        probability_labels = np.asarray(["down", "flat", "up"], dtype=object)
        probabilities = joined[probability_columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        expected = np.column_stack([
            joined["label"].astype(str).eq(class_name).astype(float).to_numpy()
            for class_name in probability_labels
        ])
        joined["predicted_label"] = probability_labels[np.argmax(probabilities, axis=1)]
        joined["correct"] = joined["predicted_label"].eq(joined["label"].astype(str))
        joined["brier_score"] = np.sum((probabilities - expected) ** 2, axis=1)
        predicted_return = pd.to_numeric(joined.get("expected_excess_return"), errors="coerce")
        actual_return = pd.to_numeric(joined["excess_return"], errors="coerce")
        joined["return_error"] = predicted_return - actual_return
        joined["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        joined["as_of"] = joined["trade_date"].astype(str)
        if "model_version" not in joined.columns:
            joined["model_version"] = ""
        output_columns = [
            "as_of", "code", "horizon", "model_version", "active_status", "confidence",
            "predicted_label", "label", "correct", "brier_score",
            "expected_excess_return", "excess_return", "return_error", "evaluated_at",
        ]
        for column in output_columns:
            if column not in joined.columns:
                joined[column] = pd.NA
        destination = self.repo_root / "data" / self.market / self.agent / "prediction_accuracy.csv"
        if destination.exists() and destination.stat().st_size > 0:
            existing = pd.read_csv(
                destination,
                dtype={"as_of": str, "code": str, "model_version": str},
            )
            joined = pd.concat([existing, joined[output_columns]], ignore_index=True, sort=False)
        else:
            joined = joined[output_columns]
        joined = (
            joined.sort_values("evaluated_at", kind="stable")
            .drop_duplicates(["as_of", "code", "horizon", "model_version"], keep="last")
            .sort_values(["as_of", "horizon", "code"], kind="stable")
            .reset_index(drop=True)
        )
        write_dataframe_csv_atomic(joined, destination, index=False)
        return {
            "status": "complete",
            "evaluated": int(len(joined)),
            "hit_rate": float(pd.to_numeric(joined["correct"], errors="coerce").mean()),
            "mean_brier_score": float(pd.to_numeric(joined["brier_score"], errors="coerce").mean()),
            "path": str(destination),
        }
    def predict(self, *, horizon: int | None = None) -> dict[str, Any]:
        health_path = self.research_root / "prediction_health" / self.market / f"{self.run_key}-{self.agent}.json"
        try:
            features = self.store.read_feature_snapshot(self.market, self.as_of)
            feature_dates = (
                features["trade_date"]
                .astype("string")
                .str.replace("-", "", regex=False)
                .str[:8]
            )
            eligible_dates = feature_dates.loc[feature_dates.le(self.run_key)]
            if eligible_dates.empty:
                raise ValueError(f"prediction_market_date_unavailable:{self.market}")
            market_date = str(eligible_dates.max())
            latest_by_code = features.assign(_trade_date_key=feature_dates)
            latest_by_code = (
                latest_by_code.loc[latest_by_code["_trade_date_key"].le(self.run_key)]
                .sort_values(["_trade_date_key", "code"], kind="stable")
                .drop_duplicates("code", keep="last")
            )
            current_mask = latest_by_code["_trade_date_key"].eq(market_date)
            latest = (
                latest_by_code.loc[current_mask]
                .sort_values(["trade_date", "code"], kind="stable")
                .drop_duplicates("code", keep="last")
                .drop(columns=["_trade_date_key"])
                .reset_index(drop=True)
            )
            freshness = {
                "prediction_market_date": market_date,
                "feature_snapshot_rows": int(len(features)),
                "latest_instrument_rows": int(len(latest_by_code)),
                "current_market_rows": int(len(latest)),
                "stale_rows_rejected": int(len(latest_by_code) - len(latest)),
            }
            latest, prediction_universe = self._prediction_universe(latest)
            prediction_universe = {**prediction_universe, **freshness}
            rows: list[dict[str, Any]] = []
            artifacts: dict[str, str] = {}
            statuses: dict[str, str] = {}
            failures: list[dict[str, Any]] = []
            cycle_counts: dict[str, int] = {}
            iteration_candidates: dict[str, dict[str, Any]] = {}
            drift_assessments: dict[str, dict[str, Any]] = {}
            model_resolution: dict[str, dict[str, str]] = {}
            market_prediction_cache: dict[
                tuple[int, str], tuple[Any, list, dict[str, Any]]
            ] = {}
            regime, regime_stability = self._current_regime_context()
            target_horizons = (horizon,) if horizon is not None else (3, 5, 10, 20)
            account_scopes = (
                sorted(
                    str(value).strip()
                    for value in latest["research_scope"].dropna().astype(str).unique()
                    if str(value).strip()
                )
                if "research_scope" in latest.columns else []
            )
            scope_batches: list[tuple[str | None, pd.DataFrame]] = (
                [
                    (
                        scope,
                        latest.loc[
                            latest["research_scope"].astype(str).eq(scope)
                        ].copy(),
                    )
                    for scope in account_scopes
                ]
                if account_scopes else [(None, latest)]
            )
            for target_horizon in target_horizons:
                for account_scope, scoped_latest in scope_batches:
                    key = (
                        f"{account_scope}:{target_horizon}"
                        if account_scope else str(target_horizon)
                    )
                    try:
                        artifact, role_status, provenance = (
                            self._resolve_model_roles_with_provenance(
                                target_horizon,
                                account_scope,
                            )
                        )
                        status = str(role_status.get("ranker", "research"))
                        bundle = load_model_bundle(artifact)
                        if provenance["resolution"] == "market_fallback":
                            cache_key = (target_horizon, str(artifact))
                            cached = market_prediction_cache.get(cache_key)
                            if cached is None:
                                market_records = generate_predictions(
                                    bundle,
                                    latest,
                                    as_of=self.as_of,
                                    horizon=target_horizon,
                                    regime=regime,
                                    data_quality=1.0,
                                    regime_stability=regime_stability,
                                    feature_snapshot_id=(
                                        f"{self.market}-{self.run_key}"
                                    ),
                                    active_status=(
                                        status
                                        if status == "active"
                                        else "inactive"
                                    ),
                                    role_status=role_status,
                                )
                                market_records, drift = (
                                    self._assess_model_drift(
                                        target_horizon,
                                        bundle,
                                        market_records,
                                        role_status=role_status,
                                        account_scope=None,
                                    )
                                )
                                cached = (bundle, market_records, drift)
                                market_prediction_cache[cache_key] = cached
                            bundle, market_records, drift = cached
                            records = [
                                replace(
                                    record,
                                    account_scope=str(account_scope),
                                )
                                for record in market_records
                                if str(
                                    record.metadata.get("research_scope")
                                    or record.metadata.get("account_id")
                                    or record.account_scope
                                    or ""
                                ) == str(account_scope)
                            ]
                            if not records:
                                raise RuntimeError(
                                    "market_model_scope_predictions_missing:"
                                    f"{account_scope}:{target_horizon}"
                                )
                        else:
                            records = generate_predictions(
                                bundle,
                                scoped_latest,
                                as_of=self.as_of,
                                horizon=target_horizon,
                                regime=regime,
                                data_quality=1.0,
                                regime_stability=regime_stability,
                                feature_snapshot_id=(
                                    f"{self.market}-{self.run_key}"
                                ),
                                active_status=(
                                    status
                                    if status == "active"
                                    else "inactive"
                                ),
                                role_status=role_status,
                            )
                            records, drift = self._assess_model_drift(
                                target_horizon,
                                bundle,
                                records,
                                role_status=role_status,
                                account_scope=(
                                    provenance["selected_scope"] or None
                                ),
                            )
                        drift_assessments[key] = drift
                        model_resolution[key] = provenance
                        artifacts[key] = str(artifact)
                        statuses[key] = status
                        rows.extend(self._prediction_rows(records))
                        if self.agent == "codex" and account_scope is not None:
                            candidate = self._write_iteration_candidate_predictions(
                                target_horizon,
                                scoped_latest,
                                canonical_bundle=bundle,
                                canonical_records=records,
                                regime=regime,
                                regime_stability=regime_stability,
                                account_scope=account_scope,
                            )
                            if candidate is not None:
                                iteration_candidates[key] = candidate
                                if candidate.get("shadow_cycles") is not None:
                                    cycle_counts[key] = int(candidate["shadow_cycles"])
                                if candidate["model_version"] == bundle.model_version:
                                    statuses[key] = str(candidate["status"])
                    except Exception as exc:  # noqa: BLE001 - preserve successful scopes/horizons
                        failures.append({
                            "horizon": target_horizon,
                            "account_scope": str(account_scope or ""),
                            "error": str(exc)[:240],
                        })
                if self.agent == "codex":
                    try:
                        cached_market = next(
                            (
                                cached
                                for (cached_horizon, _), cached
                                in market_prediction_cache.items()
                                if cached_horizon == target_horizon
                            ),
                            None,
                        )
                        candidate = self._write_iteration_candidate_predictions(
                            target_horizon,
                            latest,
                            canonical_bundle=(
                                cached_market[0]
                                if cached_market is not None else None
                            ),
                            canonical_records=(
                                cached_market[1]
                                if cached_market is not None else None
                            ),
                            regime=regime,
                            regime_stability=regime_stability,
                            account_scope=None,
                        )
                        if candidate is not None:
                            candidate_key = f"market:{target_horizon}"
                            iteration_candidates[candidate_key] = candidate
                            if candidate.get("shadow_cycles") is not None:
                                cycle_counts[candidate_key] = int(
                                    candidate["shadow_cycles"]
                                )
                    except Exception as exc:  # noqa: BLE001 - formal prediction remains usable
                        failures.append({
                            "horizon": target_horizon,
                            "account_scope": "",
                            "stage": "market_iteration_candidate",
                            "error": str(exc)[:240],
                        })
            if not rows:
                raise RuntimeError(f"prediction_models_unavailable:{failures}")
            destination = self.repo_root / "data" / self.market / self.agent / "predictions" / f"{self.run_key}.parquet"
            self.store.write_parquet_atomic(destination, pd.DataFrame(rows))
            try:
                accuracy_backfill = self.backfill_prediction_accuracy()
            except Exception as exc:  # noqa: BLE001 - diagnostics cannot invalidate fresh predictions
                accuracy_backfill = {"status": "failed", "evaluated": 0, "error": str(exc)[:240]}
            health = {
                "status": "partial" if failures else "complete",
                "predictions": len(rows),
                "horizons": sorted(
                    {
                        int(value.rsplit(":", 1)[-1])
                        for value in artifacts
                    }
                ),
                "account_scopes": account_scopes,
                "artifacts": artifacts,
                "active_status": statuses,
                "failures": failures,
                "shadow_cycles": cycle_counts,
                "iteration_candidates": iteration_candidates,
                "model_resolution": model_resolution,
                "drift": drift_assessments,
                "regime": regime,
                "regime_stability": regime_stability,
                "accuracy_backfill": accuracy_backfill,
                "prediction_universe": prediction_universe,
            }
        except Exception as exc:  # noqa: BLE001 - trading path remains unchanged
            health = {"status": "fallback", "predictions": 0, "error": str(exc)[:240]}
        write_text_atomic(health_path, json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**health, "health_path": str(health_path)}

    def _assess_model_drift(
        self,
        horizon: int,
        bundle: Any,
        records: list,
        *,
        role_status: dict[str, str],
        account_scope: str | None = None,
    ) -> tuple[list, dict[str, Any]]:
        if not records:
            return records, {
                "status": "insufficient_evidence",
                "breaches": [],
                "evidence_gaps": ["predictions_missing"],
            }
        metadata = [record.metadata for record in records]
        feature_psi = float(np.mean([
            float(item.get("feature_drift_mean_psi", 0.0)) for item in metadata
        ]))
        ood_ratio = float(np.mean([
            float(item.get("out_of_distribution_ratio", 0.0)) for item in metadata
        ]))
        distribution = (
            float(np.mean([record.p_down for record in records])),
            float(np.mean([record.p_flat for record in records])),
            float(np.mean([record.p_up for record in records])),
        )
        class_balance = bundle.metrics.get("class_balance") or {}
        reference = tuple(
            float(class_balance.get(label, 1.0 / 3.0))
            for label in ("down", "flat", "up")
        )
        registry = ModelRegistry(
            self._model_root(horizon, account_scope) / "registry.json"
        )
        state = registry._read()
        active = str(
            (state.get("champion_model_versions") or {}).get("ranker")
            or state.get("champion_model_version")
            or ""
        )
        previous = str(
            (state.get("previous_champion_model_versions") or {}).get("ranker")
            or ""
        )
        enforceable_roles = tuple(
            role
            for role, status in role_status.items()
            if status in {"shadow", "active"}
        )
        monitor = DriftLifecycle(
            self._model_root(horizon, account_scope) / "drift_lifecycle.json"
        )
        observation = DriftObservation(
            model_version=str(bundle.model_version),
            as_of=self.as_of,
            feature_psi=feature_psi,
            ood_ratio=ood_ratio,
            prediction_distribution=distribution,
            reference_prediction_distribution=reference,
        )
        observation_status = "recorded"
        try:
            assessment = monitor.record(
                observation,
                active_model_version=active or None,
                previous_champion_version=previous or None,
                quarantine_eligible=bool(enforceable_roles),
            )
        except ValueError as exc:
            active_roles = {
                role for role, status in role_status.items() if status == "active"
            }
            if str(exc) != "drift_observation_conflict" or active_roles:
                raise
            assessment = monitor.assessment_for(
                str(bundle.model_version),
                self.as_of,
                active_model_version=active or None,
                previous_champion_version=previous or None,
            )
            if assessment is None:
                raise
            observation_status = "reused_same_day"
        if assessment.status == "quarantined" and enforceable_roles:
            if enforceable_roles:
                registry.quarantine_roles(
                    str(bundle.model_version),
                    roles=enforceable_roles,
                    reason=",".join(assessment.breaches) or "model_drift",
                    event_id=assessment.event_id,
                )
            records = [
                replace(
                    record,
                    active_status="inactive",
                    classifier_status=(
                        "quarantined"
                        if record.classifier_status == "active"
                        else record.classifier_status
                    ),
                    ranker_status=(
                        "quarantined"
                        if record.ranker_status == "active"
                        else record.ranker_status
                    ),
                    portfolio_status=(
                        "quarantined"
                        if record.portfolio_status == "active"
                        else record.portfolio_status
                    ),
                    active_roles=(),
                    invalidated=True,
                    invalidation=tuple(
                        dict.fromkeys([*record.invalidation, "模型漂移触发隔离"])
                    ),
                )
                for record in records
            ]
        payload = asdict(assessment)
        payload["observation_status"] = observation_status
        if observation_status == "reused_same_day":
            payload["evidence_complete"] = False
            payload["evidence_gaps"] = tuple(
                sorted({*payload.get("evidence_gaps", ()), "same_day_recompute_deferred"})
            )
            payload["metric_states"] = {
                **dict(payload.get("metric_states") or {}),
                "same_day_recompute": "deferred",
            }
        return records, payload

    @staticmethod
    def _prediction_rows(records: list) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in records:
            row = asdict(record)
            metadata = dict(row.get("metadata") or {})
            for column in (
                "account_id",
                "research_scope",
                "benchmark_code",
                "index_key",
                "country",
                "theme",
                "sector",
                "asset_class",
                "exposure_group",
                "prediction_std",
                "prediction_uncertainty_bps",
                "lower_confidence_edge",
                "alpha_half_life_days",
                "edge_calibration_available",
                "edge_calibration_reason",
                "calibration_version",
                "calibrator_hash",
                "feature_schema_hash",
                "model_artifact_hash",
            ):
                if metadata.get(column) is not None:
                    row[column] = metadata[column]
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
            row["invalidation"] = json.dumps(row["invalidation"], ensure_ascii=False)
            row["metadata"] = json.dumps(metadata, ensure_ascii=False)
            rows.append(row)
        return rows

    def _advance_shadow_cycle(
        self,
        horizon: int,
        bundle: Any,
        prediction_count: int,
        *,
        account_scope: str | None = None,
    ) -> dict[str, Any]:
        model_root = self._model_root(horizon, account_scope)
        forward_evidence = load_forward_portfolio_evidence(
            iteration_portfolio_dir(
                self.repo_root,
                self.market,
                horizon,
                bundle.model_version,
                account_scope=account_scope,
            ),
            expected_account_ids=(
                (str(account_scope),)
                if account_scope
                else tuple(
                    str(account.get("id")) for account in self._baseline_accounts()
                )
            ),
            require_lookthrough=self.market == "cn_qdii_etf",
        )
        cycle = ShadowCycleTracker(model_root / "shadow_cycles.json").record(
            bundle.model_version,
            self.as_of,
            {
                "predictions": prediction_count,
                "calibration_quality": bundle.metrics.get("calibration_quality"),
                **forward_evidence,
            },
        )
        registry = ModelRegistry(model_root / "registry.json")
        state = registry._read()
        model = ((state.get("models") or {}).get(bundle.model_version) or {})
        status = str(model.get("status", "shadow"))
        if cycle["is_new_cycle"] and status == "shadow":
            role_status = model.setdefault("role_status", {})
            for role in ("classifier", "ranker", "portfolio"):
                role_status.setdefault(role, "shadow")
            registry._write(state)
            reports = evaluate_role_activation(
                activation_evidence_from_metrics(
                    {**bundle.metrics, **forward_evidence},
                    shadow_cycles=cycle["count"],
                ),
                current_status="shadow",
                target_status="active",
            )
            for role, report in reports.items():
                state = registry.record_role_gate(bundle.model_version, role, report)
            status = str(state["models"][bundle.model_version]["status"])
        return {**cycle, "status": status}

    def _write_iteration_candidate_predictions(
        self,
        horizon: int,
        features: pd.DataFrame,
        *,
        canonical_bundle: Any | None,
        canonical_records: list | None,
        regime: str,
        regime_stability: float,
        account_scope: str | None = None,
    ) -> dict[str, Any] | None:
        candidate = ensure_iteration_candidate(
            self.repo_root,
            self.market,
            horizon,
            account_scope=account_scope,
            as_of=self.as_of,
        )
        if candidate is None:
            return None
        version = str(candidate["model_version"])
        if (
            canonical_bundle is not None
            and version == str(canonical_bundle.model_version)
        ):
            bundle = canonical_bundle
            records = canonical_records or []
        else:
            artifact = candidate.get("artifact")
            if not artifact:
                raise FileNotFoundError(f"iteration_candidate_artifact_missing:{version}")
            bundle = load_model_bundle(Path(str(artifact)))
            records = generate_predictions(
                bundle,
                features,
                as_of=self.as_of,
                horizon=horizon,
                regime=regime,
                data_quality=1.0,
                regime_stability=regime_stability,
                feature_snapshot_id=f"{self.market}-{self.run_key}",
                active_status="inactive",
            )
        destination = iteration_prediction_path(
            self.repo_root,
            self.market,
            horizon,
            version,
            self.as_of,
            account_scope=account_scope,
        )
        self.store.write_parquet_atomic(destination, pd.DataFrame(self._prediction_rows(records)))
        if candidate["status"] == "shadow":
            cycle = self._advance_shadow_cycle(
                horizon,
                bundle,
                len(records),
                account_scope=account_scope,
            )
            candidate = {
                **candidate,
                "status": cycle["status"],
                "shadow_cycles": cycle["count"],
                "shadow_cycles_remaining": cycle["remaining"],
            }
        return {**candidate, "prediction_path": str(destination), "predictions": len(records)}
