from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from stock_analyze.dashboard_permanent_portfolio import (
    build_dashboard_permanent_portfolio_data,
)
from stock_analyze.research.permanent_portfolio.contract import canonical_hash


def _write_dashboard(root: Path, payload: dict[str, object]) -> Path:
    study = dict(payload["study"])
    study["state_sha256"] = canonical_hash(study)
    signed = {**payload, "study": study}
    signed["dashboard_sha256"] = canonical_hash(signed)
    path = root / "reports/research/permanent_portfolio/v1/dashboard.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(signed), encoding="utf-8")
    return path


class PermanentPortfolioDashboardTests(unittest.TestCase):
    def test_resource_exposes_only_historical_and_forward_windows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dashboard(
                root,
                {
                    "schema_version": 1,
                    "generated_at": "2026-08-30T12:00:00+00:00",
                    "study": {
                        "status": "holdout_complete",
                        "development_sha256": "a",
                    },
                    "historical": {
                        "start_date": "20180101",
                        "end_date": "20260828",
                        "stage_boundaries": [
                            {
                                "date": "20250101",
                                "before_label": "开发期",
                                "after_label": "盲测期",
                            }
                        ],
                        "portfolios": {},
                    },
                    "development": {"forbidden": 1},
                    "holdout": {
                        "forbidden": 2,
                    },
                    "forward": {"status": "unavailable"},
                },
            )

            payload = build_dashboard_permanent_portfolio_data(repo_root=root)

            self.assertEqual(set(payload["windows"]), {"historical", "forward"})
            self.assertEqual(
                payload["windows"]["historical"]["stage_boundaries"][0]["date"],
                "20250101",
            )
            self.assertNotIn("forbidden", json.dumps(payload))

    def test_missing_artifact_is_a_bounded_empty_resource(self) -> None:
        with TemporaryDirectory() as tmp:
            payload = build_dashboard_permanent_portfolio_data(
                repo_root=Path(tmp)
            )

            self.assertEqual(payload["status"], "unavailable")
            self.assertEqual(payload["strategies"], [])
            self.assertEqual(payload["errors"], ["artifact_missing"])

    def test_completed_payload_is_bounded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_dashboard(
                root,
                {
                    "schema_version": 1,
                    "generated_at": "2026-08-30T12:00:00+00:00",
                    "study": {
                        "status": "holdout_complete",
                        "holdout_sha256": "b",
                    },
                    "historical": {
                        "portfolios": {
                            "fixed": {
                                "series": [
                                    {"date": str(index), "normalized_nav": 1.0}
                                    for index in range(6000)
                                ],
                                "metrics": {},
                                "trades": [
                                    {
                                        "trade_date": f"2024{index // 28 + 1:02d}{index % 28 + 1:02d}",
                                        "side": "buy",
                                        "role": "equity",
                                        "shares": 100,
                                        "price": 4.0,
                                    }
                                    for index in range(100)
                                ],
                                "nav": [
                                    {
                                        "date": "20241230",
                                        "cash": 900.0,
                                        "market_value": 209000.0,
                                        "total_value": 209900.0,
                                    },
                                    {
                                        "date": "20241231",
                                        "cash": 1000.0,
                                        "market_value": 210000.0,
                                        "total_value": 211000.0,
                                    }
                                ],
                                "positions": [
                                    {
                                        "role": "equity",
                                        "code": "510300.SH",
                                        "shares": 10000,
                                        "last_price": 4.0,
                                        "market_value": 40000.0,
                                    }
                                ],
                                "targets": [
                                    {
                                        "role": "gold",
                                        "signal_date": f"20240{index + 1}01",
                                        "target_weight": 0.25,
                                    }
                                    for index in range(8)
                                ]
                                + [
                                    {
                                        "role": "equity",
                                        "signal_date": "20241231",
                                        "target_weight": 0.25,
                                    }
                                ],
                                "pending": [],
                            },
                            "equity_buy_hold": {
                                "series": [
                                    {
                                        "date": "20241231",
                                        "normalized_nav": 1.2,
                                    }
                                ],
                                "metrics": {},
                                "trades": [],
                            },
                        },
                        "unexpected": list(range(10000)),
                    },
                    "holdout": {"portfolios": {}},
                    "forward": {"status": "unavailable"},
                },
            )

            payload = build_dashboard_permanent_portfolio_data(repo_root=root)

            series = payload["windows"]["historical"]["portfolios"]["fixed"][
                "series"
            ]
            self.assertLessEqual(len(series), 1200)
            self.assertNotIn(
                "unexpected",
                payload["windows"]["historical"],
            )
            fixed = payload["windows"]["historical"]["portfolios"]["fixed"]
            benchmark = payload["windows"]["historical"]["portfolios"][
                "equity_buy_hold"
            ]
            self.assertEqual(fixed["nav"][0]["cash"], 1000.0)
            self.assertEqual(fixed["positions"][0]["shares"], 10000)
            self.assertEqual(fixed["targets"][-1]["role"], "equity")
            self.assertEqual(len(fixed["trades"]), 100)
            self.assertEqual(
                benchmark["series"][0]["normalized_nav"],
                1.2,
            )
            self.assertLess(
                len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                750_000,
            )

    def test_tampered_signed_report_is_unavailable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_dashboard(
                root,
                {
                    "schema_version": 1,
                    "study": {"status": "development_complete"},
                    "historical": {"portfolios": {}},
                    "forward": {"status": "unavailable"},
                },
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["study"]["status"] = "holdout_complete"
            path.write_text(json.dumps(payload), encoding="utf-8")

            result = build_dashboard_permanent_portfolio_data(repo_root=root)

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["errors"], ["artifact_checksum"])


if __name__ == "__main__":
    unittest.main()
