"""Tests for the audited, research-only multi-agent workflow."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

from stock_analyze.research.multi_agent_workflow import (
    ResearchLLMResponse,
    build_research_evidence,
    run_multi_agent_research,
)
from stock_analyze.research.storage import ResearchStore


class EvidenceBuilderTests(unittest.TestCase):
    def test_builds_a_share_evidence_from_existing_feature_snapshot_and_catalog(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchStore(root / "data/research")
            store.write_feature_snapshot(
                "a_share",
                "20260820",
                pd.DataFrame([
                    {
                        "code": "000001.SZ",
                        "name": "平安银行",
                        "industry": "银行",
                        "trade_date": "20260820",
                        "close": 12.34,
                        "momentum_20": 0.08,
                        "realized_volatility_20": 0.21,
                        "pe_ttm": 5.6,
                        "roe": 11.2,
                        "event_score": 0.3,
                    },
                ]),
            )
            catalog = root / "data/research/universe_catalogs/latest.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(
                json.dumps({
                    "a_share": {
                        "records": [{
                            "ts_code": "000001.SZ",
                            "research_scopes": ["hs300"],
                            "membership_date": "20260803",
                        }]
                    }
                }),
                encoding="utf-8",
            )

            evidence = build_research_evidence(
                repo_root=root,
                market="a_share",
                code="000001.SZ",
                as_of="2026-08-22",
            )

        self.assertEqual(evidence["instrument"]["name"], "平安银行")
        self.assertEqual(evidence["as_of"], "20260820")
        self.assertEqual(evidence["facts"]["technical"]["momentum_20"], 0.08)
        self.assertEqual(evidence["facts"]["fundamentals"]["pe_ttm"], 5.6)
        self.assertEqual(evidence["facts"]["event_lite"]["event_score"], 0.3)
        self.assertEqual(evidence["research_catalog"]["research_scopes"], ["hs300"])
        self.assertTrue(evidence["research_only"])
        self.assertIn("feature_snapshot", evidence["sources"][0]["kind"])

    def test_fails_closed_when_requested_code_is_not_in_current_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ResearchStore(root / "data/research")
            store.write_feature_snapshot(
                "a_share",
                "20260820",
                pd.DataFrame([{"code": "000001.SZ", "trade_date": "20260820"}]),
            )

            with self.assertRaisesRegex(ValueError, "research_evidence_code_missing"):
                build_research_evidence(
                    repo_root=root,
                    market="a_share",
                    code="000002.SZ",
                    as_of="2026-08-22",
                )


class _FakeLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, role: str, prompt: str, model: str, max_output_tokens: int):
        self.calls.append(role)
        if role == "bear":
            return ResearchLLMResponse(content="not-json", model=model)
        return ResearchLLMResponse(
            content=json.dumps({
                "summary": f"{role} summary",
                "stance": "observe",
                "risks": ["data limitation"],
                "confidence": 42,
            }),
            model=model,
            prompt_tokens=11,
            completion_tokens=7,
        )


class _ThrowingLLM(_FakeLLM):
    def complete(self, *, role: str, prompt: str, model: str, max_output_tokens: int):
        if role == "news":
            raise RuntimeError("transport unavailable")
        return super().complete(
            role=role,
            prompt=prompt,
            model=model,
            max_output_tokens=max_output_tokens,
        )


class MultiAgentWorkflowTests(unittest.TestCase):
    def test_writes_auditable_artifacts_and_degrades_invalid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_multi_agent_research(
                repo_root=root,
                evidence={
                    "market": "a_share",
                    "as_of": "20260820",
                    "instrument": {"code": "000001.SZ", "name": "平安银行"},
                    "facts": {"technical": {"momentum_20": 0.08}},
                    "sources": [{"kind": "feature_snapshot"}],
                    "research_only": True,
                },
                llm_client=_FakeLLM(),
                model="test-model",
                run_id="20260820-000001SZ-test",
            )
            output = Path(result["output_dir"])
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            payload = json.loads((output / "result.json").read_text(encoding="utf-8"))

            self.assertEqual(result["status"], "completed_with_degradation")
            self.assertEqual(manifest["run_id"], "20260820-000001SZ-test")
            self.assertEqual(payload["roles"]["bear"]["status"], "degraded")
            self.assertTrue((output / "audit/05_bear.json").exists())
            self.assertTrue((output / "digest.md").exists())
            self.assertTrue((output / "full_report.md").exists())
            self.assertEqual(manifest["execution_effect"], "none_research_only")

    def test_degrades_a_role_when_the_model_transport_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            result = run_multi_agent_research(
                repo_root=Path(tmp),
                evidence={
                    "market": "a_share",
                    "as_of": "20260820",
                    "instrument": {"code": "000001.SZ", "name": "平安银行"},
                },
                llm_client=_ThrowingLLM(),
                model="test-model",
                run_id="20260820-000001SZ-transport-error",
            )
            payload = json.loads(Path(result["result_path"]).read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "completed_with_degradation")
        self.assertEqual(payload["roles"]["news"]["status"], "degraded")
        self.assertIn("transport unavailable", payload["roles"]["news"]["error"])
