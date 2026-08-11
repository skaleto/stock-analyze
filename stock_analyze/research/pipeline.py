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
from typing import Any

import numpy as np
import pandas as pd

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
from .portfolio_replay import SIMULATOR_VERSION
from .prediction import generate_predictions
from .regime import classify_regimes
from .source_features import (
    SourceCollection,
    add_industry_features,
    attach_industry_membership,
    attach_point_in_time_features,
    attach_qdii_point_in_time_features,
    build_fundamental_history,
    build_regime_components,
    build_source_features,
)
from .storage import ResearchStore
from .technical_features import compute_technical_features
from .trial_ledger import DEFAULT_CLASSICAL_TRIAL_SPECS, TrialLedger
from .universe import attach_point_in_time_universe


class ResearchPipeline:
    _FEATURE_BATCH_SIZE = 32
    _REGIME_SOURCE_NAMES = {
        "cn_pmi", "cn_m", "cn_cpi", "cn_ppi", "shibor", "us_tycr",
        "index_global", "fx_daily",
    }

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

    def _history_files(self) -> list[Path]:
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
        frame = pd.read_csv(path, dtype={"ts_code": str, "trade_date": str, "日期": str})
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
        normalized = frame.rename(columns=aliases).copy()
        normalized["code"] = code
        normalized["trade_date"] = normalized["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
        for column in ("open", "high", "low", "close", "volume", "amount", "turnover_rate"):
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
                "turnover_rate",
            )
            if column in normalized.columns
        ]
        return normalized[keep]

    @staticmethod
    def _compact_numeric_features(frame: pd.DataFrame, *, copy: bool = True) -> pd.DataFrame:
        compact = frame.copy() if copy else frame
        for column in compact.select_dtypes(include=["float64"]).columns:
            compact[column] = compact[column].astype(np.float32)
        return compact

    def prepare_data(self, *, force: bool = False) -> dict[str, Any]:
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
            persisted_sources = self._load_persisted_source_frames()
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
            source_frames = self._load_persisted_source_frames()
            source_count = sum(not frame.empty for frame in source_frames.values())
        if source_frames:
            if self.market == "a_share":
                fundamental_history = build_fundamental_history(source_frames)
                featured = attach_point_in_time_features(featured, fundamental_history)
                featured = attach_industry_membership(
                    featured,
                    source_frames.get("index_member_all", pd.DataFrame()),
                )
                featured = self._attach_a_share_industry_fallback(featured)
            else:
                featured = attach_qdii_point_in_time_features(featured, source_frames)
            regime_sources = self._load_regime_source_frames()
            regime_sources.update(source_frames)
            market_context = build_regime_components(regime_sources, featured["trade_date"])
            featured = featured.merge(market_context, on="trade_date", how="left")
            source_features = build_source_features(source_frames)
            if not source_features.empty:
                source_features = source_features.set_index("code")
                latest_indices = featured.sort_values("trade_date").groupby("code").tail(1).index
                for column in source_features.columns.difference(["ts_code"]):
                    mapped = featured.loc[latest_indices, "code"].map(source_features[column])
                    if column not in featured.columns:
                        featured[column] = np.nan
                    current = featured.loc[latest_indices, column]
                    featured.loc[latest_indices, column] = current.where(current.notna(), mapped)
        if self.market == "cn_qdii_etf":
            featured = self._attach_qdii_metadata(featured)
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
                    compute_technical_features(history),
                    copy=False,
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
            return pd.read_parquet(batch_root)

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

    def _research_portfolio_contract(self) -> dict[str, Any]:
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
            return {
                "accounts": [dict(account) for account in baseline.get("accounts") or []],
                "trading": dict(baseline.get("trading") or {}),
                "schedule": dict(baseline.get("schedule") or {}),
                "execution_policy": execution_policy,
            }
        return {
            "accounts": self._baseline_accounts(),
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

    def _load_persisted_source_frames(self) -> dict[str, pd.DataFrame]:
        if self._persisted_source_frames_cache is not None:
            return self._persisted_source_frames_cache
        market_root = self.research_root / "raw" / self.market
        if not market_root.exists():
            return {}
        runs = sorted(
            path for path in market_root.iterdir()
            if path.is_dir() and path.name.isdigit() and path.name <= self.run_key
        )
        if not runs:
            return {}
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
            for path in run.glob("*.parquet"):
                if path.stem == "source_health":
                    continue
                versions.setdefault(path.stem, []).append(pd.read_parquet(path))
        frames = {
            name: self._merge_source_versions(items)
            for name, items in versions.items()
        }
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
        candidates = sorted(
            path for path in cache.glob("stock_basic_*.csv")
            if path.stem.rsplit("_", 1)[-1].isdigit() and path.stem.rsplit("_", 1)[-1] <= self.run_key
        )
        if not candidates:
            return features
        basic = pd.read_csv(candidates[-1], dtype={"code": str})
        if not {"code", "industry"}.issubset(basic.columns):
            return features
        mapping = basic.drop_duplicates("code", keep="last").set_index("code")["industry"]
        result = features.copy()
        fallback = result["code"].astype("string").str.zfill(6).map(mapping)
        missing = result["industry"].isna() | result["industry"].eq("unclassified")
        result.loc[missing, "industry"] = fallback.loc[missing].fillna("unclassified")
        result.loc[missing & fallback.notna(), "industry_l2"] = fallback.loc[missing & fallback.notna()]
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

    def _model_root(self, horizon: int) -> Path:
        return self.research_root / "models" / self.market / str(horizon)

    def train_models(self) -> dict[str, Any]:
        snapshot_date = self.store.latest_common_snapshot_date(
            self.market,
            as_of=self.as_of,
        )
        features = self.store.read_feature_snapshot(self.market, snapshot_date)
        labels = self.store.read_label_snapshot(self.market, snapshot_date)
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
                model_root = self._model_root(horizon)
                portfolio_contract = self._research_portfolio_contract()
                contract_hash = hashlib.sha256(
                    json.dumps(portfolio_contract, sort_keys=True).encode("utf-8")
                ).hexdigest()[:16]
                trial_family_id = ":".join((
                    self.market,
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
                )
                trial_registry = TrialRegistry(model_root / "trials.jsonl")
                metrics = bundle.metrics
                period_dates = list(metrics.get("portfolio_period_return_dates") or [])
                period_returns = list(metrics.get("portfolio_period_returns") or [])
                trial = trial_registry.record({
                    "trial_id": f"{self.market}:{horizon}:{self.run_key}:{bundle.model_version}",
                    "experiment_id": trial_family_id,
                    "trial_family_id": trial_family_id,
                    "model_version": bundle.model_version,
                    "as_of": self.as_of,
                    "snapshot_date": snapshot_date,
                    "market": self.market,
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
                failures.append({"horizon": horizon, "error": str(exc)[:200]})
        return {
            "status": "complete" if trained else "failed",
            "snapshot_date": snapshot_date,
            "trained": trained,
            "failures": failures,
        }

    def _resolve_model(self, horizon: int) -> tuple[Path, str]:
        artifact, statuses = self._resolve_model_roles(horizon)
        return artifact, str(statuses.get("ranker", "research"))

    def _resolve_model_roles(self, horizon: int) -> tuple[Path, dict[str, str]]:
        model_root = self._model_root(horizon)
        registry_path = model_root / "registry.json"
        if registry_path.exists():
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            selected = select_registry_model(state, role="ranker")
            if selected is not None:
                _, metadata = selected
                fallback = str(metadata.get("status", "research"))
                statuses = {
                    role: str((metadata.get("role_status") or {}).get(role, fallback))
                    for role in ("classifier", "ranker", "portfolio")
                }
                return Path(metadata["artifact"]), statuses
        return model_root / "missing.joblib", {
            "classifier": "research",
            "ranker": "research",
            "portfolio": "research",
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
            regime, regime_stability = self._current_regime_context()
            target_horizons = (horizon,) if horizon is not None else (3, 5, 10, 20)
            for target_horizon in target_horizons:
                try:
                    artifact, role_status = self._resolve_model_roles(target_horizon)
                    status = str(role_status.get("ranker", "research"))
                    bundle = load_model_bundle(artifact)
                    records = generate_predictions(
                        bundle,
                        latest,
                        as_of=self.as_of,
                        horizon=target_horizon,
                        regime=regime,
                        data_quality=1.0,
                        regime_stability=regime_stability,
                        feature_snapshot_id=f"{self.market}-{self.run_key}",
                        active_status=status if status == "active" else "inactive",
                        role_status=role_status,
                    )
                    records, drift = self._assess_model_drift(
                        target_horizon,
                        bundle,
                        records,
                        role_status=role_status,
                    )
                    drift_assessments[str(target_horizon)] = drift
                    artifacts[str(target_horizon)] = str(artifact)
                    statuses[str(target_horizon)] = status
                    rows.extend(self._prediction_rows(records))
                    if self.agent == "codex":
                        candidate = self._write_iteration_candidate_predictions(
                            target_horizon,
                            latest,
                            canonical_bundle=bundle,
                            canonical_records=records,
                            regime=regime,
                            regime_stability=regime_stability,
                        )
                        if candidate is not None:
                            iteration_candidates[str(target_horizon)] = candidate
                            if candidate.get("shadow_cycles") is not None:
                                cycle_counts[str(target_horizon)] = int(candidate["shadow_cycles"])
                            if candidate["model_version"] == bundle.model_version:
                                statuses[str(target_horizon)] = str(candidate["status"])
                except Exception as exc:  # noqa: BLE001 - preserve successful horizons
                    failures.append({"horizon": target_horizon, "error": str(exc)[:240]})
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
                "horizons": sorted(int(value) for value in artifacts),
                "artifacts": artifacts,
                "active_status": statuses,
                "failures": failures,
                "shadow_cycles": cycle_counts,
                "iteration_candidates": iteration_candidates,
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
        registry = ModelRegistry(self._model_root(horizon) / "registry.json")
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
            self._model_root(horizon) / "drift_lifecycle.json"
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
            ):
                if metadata.get(column) is not None:
                    row[column] = metadata[column]
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
            row["invalidation"] = json.dumps(row["invalidation"], ensure_ascii=False)
            row["metadata"] = json.dumps(metadata, ensure_ascii=False)
            rows.append(row)
        return rows

    def _advance_shadow_cycle(self, horizon: int, bundle: Any, prediction_count: int) -> dict[str, Any]:
        model_root = self._model_root(horizon)
        forward_evidence = load_forward_portfolio_evidence(
            iteration_portfolio_dir(
                self.repo_root,
                self.market,
                horizon,
                bundle.model_version,
            ),
            expected_account_ids=tuple(
                str(account.get("id")) for account in self._baseline_accounts()
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
        canonical_bundle: Any,
        canonical_records: list,
        regime: str,
        regime_stability: float,
    ) -> dict[str, Any] | None:
        candidate = ensure_iteration_candidate(
            self.repo_root,
            self.market,
            horizon,
            as_of=self.as_of,
        )
        if candidate is None:
            return None
        version = str(candidate["model_version"])
        if version == str(canonical_bundle.model_version):
            bundle = canonical_bundle
            records = canonical_records
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
        )
        self.store.write_parquet_atomic(destination, pd.DataFrame(self._prediction_rows(records)))
        if candidate["status"] == "shadow":
            cycle = self._advance_shadow_cycle(horizon, bundle, len(records))
            candidate = {
                **candidate,
                "status": cycle["status"],
                "shadow_cycles": cycle["count"],
                "shadow_cycles_remaining": cycle["remaining"],
            }
        return {**candidate, "prediction_path": str(destination), "predictions": len(records)}
