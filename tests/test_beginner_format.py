from __future__ import annotations

import unittest

from stock_analyze.beginner_format import cn_date, cn_relative_date, cny, pct


class CnyTests(unittest.TestCase):
    def test_below_wan_renders_as_yuan(self) -> None:
        self.assertEqual(cny(0), "0元")
        self.assertEqual(cny(1234), "1,234元")
        self.assertEqual(cny(9999), "9,999元")

    def test_wan_scale(self) -> None:
        self.assertEqual(cny(12345), "1.23万元")
        self.assertEqual(cny(1_000_000), "100.00万元")
        # 99,999,999 / 10000 = 9999.9999 → rounds to 10000.00 with 2dp
        self.assertEqual(cny(99_990_000), "9999.00万元")

    def test_yi_scale(self) -> None:
        self.assertEqual(cny(123_456_789), "1.23亿元")
        self.assertEqual(cny(1_000_000_000), "10.00亿元")

    def test_negative_values(self) -> None:
        self.assertEqual(cny(-9876), "-9,876元")
        self.assertEqual(cny(-12345), "-1.23万元")

    def test_invalid_returns_dash(self) -> None:
        self.assertEqual(cny(None), "-")
        self.assertEqual(cny(""), "-")
        self.assertEqual(cny("nan"), "-")
        self.assertEqual(cny(float("nan")), "-")
        self.assertEqual(cny("not a number"), "-")

    def test_string_numeric_input(self) -> None:
        self.assertEqual(cny("1234"), "1,234元")
        self.assertEqual(cny("12,345"), "1.23万元")


class PctTests(unittest.TestCase):
    def test_signed_positive_includes_plus(self) -> None:
        self.assertEqual(pct(0.0132), "+1.32%")

    def test_signed_negative_keeps_minus(self) -> None:
        self.assertEqual(pct(-0.0084), "-0.84%")

    def test_zero(self) -> None:
        self.assertEqual(pct(0), "0.00%")

    def test_unsigned_drops_plus(self) -> None:
        self.assertEqual(pct(0.0132, signed=False), "1.32%")
        # negative still shows the minus
        self.assertEqual(pct(-0.0084, signed=False), "-0.84%")

    def test_color_wraps_span(self) -> None:
        self.assertEqual(pct(0.05, color=True), '<span class="pos">+5.00%</span>')
        self.assertEqual(pct(-0.05, color=True), '<span class="neg">-5.00%</span>')
        self.assertEqual(pct(0, color=True), '<span class="zero">0.00%</span>')

    def test_invalid_returns_dash(self) -> None:
        self.assertEqual(pct(None), "-")
        self.assertEqual(pct("--"), "-")
        self.assertEqual(pct(float("nan")), "-")


class CnDateTests(unittest.TestCase):
    def test_same_year_drops_year(self) -> None:
        self.assertEqual(cn_date("2026-05-22", today="2026-05-23"), "5月22日")
        self.assertEqual(cn_date("2026-01-01", today="2026-12-31"), "1月1日")

    def test_different_year_keeps_year(self) -> None:
        self.assertEqual(cn_date("2025-12-18", today="2026-05-23"), "2025年12月18日")

    def test_accepts_multiple_input_formats(self) -> None:
        self.assertEqual(cn_date("2026/05/22", today="2026-05-23"), "5月22日")
        self.assertEqual(cn_date("20260522", today="2026-05-23"), "5月22日")

    def test_invalid_input_returns_dash(self) -> None:
        self.assertEqual(cn_date(None), "-")
        self.assertEqual(cn_date(""), "-")
        self.assertEqual(cn_date("not-a-date"), "-")


class CnRelativeDateTests(unittest.TestCase):
    TODAY = "2026-05-23"  # a Saturday

    def test_today(self) -> None:
        self.assertEqual(cn_relative_date("2026-05-23", today=self.TODAY), "今天")

    def test_yesterday(self) -> None:
        self.assertEqual(cn_relative_date("2026-05-22", today=self.TODAY), "昨天")

    def test_day_before_yesterday(self) -> None:
        self.assertEqual(cn_relative_date("2026-05-21", today=self.TODAY), "前天")

    def test_within_a_week_returns_last_weekday(self) -> None:
        # 2026-05-19 is a Tuesday; today is Saturday 2026-05-23 → 4 days back.
        self.assertEqual(cn_relative_date("2026-05-19", today=self.TODAY), "上周二")
        # 2026-05-18 Monday, 5 days back.
        self.assertEqual(cn_relative_date("2026-05-18", today=self.TODAY), "上周一")

    def test_previous_month_format(self) -> None:
        self.assertEqual(cn_relative_date("2026-04-15", today=self.TODAY), "上月15日")

    def test_same_year_older_than_month(self) -> None:
        self.assertEqual(cn_relative_date("2026-01-10", today=self.TODAY), "1月10日")

    def test_previous_year_includes_year(self) -> None:
        self.assertEqual(
            cn_relative_date("2025-12-18", today=self.TODAY),
            "2025年12月18日",
        )

    def test_invalid_returns_dash(self) -> None:
        self.assertEqual(cn_relative_date(None), "-")
        self.assertEqual(cn_relative_date(""), "-")


if __name__ == "__main__":
    unittest.main()
