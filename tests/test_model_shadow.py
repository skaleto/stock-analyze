from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from stock_analyze.cli import build_parser
from stock_analyze.model_shadow import (
    build_model_candidates,
    latest_prediction_path,
    load_shadow_profile,
    run_model_iteration,
    run_shadow_cycle,
)
from stock_analyze.store import PortfolioStore


def _prediction(
    code: str,
    *,
    horizon: int = 5,
    confidence: float = 0.70,
    p_up: float = 0.60,
    p_down: float = 0.20,
    expected: float = 0.04,
    invalidated: bool = False,
) -> dict[str, object]:
    return {
        "code": code,
        "as_of": "2026-07-17",
        "horizon": horizon,
        "confidence": confidence,
        "p_up": p_up,
        "p_flat": 1.0 - p_up - p_down,
        "p_down": p_down,
        "expected_excess_return": expected,
        "return_q10": expected - 0.10,
        "return_q50": expected,
        "return_q90": expected + 0.10,
        "invalidated": invalidated,
        "model_version": "model-v3",
        "reasons": '["MACD快线 正向贡献 0.200"]',
    }


class FakeAShareProvider:
    def price_snapshot(self, code: str, as_of: str | None = None):
        return SimpleNamespace(
            code=code,
            name="测试股份",
            trade_date=as_of,
            close=10.0,
            low_volatility_60=0.02,
        )

    def benchmark_close(self, code: str, as_of: str | None = None):
        return 4000.0, as_of

    def next_trading_day(self, value: str) -> str:
        return "2026-07-20"


class FakeETFProvider:
    def price_snapshot(self, code: str, as_of: str | None = None):
        return SimpleNamespace(
            code=code,
            name="纳指ETF",
            trade_date=as_of,
            close=2.0,
            low_volatility_60=0.02,
        )


class ModelCandidatePolicyTests(unittest.TestCase):
    def test_profiles_choose_validated_market_specific_horizons(self) -> None:
        root = Path(__file__).resolve().parents[1]

        a_share = load_shadow_profile(root, "a_share")
        qdii = load_shadow_profile(root, "cn_qdii_etf")

        self.assertEqual(a_share["horizon"], 20)
        self.assertEqual(qdii["horizon"], 5)
        self.assertEqual(a_share["initial_cash"], 1_000_000)
        self.assertEqual(qdii["initial_cash"], 1_000_000)

    def test_only_non_invalidated_confident_positive_long_signals_are_selected(self) -> None:
        profile = {
            "horizon": 5,
            "minimum_confidence": 0.55,
            "top_n": 5,
            "max_single_weight": 0.20,
        }
        frame = pd.DataFrame(
            [
                _prediction("513100", expected=0.04),
                _prediction("513500", confidence=0.40),
                _prediction("159941", p_up=0.20, p_down=0.60),
                _prediction("513520", expected=-0.01),
                _prediction("159920", invalidated=True),
                _prediction("513030", horizon=20),
            ]
        )

        selected, diagnostics = build_model_candidates(frame, profile)

        self.assertEqual(selected["code"].tolist(), ["513100"])
        self.assertTrue(bool(selected.iloc[0]["prediction_applied"]))
        self.assertGreater(float(selected.iloc[0]["expected_volatility"]), 0.0)
        self.assertIn("模型5日", str(selected.iloc[0]["score_detail"]))
        self.assertEqual(diagnostics["source_rows"], 6)
        self.assertEqual(diagnostics["eligible_rows"], 1)
        self.assertEqual(diagnostics["invalidated_rows"], 1)

    def test_higher_expected_return_per_risk_ranks_first(self) -> None:
        profile = {
            "horizon": 5,
            "minimum_confidence": 0.55,
            "top_n": 5,
            "max_single_weight": 0.20,
        }
        strong = _prediction("513100", expected=0.06)
        strong["return_q10"], strong["return_q90"] = -0.01, 0.09
        weak = _prediction("513500", expected=0.03)
        weak["return_q10"], weak["return_q90"] = -0.10, 0.16

        selected, _ = build_model_candidates(pd.DataFrame([weak, strong]), profile)

        self.assertEqual(selected.iloc[0]["code"], "513100")
        self.assertGreater(float(selected.iloc[0]["score"]), float(selected.iloc[1]["score"]))

    def test_latest_prediction_never_reads_a_future_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "data" / "a_share" / "codex" / "predictions"
            directory.mkdir(parents=True)
            for value in ("20260716", "20260717", "20260720"):
                (directory / f"{value}.parquet").touch()

            path = latest_prediction_path(Path(tmp), "a_share", "2026-07-17")

        self.assertEqual(path.name, "20260717.parquet")


class ModelShadowCycleTests(unittest.TestCase):
    @staticmethod
    def _write_candidate(
        root: Path,
        *,
        version: str,
        champion: str | None = None,
        status: str = "research",
    ) -> None:
        model_root = root / "data" / "research" / "models" / "cn_qdii_etf" / "5"
        model_root.mkdir(parents=True, exist_ok=True)
        models = {
            version: {
                "status": status,
                "registered_at": "2026-07-17T12:00:00+08:00",
                "artifact": str(model_root / f"{version}.joblib"),
            }
        }
        if champion:
            models[champion] = {
                "status": "active",
                "registered_at": "2026-07-16T12:00:00+08:00",
                "artifact": str(model_root / f"{champion}.joblib"),
            }
        (model_root / "registry.json").write_text(json.dumps({
            "champion_model_version": champion,
            "models": models,
        }), encoding="utf-8")
        prediction_dir = (
            root
            / "data"
            / "research"
            / "iteration_predictions"
            / "cn_qdii_etf"
            / "5"
            / version
        )
        prediction_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([_prediction("513100") | {"model_version": version}]).to_parquet(
            prediction_dir / "20260717.parquet",
            index=False,
        )

    def test_bearish_a_share_snapshot_stays_in_cash_with_explicit_status(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile = load_shadow_profile(root, "a_share")
        bearish = pd.DataFrame(
            [_prediction("000001", horizon=20, p_up=0.25, p_down=0.60, expected=-0.02)]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)

            result = run_shadow_cycle(
                market="a_share",
                profile=profile,
                store=store,
                provider=FakeAShareProvider(),
                predictions=bearish,
                as_of="2026-07-17",
                prediction_as_of="2026-07-17",
                run_id="shadow-test-a",
            )

            status = json.loads((Path(tmp) / "shadow_status.json").read_text(encoding="utf-8"))
            nav = store.read_nav()

        self.assertEqual(result["pending_orders"], 0)
        self.assertTrue(result["cash_only"])
        self.assertEqual(status["cash_reason"], "模型未发现满足条件的上行机会")
        self.assertEqual(float(nav.iloc[-1]["total_value"]), 1_000_000.0)

    def test_etf_cycle_creates_named_next_day_orders_and_is_idempotent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        profile = load_shadow_profile(root, "cn_qdii_etf")
        predictions = pd.DataFrame(
            [
                _prediction("513100", expected=0.05),
                _prediction("513500", expected=0.04),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioStore(tmp)
            first = run_shadow_cycle(
                market="cn_qdii_etf",
                profile=profile,
                store=store,
                provider=FakeETFProvider(),
                predictions=predictions,
                as_of="2026-07-17",
                prediction_as_of="2026-07-17",
                run_id="shadow-test-etf-1",
            )
            first_pending = store.load_pending()
            second = run_shadow_cycle(
                market="cn_qdii_etf",
                profile=profile,
                store=store,
                provider=FakeETFProvider(),
                predictions=predictions,
                as_of="2026-07-17",
                prediction_as_of="2026-07-17",
                run_id="shadow-test-etf-2",
            )
            second_pending = store.load_pending()
            nav = store.read_nav()

        self.assertEqual(first["pending_orders"], 2)
        self.assertTrue(first["decision_changed"])
        self.assertFalse(second["decision_changed"])
        self.assertEqual(len(first_pending), 2)
        self.assertEqual(first_pending, second_pending)
        self.assertEqual(
            [row["target_weight"] for row in first["selected"]],
            [row["target_weight"] for row in second["selected"]],
        )
        self.assertTrue(all(row["target_weight"] > 0 for row in second["selected"]))
        self.assertTrue(all(order["trade_date"] == "2026-07-20" for order in second_pending))
        self.assertTrue(all(order["name"] == "纳指ETF" for order in second_pending))
        self.assertEqual(len(nav), 1)

    def test_model_versions_use_isolated_portfolio_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = Path(__file__).resolve().parents[1] / "configs" / "model_shadow.json"
            (root / "configs").mkdir()
            (root / "configs" / "model_shadow.json").write_text(
                source_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self._write_candidate(root, version="candidate-v1")

            def fake_cycle(**kwargs):
                kwargs["store"].data_dir.mkdir(parents=True, exist_ok=True)
                (kwargs["store"].data_dir / "state.json").write_text(
                    json.dumps({"version": kwargs["predictions"].iloc[0]["model_version"]}),
                    encoding="utf-8",
                )
                return {"cash_only": True, "pending_orders": 0}

            market_module = SimpleNamespace(
                make_provider=lambda **_kwargs: SimpleNamespace(persist_health=lambda: None)
            )
            with (
                patch("stock_analyze.model_shadow.competition.get_market_module", return_value=market_module),
                patch("stock_analyze.model_shadow.run_shadow_cycle", side_effect=fake_cycle),
            ):
                first = run_model_iteration(
                    repo_root=root,
                    market="cn_qdii_etf",
                    as_of="2026-07-17",
                )
                self._write_candidate(root, version="candidate-v2", champion="candidate-v1")
                second = run_model_iteration(
                    repo_root=root,
                    market="cn_qdii_etf",
                    as_of="2026-07-17",
                )

            first_state = json.loads((
                root / "data" / "model_iterations" / "cn_qdii_etf" / "5" / "candidate-v1" / "state.json"
            ).read_text())
            second_state = json.loads((
                root / "data" / "model_iterations" / "cn_qdii_etf" / "5" / "candidate-v2" / "state.json"
            ).read_text())

        self.assertEqual(first["model_version"], "candidate-v1")
        self.assertEqual(second["model_version"], "candidate-v2")
        self.assertEqual(first_state["version"], "candidate-v1")
        self.assertEqual(second_state["version"], "candidate-v2")

    def test_missing_candidate_prediction_never_falls_back_to_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = Path(__file__).resolve().parents[1] / "configs" / "model_shadow.json"
            (root / "configs").mkdir()
            (root / "configs" / "model_shadow.json").write_text(
                source_config.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            self._write_candidate(root, version="candidate-v2", champion="champion-v1")
            candidate_prediction = (
                root / "data" / "research" / "iteration_predictions" / "cn_qdii_etf" / "5" / "candidate-v2" / "20260717.parquet"
            )
            candidate_prediction.unlink()
            canonical_dir = root / "data" / "cn_qdii_etf" / "codex" / "predictions"
            canonical_dir.mkdir(parents=True)
            pd.DataFrame([_prediction("513100") | {"model_version": "champion-v1"}]).to_parquet(
                canonical_dir / "20260717.parquet",
                index=False,
            )

            result = run_model_iteration(
                repo_root=root,
                market="cn_qdii_etf",
                as_of="2026-07-17",
            )

        self.assertEqual(result["status"], "prediction_missing")
        self.assertEqual(result["model_version"], "candidate-v2")
        self.assertNotIn("prediction_path", result)

    def test_cli_exposes_agent_free_model_iteration_command_and_shadow_alias(self) -> None:
        iteration_args = build_parser().parse_args(
            [
                "--market",
                "cn_qdii_etf",
                "--as-of",
                "2026-07-17",
                "run-model-iteration",
                "--offline",
            ]
        )
        args = build_parser().parse_args(
            [
                "--market",
                "cn_qdii_etf",
                "--as-of",
                "2026-07-17",
                "run-model-shadow",
                "--offline",
            ]
        )

        self.assertEqual(iteration_args.command, "run-model-iteration")
        self.assertTrue(iteration_args.offline)
        self.assertIsNone(iteration_args.agent)
        self.assertEqual(args.command, "run-model-shadow")
        self.assertTrue(args.offline)
        self.assertIsNone(args.agent)


if __name__ == "__main__":
    unittest.main()
