"""Tests for backtest data preparation.

These tests mock the Tushare client; no live network or token required.
"""
from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pandas as pd

from stock_analyze.markets.a_share.backtest import data_prep


def _stub_pro(daily_df=None, daily_basic_df=None, stock_basic_df=None,
              fina_df=None, adj_df=None, index_weight_df=None,
              trade_cal_df=None):
    """Build a MagicMock that mimics a Tushare pro_api instance."""
    pro = MagicMock()
    pro.daily.return_value = daily_df if daily_df is not None else pd.DataFrame()
    pro.daily_basic.return_value = daily_basic_df if daily_basic_df is not None else pd.DataFrame()
    pro.stock_basic.return_value = stock_basic_df if stock_basic_df is not None else pd.DataFrame()
    pro.fina_indicator.return_value = fina_df if fina_df is not None else pd.DataFrame()
    pro.adj_factor.return_value = adj_df if adj_df is not None else pd.DataFrame()
    pro.index_weight.return_value = index_weight_df if index_weight_df is not None else pd.DataFrame()
    pro.trade_cal.return_value = trade_cal_df if trade_cal_df is not None else pd.DataFrame()
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

    def test_idempotent_skips_existing_daily(self):
        """Already-fetched dates should NOT trigger pro.daily a second time."""
        # Pre-populate one date as "already fetched"
        existing = self.cache_root / 'daily' / '2021-01-04.csv'
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text('ts_code,open,close\n000001.SZ,10,10.5\n')
        # And the same for daily_basic so the prep won't need to re-fetch
        db_existing = self.cache_root / 'daily_basic' / '2021-01-04.csv'
        db_existing.parent.mkdir(parents=True, exist_ok=True)
        db_existing.write_text('ts_code,pe,pb\n000001.SZ,5.5,1.1\n')
        meta_path = self.cache_root / '_meta.json'
        meta_path.write_text(json.dumps({
            'daily_dates_done': ['2021-01-04'],
            'daily_basic_dates_done': ['2021-01-04'],
            'fina_codes_done': [],
            'adj_factor_codes_done': [],
            'index_weight_months_done': [],
            'stock_basic_done': True,
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


if __name__ == '__main__':
    unittest.main()
