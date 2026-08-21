"""Tests for backtest data preparation.

These tests mock the Tushare client; no live network or token required.
"""
from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pandas as pd

from stock_analyze.markets.a_share.backtest import data_prep


def _stub_pro(daily_df=None, daily_basic_df=None, stock_basic_df=None,
              fina_df=None, adj_df=None, index_weight_df=None,
              index_daily_df=None, trade_cal_df=None, namechange_df=None,
              stock_st_df=None, suspend_df=None, income_df=None,
              balancesheet_df=None, cashflow_df=None):
    """Build a MagicMock that mimics a Tushare pro_api instance."""
    pro = MagicMock()
    pro.daily.return_value = daily_df if daily_df is not None else pd.DataFrame()
    pro.daily_basic.return_value = daily_basic_df if daily_basic_df is not None else pd.DataFrame()
    pro.stock_basic.return_value = stock_basic_df if stock_basic_df is not None else pd.DataFrame()
    pro.fina_indicator.return_value = fina_df if fina_df is not None else pd.DataFrame()
    pro.income.return_value = income_df if income_df is not None else pd.DataFrame(
        columns=['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
                 'update_flag', 'revenue', 'operate_profit', 'n_income',
                 'total_cogs', 'rd_exp']
    )
    pro.balancesheet.return_value = (
        balancesheet_df if balancesheet_df is not None else pd.DataFrame(
            columns=['ts_code', 'ann_date', 'f_ann_date', 'end_date',
                     'report_type', 'update_flag', 'total_assets']
        )
    )
    pro.cashflow.return_value = cashflow_df if cashflow_df is not None else pd.DataFrame(
        columns=['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type',
                 'update_flag', 'n_cashflow_act', 'free_cashflow']
    )
    pro.adj_factor.return_value = adj_df if adj_df is not None else pd.DataFrame()
    pro.index_weight.return_value = index_weight_df if index_weight_df is not None else pd.DataFrame()
    pro.index_daily.return_value = index_daily_df if index_daily_df is not None else pd.DataFrame()
    pro.trade_cal.return_value = trade_cal_df if trade_cal_df is not None else pd.DataFrame()
    pro.namechange.return_value = namechange_df if namechange_df is not None else pd.DataFrame()
    pro.stock_st.return_value = stock_st_df if stock_st_df is not None else pd.DataFrame()
    pro.suspend_d.return_value = suspend_df if suspend_df is not None else pd.DataFrame()
    return pro


def _trade_cal(dates):
    """Build a trade_cal DataFrame with given YYYYMMDD strings (all open)."""
    return pd.DataFrame({'cal_date': list(dates), 'is_open': [1] * len(dates)})


def _stock_basic(rows):
    """rows = [(ts_code, name, list_date, delist_date, industry), ...]"""
    return pd.DataFrame({
        'ts_code': [r[0] for r in rows],
        'symbol': [r[0].split('.')[0] for r in rows],
        'name': [r[1] for r in rows],
        'area': ['SH'] * len(rows),
        'industry': [r[4] for r in rows],
        'list_date': [r[2] for r in rows],
        'delist_date': [r[3] for r in rows],
    })


class PrepareBacktestDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache_root = Path(self.tmp.name) / 'backtest_cache'

    def tearDown(self):
        self.tmp.cleanup()

    def test_formal_benchmark_collection_keeps_only_hs300_and_zz500(self):
        self.assertEqual(
            data_prep.INDEX_CODES,
            [("000300.SH", "000300"), ("000905.SH", "000905")],
        )

    def test_writes_daily_csv_per_date(self):
        """prepare_backtest_data fetches pro.daily per date and writes a CSV per date."""
        fake_daily = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'trade_date': ['20210104', '20210104'],
            'open': [10.0, 20.0],
            'close': [10.5, 19.8],
            'high': [11.0, 20.5],
            'low': [9.8, 19.5],
            'vol': [1000, 2000],
            'amount': [10000.0, 39600.0],
        })
        pro = _stub_pro(
            daily_df=fake_daily,
            daily_basic_df=pd.DataFrame(),
            stock_basic_df=_stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')]),
            trade_cal_df=_trade_cal(['20210104', '20210105']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4),
                end=date(2021, 1, 5),
                cache_root=self.cache_root,
            )

        for d_iso in ['2021-01-04', '2021-01-05']:
            out = self.cache_root / 'daily' / f'{d_iso}.csv'
            self.assertTrue(out.exists(), f'Expected {out} to exist')
            df = pd.read_csv(out)
            self.assertIn('ts_code', df.columns)

    def test_trade_calendar_extension_preserves_existing_dates(self):
        self.cache_root.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'cal_date': ['20210104'],
            'is_open': [1],
        }).to_csv(self.cache_root / 'trade_cal.csv', index=False)
        pro = _stub_pro(trade_cal_df=_trade_cal(['20200102']))

        requested = data_prep._fetch_trade_cal(
            pro,
            date(2020, 1, 1),
            date(2020, 1, 31),
            self.cache_root,
        )

        merged = pd.read_csv(self.cache_root / 'trade_cal.csv', dtype={'cal_date': str})
        self.assertEqual(set(merged['cal_date']), {'20200102', '20210104'})
        self.assertEqual(requested, ['20200102'])

    def test_fina_extension_merges_existing_periods(self):
        path = self.cache_root / 'fina_indicator' / '000001.SZ.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'ann_date': ['20210401'],
            'end_date': ['20201231'],
            'roe': [10.0],
        }).to_csv(path, index=False)
        pro = _stub_pro(fina_df=pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'ann_date': ['20200401'],
            'end_date': ['20191231'],
            'roe': [9.0],
        }))

        data_prep._fetch_fina(
            pro,
            '000001.SZ',
            date(2019, 1, 1),
            date(2020, 12, 31),
            self.cache_root,
        )

        merged = pd.read_csv(path, dtype={'ann_date': str, 'end_date': str})
        self.assertEqual(set(merged['end_date']), {'20191231', '20201231'})

    def test_statement_extension_preserves_publication_revisions(self):
        pro = _stub_pro(income_df=pd.DataFrame([
            {
                'ts_code': '000001.SZ', 'ann_date': '20200401',
                'f_ann_date': '20200401', 'end_date': '20191231',
                'report_type': '1', 'update_flag': '0',
                'revenue': 100.0, 'operate_profit': 20.0,
                'n_income': 15.0, 'total_cogs': 70.0, 'rd_exp': 5.0,
            },
            {
                'ts_code': '000001.SZ', 'ann_date': '20200420',
                'f_ann_date': '20200420', 'end_date': '20191231',
                'report_type': '1', 'update_flag': '1',
                'revenue': 110.0, 'operate_profit': 22.0,
                'n_income': 16.0, 'total_cogs': 76.0, 'rd_exp': 6.0,
            },
        ]))

        ok = data_prep._fetch_financial_statement(
            pro,
            'income',
            '000001.SZ',
            date(2019, 1, 1),
            date(2020, 12, 31),
            self.cache_root,
        )

        self.assertTrue(ok)
        path = self.cache_root / 'income' / '000001.SZ.csv'
        frame = pd.read_csv(
            path,
            dtype={
                'ts_code': str, 'ann_date': str, 'f_ann_date': str,
                'end_date': str, 'report_type': str, 'update_flag': str,
            },
        )
        self.assertEqual(frame['ann_date'].tolist(), ['20200401', '20200420'])
        self.assertEqual(frame['update_flag'].tolist(), ['0', '1'])

    def test_adj_factor_extension_merges_existing_periods(self):
        path = self.cache_root / 'adj_factor' / '000001.SZ.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20210104'],
            'adj_factor': [1.1],
        }).to_csv(path, index=False)
        pro = _stub_pro(adj_df=pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20200102'],
            'adj_factor': [1.0],
        }))

        data_prep._fetch_adj(
            pro,
            '000001.SZ',
            date(2020, 1, 1),
            date(2020, 12, 31),
            self.cache_root,
        )

        merged = pd.read_csv(path, dtype={'trade_date': str})
        self.assertEqual(set(merged['trade_date']), {'20200102', '20210104'})

    def test_adj_factor_long_range_is_split_before_tushare_row_limit(self):
        calls = []
        pro = _stub_pro()

        def fetch_window(**kwargs):
            calls.append((kwargs['start_date'], kwargs['end_date']))
            return pd.DataFrame({
                'ts_code': [kwargs['ts_code']],
                'trade_date': [kwargs['end_date']],
                'adj_factor': [1.0],
            })

        pro.adj_factor.side_effect = fetch_window
        ok = data_prep._fetch_adj(
            pro,
            '000001.SZ',
            date(2018, 1, 1),
            date(2026, 8, 7),
            self.cache_root,
        )

        self.assertTrue(ok)
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[0][0], '20180101')
        self.assertEqual(calls[-1][1], '20260807')
        for (_, previous_end), (next_start, _) in zip(calls, calls[1:]):
            self.assertEqual(
                date.fromisoformat(next_start[:4] + '-' + next_start[4:6] + '-' + next_start[6:])
                - date.fromisoformat(previous_end[:4] + '-' + previous_end[4:6] + '-' + previous_end[6:]),
                timedelta(days=1),
            )
        self.assertTrue(all(
            (
                date.fromisoformat(end[:4] + '-' + end[4:6] + '-' + end[6:])
                - date.fromisoformat(start[:4] + '-' + start[4:6] + '-' + start[6:])
            ).days <= 5 * 366
            for start, end in calls
        ))

    def test_adj_factor_empty_active_window_is_retried_without_refetching_progress(self):
        pro = _stub_pro()
        responses = [
            pd.DataFrame({
                'ts_code': ['000001.SZ'],
                'trade_date': ['20141231'],
                'adj_factor': [1.0],
            }),
            pd.DataFrame(columns=['ts_code', 'trade_date', 'adj_factor']),
        ]
        pro.adj_factor.side_effect = responses

        first = data_prep._fetch_adj(
            pro,
            '000001.SZ',
            date(2010, 1, 1),
            date(2021, 12, 31),
            self.cache_root,
            active_start=date(2010, 1, 1),
            active_end=date(2021, 12, 31),
        )

        self.assertFalse(first)
        self.assertEqual(pro.adj_factor.call_count, 2)
        progress = json.loads(
            (self.cache_root / 'adj_factor' / '000001.SZ.windows.json').read_text()
        )
        self.assertEqual(len(progress['completed_windows']), 1)

        pro.adj_factor.reset_mock()

        def recovered(**kwargs):
            return pd.DataFrame({
                'ts_code': [kwargs['ts_code']],
                'trade_date': [kwargs['end_date']],
                'adj_factor': [1.0],
            })

        pro.adj_factor.side_effect = recovered
        second = data_prep._fetch_adj(
            pro,
            '000001.SZ',
            date(2010, 1, 1),
            date(2021, 12, 31),
            self.cache_root,
            active_start=date(2010, 1, 1),
            active_end=date(2021, 12, 31),
        )

        self.assertTrue(second)
        self.assertEqual(pro.adj_factor.call_count, 2)
        merged = pd.read_csv(
            self.cache_root / 'adj_factor' / '000001.SZ.csv',
            dtype={'ts_code': str, 'trade_date': str},
        )
        self.assertEqual(len(merged), 3)

    def test_adj_factor_empty_window_outside_lifecycle_is_valid(self):
        pro = _stub_pro(
            adj_df=pd.DataFrame(columns=['ts_code', 'trade_date', 'adj_factor'])
        )

        ok = data_prep._fetch_adj(
            pro,
            '000001.SZ',
            date(2010, 1, 1),
            date(2010, 12, 31),
            self.cache_root,
            active_start=date(2015, 1, 1),
            active_end=date(2021, 12, 31),
        )

        self.assertTrue(ok)

    def test_code_scoped_endpoints_are_idempotent_per_requested_range(self):
        stock_basic = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ])
        pro = _stub_pro(
            stock_basic_df=stock_basic,
            fina_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'ann_date': ['20210401'],
                'end_date': ['20201231'],
            }),
            adj_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
                'adj_factor': [1.0],
            }),
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        pro.fina_indicator.reset_mock()
        pro.adj_factor.reset_mock()
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        pro.fina_indicator.assert_not_called()
        pro.adj_factor.assert_not_called()

        pro.trade_cal.return_value = _trade_cal(['20200102'])
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2020, 1, 2), end=date(2020, 1, 2),
                cache_root=self.cache_root,
            )
        self.assertEqual(pro.fina_indicator.call_count, 1)
        self.assertEqual(pro.adj_factor.call_count, 1)

    def test_legacy_adj_range_marker_cannot_skip_window_progress_validation(self):
        start = date(2010, 1, 1)
        end = date(2021, 12, 31)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'symbol': ['000001'],
            'name': ['平安银行'],
            'area': ['深圳'],
            'industry': ['银行'],
            'list_date': ['19910403'],
            'delist_date': [''],
            'list_status': ['L'],
        }).to_csv(self.cache_root / 'stock_basic.csv', index=False)
        adj_path = self.cache_root / 'adj_factor' / '000001.SZ.csv'
        adj_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20100104'],
            'adj_factor': [1.0],
        }).to_csv(adj_path, index=False)
        data_prep._save_meta(self.cache_root, {
            **data_prep._DEFAULT_META,
            'stock_basic_done': True,
            'stock_basic_statuses_done': list(data_prep.STOCK_BASIC_STATUSES),
            'adj_factor_codes_done': ['000001.SZ'],
            'adj_factor_code_ranges_done': [
                data_prep._code_range_key('000001.SZ', start, end)
            ],
        })
        pro = _stub_pro()

        def fetch_window(**kwargs):
            return pd.DataFrame({
                'ts_code': [kwargs['ts_code']],
                'trade_date': [kwargs['end_date']],
                'adj_factor': [1.0],
            })

        pro.adj_factor.side_effect = fetch_window
        with (
            patch(
                'stock_analyze.markets.a_share.backtest.data_prep._make_pro_client',
                return_value=pro,
            ),
            patch('stock_analyze.markets.a_share.backtest.data_prep._throttle'),
        ):
            data_prep.prepare_backtest_data(
                start=start,
                end=end,
                cache_root=self.cache_root,
                phases={'adjustments'},
            )

        self.assertGreater(pro.adj_factor.call_count, 0)
        progress = json.loads(
            (self.cache_root / 'adj_factor' / '000001.SZ.windows.json').read_text()
        )
        self.assertEqual(
            len(progress['completed_windows']),
            pro.adj_factor.call_count,
        )

    def test_idempotent_skips_existing_daily(self):
        """Already-fetched dates should NOT trigger pro.daily a second time."""
        # Pre-populate one date as "already fetched"
        existing = self.cache_root / 'daily' / '2021-01-04.csv'
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(
            'ts_code,trade_date,open,high,low,close,vol,amount\n'
            '000001.SZ,20210104,10,11,9,10.5,1000,10000\n'
        )
        # And the same for daily_basic so the prep won't need to re-fetch
        db_existing = self.cache_root / 'daily_basic' / '2021-01-04.csv'
        db_existing.parent.mkdir(parents=True, exist_ok=True)
        db_existing.write_text(
            'ts_code,trade_date,pe_ttm,pb\n000001.SZ,20210104,5.5,1.1\n'
        )
        meta_path = self.cache_root / '_meta.json'
        meta_path.write_text(json.dumps({
            'daily_dates_done': ['2021-01-04'],
            'daily_basic_dates_done': ['2021-01-04'],
            'fina_codes_done': [],
            'adj_factor_codes_done': [],
            'index_weight_months_done': [],
            'stock_basic_done': True,
            'stock_basic_statuses_done': ['D', 'L', 'P'],
        }))
        # stock_basic.csv also exists
        _stock_basic([]).to_csv(self.cache_root / 'stock_basic.csv', index=False)

        pro = _stub_pro(
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4),
                end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        # daily was NOT called because 2021-01-04 was in _meta.json
        pro.daily.assert_not_called()
        pro.daily_basic.assert_not_called()

    def test_empty_daily_responses_are_not_marked_complete(self):
        pro = _stub_pro(
            stock_basic_df=_stock_basic([]),
            trade_cal_df=_trade_cal(['20210104']),
        )

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertNotIn('2021-01-04', meta['daily_dates_done'])
        self.assertNotIn('2021-01-04', meta['daily_basic_dates_done'])
        self.assertFalse((self.cache_root / 'daily' / '2021-01-04.csv').exists())
        self.assertFalse((self.cache_root / 'daily_basic' / '2021-01-04.csv').exists())

    def test_missing_daily_files_are_refetched_even_when_meta_says_done(self):
        self.cache_root.mkdir(parents=True, exist_ok=True)
        stock_basic = _stock_basic([]).assign(list_status=pd.Series(dtype=str))
        stock_basic.to_csv(self.cache_root / 'stock_basic.csv', index=False)
        (self.cache_root / '_meta.json').write_text(json.dumps({
            'daily_dates_done': ['2021-01-04'],
            'daily_basic_dates_done': ['2021-01-04'],
            'stock_basic_done': True,
            'stock_basic_statuses_done': ['D', 'L', 'P'],
        }))
        daily = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
            'open': [10.0], 'high': [10.1], 'low': [9.9], 'close': [10.0],
            'vol': [1_000.0], 'amount': [10_000.0],
        })
        daily_basic = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
            'pe_ttm': [10.0], 'pb': [1.0],
        })
        pro = _stub_pro(
            daily_df=daily,
            daily_basic_df=daily_basic,
            trade_cal_df=_trade_cal(['20210104']),
        )

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        pro.daily.assert_called_once_with(trade_date='20210104')
        pro.daily_basic.assert_called_once_with(trade_date='20210104')
        self.assertTrue((self.cache_root / 'daily' / '2021-01-04.csv').exists())
        self.assertTrue((self.cache_root / 'daily_basic' / '2021-01-04.csv').exists())

    def test_incomplete_stock_status_response_is_not_marked_complete(self):
        listed = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ])
        pro = _stub_pro(trade_cal_df=_trade_cal([]))
        pro.stock_basic.side_effect = [listed, pd.DataFrame(), _stock_basic([])]

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertFalse(meta['stock_basic_done'])
        self.assertEqual(meta['stock_basic_statuses_done'], ['L', 'P'])

    def test_incomplete_stock_status_retry_preserves_last_complete_cache(self):
        self.cache_root.mkdir(parents=True, exist_ok=True)
        existing = pd.concat([
            _stock_basic([
                ('000001.SZ', '平安银行', '19910403', None, '银行'),
            ]).assign(list_status='L'),
            _stock_basic([
                ('000002.SZ', '退市样本', '19910129', '20200101', '综合'),
            ]).assign(list_status='D'),
            _stock_basic([
                ('000003.SZ', '暂停样本', '19910703', None, '制造'),
            ]).assign(list_status='P'),
        ], ignore_index=True)
        existing.to_csv(self.cache_root / 'stock_basic.csv', index=False)

        pro = _stub_pro()
        pro.stock_basic.side_effect = [
            _stock_basic([
                ('000004.SZ', '新上市样本', '20200102', None, '制造'),
            ]),
            pd.DataFrame(),
            _stock_basic([]),
        ]

        _, completed = data_prep._fetch_stock_basic(pro, self.cache_root)

        persisted = pd.read_csv(
            self.cache_root / 'stock_basic.csv', dtype={'ts_code': str}
        )
        self.assertEqual(set(completed), {'L', 'P'})
        self.assertEqual(
            set(persisted['ts_code']), {'000001.SZ', '000002.SZ', '000003.SZ'}
        )

    def test_invalid_code_scoped_responses_are_retried(self):
        stock_basic = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ])
        pro = _stub_pro(
            stock_basic_df=stock_basic,
            trade_cal_df=_trade_cal([]),
        )

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertEqual(meta['fina_code_ranges_done'], [])
        self.assertEqual(meta['adj_factor_code_ranges_done'], [])

    def test_writes_daily_basic_csv_per_date(self):
        fake_db = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20210104'],
            'pe_ttm': [5.5],
            'pb': [1.1],
            'dv_ttm': [0.04],
            'total_mv': [1.2e6],
            'circ_mv': [1.0e6],
            'turnover_rate': [0.5],
        })
        pro = _stub_pro(
            daily_basic_df=fake_db,
            stock_basic_df=_stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')]),
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        out = self.cache_root / 'daily_basic' / '2021-01-04.csv'
        self.assertTrue(out.exists())
        df = pd.read_csv(out)
        self.assertIn('pe_ttm', df.columns)

    def test_writes_stock_basic_once(self):
        sb = _stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')])
        pro = _stub_pro(stock_basic_df=sb, trade_cal_df=_trade_cal(['20210104']))
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        out = self.cache_root / 'stock_basic.csv'
        self.assertTrue(out.exists())
        df = pd.read_csv(out)
        self.assertEqual(df.iloc[0]['ts_code'], '000001.SZ')

        # Second call should NOT re-fetch stock_basic
        pro.stock_basic.reset_mock()
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        pro.stock_basic.assert_not_called()

    def test_stock_basic_includes_delisted_and_paused_securities(self):
        listed = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ])
        delisted = _stock_basic([
            ('000003.SZ', '退市样本', '19910703', '20020614', '综合'),
        ])
        paused = _stock_basic([
            ('000004.SZ', '暂停样本', '19910114', None, '软件'),
        ])
        pro = _stub_pro(trade_cal_df=_trade_cal(['20210104']))
        pro.stock_basic.side_effect = [listed, delisted, paused]

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        calls = [call.kwargs['list_status'] for call in pro.stock_basic.call_args_list]
        self.assertEqual(calls, ['L', 'D', 'P'])
        frame = pd.read_csv(
            self.cache_root / 'stock_basic.csv',
            dtype={'ts_code': str, 'list_status': str},
        )
        self.assertEqual(set(frame['ts_code']), {'000001.SZ', '000003.SZ', '000004.SZ'})
        self.assertEqual(set(frame['list_status']), {'L', 'D', 'P'})
        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertEqual(meta['stock_basic_statuses_done'], ['D', 'L', 'P'])

    def test_legacy_listed_only_meta_triggers_stock_basic_repair(self):
        self.cache_root.mkdir(parents=True, exist_ok=True)
        _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ]).to_csv(self.cache_root / 'stock_basic.csv', index=False)
        (self.cache_root / '_meta.json').write_text(json.dumps({
            'stock_basic_done': True,
            'stock_basic_statuses_done': ['L'],
        }))
        listed = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
        ])
        delisted = _stock_basic([
            ('000003.SZ', '退市样本', '19910703', '20020614', '综合'),
        ])
        pro = _stub_pro(trade_cal_df=_trade_cal([]))
        pro.stock_basic.side_effect = [listed, delisted, _stock_basic([])]

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )

        repaired = pd.read_csv(self.cache_root / 'stock_basic.csv', dtype={'ts_code': str})
        self.assertIn('000003.SZ', set(repaired['ts_code']))

    def test_writes_fina_indicator_per_code(self):
        sb = _stock_basic([
            ('000001.SZ', '平安银行', '19910403', None, '银行'),
            ('000002.SZ', '万科A', '19910129', None, '房地产'),
        ])
        fake_fina = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'ann_date': ['20210330'],
            'end_date': ['20201231'],
            'roe': [10.5],
            'grossprofit_margin': [40.0],
            'debt_to_assets': [92.3],
            'netprofit_yoy': [3.5],
        })
        pro = _stub_pro(
            stock_basic_df=sb,
            fina_df=fake_fina,
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        for code in ('000001.SZ', '000002.SZ'):
            self.assertTrue((self.cache_root / 'fina_indicator' / f'{code}.csv').exists())

    def test_writes_adj_factor_per_code(self):
        sb = _stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')])
        fake_adj = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20210104'],
            'adj_factor': [1.0],
        })
        pro = _stub_pro(
            stock_basic_df=sb,
            adj_df=fake_adj,
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        self.assertTrue((self.cache_root / 'adj_factor' / '000001.SZ.csv').exists())

    def test_writes_index_weight_per_month(self):
        sb = _stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')])
        fake_iw = pd.DataFrame({
            'index_code': ['000300.SH', '000300.SH'],
            'con_code': ['000001.SZ', '000002.SZ'],
            'trade_date': ['20210101', '20210101'],
            'weight': [0.5, 0.5],
        })
        pro = _stub_pro(
            stock_basic_df=sb,
            index_weight_df=fake_iw,
            trade_cal_df=_trade_cal(['20210104', '20210204']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 2, 4),
                cache_root=self.cache_root,
            )
        # Should have one snapshot per (index, month)
        self.assertTrue((self.cache_root / 'index_weight' / '000300_2021-01.csv').exists())
        self.assertTrue((self.cache_root / 'index_weight' / '000300_2021-02.csv').exists())
        self.assertTrue((self.cache_root / 'index_weight' / '000905_2021-01.csv').exists())
        self.assertTrue((self.cache_root / 'index_weight' / '000905_2021-02.csv').exists())

    def test_index_weight_fetch_uses_prior_window_and_latest_snapshot(self):
        pro = MagicMock()
        pro.index_weight.return_value = pd.DataFrame({
            "index_code": ["000905.SH", "000905.SH", "000905.SH"],
            "con_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "trade_date": ["20201231", "20201231", "20200930"],
            "weight": [0.6, 0.4, 1.0],
        })

        data_prep._fetch_index_weight(
            pro,
            "000905.SH",
            "000905",
            date(2021, 1, 1),
            self.cache_root,
        )

        pro.index_weight.assert_called_once_with(
            index_code="000905.SH",
            start_date=(date(2021, 1, 1) - timedelta(days=95)).strftime("%Y%m%d"),
            end_date="20210101",
        )
        written = pd.read_csv(
            self.cache_root / "index_weight" / "000905_2021-01.csv",
            dtype={"con_code": str, "trade_date": str},
        )
        self.assertEqual(set(written["con_code"]), {"000001.SZ", "000002.SZ"})
        self.assertEqual(set(written["trade_date"]), {"20201231"})

    def test_writes_dedicated_benchmark_history(self):
        benchmark = pd.DataFrame({
            "ts_code": ["000300.SH", "000300.SH"],
            "trade_date": ["20210105", "20210104"],
            "close": [5010.0, 5000.0],
        })
        pro = _stub_pro(
            stock_basic_df=_stock_basic([]),
            index_daily_df=benchmark,
            trade_cal_df=_trade_cal(["20210104", "20210105"]),
        )

        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 5),
                cache_root=self.cache_root,
            )

        for code in ("000300", "000905"):
            path = self.cache_root / "benchmark_daily" / f"{code}.csv"
            self.assertTrue(path.exists())
            saved = pd.read_csv(path, dtype={"trade_date": str, "ts_code": str})
            self.assertEqual(saved["trade_date"].tolist(), ["20210104", "20210105"])
        self.assertEqual(pro.index_daily.call_count, 2)

    def test_writes_trade_cal(self):
        pro = _stub_pro(trade_cal_df=_trade_cal(['20210104', '20210105']))
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 5),
                cache_root=self.cache_root,
            )
        out = self.cache_root / 'trade_cal.csv'
        self.assertTrue(out.exists())
        df = pd.read_csv(out, dtype={'cal_date': str})
        self.assertIn('20210104', df['cal_date'].tolist())

    def test_meta_progress_saved(self):
        sb = _stock_basic([('000001.SZ', '平安银行', '19910403', None, '银行')])
        pro = _stub_pro(
            stock_basic_df=sb,
            daily_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
                'open': [10.0], 'high': [10.1], 'low': [9.9], 'close': [10.0],
                'vol': [1_000.0], 'amount': [10_000.0],
            }),
            daily_basic_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
            }),
            fina_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'ann_date': ['20210401'],
                'end_date': ['20201231'],
            }),
            adj_df=pd.DataFrame({
                'ts_code': ['000001.SZ'], 'trade_date': ['20210104'],
                'adj_factor': [1.0],
            }),
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
            )
        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertIn('2021-01-04', meta['daily_dates_done'])
        self.assertIn('2021-01-04', meta['daily_basic_dates_done'])
        self.assertTrue(meta['stock_basic_done'])
        self.assertIn('000001.SZ', meta['fina_codes_done'])
        self.assertIn('000001.SZ', meta['adj_factor_codes_done'])

    def test_force_reruns_even_if_already_done(self):
        # Pre-populate
        meta_path = self.cache_root / '_meta.json'
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps({
            'daily_dates_done': ['2021-01-04'],
            'daily_basic_dates_done': ['2021-01-04'],
            'fina_codes_done': [],
            'adj_factor_codes_done': [],
            'index_weight_months_done': [],
            'stock_basic_done': True,
        }))
        _stock_basic([]).to_csv(self.cache_root / 'stock_basic.csv', index=False)

        pro = _stub_pro(
            daily_df=pd.DataFrame({'ts_code': ['000001.SZ'], 'close': [10.0]}),
            trade_cal_df=_trade_cal(['20210104']),
        )
        with patch('stock_analyze.markets.a_share.backtest.data_prep._make_pro_client', return_value=pro):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4), end=date(2021, 1, 4),
                cache_root=self.cache_root,
                force=True,
            )
        # With force=True, daily was re-called
        pro.daily.assert_called()

    def test_historical_index_union_limits_code_scoped_endpoints(self):
        stock_basic = _stock_basic([
            ('000001.SZ', '成分一', '19910403', None, '银行'),
            ('000002.SZ', '成分二', '19910129', None, '地产'),
            ('000003.SZ', '非成分', '19910703', None, '制造'),
        ])
        index_weight = pd.DataFrame({
            'index_code': ['000300.SH', '000300.SH'],
            'con_code': ['000001.SZ', '000002.SZ'],
            'trade_date': ['20201231', '20201231'],
            'weight': [0.6, 0.4],
        })
        pro = _stub_pro(
            stock_basic_df=stock_basic,
            index_weight_df=index_weight,
            fina_df=pd.DataFrame(columns=['ts_code', 'ann_date', 'end_date']),
            adj_df=pd.DataFrame(columns=['ts_code', 'trade_date', 'adj_factor']),
        )

        with patch(
            'stock_analyze.markets.a_share.backtest.data_prep._make_pro_client',
            return_value=pro,
        ):
            summary = data_prep.prepare_backtest_data(
                start=date(2021, 1, 1),
                end=date(2021, 1, 31),
                cache_root=self.cache_root,
                phases={'universe', 'fundamentals', 'adjustments'},
                code_scope='historical-index-union',
            )

        fina_codes = {call.kwargs['ts_code'] for call in pro.fina_indicator.call_args_list}
        adj_codes = {call.kwargs['ts_code'] for call in pro.adj_factor.call_args_list}
        self.assertEqual(fina_codes, {'000001.SZ', '000002.SZ'})
        self.assertEqual(adj_codes, {'000001.SZ', '000002.SZ'})
        self.assertEqual(summary['scope_codes'], 2)
        self.assertEqual(summary['batch_codes'], 2)

    def test_code_batch_slice_is_deterministic(self):
        stock_basic = _stock_basic([
            ('000003.SZ', '三', '19910703', None, '制造'),
            ('000001.SZ', '一', '19910403', None, '银行'),
            ('000002.SZ', '二', '19910129', None, '地产'),
        ])
        pro = _stub_pro(
            stock_basic_df=stock_basic,
            fina_df=pd.DataFrame(columns=['ts_code', 'ann_date', 'end_date']),
        )

        with patch(
            'stock_analyze.markets.a_share.backtest.data_prep._make_pro_client',
            return_value=pro,
        ):
            summary = data_prep.prepare_backtest_data(
                start=date(2021, 1, 1),
                end=date(2021, 1, 31),
                cache_root=self.cache_root,
                phases={'universe', 'fundamentals'},
                code_offset=1,
                code_limit=1,
            )

        pro.fina_indicator.assert_called_once_with(
            ts_code='000002.SZ',
            start_date='20210101',
            end_date='20210131',
            fields=(
                'ts_code,ann_date,end_date,roe,grossprofit_margin,'
                'debt_to_assets,netprofit_yoy,roic,netprofit_margin,'
                'current_ratio,quick_ratio,assets_turn,q_sales_yoy,q_op_qoq,ocf_yoy'
            ),
        )
        for endpoint in ('income', 'balancesheet', 'cashflow'):
            method = getattr(pro, endpoint)
            method.assert_called_once()
            self.assertEqual(method.call_args.kwargs['ts_code'], '000002.SZ')
            self.assertEqual(method.call_args.kwargs['start_date'], '20210101')
            self.assertEqual(method.call_args.kwargs['end_date'], '20210131')
        self.assertEqual(summary['scope_codes'], 3)
        self.assertEqual(summary['batch_codes'], 1)

    def test_statements_phase_skips_existing_fina_indicator_work(self):
        pro = _stub_pro(
            stock_basic_df=_stock_basic([
                ('000001.SZ', '一', '19910403', None, '银行'),
            ]),
        )

        with (
            patch(
                'stock_analyze.markets.a_share.backtest.data_prep._make_pro_client',
                return_value=pro,
            ),
            patch('stock_analyze.markets.a_share.backtest.data_prep._throttle'),
        ):
            summary = data_prep.prepare_backtest_data(
                start=date(2018, 1, 1),
                end=date(2026, 8, 7),
                cache_root=self.cache_root,
                phases={'statements'},
                code_limit=1,
            )

        pro.fina_indicator.assert_not_called()
        for endpoint in ('income', 'balancesheet', 'cashflow'):
            getattr(pro, endpoint).assert_called_once()
        self.assertEqual(summary['phases'], ['statements'])
        self.assertEqual(summary['batch_codes'], 1)

    def test_namechange_ranges_merge_and_preserve_history(self):
        path = self.cache_root / 'namechange' / '000001.SZ.csv'
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'name': ['旧名称'],
            'start_date': ['20100101'],
            'end_date': ['20191231'],
            'ann_date': ['20100101'],
            'change_reason': ['更名'],
        }).to_csv(path, index=False)
        pro = _stub_pro(namechange_df=pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'name': ['新名称'],
            'start_date': ['20200101'],
            'end_date': [None],
            'ann_date': ['20200101'],
            'change_reason': ['更名'],
        }))

        self.assertTrue(data_prep._fetch_namechange(pro, '000001.SZ', self.cache_root))

        persisted = pd.read_csv(path, dtype={'start_date': str, 'end_date': str})
        self.assertEqual(set(persisted['name']), {'旧名称', '新名称'})

    def test_valid_empty_suspend_date_is_marked_complete(self):
        pro = _stub_pro(
            trade_cal_df=_trade_cal(['20210104']),
            suspend_df=pd.DataFrame(columns=[
                'ts_code', 'trade_date', 'suspend_timing', 'suspend_type',
            ]),
        )

        with patch(
            'stock_analyze.markets.a_share.backtest.data_prep._make_pro_client',
            return_value=pro,
        ):
            data_prep.prepare_backtest_data(
                start=date(2021, 1, 4),
                end=date(2021, 1, 4),
                cache_root=self.cache_root,
                phases={'calendar', 'status'},
                status_provider='tushare',
            )

        meta = json.loads((self.cache_root / '_meta.json').read_text())
        self.assertIn('2021-01-04', meta['suspend_dates_done'])
        written = pd.read_csv(self.cache_root / 'suspend_d' / '2021-01-04.csv')
        self.assertEqual(
            list(written.columns),
            ['ts_code', 'trade_date', 'suspend_timing', 'suspend_type'],
        )

    def test_baostock_status_fallback_preserves_source_provenance(self):
        class FakeResult:
            error_code = '0'
            error_msg = ''
            fields = ['date', 'code', 'tradestatus', 'isST']

            def __init__(self):
                self._rows = iter([
                    ['2021-01-04', 'sz.000001', '1', '0'],
                    ['2021-01-05', 'sz.000001', '0', '1'],
                ])
                self._current = None

            def next(self):
                self._current = next(self._rows, None)
                return self._current is not None

            def get_row_data(self):
                return self._current

        baostock = MagicMock()
        baostock.query_history_k_data_plus.return_value = FakeResult()

        ok = data_prep._fetch_baostock_status(
            baostock,
            '000001.SZ',
            date(2021, 1, 4),
            date(2021, 1, 5),
            self.cache_root,
        )

        self.assertTrue(ok)
        saved = pd.read_csv(
            self.cache_root / 'baostock_status' / '000001.SZ.csv',
            dtype={'ts_code': str, 'trade_date': str},
        )
        self.assertEqual(set(saved['st_source']), {'baostock_history_isST_v1'})
        self.assertEqual(saved['trade_date'].tolist(), ['20210104', '20210105'])
        baostock.query_history_k_data_plus.assert_called_once_with(
            'sz.000001',
            'date,code,tradestatus,isST',
            start_date='2021-01-04',
            end_date='2021-01-05',
            frequency='d',
            adjustflag='3',
        )


if __name__ == '__main__':
    unittest.main()
