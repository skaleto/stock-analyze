"""Typed fail-closed HTTPS transport for the Tushare Pro API."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx
import pandas as pd


class TushareTransportError(RuntimeError):
    """Base class for bounded Tushare transport failures."""


class TushareRetryableError(TushareTransportError):
    """A timeout, throttle, or provider outage that may succeed later."""


class TushareTerminalError(TushareTransportError):
    """An authentication, entitlement, parameter, or shape failure."""


class TushareProTransport:
    def __init__(
        self,
        token: str,
        *,
        endpoint: str = "https://api.tushare.pro",
        http_client=None,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
        max_backoff_seconds: float = 4.0,
        rate_limit_backoff_seconds: float = 21.0,
        max_rate_limit_backoff_seconds: float = 60.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        parsed = urlparse(str(endpoint))
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("tushare_endpoint_must_be_https")
        normalized_token = str(token).strip()
        if not normalized_token:
            raise ValueError("tushare_token_missing")
        self.token = normalized_token
        self.endpoint = str(endpoint)
        self.http_client = http_client or httpx.Client()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.max_backoff_seconds = max(
            self.backoff_seconds,
            float(max_backoff_seconds),
        )
        self.rate_limit_backoff_seconds = max(
            0.0,
            float(rate_limit_backoff_seconds),
        )
        self.max_rate_limit_backoff_seconds = max(
            self.rate_limit_backoff_seconds,
            float(max_rate_limit_backoff_seconds),
        )
        self.sleeper = sleeper

    def anns_d(self, **kwargs) -> pd.DataFrame:
        return self._query("anns_d", **kwargs)

    def trade_cal(self, **kwargs) -> pd.DataFrame:
        return self._query("trade_cal", **kwargs)

    def stock_basic(self, **kwargs) -> pd.DataFrame:
        return self._query("stock_basic", **kwargs)

    def fund_basic(self, **kwargs) -> pd.DataFrame:
        return self._query("fund_basic", **kwargs)

    def _query(self, api_name: str, **kwargs) -> pd.DataFrame:
        fields = str(kwargs.pop("fields", "")).strip()
        if not fields:
            raise TushareTerminalError(
                f"tushare_fields_required:{api_name}"
            )
        last_error: TushareRetryableError | None = None
        for attempt in range(self.max_attempts):
            try:
                return self._query_once(
                    api_name,
                    fields=fields,
                    params=kwargs,
                )
            except TushareRetryableError as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    raise
                if str(exc).startswith(
                    "tushare_business_rate_limit:"
                ):
                    delay = min(
                        self.rate_limit_backoff_seconds
                        * (2 ** attempt),
                        self.max_rate_limit_backoff_seconds,
                    )
                else:
                    delay = min(
                        self.backoff_seconds * (2 ** attempt),
                        self.max_backoff_seconds,
                    )
                self.sleeper(delay)
        raise last_error or TushareRetryableError(
            "tushare_retry_exhausted"
        )

    def _query_once(
        self,
        api_name: str,
        *,
        fields: str,
        params: dict[str, object],
    ) -> pd.DataFrame:
        try:
            response = self.http_client.post(
                self.endpoint,
                json={
                    "api_name": api_name,
                    "token": self.token,
                    "params": params,
                    "fields": fields,
                },
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TushareRetryableError(
                f"tushare_transport_retryable:{type(exc).__name__}"
            ) from exc

        status = int(getattr(response, "status_code", 0))
        if status == 429 or status >= 500:
            raise TushareRetryableError(
                f"tushare_http_retryable:{status}"
            )
        if status < 200 or status >= 300:
            raise TushareTerminalError(
                f"tushare_http_terminal:{status}"
            )
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - typed JSON boundary
            raise TushareTerminalError(
                "tushare_json_invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise TushareTerminalError("tushare_payload_invalid")

        try:
            code = int(payload.get("code"))
        except (TypeError, ValueError) as exc:
            raise TushareTerminalError(
                "tushare_business_code_invalid"
            ) from exc
        if code != 0:
            message = self._safe_message(payload.get("msg"))
            if self._is_rate_limit(code, message):
                raise TushareRetryableError(
                    f"tushare_business_rate_limit:{code}:{message}"
                )
            raise TushareTerminalError(
                f"tushare_business_terminal:{code}:{message}"
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise TushareTerminalError("tushare_data_invalid")
        response_fields = data.get("fields")
        items = data.get("items")
        expected_fields = fields.split(",")
        if (
            not isinstance(response_fields, list)
            or any(not isinstance(value, str) for value in response_fields)
            or response_fields != expected_fields
        ):
            raise TushareTerminalError(
                "tushare_response_fields_invalid"
            )
        if not isinstance(items, list):
            raise TushareTerminalError(
                "tushare_response_items_invalid"
            )
        if any(
            not isinstance(row, list)
            or len(row) != len(response_fields)
            for row in items
        ):
            raise TushareTerminalError(
                "tushare_response_row_shape_invalid"
            )
        return pd.DataFrame(items, columns=response_fields)

    def _safe_message(self, value: object) -> str:
        message = " ".join(str(value or "").split())[:200]
        return message.replace(self.token, "[REDACTED]")

    @staticmethod
    def _is_rate_limit(code: int, message: str) -> bool:
        normalized = message.casefold()
        return code in {429, -429, -2002, 40203} or any(
            marker in normalized
            for marker in (
                "每分钟",
                "每小时",
                "访问频率",
                "频率超限",
                "次/分钟",
                "限流",
                "rate limit",
                "too many",
            )
        )
