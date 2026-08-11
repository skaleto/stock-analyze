from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.formal_lineage import (
    record_formal_decision,
    record_formal_fills,
    record_nav_attribution,
)
from stock_analyze.research.lineage import LineageConflictError, ResearchLineageStore
from stock_analyze.store import PortfolioStore


class ResearchLineageStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = ResearchLineageStore(self.root / "lineage.sqlite3")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def decision_run(run_id: str = "run-1") -> dict[str, object]:
        return {
            "decision_run_id": run_id,
            "agent_id": "codex",
            "market": "a_share",
            "strategy_id": "trend",
            "as_of": "2026-07-24",
            "account_state_hash": "account-before-1",
            "feature_snapshot_id": "features-1",
            "model_versions": {"20": "model-20-v1"},
            "horizon_weights": {"20": 1.0},
        }

    @staticmethod
    def candidate(
        candidate_id: str = "candidate-1",
        run_id: str = "run-1",
    ) -> dict[str, object]:
        return {
            "candidate_evaluation_id": candidate_id,
            "decision_run_id": run_id,
            "security_code": "000001",
            "eligible": True,
            "rank_score": 0.73,
            "rejection_reason": "",
            "constraints": {"industry_cap": "passed"},
        }

    @staticmethod
    def allocation(
        allocation_id: str = "allocation-1",
        run_id: str = "run-1",
        candidate_id: str = "candidate-1",
    ) -> dict[str, object]:
        return {
            "target_allocation_id": allocation_id,
            "decision_run_id": run_id,
            "candidate_evaluation_id": candidate_id,
            "security_code": "000001",
            "target_weight": 0.1,
            "target_quantity": 100,
        }

    @staticmethod
    def order(
        order_id: str = "order-1",
        run_id: str = "run-1",
        allocation_id: str = "allocation-1",
    ) -> dict[str, object]:
        return {
            "order_id": order_id,
            "decision_run_id": run_id,
            "target_allocation_id": allocation_id,
            "security_code": "000001",
            "side": "buy",
            "quantity": 100,
            "limit_price": 10.0,
        }

    @staticmethod
    def fill(fill_id: str = "fill-1", order_id: str = "order-1") -> dict[str, object]:
        return {
            "fill_id": fill_id,
            "order_id": order_id,
            "filled_at": "2026-07-27T01:31:00Z",
            "quantity": 100,
            "price": 9.95,
            "fees": 5.0,
        }

    @staticmethod
    def attribution(
        attribution_id: str = "pnl-1",
        run_id: str = "run-1",
        fill_id: str = "fill-1",
    ) -> dict[str, object]:
        return {
            "pnl_attribution_id": attribution_id,
            "decision_run_id": run_id,
            "fill_id": fill_id,
            "security_code": "000001",
            "as_of": "2026-07-27",
            "gross_pnl": 50.0,
            "cost_pnl": -5.0,
            "net_pnl": 45.0,
            "components": {"alpha": 35.0, "market": 15.0},
        }

    @staticmethod
    def trial(trial_id: str = "trial-1") -> dict[str, object]:
        return {
            "trial_id": trial_id,
            "experiment_id": "experiment-1",
            "market": "a_share",
            "horizon": 20,
            "model_version": "model-20-v1",
            "params": {"seed": 7, "features": ["momentum_20"]},
            "metrics": {"rank_ic": 0.08, "net_excess_return": 0.12},
        }

    def write_graph(self, store: ResearchLineageStore | None = None) -> None:
        target = store or self.store
        target.append_decision_runs(self.decision_run())
        target.append_candidate_evaluations([self.candidate()])
        target.append_target_allocations(self.allocation())
        target.append_orders([self.order()])
        target.append_fills(self.fill())
        target.append_pnl_attributions([self.attribution()])
        target.append_experiment_trials(self.trial())

    def test_schema_has_all_entities_and_database_enforces_append_only(self) -> None:
        self.write_graph()

        with self.store.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertTrue(
                {
                    "decision_runs",
                    "candidate_evaluations",
                    "target_allocations",
                    "orders",
                    "fills",
                    "pnl_attributions",
                    "experiment_trials",
                }.issubset(tables)
            )
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE decision_runs SET payload_json='{}' "
                    "WHERE decision_run_id='run-1'"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM decision_runs WHERE decision_run_id='run-1'"
                )

        self.assertEqual(self.store.integrity_check(), "ok")
        self.assertEqual(self.store.foreign_key_violations(), [])

    def test_each_writer_accepts_dict_or_list_and_is_idempotent(self) -> None:
        writes = [
            (self.store.append_decision_runs, self.decision_run()),
            (self.store.append_candidate_evaluations, self.candidate()),
            (self.store.append_target_allocations, self.allocation()),
            (self.store.append_orders, self.order()),
            (self.store.append_fills, self.fill()),
            (self.store.append_pnl_attributions, self.attribution()),
            (self.store.append_experiment_trials, self.trial()),
        ]

        for writer, row in writes:
            self.assertEqual(writer(row), 1)
            reordered = dict(reversed(list(row.items())))
            self.assertEqual(writer([reordered]), 0)

        for table in ResearchLineageStore.TABLES:
            self.assertEqual(self.store.count(table), 1)

    def test_same_primary_key_with_different_content_raises_and_batch_rolls_back(self) -> None:
        self.store.append_decision_runs(self.decision_run())
        conflicting = {**self.decision_run(), "strategy_id": "defensive"}

        with self.assertRaisesRegex(
            LineageConflictError,
            "decision_runs:run-1",
        ):
            self.store.append_decision_runs(
                [self.decision_run("run-2"), conflicting]
            )

        self.assertEqual(self.store.count("decision_runs"), 1)
        self.assertEqual(
            self.store.query("decision_runs", {"decision_run_id": "run-2"}),
            [],
        )

    def test_foreign_keys_reject_orphaned_lineage(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append_candidate_evaluations(
                self.candidate(run_id="missing-run")
            )

        self.store.append_decision_runs(self.decision_run())
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append_target_allocations(
                self.allocation(candidate_id="missing-candidate")
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append_fills(self.fill(order_id="missing-order"))

    def test_query_and_decision_projection_reconstruct_complete_chain(self) -> None:
        self.write_graph()

        orders = self.store.query(
            "orders",
            {"decision_run_id": "run-1", "security_code": "000001"},
        )
        self.assertEqual(orders, [self.order()])

        projected = self.store.project(decision_run_id="run-1")
        self.assertEqual(projected["decision_runs"], [self.decision_run()])
        self.assertEqual(projected["candidate_evaluations"], [self.candidate()])
        self.assertEqual(projected["target_allocations"], [self.allocation()])
        self.assertEqual(projected["orders"], [self.order()])
        self.assertEqual(projected["fills"], [self.fill()])
        self.assertEqual(projected["pnl_attributions"], [self.attribution()])
        self.assertEqual(projected["experiment_trials"], [])

        full_projection = self.store.project()
        self.assertEqual(full_projection["experiment_trials"], [self.trial()])

    def test_rebuild_atomically_replaces_projection_without_touching_account_csv(self) -> None:
        source = ResearchLineageStore(self.root / "source.sqlite3")
        self.write_graph(source)
        projection = source.project()

        self.store.append_decision_runs(self.decision_run("obsolete-run"))
        account_csv = self.root / "daily_nav.csv"
        account_csv.write_text("trade_date,nav\n2026-07-24,1.0\n", encoding="utf-8")

        inserted = self.store.rebuild(projection)

        self.assertEqual(inserted, 7)
        self.assertEqual(self.store.project(), projection)
        self.assertEqual(
            account_csv.read_text(encoding="utf-8"),
            "trade_date,nav\n2026-07-24,1.0\n",
        )

    def test_failed_rebuild_preserves_existing_projection(self) -> None:
        self.store.append_decision_runs(self.decision_run())
        broken = {
            table: [] for table in ResearchLineageStore.TABLES
        }
        broken["candidate_evaluations"] = [
            self.candidate(run_id="missing-run")
        ]

        with self.assertRaises(sqlite3.IntegrityError):
            self.store.rebuild(broken)

        self.assertEqual(
            self.store.query("decision_runs"),
            [self.decision_run()],
        )

    def test_query_rejects_unknown_tables_and_columns(self) -> None:
        with self.assertRaisesRegex(ValueError, "lineage_table_invalid"):
            self.store.query("sqlite_master")
        with self.assertRaisesRegex(ValueError, "lineage_filter_invalid"):
            self.store.query("orders", {"payload_json": "{}"})

    def test_formal_projection_records_decision_to_pnl_without_mutating_state(self) -> None:
        data_dir = self.root / "data" / "a_share" / "codex"
        portfolio = PortfolioStore(data_dir)
        config = {
            "agent_id": "codex",
            "strategy_id": "trend-v1",
            "accounts": [{
                "id": "hs300",
                "name": "沪深300",
                "scope": "hs300",
                "benchmark": "000300",
                "cash": 100_000,
            }],
        }
        portfolio.initialize(config)
        portfolio.write_factor_snapshot(
            pd.DataFrame([
                {
                    "account_id": "hs300", "code": "000001",
                    "factor": "momentum_20", "contribution": 0.8,
                    "valid": True, "selected": True,
                    "signal_date": "2026-07-23",
                },
                {
                    "account_id": "hs300", "code": "000002",
                    "factor": "momentum_20", "contribution": 0.3,
                    "valid": True, "selected": False,
                    "signal_date": "2026-07-23",
                },
            ]),
            "formal-run",
        )
        generated = [{
            "account_id": "hs300",
            "signal_date": "2026-07-23",
            "execute_after": "2026-07-24",
            "strategy_id": "trend-v1",
            "selected_codes": ["000001"],
            "target_weights": {"000001": 0.5},
            "orders": [{
                "code": "000001", "side": "buy", "delta_shares": 100,
                "target_weight": 0.5, "trade_date": "2026-07-24",
            }],
        }]
        state_before = portfolio.state_path.read_bytes()

        first = record_formal_decision(
            repo_root=self.root,
            market="a_share",
            config=config,
            store=portfolio,
            run_id="formal-run",
            as_of="2026-07-23",
            generated=generated,
        )
        second = record_formal_decision(
            repo_root=self.root,
            market="a_share",
            config=config,
            store=portfolio,
            run_id="formal-run",
            as_of="2026-07-23",
            generated=generated,
        )

        self.assertEqual(first["inserted"]["decision_runs"], 1)
        self.assertEqual(second["inserted"]["decision_runs"], 0)
        self.assertEqual(portfolio.state_path.read_bytes(), state_before)
        lineage = ResearchLineageStore(
            self.root / "data" / "shared" / "research_lineage.sqlite3"
        )
        self.assertEqual(lineage.count("decision_runs"), 1)
        self.assertEqual(lineage.count("candidate_evaluations"), 2)
        self.assertEqual(lineage.count("target_allocations"), 1)
        self.assertEqual(lineage.count("orders"), 1)

        trade = {
            "trade_date": "2026-07-24", "account_id": "hs300",
            "code": "000001", "side": "buy", "shares": 100,
            "price": 10.0, "commission": 1.0, "stamp_tax": 0.0,
            "slippage": 0.2, "net_amount": -1001.2,
        }
        fills = record_formal_fills(
            repo_root=self.root,
            market="a_share",
            agent_id="codex",
            trades=[trade],
        )
        self.assertEqual(fills["inserted"], 1)
        self.assertEqual(fills["unmatched"], 0)

        portfolio.append_nav([
            {
                "date": "2026-07-23", "account_id": "hs300",
                "cash": 50_000, "settlement_receivable": 0,
                "market_value": 50_000, "total_value": 100_000,
                "benchmark_code": "000300", "benchmark_close": 100,
                "benchmark_date": "2026-07-23", "notes": "",
            },
            {
                "date": "2026-07-24", "account_id": "hs300",
                "cash": 49_000, "settlement_receivable": 0,
                "market_value": 51_500, "total_value": 100_500,
                "benchmark_code": "000300", "benchmark_close": 101,
                "benchmark_date": "2026-07-24", "notes": "",
            },
        ])
        attribution = record_nav_attribution(
            repo_root=self.root,
            market="a_share",
            agent_id="codex",
            store=portfolio,
            nav_rows=[{
                "date": "2026-07-24", "account_id": "hs300",
                "market_value": 51_500, "total_value": 100_500,
                "benchmark_close": 101,
            }],
            trades=[trade],
            decision_ids=fills["decision_ids"],
        )

        self.assertEqual(attribution["status"], "complete")
        self.assertEqual(attribution["inserted"], 1)
        summary = lineage.query(
            "pnl_attributions",
            {"security_code": "__PORTFOLIO__"},
        )[0]
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["reconciliation_delta"], 0.0)
        self.assertEqual(summary["strategy_id"], "trend-v1")
        self.assertEqual(summary["account_id"], "hs300")
        self.assertEqual(summary["model_policy_status"], "rule_only")
        self.assertEqual(summary["model_versions"], {})
        self.assertEqual(summary["model_selection_pnl"], 0.0)
        self.assertGreaterEqual(summary["explained_ratio"], 0.95)
        self.assertNotIn("residual_above_limit", summary["unavailable_inputs"])
        self.assertIn("factor_attribution", summary["unavailable_inputs"])

    def test_formal_projection_records_applied_model_versions_and_allocation_risk(self) -> None:
        data_dir = self.root / "data" / "a_share" / "codex"
        portfolio = PortfolioStore(data_dir)
        config = {
            "agent_id": "codex",
            "strategy_id": "trend-v2",
            "accounts": [{
                "id": "hs300",
                "scope": "hs300",
                "benchmark": "000300",
                "cash": 100_000,
            }],
        }
        policy_path = self.root / "configs" / "strategy_competition.json"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(
            """
            {
              "model_policy": {
                "trend": {
                  "required_role": "ranker",
                  "horizon_weights": {"3": 0.5, "5": 0.5},
                  "max_prediction_age_days": 3,
                  "missing_behavior": "rule_only"
                }
              }
            }
            """,
            encoding="utf-8",
        )
        portfolio.initialize(config)
        portfolio.write_factor_snapshot(
            pd.DataFrame([
                {
                    "account_id": "hs300",
                    "code": "000001",
                    "factor": "momentum_20",
                    "contribution": 0.8,
                    "valid": True,
                    "selected": True,
                    "signal_date": "2026-07-24",
                    "prediction_applied": True,
                    "prediction_horizons": "3,5",
                    "prediction_model_versions": "ranker-3-v2,ranker-5-v4",
                    "prediction_fallback_reason": "",
                },
                {
                    "account_id": "hs300",
                    "code": "000002",
                    "factor": "momentum_20",
                    "contribution": 0.3,
                    "valid": True,
                    "selected": False,
                    "signal_date": "2026-07-24",
                    "prediction_applied": False,
                    "prediction_horizons": "",
                    "prediction_model_versions": "",
                    "prediction_fallback_reason": "declared_horizon_unavailable_or_ineligible",
                },
            ]),
            "formal-model-run",
        )
        diagnostics = {
            "expected_alpha": 0.03,
            "expected_cost": 0.0007,
            "risk_contributions": {"000001": 0.72},
            "binding_constraints": ["max_name_weight"],
            "fallback_reason": None,
        }

        result = record_formal_decision(
            repo_root=self.root,
            market="a_share",
            config=config,
            store=portfolio,
            run_id="formal-model-run",
            as_of="2026-07-24",
            generated=[{
                "account_id": "hs300",
                "target_weights": {"000001": 0.4},
                "optimizer_diagnostics": diagnostics,
                "orders": [],
            }],
        )

        self.assertEqual(result["inserted"]["orders"], 0)
        lineage = ResearchLineageStore(
            self.root / "data" / "shared" / "research_lineage.sqlite3"
        )
        decision = lineage.query("decision_runs")[0]
        self.assertEqual(decision["model_role"], "ranker")
        self.assertEqual(
            decision["model_versions"],
            {"3": "ranker-3-v2", "5": "ranker-5-v4"},
        )
        self.assertEqual(decision["model_policy_status"], "active")
        self.assertEqual(decision["model_applied_candidates"], 1)
        self.assertEqual(decision["model_candidate_coverage"], 0.5)
        self.assertEqual(decision["model_fallback_reason"], "")

        candidates = {
            row["security_code"]: row
            for row in lineage.query("candidate_evaluations")
        }
        self.assertTrue(candidates["000001"]["prediction_applied"])
        self.assertEqual(
            candidates["000001"]["prediction_model_versions"],
            {"3": "ranker-3-v2", "5": "ranker-5-v4"},
        )
        allocation = lineage.query("target_allocations")[0]
        self.assertEqual(allocation["expected_cost"], 0.0007)
        self.assertEqual(allocation["risk_contribution"], 0.72)
        self.assertEqual(
            allocation["binding_constraints"],
            ["max_name_weight"],
        )

    def test_formal_projection_records_rule_only_when_model_evidence_is_missing(self) -> None:
        data_dir = self.root / "data" / "a_share" / "claude"
        portfolio = PortfolioStore(data_dir)
        config = {
            "agent_id": "claude",
            "strategy_id": "defensive-v2",
            "accounts": [{
                "id": "hs300",
                "scope": "hs300",
                "benchmark": "000300",
                "cash": 100_000,
            }],
        }
        policy_path = self.root / "configs" / "strategy_competition.json"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(
            """
            {
              "model_policy": {
                "defensive": {
                  "required_role": "ranker",
                  "horizon_weights": {"10": 0.4, "20": 0.6},
                  "missing_behavior": "rule_only"
                }
              }
            }
            """,
            encoding="utf-8",
        )
        portfolio.initialize(config)
        portfolio.write_factor_snapshot(
            pd.DataFrame([{
                "account_id": "hs300",
                "code": "000001",
                "factor": "roe",
                "contribution": 0.5,
                "valid": True,
                "selected": True,
                "signal_date": "2026-07-24",
            }]),
            "formal-rule-run",
        )

        record_formal_decision(
            repo_root=self.root,
            market="a_share",
            config=config,
            store=portfolio,
            run_id="formal-rule-run",
            as_of="2026-07-24",
            generated=[{
                "account_id": "hs300",
                "target_weights": {"000001": 0.4},
                "orders": [],
            }],
        )

        lineage = ResearchLineageStore(
            self.root / "data" / "shared" / "research_lineage.sqlite3"
        )
        decision = lineage.query("decision_runs")[0]
        self.assertEqual(decision["model_versions"], {})
        self.assertEqual(decision["model_policy_status"], "rule_only")
        self.assertEqual(
            decision["model_fallback_reason"],
            "prediction_application_evidence_missing",
        )


if __name__ == "__main__":
    unittest.main()
