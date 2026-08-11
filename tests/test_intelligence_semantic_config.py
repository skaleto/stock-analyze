from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_CONFIG = ROOT / "configs" / "intelligence_semantic.yaml"
EXECUTOR_CONFIG = (
    ROOT / "deploy" / "intelligence-semantic-executor.deepseek.yaml"
)
EXTRACTION_PROFILE = (
    ROOT
    / "configs"
    / "intelligence_extraction_profiles"
    / "a_share_announcement_v1.json"
)
TAXONOMY_CONFIG = ROOT / "configs" / "intelligence_event_taxonomy_v1.json"

EVENT_TYPES = {
    "earnings_forecast",
    "earnings_flash",
    "buyback",
    "shareholder_change",
    "dividend",
    "major_contract",
    "merger_restructuring",
    "equity_financing",
    "guarantee",
    "pledge_freeze",
    "litigation_arbitration",
    "investigation_penalty",
    "risk_warning_delisting",
    "capacity_project",
    "control_change",
}

LIFECYCLES = {
    "planned",
    "approved",
    "in_progress",
    "completed",
    "cancelled",
    "revised",
    "uncertain",
}

ENTITY_SUBJECT_FIELDS = {
    "issuer",
    "holder",
    "counterparty",
    "target",
    "beneficiary",
    "authority",
    "old_controller",
    "new_controller",
    "investor",
    "pledgee",
    "subject_person",
    "court",
}

ALLOWED_DATE_KEYS = {
    "approval_date",
    "board_approval_date",
    "change_date",
    "contract_date",
    "decision_date",
    "effective_date",
    "guarantee_date",
    "record_date",
    "start_date",
}

ALLOWED_METADATA_KEYS = {
    "announcement_id",
    "source_id",
    "ts_code",
}

INHERITANCE_FALLBACK = {
    "never": "not_applicable",
    "if_matched": "validate_default",
    "required": "reject",
}

FACT_RULE_KEYS = {
    "all_of",
    "one_of_sets",
    "required_dates",
    "inherit_prior",
    "unmatched_fallback",
}


class IntelligenceSemanticConfigTest(unittest.TestCase):
    def _semantic_config(self) -> dict:
        self.assertTrue(
            SEMANTIC_CONFIG.exists(),
            f"missing semantic config: {SEMANTIC_CONFIG}",
        )
        return yaml.safe_load(SEMANTIC_CONFIG.read_text(encoding="utf-8"))

    def _taxonomy(self) -> dict:
        self.assertTrue(
            TAXONOMY_CONFIG.exists(),
            f"missing taxonomy config: {TAXONOMY_CONFIG}",
        )
        return json.loads(TAXONOMY_CONFIG.read_text(encoding="utf-8"))

    def _intelligence_configs(self) -> list[tuple[Path, dict]]:
        configs: list[tuple[Path, dict]] = []
        for path in sorted((ROOT / "configs").glob("intelligence*")):
            if path.suffix == ".json":
                payload = json.loads(path.read_text(encoding="utf-8"))
            elif path.suffix in {".yaml", ".yml"}:
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            else:
                continue
            configs.append((path, payload))
        return configs

    @staticmethod
    def _mappings(value, location: str = "root"):
        if isinstance(value, dict):
            yield location, value
            for key, child in value.items():
                yield from IntelligenceSemanticConfigTest._mappings(
                    child,
                    f"{location}.{key}",
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from IntelligenceSemanticConfigTest._mappings(
                    child,
                    f"{location}[{index}]",
                )

    @staticmethod
    def _declared_facts(event: dict) -> set[str]:
        facts = set(event["optional_facts"])
        required = event["required_facts"]
        if "default" in required:
            default = required["default"]
            overrides = required["by_lifecycle"]
        else:
            default = required
            overrides = {}
        for rule in [default, *overrides.values()]:
            facts.update(rule["all_of"])
            for fact_set in rule["one_of_sets"]:
                facts.update(fact_set)
        return facts

    def test_semantic_dependencies_are_bounded(self) -> None:
        requirements = {
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertTrue(
            {
                "httpx>=0.27.0,<1.0.0",
                "jsonschema>=4.23.0,<5.0.0",
                "oss2>=2.19.0,<3.0.0",
                "pdfplumber>=0.11.0,<1.0.0",
                "pymupdf>=1.24.0,<2.0.0",
                "pytesseract>=0.3.13,<1.0.0",
            }.issubset(requirements)
        )

    def test_tushare_source_declares_complete_history_contract(self) -> None:
        config = yaml.safe_load(
            (ROOT / "configs" / "intelligence_sources.yaml").read_text(encoding="utf-8")
        )
        source = config["sources"]["tushare_announcement"]
        self.assertIn("fields", source)
        self.assertEqual(
            source["fields"],
            ["ann_date", "ts_code", "name", "title", "url", "rec_time"],
        )
        self.assertEqual(source["full_history_start"], "1990-12-19")
        self.assertEqual(source["history_partition"], "month")
        self.assertEqual(source["history_min_partition"], "day")
        self.assertEqual(source["reconcile_lookback_days"], 2)
        self.assertEqual(source["exclude_security_prefixes"], ["200", "900"])
        self.assertEqual(source["endpoint"], "https://api.tushare.pro")
        self.assertEqual(source["history_completeness"], "adaptive_date_split")
        self.assertEqual(
            source["single_day_fallback"],
            "fixed_authoritative_security_universe",
        )
        self.assertEqual(
            source["stock_basic_list_statuses"],
            ["L", "D", "P", "G"],
        )
        self.assertEqual(source["stock_basic_page_size"], 6000)
        self.assertEqual(source["fund_basic_market"], "E")
        self.assertEqual(source["fund_basic_statuses"], ["L", "D", "I"])
        self.assertTrue(source["fund_basic_include_unfiltered"])
        self.assertEqual(source["fund_basic_page_size"], 15000)
        self.assertEqual(source["trade_calendar_mode"], "full_natural_days")
        self.assertEqual(source["trade_calendar_boundary_buffer_days"], 45)
        self.assertEqual(source["trade_calendar_max_next_open_gap_days"], 45)
        self.assertEqual(
            source["trade_calendar_cache"],
            "reference/tushare_sse_trade_calendar.csv",
        )
        self.assertEqual(source["backfill_lease_seconds"], 300)

    def test_production_artifacts_use_the_hangzhou_internal_access_point(
        self,
    ) -> None:
        config = self._semantic_config()
        store = config["artifact_store"]
        self.assertEqual(store["production_kind"], "oss")
        self.assertEqual(
            store["endpoint"],
            "https://oss-cn-hangzhou-internal.aliyuncs.com",
        )
        self.assertEqual(store["bucket"], "stock-analyze-hz")
        self.assertEqual(
            store["access_point_alias"],
            (
                "stock-analyze-hz-"
                "9c905c5c1755f4d81c452a8e6347839f1a-ossalias"
            ),
        )
        self.assertEqual(store["key_prefix"], "announcements")
        self.assertEqual(store["download_workers"], 4)
        self.assertEqual(store["download_max_attempts"], 2)
        self.assertEqual(
            store["download_connect_timeout_seconds"],
            5,
        )
        self.assertEqual(
            store["download_read_timeout_seconds"],
            15,
        )
        self.assertEqual(
            store["download_total_timeout_seconds"],
            30,
        )
        self.assertEqual(
            store["minimum_ecs_free_bytes"],
            5 * 1024 * 1024 * 1024,
        )
        self.assertEqual(config["parser"]["workers"], 1)
        self.assertEqual(
            store["credential_env"],
            {
                "access_key_id_file": "INTELLIGENCE_OSS_ACCESS_KEY_ID_FILE",
                "access_key_secret_file": "INTELLIGENCE_OSS_ACCESS_KEY_SECRET_FILE",
            },
        )
        self.assertTrue(
            all(name.endswith("_FILE") for name in store["credential_env"].values())
        )
        self.assertEqual(
            set(store["allowed_hosts"]),
            {
                "static.cninfo.com.cn",
                "www.cninfo.com.cn",
                "dataclouds.cninfo.com.cn",
            },
        )

    def test_production_semantic_settings_use_one_provider_neutral_contract(
        self,
    ) -> None:
        config = self._semantic_config()
        self.assertNotIn("semantic", config)
        self.assertNotIn("benchmark", config)
        self.assertEqual(
            config["production_extraction_profile"],
            "a-share-announcement-mentions-v1",
        )
        serialized = SEMANTIC_CONFIG.read_text(encoding="utf-8").casefold()
        for retired in ("candidate-a", "candidate-b", "champion", "gold"):
            self.assertNotIn(retired, serialized)

        executor = yaml.safe_load(
            EXECUTOR_CONFIG.read_text(encoding="utf-8")
        )["executor"]
        self.assertEqual(
            executor["base_url"],
            "https://api.deepseek.com",
        )
        self.assertEqual(
            executor["api_key_file_env"],
            "INTELLIGENCE_LLM_API_KEY_FILE",
        )
        self.assertEqual(executor["response_format"], "json_object")
        self.assertFalse(executor["server_side_json_schema"])
        self.assertTrue(executor["local_schema_validation"])

        profile = json.loads(EXTRACTION_PROFILE.read_text(encoding="utf-8"))
        self.assertEqual(profile["profile_id"], "a-share-announcement-v1")
        self.assertEqual(profile["prompt_version"], "semantic-extract-v5")
        self.assertEqual(
            profile["schema_version"],
            "announcement-events-v1-lite",
        )
        self.assertEqual(
            profile["taxonomy_version"],
            "cn-announcement-taxonomy-v1",
        )
        self.assertEqual(
            profile["evidence_contract"],
            "unique-verbatim-quote-v1",
        )
        self.assertEqual(profile["decision_use"], "research_feature_only")

    def test_production_config_has_no_retired_multi_model_benchmark(self) -> None:
        config = self._semantic_config()
        self.assertEqual(config["schema_version"], 1)
        self.assertEqual(
            config["availability"]["historical_cutoff"],
            "2026-07-17T23:59:59+08:00",
        )
        self.assertEqual(
            config["availability"]["missing_rec_time_policy"],
            "next_trading_day_open",
        )
        self.assertEqual(config["parser"]["version"], "announcement-layout-v1")
        self.assertEqual(config["parser"]["ocr_languages"], "chi_sim+eng")
        self.assertFalse(
            (ROOT / "configs" / "research" / "intelligence_semantic_benchmark.yaml").exists()
        )
        self.assertNotIn("candidate_profiles", config)
        self.assertNotIn("benchmark", config)

    def test_provider_neutral_exchange_does_not_resolve_candidate_profiles(
        self,
    ) -> None:
        source = (
            ROOT
            / "stock_analyze"
            / "intelligence"
            / "semantic"
            / "exchange.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("candidate_profiles", source)
        self.assertNotIn(
            'root / "configs" / "intelligence_semantic.yaml"',
            source,
        )

    def test_intelligence_configs_only_reference_account_secrets_by_file_env(self) -> None:
        direct_secret_field = re.compile(
            r"^(api_key|access_key_id|access_key_secret|access_token|"
            r"password|secret|token)$"
        )
        public_identifiers = 0
        for path, config in self._intelligence_configs():
            for location, mapping in self._mappings(config):
                if "public_site_key" in mapping:
                    public_identifiers += 1
                    self.assertEqual(
                        mapping.get("credential_class"),
                        "public_client_identifier",
                        f"{path}:{location}",
                    )
                    self.assertTrue(str(mapping["public_site_key"]).strip())

                if "credential_env" in mapping:
                    references = mapping["credential_env"]
                    self.assertIsInstance(references, dict, f"{path}:{location}")
                    for value in references.values():
                        self.assertRegex(
                            value,
                            r"^[A-Z][A-Z0-9_]*_FILE$",
                            f"{path}:{location}",
                        )

                for key, value in mapping.items():
                    self.assertIsNone(
                        direct_secret_field.fullmatch(str(key)),
                        f"literal account secret field forbidden: {path}:{location}.{key}",
                    )
                    if str(key).endswith("_file_env"):
                        self.assertRegex(
                            value,
                            r"^[A-Z][A-Z0-9_]*_FILE$",
                            f"{path}:{location}.{key}",
                        )

        self.assertEqual(public_identifiers, 1)

    def test_ndrc_public_search_identifier_is_explicitly_classified(self) -> None:
        config = yaml.safe_load(
            (ROOT / "configs" / "intelligence_sources.yaml").read_text(encoding="utf-8")
        )
        ndrc = config["sources"]["ndrc_policy"]
        self.assertNotIn("api_key", ndrc)
        self.assertEqual(ndrc["credential_class"], "public_client_identifier")
        self.assertTrue(ndrc["public_site_key"])

    def test_taxonomy_freezes_exactly_fifteen_event_contracts(self) -> None:
        taxonomy = self._taxonomy()
        self.assertEqual(taxonomy["schema_version"], 1)
        self.assertEqual(taxonomy["taxonomy_version"], "cn-announcement-taxonomy-v1")
        events = {row["event_type"]: row for row in taxonomy["events"]}
        self.assertEqual(set(events), EVENT_TYPES)
        self.assertEqual(len(taxonomy["events"]), len(events))

        required_keys = {
            "event_type",
            "allowed_lifecycle",
            "required_subject_roles",
            "required_facts",
            "optional_facts",
            "direction_rule",
            "dedupe_fields",
            "default_horizon_days",
        }
        for event_type, event in events.items():
            with self.subTest(event_type=event_type):
                self.assertEqual(set(event), required_keys)
                self.assertTrue(event["allowed_lifecycle"])
                self.assertTrue(set(event["allowed_lifecycle"]).issubset(LIFECYCLES))
                self.assertTrue(event["required_subject_roles"])
                self.assertIsInstance(event["optional_facts"], list)
                self.assertRegex(event["direction_rule"], re.compile(r"^[a-z0-9_]+$"))
                self.assertTrue(event["dedupe_fields"])
                self.assertGreater(event["default_horizon_days"], 0)

    def test_required_fact_rules_have_explicit_lifecycle_semantics(self) -> None:
        for event in self._taxonomy()["events"]:
            event_type = event["event_type"]
            required = event["required_facts"]
            with self.subTest(event_type=event_type):
                self.assertEqual(set(required), {"default", "by_lifecycle"})
                self.assertEqual(required["default"]["inherit_prior"], "never")
                self.assertEqual(
                    required["default"]["unmatched_fallback"],
                    "not_applicable",
                )
                overrides = required["by_lifecycle"]
                self.assertTrue(set(overrides).issubset(set(event["allowed_lifecycle"])))

                for lifecycle in {
                    "in_progress",
                    "cancelled",
                    "revised",
                    "completed",
                } & set(
                    event["allowed_lifecycle"]
                ):
                    self.assertIn(lifecycle, overrides)

                for lifecycle, rule in {
                    "default": required["default"],
                    **overrides,
                }.items():
                    self.assertEqual(set(rule), FACT_RULE_KEYS)
                    self.assertIn(rule["inherit_prior"], INHERITANCE_FALLBACK)
                    self.assertEqual(
                        rule["unmatched_fallback"],
                        INHERITANCE_FALLBACK[rule["inherit_prior"]],
                    )
                    self.assertIsInstance(rule["all_of"], list)
                    self.assertIsInstance(rule["one_of_sets"], list)
                    self.assertIsInstance(rule["required_dates"], list)
                    self.assertTrue(
                        set(rule["required_dates"]).issubset(ALLOWED_DATE_KEYS)
                    )
                    self.assertTrue(
                        all(isinstance(group, list) and group for group in rule["one_of_sets"])
                    )
                    if lifecycle == "in_progress":
                        self.assertEqual(rule["inherit_prior"], "if_matched")
                        self.assertEqual(
                            rule["unmatched_fallback"],
                            "validate_default",
                        )
                    if lifecycle in {"cancelled", "revised"}:
                        self.assertEqual(rule["inherit_prior"], "required")
                        self.assertEqual(rule["unmatched_fallback"], "reject")
                        self.assertNotEqual(rule, required["default"])

    def test_entity_subjects_never_appear_as_facts(self) -> None:
        for event in self._taxonomy()["events"]:
            declared_facts = self._declared_facts(event)
            with self.subTest(event_type=event["event_type"]):
                self.assertFalse(declared_facts & ENTITY_SUBJECT_FIELDS)
                self.assertTrue(
                    set(event["required_subject_roles"]).issubset(
                        ENTITY_SUBJECT_FIELDS
                    )
                )

    def test_dedupe_fields_are_typed_and_resolve_to_declared_sources(self) -> None:
        source_pattern = re.compile(
            r"^(subject|fact|date|metadata):([a-z][a-z0-9_]*)$"
        )
        for event in self._taxonomy()["events"]:
            subjects = set(event["required_subject_roles"])
            facts = self._declared_facts(event)
            with self.subTest(event_type=event["event_type"]):
                for source in event["dedupe_fields"]:
                    match = source_pattern.fullmatch(source)
                    self.assertIsNotNone(match, source)
                    prefix, key = match.groups()
                    if prefix == "subject":
                        self.assertIn(key, subjects)
                    elif prefix == "fact":
                        self.assertIn(key, facts)
                    elif prefix == "date":
                        self.assertIn(key, ALLOWED_DATE_KEYS)
                    else:
                        self.assertIn(key, ALLOWED_METADATA_KEYS)

    def test_capacity_project_dedupe_uses_required_stable_fields(self) -> None:
        events = {
            event["event_type"]: event
            for event in self._taxonomy()["events"]
        }

        self.assertEqual(
            events["capacity_project"]["dedupe_fields"],
            ["subject:issuer", "fact:project_type"],
        )

    def test_body_identifiers_are_evidence_backed_facts(self) -> None:
        events = {
            event["event_type"]: event
            for event in self._taxonomy()["events"]
        }
        expected = {
            "litigation_arbitration": "case_number",
            "investigation_penalty": "document_number",
        }
        for event_type, fact_name in expected.items():
            event = events[event_type]
            with self.subTest(event_type=event_type):
                self.assertIn(fact_name, event["optional_facts"])
                self.assertIn(f"fact:{fact_name}", event["dedupe_fields"])
                self.assertNotIn(
                    f"metadata:{fact_name}",
                    event["dedupe_fields"],
                )

    def test_taxonomy_keeps_selected_materiality_baselines(self) -> None:
        events = {
            event["event_type"]: event
            for event in self._taxonomy()["events"]
        }
        forecast_required = events["earnings_forecast"]["required_facts"]
        self.assertIn("default", forecast_required)
        forecast = forecast_required["default"]
        self.assertTrue({"period", "yoy_lower", "yoy_upper"}.issubset(forecast["all_of"]))
        self.assertIn(
            ["net_profit_lower", "net_profit_upper"],
            forecast["one_of_sets"],
        )

        buyback = events["buyback"]
        self.assertEqual(buyback["default_horizon_days"], 60)
        self.assertEqual(
            buyback["required_facts"]["by_lifecycle"]["completed"]["inherit_prior"],
            "if_matched",
        )
        self.assertNotIn(
            "price_cap",
            buyback["required_facts"]["by_lifecycle"]["completed"]["all_of"],
        )

        dividend_completed = events["dividend"]["required_facts"]["by_lifecycle"][
            "completed"
        ]
        self.assertIn("required_dates", dividend_completed)
        self.assertEqual(dividend_completed["required_dates"], ["record_date"])
        self.assertEqual(dividend_completed["inherit_prior"], "if_matched")
        self.assertEqual(
            dividend_completed["unmatched_fallback"],
            "validate_default",
        )
        self.assertIn("date:record_date", events["dividend"]["dedupe_fields"])

        control_change = events["control_change"]
        self.assertEqual(
            control_change["required_facts"]["default"]["all_of"],
            ["change_method"],
        )
        self.assertEqual(events["capacity_project"]["default_horizon_days"], 120)


if __name__ == "__main__":
    unittest.main()
