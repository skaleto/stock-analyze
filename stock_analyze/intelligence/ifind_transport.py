"""Secret-safe subprocess transport for the isolated iFinD SDK runtime."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ALLOWED_OPERATIONS = {"statistics", "hq", "report_query", "basic_data"}
ORIGINAL_DAILY_PARAMETERS = (
    "Interval:D,CPS:1,Currency:YSHB,fill:Omit"
)
DEFAULT_HQ_INDICATORS = "open,high,low,close,volume,amount"


class IfindSdkError(RuntimeError):
    pass


class IfindSdkTransport:
    def __init__(
        self,
        *,
        repo_root: str | Path,
        python_path: str | Path | None = None,
        gateway_path: str | Path | None = None,
        sdk_lib_dirs: Sequence[str | Path] | None = None,
        username_file: str | Path | None = None,
        password_file: str | Path | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        root = Path(repo_root)
        self.python_path = Path(
            python_path
            or os.environ.get(
                "IFIND_PYTHON",
                "/opt/stock-analyze/vendor/ifind/venv/bin/python",
            )
        )
        self.gateway_path = Path(
            gateway_path
            or os.environ.get(
                "IFIND_GATEWAY",
                root / "scripts" / "ifind_sdk_gateway.py",
            )
        )
        if sdk_lib_dirs is None:
            raw_dirs = os.environ.get("IFIND_SDK_LIB_DIRS", "")
            sdk_lib_dirs = tuple(
                value for value in raw_dirs.split(":") if value
            ) or (
                "/opt/stock-analyze/vendor/ifind/1.10.22.44.001/bin64",
                (
                    "/opt/stock-analyze/vendor/ifind/"
                    "1.10.22.44.001/compat/root/lib/x86_64-linux-gnu"
                ),
            )
        self.sdk_lib_dirs = tuple(Path(value) for value in sdk_lib_dirs)
        self.username_file = Path(
            username_file
            or os.environ.get(
                "IFIND_USER_FILE",
                "/etc/stock-analyze/secrets/ifind_username",
            )
        )
        self.password_file = Path(
            password_file
            or os.environ.get(
                "IFIND_PASSWORD_FILE",
                "/etc/stock-analyze/secrets/ifind_password",
            )
        )
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    @staticmethod
    def history_action(
        *,
        action_id: str,
        codes: Iterable[str],
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        return {
            "id": str(action_id),
            "op": "hq",
            "codes": [str(code).upper() for code in codes],
            "indicators": DEFAULT_HQ_INDICATORS,
            "parameters": ORIGINAL_DAILY_PARAMETERS,
            "start_date": str(start_date),
            "end_date": str(end_date),
        }

    @staticmethod
    def announcement_action(
        *,
        action_id: str,
        start_date: str,
        end_date: str,
        codes: Iterable[str] = (),
        full_market: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": str(action_id),
            "op": "report_query",
            "codes": [str(code).upper() for code in codes],
            "start_date": str(start_date),
            "end_date": str(end_date),
            "full_market": bool(full_market),
        }

    def available(self) -> tuple[bool, tuple[str, ...]]:
        required = (
            self.python_path,
            self.gateway_path,
            self.username_file,
            self.password_file,
            *self.sdk_lib_dirs,
        )
        missing = tuple(str(path) for path in required if not path.exists())
        return not missing, missing

    def execute(
        self,
        actions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        normalized = [dict(action) for action in actions]
        if not normalized:
            raise ValueError("ifind_actions_empty")
        if any(
            str(action.get("op") or "") not in ALLOWED_OPERATIONS
            for action in normalized
        ):
            raise ValueError("ifind_operation_not_allowed")
        available, missing = self.available()
        if not available:
            raise IfindSdkError(
                "ifind_runtime_missing:" + ",".join(missing)
            )
        env = dict(os.environ)
        inherited = env.get("LD_LIBRARY_PATH", "")
        private_dirs = ":".join(str(path) for path in self.sdk_lib_dirs)
        env["LD_LIBRARY_PATH"] = (
            f"{private_dirs}:{inherited}" if inherited else private_dirs
        )
        env["IFIND_USER_FILE"] = str(self.username_file)
        env["IFIND_PASSWORD_FILE"] = str(self.password_file)
        try:
            completed = subprocess.run(
                [str(self.python_path), str(self.gateway_path)],
                input=json.dumps(
                    {"actions": normalized},
                    ensure_ascii=False,
                ),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise IfindSdkError("ifind_gateway_timeout") from exc
        if completed.returncode != 0:
            raise IfindSdkError(
                f"ifind_gateway_failed:{completed.returncode}"
            )
        output_lines = [
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        try:
            payload = json.loads(output_lines[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise IfindSdkError("ifind_gateway_invalid_json") from exc
        if not isinstance(payload, dict):
            raise IfindSdkError("ifind_gateway_invalid_payload")
        if payload.get("status") != "success":
            raise IfindSdkError("ifind_gateway_reported_failure")
        return payload


def result_by_id(
    payload: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    results = payload.get("results") or []
    if not isinstance(results, list):
        raise IfindSdkError("ifind_gateway_results_invalid")
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in results:
        if not isinstance(item, Mapping):
            raise IfindSdkError("ifind_gateway_result_invalid")
        action_id = str(item.get("id") or "")
        if not action_id or action_id in indexed:
            raise IfindSdkError("ifind_gateway_result_id_invalid")
        indexed[action_id] = item
    return indexed


__all__ = [
    "DEFAULT_HQ_INDICATORS",
    "IfindSdkError",
    "IfindSdkTransport",
    "ORIGINAL_DAILY_PARAMETERS",
    "result_by_id",
]
