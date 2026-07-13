"""End-to-end research preparation, study, training, and prediction workflows."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..utils import write_text_atomic
from .activation import (
    ModelRegistry,
    ShadowCycleTracker,
    activation_evidence_from_metrics,
    evaluate_activation,
    select_registry_model,
)
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
        max_full_history_instruments: int = 60,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.market = market
        self.agent = agent
        self.as_of = as_of or date.today().isoformat()
        self.offline = offline
        self.max_full_history_instruments = max(1, int(max_full_history_instruments))
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
        history_files = self._history_files()
        if not history_files:
            raise FileNotFoundError(f"research_history_cache_missing:{self._cache_dir()}")
        all_codes = [
            match.group(1)
            for path in history_files
            if (match := re.search(r"(?:fund_daily|history)_(\d{6})", path.name))
        ]
        full_history_codes = self._full_history_codes(all_codes)
        feature_parts: list[pd.DataFrame] = []
        for path in history_files:
            history = self._normalize_history(path)
            history = history.loc[history["trade_date"] <= self.run_key]
            if history.empty:
                continue
            part = compute_technical_features(history)
            code = str(part.iloc[-1]["code"])
            keep_full = self.market != "a_share" or code in full_history_codes
            part["history_role"] = "full" if keep_full else "latest_only"
            feature_parts.append(part if keep_full else part.tail(1))
        if not feature_parts:
            raise FileNotFoundError(f"research_history_cache_empty:{self._cache_dir()}")
        featured = pd.concat(feature_parts, ignore_index=True)
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
            "full_history_instruments": len(full_history_codes) if self.market == "a_share" else int(featured["code"].nunique()),
        }

    def _full_history_codes(self, all_codes: list[str]) -> set[str]:
        if self.market != "a_share":
            return set(all_codes)
        available = set(all_codes)
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
        prioritized = list(dict.fromkeys(code for code in priority if code in available))
        remaining = sorted(
            available.difference(prioritized),
            key=lambda code: hashlib.sha256(f"a-share-research-v1|{code}".encode("utf-8")).hexdigest(),
        )
        ordered = [*prioritized, *remaining]
        return set(ordered[: self.max_full_history_instruments])

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
                model = state.setdefault("models", {}).setdefault(
                    bundle.model_version,
                    {"status": "research", "gate_history": []},
                )
                model["artifact"] = str(artifact)
                model.setdefault("registered_at", datetime.now(timezone.utc).isoformat())
                registry._write(state)
                gate = None
                if model.get("status", "research") == "research":
                    gate = evaluate_activation(
                        activation_evidence_from_metrics(bundle.metrics),
                        current_status="research",
                        target_status="shadow",
                    )
                    state = registry.record_gate(bundle.model_version, gate)
                trained.append({
                    "horizon": horizon,
                    "model_version": bundle.model_version,
                    "artifact": str(artifact),
                    "status": state["models"][bundle.model_version]["status"],
                    "gate_passed": gate.passed if gate is not None else None,
                    "gate_reasons": list(gate.reasons) if gate is not None else [],
                })
            except Exception as exc:  # noqa: BLE001 - one horizon must not erase others
                failures.append({"horizon": horizon, "error": str(exc)[:200]})
        return {"status": "complete" if trained else "failed", "trained": trained, "failures": failures}

    def _resolve_model(self, horizon: int) -> tuple[Path, str]:
        model_root = self._model_root(horizon)
        registry_path = model_root / "registry.json"
        if registry_path.exists():
            state = json.loads(registry_path.read_text(encoding="utf-8"))
            selected = select_registry_model(state)
            if selected is not None:
                _, metadata = selected
                return Path(metadata["artifact"]), str(metadata.get("status", "research"))
        return model_root / "missing.joblib", "research"

    def predict(self, *, horizon: int | None = None) -> dict[str, Any]:
        health_path = self.research_root / "prediction_health" / self.market / f"{self.run_key}-{self.agent}.json"
        try:
            features = self.store.read_feature_snapshot(self.market, self.as_of)
            latest = features.sort_values("trade_date").groupby("code", as_index=False).tail(1)
            rows: list[dict[str, Any]] = []
            artifacts: dict[str, str] = {}
            statuses: dict[str, str] = {}
            failures: list[dict[str, Any]] = []
            cycle_counts: dict[str, int] = {}
            target_horizons = (horizon,) if horizon is not None else (3, 5, 10, 20)
            for target_horizon in target_horizons:
                try:
                    artifact, status = self._resolve_model(target_horizon)
                    bundle = load_model_bundle(artifact)
                    records = generate_predictions(
                        bundle,
                        latest,
                        as_of=self.as_of,
                        horizon=target_horizon,
                        regime="unknown",
                        data_quality=1.0,
                        regime_stability=0.5,
                        feature_snapshot_id=f"{self.market}-{self.run_key}",
                        active_status=status if status == "active" else "inactive",
                    )
                    artifacts[str(target_horizon)] = str(artifact)
                    statuses[str(target_horizon)] = status
                    rows.extend(self._prediction_rows(records))
                    if status == "shadow" and self.agent == "codex":
                        cycle = self._advance_shadow_cycle(target_horizon, bundle, len(records))
                        cycle_counts[str(target_horizon)] = cycle["count"]
                        statuses[str(target_horizon)] = cycle["status"]
                    if self.agent == "codex":
                        self._run_shadow_challengers(target_horizon, latest, exclude_version=bundle.model_version)
                except Exception as exc:  # noqa: BLE001 - preserve successful horizons
                    failures.append({"horizon": target_horizon, "error": str(exc)[:240]})
            if not rows:
                raise RuntimeError(f"prediction_models_unavailable:{failures}")
            destination = self.repo_root / "data" / self.market / self.agent / "predictions" / f"{self.run_key}.parquet"
            self.store.write_parquet_atomic(destination, pd.DataFrame(rows))
            health = {
                "status": "partial" if failures else "complete",
                "predictions": len(rows),
                "horizons": sorted(int(value) for value in artifacts),
                "artifacts": artifacts,
                "active_status": statuses,
                "failures": failures,
                "shadow_cycles": cycle_counts,
            }
        except Exception as exc:  # noqa: BLE001 - trading path remains unchanged
            health = {"status": "fallback", "predictions": 0, "error": str(exc)[:240]}
        write_text_atomic(health_path, json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**health, "health_path": str(health_path)}

    @staticmethod
    def _prediction_rows(records: list) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in records:
            row = asdict(record)
            row["reasons"] = json.dumps(row["reasons"], ensure_ascii=False)
            row["invalidation"] = json.dumps(row["invalidation"], ensure_ascii=False)
            row["metadata"] = json.dumps(row["metadata"], ensure_ascii=False)
            rows.append(row)
        return rows

    def _advance_shadow_cycle(self, horizon: int, bundle: Any, prediction_count: int) -> dict[str, Any]:
        model_root = self._model_root(horizon)
        cycle = ShadowCycleTracker(model_root / "shadow_cycles.json").record(
            bundle.model_version,
            self.as_of,
            {"predictions": prediction_count, "calibration_quality": bundle.metrics.get("calibration_quality")},
        )
        registry = ModelRegistry(model_root / "registry.json")
        state = registry._read()
        status = str(((state.get("models") or {}).get(bundle.model_version) or {}).get("status", "shadow"))
        if cycle["is_new_cycle"] and status == "shadow":
            report = evaluate_activation(
                activation_evidence_from_metrics(bundle.metrics, shadow_cycles=cycle["count"]),
                current_status="shadow",
                target_status="active",
            )
            state = registry.record_gate(bundle.model_version, report)
            status = str(state["models"][bundle.model_version]["status"])
        return {**cycle, "status": status}

    def _run_shadow_challengers(self, horizon: int, features: pd.DataFrame, *, exclude_version: str) -> None:
        model_root = self._model_root(horizon)
        registry_path = model_root / "registry.json"
        if not registry_path.exists():
            return
        state = json.loads(registry_path.read_text(encoding="utf-8"))
        candidates = [
            (version, metadata)
            for version, metadata in (state.get("models") or {}).items()
            if version != exclude_version and metadata.get("status") == "shadow"
        ]
        if not candidates:
            return
        registered = [item for item in candidates if item[1].get("registered_at")]
        version, metadata = (
            max(registered, key=lambda item: str(item[1]["registered_at"]))
            if registered else candidates[-1]
        )
        bundle = load_model_bundle(Path(metadata["artifact"]))
        records = generate_predictions(
            bundle,
            features,
            as_of=self.as_of,
            horizon=horizon,
            regime="unknown",
            data_quality=1.0,
            regime_stability=0.5,
            feature_snapshot_id=f"{self.market}-{self.run_key}",
            active_status="inactive",
        )
        destination = self.research_root / "shadow_predictions" / self.market / str(horizon) / version / f"{self.run_key}.parquet"
        self.store.write_parquet_atomic(destination, pd.DataFrame(self._prediction_rows(records)))
        self._advance_shadow_cycle(horizon, bundle, len(records))
