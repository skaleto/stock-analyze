"""End-to-end research preparation, study, training, and prediction workflows."""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .activation import ModelRegistry, ShadowCycleTracker
from .event_study import build_event_study
from .events import detect_events
from .labels import build_forward_labels
from .models import load_model_bundle, save_model_bundle, train_model_bundle
from .prediction import generate_predictions
from .regime import classify_regimes
from .source_features import SourceCollection, build_source_features
from .storage import ResearchStore
from .technical_features import compute_technical_features


class ResearchPipeline:
    def __init__(
        self,
        repo_root: str | Path,
        *,
        market: str,
        agent: str,
        as_of: str | None = None,
        offline: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.market = market
        self.agent = agent
        self.as_of = as_of or date.today().isoformat()
        self.offline = offline
        self.research_root = self.repo_root / "data" / "research"
        self.store = ResearchStore(self.research_root)

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
        latest_by_code: dict[str, Path] = {}
        for path in candidates:
            match = re.search(r"(?:fund_daily|history)_(\d{6})", path.name)
            if match:
                latest_by_code[match.group(1)] = path
        return sorted(latest_by_code.values())

    @staticmethod
    def _normalize_history(path: Path) -> pd.DataFrame:
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
        keep = [column for column in ("code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover_rate") if column in normalized.columns]
        return normalized[keep]

    def prepare_data(self, *, force: bool = False) -> dict[str, Any]:
        destination = self.store.feature_snapshot_path(self.market, self.as_of)
        if destination.exists() and not force:
            rows = len(self.store.read_feature_snapshot(self.market, self.as_of))
            return {"status": "cached", "rows": rows, "path": str(destination)}
        histories = [self._normalize_history(path) for path in self._history_files()]
        histories = [frame for frame in histories if not frame.empty]
        if not histories:
            raise FileNotFoundError(f"research_history_cache_missing:{self._cache_dir()}")
        ohlcv = pd.concat(histories, ignore_index=True)
        ohlcv = ohlcv.loc[ohlcv["trade_date"] <= self.run_key]
        featured = compute_technical_features(ohlcv)
        source_count = 0
        if not self.offline:
            sources = self._collect_sources(sorted(featured["code"].dropna().astype(str).unique()))
            raw_root = self.research_root / "raw" / self.market / self.run_key
            for name, frame in sources.frames.items():
                if frame.empty:
                    continue
                self.store.write_parquet_atomic(raw_root / f"{name}.parquet", frame)
                source_count += 1
            if not sources.health.empty:
                self.store.write_parquet_atomic(raw_root / "source_health.parquet", sources.health)
            source_features = build_source_features(sources.frames)
            if not source_features.empty:
                source_features = source_features.set_index("code")
                latest_indices = featured.sort_values("trade_date").groupby("code").tail(1).index
                for column in source_features.columns.difference(["ts_code"]):
                    featured[column] = np.nan
                    featured.loc[latest_indices, column] = featured.loc[latest_indices, "code"].map(source_features[column])
        featured["feature_observed_at"] = self.as_of
        self.store.write_feature_snapshot(self.market, self.as_of, featured)
        return {
            "status": "built",
            "rows": len(featured),
            "instruments": int(featured["code"].nunique()),
            "path": str(destination),
            "offline": self.offline,
            "sources": source_count,
        }

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
        )

    def _artifact_path(self, name: str) -> Path:
        return self.research_root / name / self.market / f"{self.run_key}.parquet"

    def run_research(self) -> dict[str, Any]:
        features = self.store.read_feature_snapshot(self.market, self.as_of)
        price_columns = [column for column in ("code", "trade_date", "close") if column in features.columns]
        labels = build_forward_labels(features[price_columns])
        self.store.write_label_snapshot(self.market, self.as_of, labels)

        daily = features.groupby("trade_date", as_index=False).agg(
            momentum_20=("momentum_20", "median"),
            realized_volatility_20=("realized_volatility_20", "median"),
            volume_ratio_5_20=("volume_ratio_5_20", "median"),
        )
        daily["trend_score"] = np.tanh(daily["momentum_20"].fillna(0.0) * 8.0)
        volatility_center = daily["realized_volatility_20"].expanding(min_periods=10).median()
        volatility_scale = daily["realized_volatility_20"].expanding(min_periods=10).std().replace(0.0, np.nan)
        daily["volatility_score"] = (daily["realized_volatility_20"] - volatility_center) / volatility_scale
        daily["liquidity_score"] = np.tanh((daily["volume_ratio_5_20"].fillna(1.0) - 1.0) * 2.0)
        daily["macro_score"] = np.nan
        daily["global_risk_score"] = np.nan
        regimes = classify_regimes(daily)
        self.store.write_parquet_atomic(self._artifact_path("regimes"), regimes)

        events = detect_events(features, market=self.market)
        if not events.empty:
            event_regime = regimes[["trade_date", "composite_regime"]].rename(columns={"composite_regime": "detected_regime"})
            events = events.merge(event_regime, on="trade_date", how="left")
            events["regime"] = events["detected_regime"].fillna(events["regime"])
            events = events.drop(columns="detected_regime")
        self.store.write_parquet_atomic(self._artifact_path("events"), events)
        event_study = build_event_study(events, labels) if not events.empty else pd.DataFrame()
        self.store.write_parquet_atomic(self._artifact_path("event_studies"), event_study)
        return {
            "status": "complete",
            "stages": ["features", "labels", "events", "regimes", "event_study"],
            "features_rows": len(features),
            "labels_rows": len(labels),
            "events_rows": len(events),
            "regime_rows": len(regimes),
            "event_study_rows": len(event_study),
        }

    def _model_root(self, horizon: int) -> Path:
        return self.research_root / "models" / self.market / str(horizon)

    def train_models(self) -> dict[str, Any]:
        features = self.store.read_feature_snapshot(self.market, self.as_of)
        labels = self.store.read_label_snapshot(self.market, self.as_of)
        dataset = features.merge(labels, on=["code", "trade_date"], how="inner", suffixes=("", "_label"))
        excluded = {
            "code", "trade_date", "open", "high", "low", "close", "volume", "amount",
            "horizon", "label", "label_end_date", "absolute_return", "benchmark_return",
            "excess_return", "threshold", "max_favorable_excursion", "max_adverse_excursion",
        }
        numeric = [column for column in dataset.select_dtypes(include=[np.number]).columns if column not in excluded]
        feature_columns = [column for column in numeric if dataset[column].notna().mean() >= 0.55]
        trained: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for horizon in (3, 5, 10, 20):
            try:
                bundle = train_model_bundle(dataset, feature_columns=feature_columns, horizon=horizon)
                artifact = self._model_root(horizon) / f"{self.run_key}-{bundle.model_version}.joblib"
                save_model_bundle(bundle, artifact)
                registry = ModelRegistry(self._model_root(horizon) / "registry.json")
                state = registry._read()
                state.setdefault("models", {})[bundle.model_version] = {
                    "status": "research",
                    "artifact": str(artifact),
                    "gate_history": [],
                }
                registry._write(state)
                trained.append({"horizon": horizon, "model_version": bundle.model_version, "artifact": str(artifact)})
            except Exception as exc:  # noqa: BLE001 - one horizon must not erase others
                failures.append({"horizon": horizon, "error": str(exc)[:200]})
        return {"status": "complete" if trained else "failed", "trained": trained, "failures": failures}

    def _resolve_model(self, horizon: int) -> tuple[Path, str]:
        model_root = self._model_root(horizon)
        registry_path = model_root / "registry.json"
        if registry_path.exists():
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            champion = state.get("champion_model_version")
            models = state.get("models") or {}
            if champion and champion in models:
                return Path(models[champion]["artifact"]), "active"
            candidates = sorted(models.items(), key=lambda item: item[0])
            if candidates:
                version, metadata = candidates[-1]
                return Path(metadata["artifact"]), str(metadata.get("status", "research"))
        return model_root / "missing.joblib", "research"

    def predict(self, *, horizon: int = 5) -> dict[str, Any]:
        health_path = self.research_root / "prediction_health" / self.market / f"{self.run_key}-{self.agent}.json"
        try:
            artifact, status = self._resolve_model(horizon)
            bundle = load_model_bundle(artifact)
            features = self.store.read_feature_snapshot(self.market, self.as_of)
            latest = features.sort_values("trade_date").groupby("code", as_index=False).tail(1)
            records = generate_predictions(
                bundle,
                latest,
                as_of=self.as_of,
                horizon=horizon,
                regime="unknown",
                data_quality=1.0,
                regime_stability=0.5,
                feature_snapshot_id=f"{self.market}-{self.run_key}",
                active_status=status if status == "active" else "inactive",
            )
            rows = []
            for record in records:
                row = asdict(record)
                row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
                row["invalidation"] = json.dumps(row["invalidation"], ensure_ascii=False)
                row["metadata"] = json.dumps(row["metadata"], ensure_ascii=False)
                rows.append(row)
            destination = self.repo_root / "data" / self.market / self.agent / "predictions" / f"{self.run_key}.parquet"
            self.store.write_parquet_atomic(destination, pd.DataFrame(rows))
            health = {"status": "complete", "predictions": len(rows), "artifact": str(artifact), "active_status": status}
            if status == "shadow":
                cycle = ShadowCycleTracker(self._model_root(horizon) / "shadow_cycles.json").record(
                    bundle.model_version,
                    self.as_of,
                    {"predictions": len(rows), "calibration_quality": bundle.metrics.get("calibration_quality")},
                )
                health["shadow_cycles"] = cycle["count"]
                health["shadow_cycles_remaining"] = cycle["remaining"]
        except Exception as exc:  # noqa: BLE001 - trading path remains unchanged
            health = {"status": "fallback", "predictions": 0, "error": str(exc)[:240]}
        write_text_atomic(health_path, json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**health, "health_path": str(health_path)}
