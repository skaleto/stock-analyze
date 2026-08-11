#!/usr/bin/env python3
"""Narrow JSON gateway around the native iFinD SDK.

The main application invokes this script with the isolated iFinD Python
runtime. Credentials are read only from root-owned files named by environment
variables; request JSON can never contain account credentials.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from iFinDPy import (
    THS_BD,
    THS_DataStatistics,
    THS_HQ,
    THS_ReportQuery,
    THS_iFinDLogin,
    THS_iFinDLogout,
)


ALLOWED_OPERATIONS = {"statistics", "hq", "report_query", "basic_data"}
ALLOWED_HQ_INDICATORS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
}
CODE_PATTERN = re.compile(r"^[0-9A-Z]{6,8}\.(?:SH|SZ|BJ)$")
INDICATOR_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,80}$")


def _secret(variable: str) -> str:
    path = Path(os.environ.get(variable, ""))
    if not path.is_file():
        raise RuntimeError(f"ifind_secret_file_missing:{variable}")
    return path.read_text(encoding="utf-8").strip()


def _json_value(value: Any) -> Any:
    value = getattr(value, "data", value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _result(action_id: str, response: Any) -> dict[str, Any]:
    data = _json_value(response)
    if isinstance(response, dict):
        errorcode = response.get("errorcode")
        errmsg = response.get("errmsg")
        data_vol = response.get("dataVol")
    else:
        errorcode = getattr(response, "errorcode", None)
        errmsg = getattr(response, "errmsg", "")
        data_vol = getattr(
            response,
            "dataVol",
            getattr(response, "datavol", None),
        )
    return {
        "id": action_id,
        "errorcode": errorcode,
        "errmsg": str(errmsg or "")[:300],
        "dataVol": data_vol,
        "data": data,
    }


def _codes(action: dict[str, Any], *, allow_empty: bool = False) -> str:
    values = action.get("codes") or []
    if not isinstance(values, list):
        raise ValueError("ifind_codes_invalid")
    normalized = [str(value).strip().upper() for value in values]
    if not normalized and not allow_empty:
        raise ValueError("ifind_codes_empty")
    if len(normalized) > 1200:
        raise ValueError("ifind_codes_too_many")
    if any(not CODE_PATTERN.fullmatch(value) for value in normalized):
        raise ValueError("ifind_code_invalid")
    return ",".join(normalized)


def _execute(action: dict[str, Any]) -> dict[str, Any]:
    action_id = str(action.get("id") or "").strip()
    operation = str(action.get("op") or "").strip()
    if not action_id:
        raise ValueError("ifind_action_id_missing")
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError("ifind_operation_not_allowed")
    if operation == "statistics":
        return _result(action_id, THS_DataStatistics(False))
    if operation == "hq":
        indicators = [
            value.strip()
            for value in str(action.get("indicators") or "").split(",")
            if value.strip()
        ]
        if (
            not indicators
            or any(value not in ALLOWED_HQ_INDICATORS for value in indicators)
        ):
            raise ValueError("ifind_hq_indicators_invalid")
        response = THS_HQ(
            _codes(action),
            ",".join(indicators),
            str(action.get("parameters") or ""),
            str(action.get("start_date") or ""),
            str(action.get("end_date") or ""),
            "format:json",
        )
        return _result(action_id, response)
    if operation == "report_query":
        full_market = bool(action.get("full_market"))
        parameters = (
            "mode:allAStock;"
            if full_market
            else ""
        ) + (
            f"beginrDate:{action.get('start_date')};"
            f"endrDate:{action.get('end_date')};"
            "reportType:901"
        )
        response = THS_ReportQuery(
            _codes(action, allow_empty=full_market),
            parameters,
            (
                "reportDate:Y,thscode:Y,secName:Y,ctime:Y,"
                "reportTitle:Y,pdfURL:Y,seq:Y"
            ),
            "format:json",
        )
        return _result(action_id, response)

    indicator = str(action.get("indicator") or "").strip()
    if not INDICATOR_PATTERN.fullmatch(indicator):
        raise ValueError("ifind_basic_indicator_invalid")
    response = THS_BD(
        _codes(action),
        indicator,
        str(action.get("parameters") or ""),
        "format:json",
    )
    return _result(action_id, response)


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        actions = request.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("ifind_actions_invalid")
        username = _secret("IFIND_USER_FILE")
        password = _secret("IFIND_PASSWORD_FILE")
        login_code = THS_iFinDLogin(username, password)
        if login_code not in (0, -201):
            raise RuntimeError(f"ifind_login_failed:{login_code}")
        try:
            results = [_execute(dict(action)) for action in actions]
        finally:
            logout_code = THS_iFinDLogout() if login_code == 0 else None
        payload = {
            "status": "success",
            "login_code": login_code,
            "logout_code": logout_code,
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - secret-safe process boundary
        code = str(exc)
        if len(code) > 200 or any(
            marker in code.casefold()
            for marker in ("password", "token=", "username=")
        ):
            code = type(exc).__name__
        print(
            json.dumps(
                {"status": "failed", "error": code},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
