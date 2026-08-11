"""Deterministic security, ETF, and industry entity resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class EntityCandidate:
    entity_type: str
    entity_id: str
    entity_name: str
    industry: str
    confidence: float


class EntityResolver:
    def __init__(self, aliases: dict[str, tuple[EntityCandidate, ...]]) -> None:
        self.aliases = aliases

    def resolve(self, title: str, text: str, metadata: dict) -> tuple[dict, ...]:
        explicit_codes: set[str] = set()
        for key in ("ts_code", "code"):
            value = str(metadata.get(key) or "").strip()
            if value:
                explicit_codes.add(value)
        raw_codes = metadata.get("security_codes")
        if isinstance(raw_codes, (list, tuple)):
            explicit_codes.update(
                str(value or "").strip()
                for value in raw_codes
                if str(value or "").strip()
            )
        raw_links = metadata.get("security_links")
        if isinstance(raw_links, (list, tuple)):
            explicit_codes.update(
                str(
                    link.get("ts_code")
                    or link.get("code")
                    or ""
                ).strip()
                for link in raw_links
                if isinstance(link, dict)
                and str(
                    link.get("ts_code")
                    or link.get("code")
                    or ""
                ).strip()
            )
        matches: dict[tuple[str, str], EntityCandidate] = {}
        for explicit in sorted(explicit_codes):
            normalized = explicit.split(".")[0]
            for candidate in self.aliases.get(normalized, ()):
                matches[(candidate.entity_type, candidate.entity_id)] = candidate
        if isinstance(raw_links, (list, tuple)):
            for link in raw_links:
                if not isinstance(link, dict):
                    continue
                raw_code = str(
                    link.get("ts_code")
                    or link.get("code")
                    or ""
                ).strip()
                code = re.sub(r"\D", "", raw_code.split(".")[0])
                if not code:
                    continue
                name = " ".join(
                    str(link.get("name") or "").split()
                )
                if any(
                    key[1] == code
                    for key in matches
                ):
                    continue
                entity_type = (
                    "etf"
                    if re.search(r"(?:ETF|LOF|基金)", name, re.I)
                    else "security"
                )
                candidate = EntityCandidate(
                    entity_type,
                    code,
                    name,
                    "",
                    0.98,
                )
                matches[(entity_type, code)] = candidate
        haystack = f"{title}\n{text[:20000]}"
        for alias, candidates in self.aliases.items():
            if len(alias) < 2 or alias not in haystack:
                continue
            for candidate in candidates:
                key = (candidate.entity_type, candidate.entity_id)
                current = matches.get(key)
                if current is None or candidate.confidence > current.confidence:
                    matches[key] = candidate
        return tuple(
            matches[key].__dict__
            for key in sorted(matches)
        )


def load_entity_resolver(repo_root: str | Path) -> EntityResolver:
    root = Path(repo_root)
    records: list[dict] = []
    cache_roots = (
        root / "data" / "shared" / "cache",
        root / "data" / "a_share" / "shared" / "cache",
    )
    for cache_root in cache_roots:
        for path in sorted(cache_root.glob("spot_*.csv"))[-2:]:
            try:
                frame = pd.read_csv(path, dtype={"code": str, "ts_code": str})
            except (OSError, pd.errors.ParserError):
                continue
            for row in frame.to_dict(orient="records"):
                records.append({
                    "code": str(row.get("code") or row.get("ts_code") or "").split(".")[0],
                    "name": str(row.get("name") or row.get("名称") or ""),
                    "industry": str(row.get("industry") or ""),
                    "entity_type": "security",
                })
    universe_path = root / "data" / "cn_qdii_etf" / "shared" / "universe_latest.json"
    if universe_path.exists():
        try:
            payload = json.loads(universe_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows = payload.get("funds") or payload.get("universe") or payload if isinstance(payload, list) else []
        for row in rows if isinstance(rows, list) else []:
            records.append({
                "code": str(row.get("code") or row.get("ts_code") or "").split(".")[0],
                "name": str(row.get("name") or row.get("fund_name") or ""),
                "industry": str(row.get("theme") or row.get("index_key") or "cross_border_etf"),
                "entity_type": "etf",
            })
    aliases: dict[str, list[EntityCandidate]] = {}
    for record in records:
        code = re.sub(r"\D", "", record["code"])
        name = record["name"].strip()
        if not code:
            continue
        candidate = EntityCandidate(
            record["entity_type"], code, name, record["industry"], 1.0,
        )
        aliases.setdefault(code, []).append(candidate)
        if name:
            aliases.setdefault(name, []).append(candidate)
            short_name = re.sub(r"(?:股份|集团|有限|公司|ETF|LOF|基金)$", "", name, flags=re.I)
            if len(short_name) >= 2:
                aliases.setdefault(short_name, []).append(
                    EntityCandidate(candidate.entity_type, code, name, candidate.industry, 0.9)
                )
    return EntityResolver({key: tuple(value) for key, value in aliases.items()})
