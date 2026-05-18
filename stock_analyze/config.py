from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a config file.

    The default config is JSON syntax stored in a .yaml file so the project has
    no YAML parser dependency. If users later write real YAML, PyYAML is used
    when installed.
    """

    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                f"{config_path} is not JSON-compatible YAML. Install PyYAML or keep JSON syntax."
            ) from exc
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"{config_path} must contain a mapping at the top level")
        return data


def account_ids(config: dict[str, Any]) -> list[str]:
    return [str(account["id"]) for account in config.get("accounts", [])]

