from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from datetime import date
import math
from pathlib import Path
from typing import Any
import unittest

import yaml

from stock_analyze.research.a_share_all_cap_contract import (
    load_all_cap_contract,
    parse_all_cap_contract,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "configs" / "research" / "a_share_all_cap_v2.yaml"


class AllCapContractTests(unittest.TestCase):
    def valid_payload(self) -> dict[str, Any]:
        payload = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        return copy.deepcopy(payload)

    def payload_with_capital_weight_total(
        self,
        target_total: float,
    ) -> tuple[dict[str, Any], float]:
        payload = self.valid_payload()
        sleeves = payload["sleeves"]
        fixed_total = math.fsum(
            float(sleeves[name]["capital_weight"])
            for name in ("large", "mid", "small")
        )
        sleeves["micro"]["capital_weight"] = target_total - fixed_total
        actual_total = math.fsum(
            float(item["capital_weight"])
            for item in sleeves.values()
        )
        return payload, actual_total

    def test_loads_frozen_boundaries_weights_and_holdout_policy(self) -> None:
        contract = load_all_cap_contract(CONTRACT_PATH)

        self.assertEqual(contract.campaign_id, "a_share_all_cap_v2")
        self.assertEqual(contract.development_start, date(2018, 1, 2))
        self.assertEqual(contract.development_end, date(2024, 12, 31))
        self.assertEqual(contract.holdout_start, date(2025, 1, 1))
        self.assertEqual(contract.holdout_end, date(2026, 8, 21))
        self.assertEqual(contract.size_boundaries, (300, 800, 1800, 3800))
        self.assertAlmostEqual(
            sum(item.capital_weight for item in contract.sleeves),
            1.0,
            places=9,
        )
        self.assertEqual(
            contract.holdout_policy,
            "open_once_after_data_code_and_development_gates",
        )

    def test_contract_and_raw_payload_are_deeply_immutable(self) -> None:
        payload = self.valid_payload()
        contract = parse_all_cap_contract(payload)

        with self.assertRaises(FrozenInstanceError):
            setattr(contract, "campaign_id", "changed")
        with self.assertRaises(TypeError):
            contract.raw["campaign_id"] = "changed"  # type: ignore[index]
        with self.assertRaises(TypeError):
            contract.raw["universe"]["size_rank_boundaries"] = ()  # type: ignore[index]
        self.assertIsInstance(contract.raw["forbidden_inputs"], tuple)

        payload["universe"]["size_rank_boundaries"][0] = 1
        self.assertEqual(
            contract.raw["universe"]["size_rank_boundaries"],
            (300, 800, 1800, 3800),
        )

    def test_sleeve_contract_is_frozen(self) -> None:
        sleeve = load_all_cap_contract(CONTRACT_PATH).sleeves[0]

        with self.assertRaises(FrozenInstanceError):
            setattr(sleeve, "benchmark", "changed")

    def test_rejects_non_research_mode(self) -> None:
        payload = self.valid_payload()
        payload["research_only"] = False

        with self.assertRaisesRegex(ValueError, "all_cap_contract:research_only"):
            parse_all_cap_contract(payload)

    def test_rejects_non_monotonic_size_boundaries(self) -> None:
        payload = self.valid_payload()
        payload["universe"]["size_rank_boundaries"] = [
            800,
            300,
            1800,
            3800,
        ]

        with self.assertRaisesRegex(ValueError, "all_cap_contract:size_boundaries"):
            parse_all_cap_contract(payload)

    def test_rejects_changed_frozen_size_boundaries(self) -> None:
        payload = self.valid_payload()
        payload["universe"]["size_rank_boundaries"] = [
            300,
            800,
            1800,
            3801,
        ]
        payload["sleeves"]["micro"]["rank_max"] = 3801

        with self.assertRaisesRegex(ValueError, "all_cap_contract:size_boundaries"):
            parse_all_cap_contract(payload)

    def test_rejects_sleeve_ranges_mismatched_with_boundaries(self) -> None:
        payload = self.valid_payload()
        payload["sleeves"]["mid"]["rank_min"] = 302

        with self.assertRaisesRegex(ValueError, "all_cap_contract:sleeve_boundaries"):
            parse_all_cap_contract(payload)

    def test_rejects_invalid_chronological_window_ordering(self) -> None:
        payload = self.valid_payload()
        payload["windows"]["holdout_start"] = "2024-12-31"

        with self.assertRaisesRegex(ValueError, "all_cap_contract:windows"):
            parse_all_cap_contract(payload)

    def test_rejects_capital_weights_not_summing_to_one(self) -> None:
        payload = self.valid_payload()
        payload["sleeves"]["micro"]["capital_weight"] = 0.09

        with self.assertRaisesRegex(ValueError, "all_cap_contract:capital_weights"):
            parse_all_cap_contract(payload)

    def test_accepts_capital_weight_total_immediately_inside_tolerance(self) -> None:
        inside_total = math.nextafter(1.0 + 1e-9, 1.0)
        payload, actual_total = self.payload_with_capital_weight_total(
            inside_total
        )

        self.assertLessEqual(abs(actual_total - 1.0), 1e-9)
        contract = parse_all_cap_contract(payload)
        self.assertEqual(
            math.fsum(item.capital_weight for item in contract.sleeves),
            actual_total,
        )

    def test_rejects_capital_weight_total_immediately_outside_tolerance(self) -> None:
        outside_total = math.nextafter(1.0 + 1e-9, math.inf)
        payload, actual_total = self.payload_with_capital_weight_total(
            outside_total
        )

        self.assertGreater(abs(actual_total - 1.0), 1e-9)
        with self.assertRaisesRegex(ValueError, "all_cap_contract:capital_weights"):
            parse_all_cap_contract(payload)

    def test_rejects_missing_or_blank_benchmark_codes(self) -> None:
        for benchmark in (None, "   "):
            with self.subTest(benchmark=benchmark):
                payload = self.valid_payload()
                payload["sleeves"]["small"]["benchmark"] = benchmark

                with self.assertRaisesRegex(
                    ValueError,
                    "all_cap_contract:benchmark",
                ):
                    parse_all_cap_contract(payload)

    def test_rejects_different_holdout_policy(self) -> None:
        payload = self.valid_payload()
        payload["windows"]["holdout_policy"] = "rolling_open"

        with self.assertRaisesRegex(ValueError, "all_cap_contract:holdout_policy"):
            parse_all_cap_contract(payload)

    def test_rejects_storage_free_space_floor_below_fifteen_percent(self) -> None:
        payload = self.valid_payload()
        payload["storage"][
            "minimum_filesystem_free_fraction_after_publish"
        ] = 0.149999

        with self.assertRaisesRegex(ValueError, "all_cap_contract:storage_free_space"):
            parse_all_cap_contract(payload)


if __name__ == "__main__":
    unittest.main()
