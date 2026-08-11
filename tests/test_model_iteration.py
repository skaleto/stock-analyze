import json
import tempfile
import unittest
from pathlib import Path


class ModelIterationLifecycleTest(unittest.TestCase):
    def _write_registry(self, root: Path, market: str, horizon: int, registry: dict) -> Path:
        path = (
            root
            / "data"
            / "research"
            / "models"
            / market
            / str(horizon)
            / "registry.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(registry), encoding="utf-8")
        return path

    def test_selects_latest_shadow_before_latest_research(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "cn_qdii_etf", 5, {
                "champion_model_version": "champion",
                "models": {
                    "champion": {"status": "active", "registered_at": "2026-07-01T12:00:00+08:00"},
                    "shadow-old": {"status": "shadow", "registered_at": "2026-07-02T12:00:00+08:00"},
                    "shadow-new": {"status": "shadow", "registered_at": "2026-07-03T12:00:00+08:00"},
                    "research-newest": {"status": "research", "registered_at": "2026-07-04T12:00:00+08:00"},
                },
            })

            candidate = ensure_iteration_candidate(root, "cn_qdii_etf", 5, as_of="2026-07-18")

        self.assertEqual(candidate["model_version"], "shadow-new")
        self.assertEqual(candidate["display_version"], "Q5-V003")
        self.assertEqual(candidate["status"], "shadow")
        self.assertEqual(candidate["champion_model_version"], "champion")
        self.assertEqual(candidate["shadow_cycles"], 0)
        self.assertEqual(candidate["shadow_cycles_remaining"], 12)

    def test_research_candidate_rotates_when_newer_research_model_arrives(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate, read_iteration_state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "candidate-v1": {"status": "research", "registered_at": "2026-07-01T12:00:00+08:00"},
                },
            })
            first = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["models"]["candidate-v2"] = {
                "status": "research",
                "registered_at": "2026-07-18T12:00:00+08:00",
            }
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            second = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")
            state = read_iteration_state(root, "a_share", 20)

        self.assertEqual(first["model_version"], "candidate-v1")
        self.assertEqual(second["model_version"], "candidate-v2")
        self.assertEqual(second["display_version"], "A20-V002")
        self.assertEqual(state["history"][-1]["model_version"], "candidate-v1")
        self.assertEqual(state["history"][-1]["outcome"], "superseded")

    def test_shadow_candidate_stays_pinned_when_newer_research_model_arrives(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "shadow-v1": {
                        "status": "shadow",
                        "registered_at": "2026-07-01T12:00:00+08:00",
                    },
                },
            })
            first = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["models"]["research-v2"] = {
                "status": "research",
                "registered_at": "2026-07-18T12:00:00+08:00",
            }
            registry_path.write_text(json.dumps(registry), encoding="utf-8")

            second = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")

        self.assertEqual(first["model_version"], "shadow-v1")
        self.assertEqual(second["model_version"], "shadow-v1")

    def test_terminal_models_are_never_selected_as_iteration_candidates(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_registry(root, "a_share", 20, {
                "champion_model_version": None,
                "models": {
                    "rejected": {
                        "status": "rejected",
                        "registered_at": "2026-07-18T12:00:00+08:00",
                    },
                    "quarantined": {
                        "status": "quarantined",
                        "registered_at": "2026-07-19T12:00:00+08:00",
                    },
                },
            })

            candidate = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-20")

        self.assertIsNone(candidate)

    def test_promotion_closes_candidate_and_selects_next_version(self):
        from stock_analyze.model_iteration import ensure_iteration_candidate, read_iteration_state

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = self._write_registry(root, "a_share", 20, {
                "champion_model_version": "champion-v1",
                "models": {
                    "champion-v1": {"status": "active", "registered_at": "2026-07-01T12:00:00+08:00"},
                    "candidate-v2": {"status": "shadow", "registered_at": "2026-07-02T12:00:00+08:00"},
                    "candidate-v3": {"status": "research", "registered_at": "2026-07-03T12:00:00+08:00"},
                },
            })
            selected = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-17")
            self.assertEqual(selected["model_version"], "candidate-v2")

            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            registry["champion_model_version"] = "candidate-v2"
            registry["models"]["candidate-v2"]["status"] = "active"
            registry_path.write_text(json.dumps(registry), encoding="utf-8")
            replacement = ensure_iteration_candidate(root, "a_share", 20, as_of="2026-07-18")
            state = read_iteration_state(root, "a_share", 20)

        self.assertEqual(replacement["model_version"], "candidate-v3")
        self.assertEqual(replacement["champion_model_version"], "candidate-v2")
        self.assertEqual(state["history"][-1]["model_version"], "candidate-v2")
        self.assertEqual(state["history"][-1]["outcome"], "promoted")
        self.assertEqual(state["history"][-1]["ended_at"], "2026-07-18")

    def test_versioned_paths_keep_predictions_and_portfolios_isolated(self):
        from stock_analyze.model_iteration import (
            iteration_portfolio_dir,
            iteration_prediction_path,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prediction = iteration_prediction_path(
                root, "cn_qdii_etf", 5, "model-v4", "2026-07-18"
            )
            portfolio = iteration_portfolio_dir(root, "cn_qdii_etf", 5, "model-v4")

        self.assertEqual(
            prediction.relative_to(root).as_posix(),
            "data/research/iteration_predictions/cn_qdii_etf/5/model-v4/20260718.parquet",
        )
        self.assertEqual(
            portfolio.relative_to(root).as_posix(),
            "data/model_iterations/cn_qdii_etf/5/model-v4",
        )


if __name__ == "__main__":
    unittest.main()
