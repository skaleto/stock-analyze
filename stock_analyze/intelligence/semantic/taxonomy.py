"""Immutable, fail-closed announcement event taxonomy loading."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


VALID_LIFECYCLES = frozenset(
    {
        "planned",
        "approved",
        "in_progress",
        "completed",
        "cancelled",
        "revised",
        "uncertain",
    }
)

VALID_SUBJECT_ROLES = frozenset(
    {
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
)

VALID_DATE_FIELDS = frozenset(
    {
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
)

VALID_METADATA_FIELDS = frozenset({"announcement_id", "source_id", "ts_code"})

_TOP_LEVEL_KEYS = frozenset({"schema_version", "taxonomy_version", "events"})
_EVENT_KEYS = frozenset(
    {
        "event_type",
        "allowed_lifecycle",
        "required_subject_roles",
        "required_facts",
        "optional_facts",
        "direction_rule",
        "dedupe_fields",
        "default_horizon_days",
    }
)
_EVENT_V2_KEYS = _EVENT_KEYS | {"fact_specs"}
_FACT_SPEC_KEYS = frozenset(
    {"value_type", "allowed_unit_kinds", "evidence_terms_any"}
)
_FACT_VALUE_TYPES = frozenset({"text", "number", "ratio", "period"})
_FACT_UNIT_KINDS = frozenset(
    {
        "area",
        "area_rate",
        "count",
        "count_rate",
        "currency",
        "duration",
        "energy",
        "mass",
        "mass_rate",
        "multiple",
        "power",
        "price",
        "ratio",
        "shares",
        "unitless",
    }
)
_REQUIRED_FACT_KEYS = frozenset({"default", "by_lifecycle"})
_FACT_RULE_KEYS = frozenset(
    {
        "all_of",
        "one_of_sets",
        "required_dates",
        "inherit_prior",
        "unmatched_fallback",
    }
)
_INHERITANCE_FALLBACK = {
    "never": "not_applicable",
    "if_matched": "validate_default",
    "required": "reject",
}
_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_DEDUPE_PATTERN = re.compile(
    r"^(subject|fact|date|metadata):([a-z][a-z0-9_]*)$"
)


class TaxonomyValidationError(ValueError):
    """A taxonomy rejection with a stable machine-readable reason code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass(frozen=True)
class FactRequirement:
    all_of: tuple[str, ...]
    one_of_sets: tuple[tuple[str, ...], ...]
    required_dates: tuple[str, ...]
    inherit_prior: str
    unmatched_fallback: str

    @property
    def facts(self) -> frozenset[str]:
        return frozenset(
            (*self.all_of, *(name for group in self.one_of_sets for name in group))
        )


@dataclass(frozen=True)
class FactSpec:
    value_type: str
    allowed_unit_kinds: tuple[str, ...]
    evidence_terms_any: tuple[str, ...]


@dataclass(frozen=True)
class TaxonomyEvent:
    event_type: str
    allowed_lifecycle: tuple[str, ...]
    required_subject_roles: tuple[str, ...]
    default_requirements: FactRequirement
    lifecycle_requirements: tuple[tuple[str, FactRequirement], ...]
    optional_facts: tuple[str, ...]
    direction_rule: str
    dedupe_fields: tuple[str, ...]
    default_horizon_days: int
    fact_specs: Mapping[str, FactSpec] = field(
        repr=False,
        compare=False,
        default_factory=lambda: MappingProxyType({}),
    )

    @property
    def declared_facts(self) -> frozenset[str]:
        return self.default_requirements.facts | frozenset(self.optional_facts)

    def requirements_for(self, lifecycle: str) -> FactRequirement:
        for lifecycle_name, requirements in self.lifecycle_requirements:
            if lifecycle_name == lifecycle:
                return requirements
        return self.default_requirements

    def fact_spec(self, name: str) -> FactSpec:
        try:
            return self.fact_specs[name]
        except KeyError as exc:
            raise KeyError(
                f"taxonomy_fact_spec_unknown:{self.event_type}:{name}"
            ) from exc


@dataclass(frozen=True)
class EventTaxonomy:
    schema_version: int
    taxonomy_version: str
    events: tuple[TaxonomyEvent, ...]
    _events_by_type: Mapping[str, TaxonomyEvent] = field(
        repr=False,
        compare=False,
    )

    @classmethod
    def load(cls, path: str | Path) -> "EventTaxonomy":
        taxonomy_path = Path(path)
        try:
            payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TaxonomyValidationError(
                "taxonomy_json_invalid",
                detail=type(exc).__name__,
            ) from exc
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: object) -> "EventTaxonomy":
        if not isinstance(payload, dict):
            _fail("taxonomy_schema_invalid", "root")
        if set(payload) != _TOP_LEVEL_KEYS:
            if set(payload) - _TOP_LEVEL_KEYS:
                _fail("taxonomy_extra_property", "root")
            _fail("taxonomy_schema_invalid", "root")
        if (
            type(payload["schema_version"]) is not int
            or payload["schema_version"] not in {1, 2}
        ):
            _fail("taxonomy_schema_version_unsupported", "schema_version")
        taxonomy_version = payload["taxonomy_version"]
        if (
            not isinstance(taxonomy_version, str)
            or _VERSION_PATTERN.fullmatch(taxonomy_version) is None
        ):
            _fail("taxonomy_version_invalid", "taxonomy_version")
        raw_events = payload["events"]
        if not isinstance(raw_events, list) or not raw_events:
            _fail("taxonomy_events_missing", "events")

        events: list[TaxonomyEvent] = []
        event_names: set[str] = set()
        for index, raw_event in enumerate(raw_events):
            event = _parse_event(
                raw_event,
                index=index,
                schema_version=payload["schema_version"],
            )
            if event.event_type in event_names:
                _fail("taxonomy_event_type_duplicate", event.event_type)
            event_names.add(event.event_type)
            events.append(event)

        event_tuple = tuple(events)
        return cls(
            schema_version=payload["schema_version"],
            taxonomy_version=taxonomy_version,
            events=event_tuple,
            _events_by_type=MappingProxyType(
                {event.event_type: event for event in event_tuple}
            ),
        )

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset(self._events_by_type)

    def event(self, event_type: str) -> TaxonomyEvent:
        try:
            return self._events_by_type[event_type]
        except KeyError as exc:
            raise KeyError(f"taxonomy_event_type_unknown:{event_type}") from exc


def _parse_event(
    raw_event: object,
    *,
    index: int,
    schema_version: int,
) -> TaxonomyEvent:
    location = f"events[{index}]"
    if not isinstance(raw_event, dict):
        _fail("taxonomy_schema_invalid", location)
    event_keys = _EVENT_V2_KEYS if schema_version == 2 else _EVENT_KEYS
    if set(raw_event) != event_keys:
        if set(raw_event) - event_keys:
            _fail("taxonomy_extra_property", location)
        _fail("taxonomy_schema_invalid", location)

    event_type = raw_event["event_type"]
    if not _is_identifier(event_type):
        _fail("taxonomy_event_type_invalid", location)

    allowed_lifecycle = _string_tuple(
        raw_event["allowed_lifecycle"],
        code="taxonomy_lifecycle_invalid",
        allow_empty=False,
    )
    if len(set(allowed_lifecycle)) != len(allowed_lifecycle):
        _fail("taxonomy_lifecycle_duplicate", event_type)
    if not set(allowed_lifecycle).issubset(VALID_LIFECYCLES):
        _fail("taxonomy_lifecycle_unknown", event_type)

    subject_roles = _string_tuple(
        raw_event["required_subject_roles"],
        code="taxonomy_subject_role_invalid",
        allow_empty=False,
    )
    if len(set(subject_roles)) != len(subject_roles):
        _fail("taxonomy_subject_role_duplicate", event_type)
    if not set(subject_roles).issubset(VALID_SUBJECT_ROLES):
        _fail("taxonomy_subject_role_unknown", event_type)

    optional_facts = _identifier_tuple(
        raw_event["optional_facts"],
        code="taxonomy_optional_fact_invalid",
        allow_empty=True,
    )
    if len(set(optional_facts)) != len(optional_facts):
        _fail("taxonomy_optional_fact_duplicate", event_type)

    raw_required = raw_event["required_facts"]
    if not isinstance(raw_required, dict) or set(raw_required) != _REQUIRED_FACT_KEYS:
        if isinstance(raw_required, dict) and set(raw_required) - _REQUIRED_FACT_KEYS:
            _fail("taxonomy_extra_property", f"{event_type}.required_facts")
        _fail("taxonomy_required_facts_invalid", event_type)

    default_requirements = _parse_requirement(
        raw_required["default"],
        location=f"{event_type}.required_facts.default",
    )
    if default_requirements.inherit_prior != "never":
        _fail("taxonomy_default_inheritance_invalid", event_type)
    default_facts = default_requirements.facts
    if default_facts & set(optional_facts):
        _fail("taxonomy_fact_overlap", event_type)
    declared_facts = default_facts | frozenset(optional_facts)

    raw_overrides = raw_required["by_lifecycle"]
    if not isinstance(raw_overrides, dict):
        _fail("taxonomy_lifecycle_override_invalid", event_type)
    if not set(raw_overrides).issubset(set(allowed_lifecycle)):
        _fail("taxonomy_lifecycle_override_unknown", event_type)

    lifecycle_requirements: list[tuple[str, FactRequirement]] = []
    override_required_facts: set[str] = set()
    for lifecycle, raw_requirement in raw_overrides.items():
        if not isinstance(lifecycle, str):
            _fail("taxonomy_lifecycle_override_invalid", event_type)
        requirement = _parse_requirement(
            raw_requirement,
            location=f"{event_type}.required_facts.by_lifecycle.{lifecycle}",
        )
        unknown_facts = requirement.facts - declared_facts
        if unknown_facts:
            _fail(
                "taxonomy_required_fact_unknown",
                f"{event_type}:{sorted(unknown_facts)[0]}",
            )
        override_required_facts.update(requirement.facts)
        lifecycle_requirements.append((lifecycle, requirement))
    if override_required_facts & set(optional_facts):
        _fail("taxonomy_fact_overlap", event_type)

    direction_rule = raw_event["direction_rule"]
    if not isinstance(direction_rule, str) or not direction_rule.strip():
        _fail("taxonomy_direction_rule_missing", event_type)
    if _IDENTIFIER_PATTERN.fullmatch(direction_rule) is None:
        _fail("taxonomy_direction_rule_invalid", event_type)

    dedupe_fields = _string_tuple(
        raw_event["dedupe_fields"],
        code="taxonomy_dedupe_field_invalid",
        allow_empty=True,
    )
    if not dedupe_fields:
        _fail("taxonomy_dedupe_fields_missing", event_type)
    if len(set(dedupe_fields)) != len(dedupe_fields):
        _fail("taxonomy_dedupe_field_duplicate", event_type)
    _validate_dedupe_fields(
        dedupe_fields,
        event_type=event_type,
        subject_roles=frozenset(subject_roles),
        declared_facts=declared_facts,
    )

    horizon = raw_event["default_horizon_days"]
    if type(horizon) is not int or horizon <= 0:
        _fail("taxonomy_horizon_invalid", event_type)

    fact_specs: dict[str, FactSpec] = {}
    if schema_version == 2:
        raw_specs = raw_event["fact_specs"]
        if not isinstance(raw_specs, dict):
            _fail("taxonomy_fact_specs_invalid", event_type)
        unknown_specs = set(raw_specs) - declared_facts
        if unknown_specs:
            _fail(
                "taxonomy_fact_spec_unknown",
                f"{event_type}:{sorted(unknown_specs)[0]}",
            )
        missing_specs = declared_facts - set(raw_specs)
        if missing_specs:
            _fail(
                "taxonomy_fact_spec_missing",
                f"{event_type}:{sorted(missing_specs)[0]}",
            )
        fact_specs = {
            name: _parse_fact_spec(
                raw_spec,
                location=f"{event_type}.fact_specs.{name}",
            )
            for name, raw_spec in raw_specs.items()
        }

    return TaxonomyEvent(
        event_type=event_type,
        allowed_lifecycle=allowed_lifecycle,
        required_subject_roles=subject_roles,
        default_requirements=default_requirements,
        lifecycle_requirements=tuple(lifecycle_requirements),
        optional_facts=optional_facts,
        direction_rule=direction_rule,
        dedupe_fields=dedupe_fields,
        default_horizon_days=horizon,
        fact_specs=MappingProxyType(fact_specs),
    )


def _parse_fact_spec(raw_spec: object, *, location: str) -> FactSpec:
    if not isinstance(raw_spec, dict) or set(raw_spec) != _FACT_SPEC_KEYS:
        if isinstance(raw_spec, dict) and set(raw_spec) - _FACT_SPEC_KEYS:
            _fail("taxonomy_extra_property", location)
        _fail("taxonomy_fact_spec_invalid", location)
    value_type = raw_spec["value_type"]
    if value_type not in _FACT_VALUE_TYPES:
        _fail("taxonomy_fact_value_type_invalid", location)
    allowed_units = _string_tuple(
        raw_spec["allowed_unit_kinds"],
        code="taxonomy_fact_unit_kind_invalid",
        allow_empty=True,
    )
    if len(set(allowed_units)) != len(allowed_units):
        _fail("taxonomy_fact_unit_kind_duplicate", location)
    if not set(allowed_units).issubset(_FACT_UNIT_KINDS):
        _fail("taxonomy_fact_unit_kind_unknown", location)
    if value_type in {"text", "period"} and allowed_units:
        _fail("taxonomy_fact_unit_kind_invalid", location)
    if value_type == "ratio" and allowed_units != ("ratio",):
        _fail("taxonomy_fact_unit_kind_invalid", location)
    evidence_terms = _string_tuple(
        raw_spec["evidence_terms_any"],
        code="taxonomy_fact_evidence_term_invalid",
        allow_empty=True,
    )
    if len(set(evidence_terms)) != len(evidence_terms):
        _fail("taxonomy_fact_evidence_term_duplicate", location)
    return FactSpec(
        value_type=value_type,
        allowed_unit_kinds=allowed_units,
        evidence_terms_any=evidence_terms,
    )


def _parse_requirement(raw_rule: object, *, location: str) -> FactRequirement:
    if not isinstance(raw_rule, dict) or set(raw_rule) != _FACT_RULE_KEYS:
        if isinstance(raw_rule, dict) and set(raw_rule) - _FACT_RULE_KEYS:
            _fail("taxonomy_extra_property", location)
        _fail("taxonomy_required_fact_rule_invalid", location)

    all_of = _identifier_tuple(
        raw_rule["all_of"],
        code="taxonomy_required_fact_invalid",
        allow_empty=True,
    )
    if len(set(all_of)) != len(all_of):
        _fail("taxonomy_required_fact_duplicate", location)

    raw_groups = raw_rule["one_of_sets"]
    if not isinstance(raw_groups, list):
        _fail("taxonomy_required_fact_invalid", location)
    groups: list[tuple[str, ...]] = []
    for group_index, raw_group in enumerate(raw_groups):
        group = _identifier_tuple(
            raw_group,
            code="taxonomy_required_fact_invalid",
            allow_empty=False,
        )
        if len(set(group)) != len(group):
            _fail(
                "taxonomy_required_fact_duplicate",
                f"{location}.one_of_sets[{group_index}]",
            )
        groups.append(group)
    if len(set(groups)) != len(groups):
        _fail("taxonomy_required_fact_duplicate", f"{location}.one_of_sets")
    flattened_group_facts = [name for group in groups for name in group]
    if len(set(flattened_group_facts)) != len(flattened_group_facts):
        _fail("taxonomy_required_fact_duplicate", f"{location}.one_of_sets")
    if set(all_of) & set(flattened_group_facts):
        _fail("taxonomy_required_fact_overlap", location)

    required_dates = _identifier_tuple(
        raw_rule["required_dates"],
        code="taxonomy_required_date_invalid",
        allow_empty=True,
    )
    if len(set(required_dates)) != len(required_dates):
        _fail("taxonomy_required_date_duplicate", location)
    if not set(required_dates).issubset(VALID_DATE_FIELDS):
        _fail("taxonomy_required_date_unknown", location)

    inherit_prior = raw_rule["inherit_prior"]
    if inherit_prior not in _INHERITANCE_FALLBACK:
        _fail("taxonomy_inherit_prior_invalid", location)
    unmatched_fallback = raw_rule["unmatched_fallback"]
    if unmatched_fallback != _INHERITANCE_FALLBACK[inherit_prior]:
        _fail("taxonomy_lifecycle_fallback_invalid", location)

    return FactRequirement(
        all_of=all_of,
        one_of_sets=tuple(groups),
        required_dates=required_dates,
        inherit_prior=inherit_prior,
        unmatched_fallback=unmatched_fallback,
    )


def _validate_dedupe_fields(
    fields: tuple[str, ...],
    *,
    event_type: str,
    subject_roles: frozenset[str],
    declared_facts: frozenset[str],
) -> None:
    for field_name in fields:
        match = _DEDUPE_PATTERN.fullmatch(field_name)
        if match is None:
            _fail("taxonomy_dedupe_field_invalid", f"{event_type}:{field_name}")
        source, name = match.groups()
        valid = (
            (source == "subject" and name in subject_roles)
            or (source == "fact" and name in declared_facts)
            or (source == "date" and name in VALID_DATE_FIELDS)
            or (source == "metadata" and name in VALID_METADATA_FIELDS)
        )
        if not valid:
            _fail("taxonomy_dedupe_field_unknown", f"{event_type}:{field_name}")


def _identifier_tuple(
    value: object,
    *,
    code: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    result = _string_tuple(value, code=code, allow_empty=allow_empty)
    if any(_IDENTIFIER_PATTERN.fullmatch(item) is None for item in result):
        _fail(code)
    return result


def _string_tuple(
    value: object,
    *,
    code: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not allow_empty and not value):
        _fail(code)
    if any(not isinstance(item, str) or not item for item in value):
        _fail(code)
    return tuple(value)


def _is_identifier(value: object) -> bool:
    return (
        isinstance(value, str)
        and _IDENTIFIER_PATTERN.fullmatch(value) is not None
    )


def _fail(code: str, detail: str = "") -> None:
    raise TaxonomyValidationError(code, detail=detail)


__all__ = [
    "EventTaxonomy",
    "FactSpec",
    "FactRequirement",
    "TaxonomyEvent",
    "TaxonomyValidationError",
    "VALID_DATE_FIELDS",
    "VALID_LIFECYCLES",
    "VALID_METADATA_FIELDS",
    "VALID_SUBJECT_ROLES",
]
