from __future__ import annotations

import unittest

import httpx

from stock_analyze.intelligence.tushare_transport import (
    TushareProTransport,
    TushareRetryableError,
    TushareTerminalError,
)


FIELDS = "ann_date,ts_code,name,title,url,rec_time"


class FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeHttpClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def success_payload(items: list[list[object]]) -> dict[str, object]:
    return {
        "request_id": "request-id",
        "code": 0,
        "msg": "",
        "data": {
            "fields": FIELDS.split(","),
            "items": items,
        },
    }


class TushareProTransportTest(unittest.TestCase):
    def test_capital_actions_use_typed_https_transport(self) -> None:
        for method, api_name in (
            ("repurchase", "repurchase"),
            ("stk_holdertrade", "stk_holdertrade"),
            ("stk_holdernumber", "stk_holdernumber"),
            ("share_float", "share_float"),
        ):
            with self.subTest(method=method):
                http = FakeHttpClient([FakeResponse(200, success_payload([]))])
                transport = TushareProTransport(
                    "secret-token", http_client=http, max_attempts=1
                )
                getattr(transport, method)(
                    start_date="20200101",
                    end_date="20200131",
                    fields=FIELDS,
                )
                self.assertEqual(http.calls[0]["json"]["api_name"], api_name)

    def test_successful_query_returns_typed_frame(self) -> None:
        http = FakeHttpClient([
            FakeResponse(200, success_payload([[
                "20260724",
                "000001.SZ",
                "平安银行",
                "公告",
                "https://example.test/a.pdf",
                "2026-07-24 09:31:00",
            ]])),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=1,
        )

        frame = transport.anns_d(
            start_date="20260724",
            end_date="20260724",
            limit=2000,
            offset=0,
            fields=FIELDS,
        )

        self.assertEqual(frame.columns.tolist(), FIELDS.split(","))
        self.assertEqual(frame.iloc[0]["ts_code"], "000001.SZ")
        call = http.calls[0]
        self.assertEqual(call["url"], "https://api.tushare.pro")
        self.assertEqual(call["json"]["api_name"], "anns_d")
        self.assertEqual(call["json"]["fields"], FIELDS)
        self.assertEqual(call["json"]["token"], "secret-token")

    def test_explicit_code_zero_empty_result_is_valid(self) -> None:
        http = FakeHttpClient([
            FakeResponse(200, success_payload([])),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=1,
        )

        frame = transport.anns_d(
            ann_date="20260724",
            fields=FIELDS,
        )

        self.assertTrue(frame.empty)
        self.assertEqual(frame.columns.tolist(), FIELDS.split(","))

    def test_fund_basic_uses_typed_https_transport(self) -> None:
        fields = "ts_code,list_date,delist_date,status"
        http = FakeHttpClient([
            FakeResponse(200, {
                "request_id": "fund-request",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": fields.split(","),
                    "items": [[
                        "516390.SH",
                        "20210209",
                        None,
                        "L",
                    ]],
                },
            }),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=1,
        )

        frame = transport.fund_basic(
            market="E",
            status="L",
            limit=15000,
            offset=0,
            fields=fields,
        )

        self.assertEqual(frame.iloc[0]["ts_code"], "516390.SH")
        self.assertEqual(
            http.calls[0]["json"]["api_name"],
            "fund_basic",
        )

    def test_fund_basic_supports_strict_unfiltered_exchange_query(self) -> None:
        fields = "ts_code,list_date,delist_date,status"
        http = FakeHttpClient([
            FakeResponse(200, {
                "request_id": "fund-all-request",
                "code": 0,
                "msg": "",
                "data": {
                    "fields": fields.split(","),
                    "items": [["159756.SZ", "", "", ""]],
                },
            }),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=1,
        )

        frame = transport.fund_basic(
            market="E",
            limit=15000,
            offset=0,
            fields=fields,
        )

        self.assertEqual(frame.iloc[0]["ts_code"], "159756.SZ")
        self.assertEqual(
            http.calls[0]["json"]["params"],
            {
                "market": "E",
                "limit": 15000,
                "offset": 0,
            },
        )

    def test_http_503_is_retried_and_never_becomes_empty_data(self) -> None:
        sleeps: list[float] = []
        http = FakeHttpClient([
            FakeResponse(503, {"error": "unavailable"}),
            FakeResponse(200, success_payload([[
                "20260724", "000001.SZ", "平安银行", "公告",
                "https://example.test/a.pdf", "",
            ]])),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=2,
            backoff_seconds=0.25,
            sleeper=sleeps.append,
        )

        frame = transport.anns_d(ann_date="20260724", fields=FIELDS)

        self.assertEqual(len(frame), 1)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(len(http.calls), 2)

    def test_exhausted_timeout_raises_retryable_without_token(self) -> None:
        request = httpx.Request("POST", "https://api.tushare.pro")
        http = FakeHttpClient([
            httpx.ReadTimeout("secret-token", request=request),
            httpx.ReadTimeout("secret-token", request=request),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=2,
            sleeper=lambda _: None,
        )

        with self.assertRaises(TushareRetryableError) as captured:
            transport.anns_d(ann_date="20260724", fields=FIELDS)

        self.assertNotIn("secret-token", str(captured.exception))

    def test_rate_limit_business_error_is_retryable(self) -> None:
        http = FakeHttpClient([
            FakeResponse(200, {
                "code": -2002,
                "msg": "每分钟最多访问该接口1次",
                "data": None,
            }),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=1,
        )

        with self.assertRaisesRegex(
            TushareRetryableError,
            "business_rate_limit",
        ):
            transport.anns_d(ann_date="20260724", fields=FIELDS)

    def test_real_40203_frequency_limit_waits_for_window_reset(
        self,
    ) -> None:
        sleeps: list[float] = []
        throttled = FakeResponse(200, {
            "code": 40203,
            "msg": (
                "抱歉，您访问接口(anns_d)频率超限"
                "(300次/分钟)"
            ),
            "data": None,
        })
        http = FakeHttpClient([
            throttled,
            throttled,
            FakeResponse(200, success_payload([])),
        ])
        transport = TushareProTransport(
            "secret-token",
            http_client=http,
            max_attempts=3,
            sleeper=sleeps.append,
        )

        frame = transport.anns_d(
            ann_date="20260724",
            fields=FIELDS,
        )

        self.assertTrue(frame.empty)
        self.assertEqual(sleeps, [21.0, 42.0])
        self.assertEqual(len(http.calls), 3)

    def test_auth_permission_parameter_and_business_errors_are_terminal(self) -> None:
        for payload in (
            {"code": -2001, "msg": "没有接口访问权限", "data": None},
            {"code": -1001, "msg": "token无效", "data": None},
            {"code": -3001, "msg": "参数错误", "data": None},
        ):
            with self.subTest(payload=payload):
                transport = TushareProTransport(
                    "secret-token",
                    http_client=FakeHttpClient([FakeResponse(200, payload)]),
                    max_attempts=1,
                )
                with self.assertRaises(TushareTerminalError):
                    transport.anns_d(
                        ann_date="20260724",
                        fields=FIELDS,
                    )

    def test_http_401_and_malformed_success_shape_are_terminal(self) -> None:
        cases = (
            FakeResponse(401, {"error": "unauthorized"}),
            FakeResponse(200, {"code": 0, "msg": "", "data": None}),
            FakeResponse(200, {
                "code": 0,
                "msg": "",
                "data": {
                    "fields": ["ann_date"],
                    "items": [["20260724", "extra"]],
                },
            }),
        )
        for response in cases:
            with self.subTest(response=response):
                transport = TushareProTransport(
                    "secret-token",
                    http_client=FakeHttpClient([response]),
                    max_attempts=1,
                )
                with self.assertRaises(TushareTerminalError):
                    transport.anns_d(
                        ann_date="20260724",
                        fields=FIELDS,
                    )

    def test_non_https_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "tushare_endpoint_must_be_https",
        ):
            TushareProTransport(
                "secret-token",
                endpoint="http://api.tushare.pro",
            )


if __name__ == "__main__":
    unittest.main()
