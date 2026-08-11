"""Configuration-driven registry for official and licensed intelligence sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .http import IntelligenceHttpClient
from .sources.base import UnavailableSourceAdapter
from .sources.official import NdrcApiAdapter, OfficialHtmlAdapter, TushareAnnouncementAdapter
from .tushare_transport import TushareProTransport


def load_source_config(path: str | Path) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("intelligence_source_schema_unsupported")
    return payload


def build_adapters(repo_root: str | Path, config_path: str | Path):
    root = Path(repo_root)
    config = load_source_config(config_path)
    adapters = []
    for source, spec in (config.get("sources") or {}).items():
        if not spec.get("enabled", False):
            continue
        if spec.get("type") == "official_html":
            hosts = set(spec.get("allowed_hosts") or [])
            client = IntelligenceHttpClient(
                allowed_hosts=hosts,
                cache_dir=root / "data" / "shared" / "intelligence" / "http_cache" / source,
                min_interval_seconds=float(spec.get("min_interval_seconds") or 1.0),
            )
            adapters.append(
                OfficialHtmlAdapter(
                    source, tuple(spec.get("listing_urls") or ()), client,
                    include_path=str(spec.get("include_path") or r"."),
                )
            )
        elif spec.get("type") == "tushare_announcement":
            token = os.environ.get("TUSHARE_TOKEN", "")
            if token and bool(spec.get("entitled")):
                adapters.append(
                    TushareAnnouncementAdapter(
                        TushareProTransport(
                            token,
                            endpoint=str(
                                spec.get("endpoint")
                                or "https://api.tushare.pro"
                            ),
                        ),
                        enabled=True,
                        initial_lookback_days=int(spec.get("initial_lookback_days") or 7),
                        page_size=int(spec.get("page_size") or 2000),
                        max_pages_per_day=int(spec.get("max_pages_per_day") or 20),
                    )
                )
            else:
                reason = "entitlement_disabled" if not spec.get("entitled") else "token_missing"
                adapters.append(UnavailableSourceAdapter(str(source), reason))
        elif spec.get("type") == "ndrc_api":
            if spec.get("credential_class") != "public_client_identifier":
                raise ValueError("ndrc_public_site_key_credential_class_invalid")
            public_site_key = str(spec.get("public_site_key") or "").strip()
            if not public_site_key:
                raise ValueError("ndrc_public_site_key_missing")
            hosts = set(spec.get("allowed_hosts") or [])
            client = IntelligenceHttpClient(
                allowed_hosts=hosts,
                cache_dir=root / "data" / "shared" / "intelligence" / "http_cache" / source,
                min_interval_seconds=float(spec.get("min_interval_seconds") or 0.5),
            )
            adapters.append(NdrcApiAdapter(
                client,
                endpoint=str(spec["endpoint"]),
                site_code=str(spec["site_code"]),
                api_key=public_site_key,
                page_size=int(spec.get("page_size") or 50),
                max_pages=int(spec.get("max_pages") or 100),
            ))
        elif spec.get("type") == "contract_only":
            adapters.append(
                UnavailableSourceAdapter(
                    str(source),
                    str(spec.get("unavailable_reason") or "adapter_not_implemented"),
                )
            )
        else:
            adapters.append(
                UnavailableSourceAdapter(
                    str(source),
                    f"adapter_type_unsupported:{spec.get('type') or 'missing'}",
                )
            )
    return tuple(adapters)
