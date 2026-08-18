from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from stock_analyze.research.holder_concentration_backfill import (
    _fetch_pages,
    holder_count_partitions,
    load_holder_concentration_events,
    run_holder_concentration_backfill,
)
from stock_analyze.research.holder_concentration_study import (
    load_contract,
    run_holder_concentration_study,
    select_study_events,
)


CONTRACT = (
    Path(__file__).resolve().parents[1]
    / "configs/research/holder_concentration_study.yaml"
)


class HolderClient:
    def __init__(self, rows=None, *, paged=False):
        self.rows = list(rows or [])
        self.paged = paged
        self.offsets: list[int] = []

    def stk_holdernumber(self, **kwargs):
        self.offsets.append(int(kwargs["offset"]))
        if self.paged:
            count = 2000 if kwargs["offset"] == 0 else 1
            return pd.DataFrame([{
                "ts_code": "000001.SZ",
                "ann_date": kwargs["start_date"],
                "end_date": "20191231",
                "holder_num": 1000.0,
            }] * count)
        selected = [
            row for row in self.rows
            if kwargs["start_date"] <= row["ann_date"] <= kwargs["end_date"]
        ]
        return pd.DataFrame(
            selected, columns=["ts_code", "ann_date", "end_date", "holder_num"]
        )


def row(ann_date, end_date, holder_num, code="000001.SZ"):
    return {
        "ts_code": code, "ann_date": ann_date,
        "end_date": end_date, "holder_num": holder_num,
    }


class HolderConcentrationBackfillTest(unittest.TestCase):
    def test_seven_years_are_84_monthly_partitions(self):
        parts = holder_count_partitions("2018-01-01", "2024-12-31")
        self.assertEqual(len(parts), 84)
        self.assertEqual(parts[0], ("20180101", "20180131"))
        self.assertEqual(parts[-1], ("20241201", "20241231"))

    def test_provider_pages_until_short_page(self):
        client = HolderClient(paged=True)
        frame = _fetch_pages(client, "20200101", "20200131")
        self.assertEqual(len(frame), 2001)
        self.assertEqual(client.offsets, [0, 2000])

    def test_backfill_resumes_without_refetching_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = HolderClient()
            first = run_holder_concentration_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-02-29",
                max_partitions=1,
            )
            second = run_holder_concentration_backfill(
                tmp, client, start_date="2020-01-01", end_date="2020-02-29",
                max_partitions=1,
            )
        self.assertEqual(first["completed_partitions"], 1)
        self.assertEqual(second["completed_partitions"], 2)
        self.assertEqual(client.offsets, [0, 0])

    def test_partition_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_holder_concentration_backfill(
                tmp, HolderClient(), start_date="2020-01-01",
                end_date="2020-01-31",
            )
            path = (
                Path(tmp) / "data/research/holder_concentration_structured/v1"
                / "stk_holdernumber/20200101_20200131.parquet"
            )
            path.write_bytes(path.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "partition_tampered"):
                load_holder_concentration_events(
                    tmp, start_date="2020-01-01", end_date="2020-01-31"
                )

    def test_earliest_announcement_prevents_later_revision_lookahead(self):
        rows = [
            row("20200110", "20191231", 1000),
            row("20200210", "20191231", 700),
            row("20200420", "20200331", 800),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_holder_concentration_backfill(
                tmp, HolderClient(rows), start_date="2020-01-01",
                end_date="2020-12-31",
            )
            events, audit = load_holder_concentration_events(
                tmp, start_date="2020-01-01", end_date="2020-12-31"
            )
        self.assertTrue(audit["complete"])
        self.assertEqual(len(events), 1)
        event = events.iloc[0]
        self.assertEqual(event["family"], "holder_concentration")
        self.assertAlmostEqual(event["holder_count_change"], -0.20)
        self.assertEqual(event["ann_date"], "20200420")

    def test_conflicting_earliest_counts_are_excluded(self):
        rows = [
            row("20200110", "20191231", 1000),
            row("20200110", "20191231", 1100),
            row("20200420", "20200331", 800),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_holder_concentration_backfill(
                tmp, HolderClient(rows), start_date="2020-01-01",
                end_date="2020-12-31",
            )
            events, audit = load_holder_concentration_events(
                tmp, start_date="2020-01-01", end_date="2020-12-31"
            )
        self.assertEqual(audit["ambiguous_quarters"], 1)
        self.assertTrue(events.empty)

    def test_missing_immediately_previous_quarter_cannot_be_skipped(self):
        rows = [
            row("20191020", "20190930", 1000),
            row("20200420", "20200331", 800),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            run_holder_concentration_backfill(
                tmp, HolderClient(rows), start_date="2019-01-01",
                end_date="2020-12-31",
            )
            events, _ = load_holder_concentration_events(
                tmp, start_date="2019-01-01", end_date="2020-12-31"
            )
        self.assertTrue(events.empty)


class HolderConcentrationContractTest(unittest.TestCase):
    def test_contract_is_frozen(self):
        payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
        for field, value in (
            ("concentration_change_threshold", -0.05),
            ("primary_horizon", 60),
            ("development_end", "20251231"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "contract.json"
                path.write_text(
                    json.dumps({**payload, field: value}), encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, f"frozen:{field}"):
                    load_contract(path)

    def test_threshold_selection_is_central_and_dispersion_stays_diagnostic(self):
        contract = load_contract(CONTRACT)
        events = pd.DataFrame([
            {"family": "holder_concentration", "holder_count_change": -0.09},
            {"family": "holder_concentration", "holder_count_change": -0.10},
            {"family": "holder_dispersion", "holder_count_change": 0.10},
            {"family": "holder_dispersion", "holder_count_change": 0.09},
        ])
        selected = select_study_events(events, contract)
        self.assertEqual(
            selected["holder_count_change"].tolist(), [-0.10, 0.10]
        )
        self.assertEqual(
            selected["family"].tolist(),
            ["holder_concentration", "holder_dispersion"],
        )

    def test_incomplete_backfill_stops_before_price_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "contract.json"
            contract_path.write_bytes(CONTRACT.read_bytes())
            result = run_holder_concentration_study(
                root, snapshot_date="20260814",
                contract_path=contract_path, output_root=root / "reports",
            )
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["panel_rows"], 0)
        self.assertFalse(result["backfill"]["complete"])
        self.assertFalse(result["model_training_allowed"])


if __name__ == "__main__":
    unittest.main()
