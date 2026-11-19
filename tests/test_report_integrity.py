"""scripts/report_integrity.py: Eingaben werden nicht still verfaelscht."""

import pandas as pd
import pytest

from scripts.report_integrity import MASTER_COLUMNS, report


def _master(tmp_path):
    path = tmp_path / 'master.csv'
    row = {c: '' for c in MASTER_COLUMNS}
    row.update(transaction_date='2020-01-02', ticker='AAPL', ticker_type='stock',
               asset_class_excluded='False', trade_score=0,
               signals_triggered='accumulative')
    pd.DataFrame([row]).to_csv(path, index=False)
    return path


def _cache(tmp_path):
    path = tmp_path / 'prices.parquet'
    pd.DataFrame({'ticker': ['AAPL'], 'date': [pd.Timestamp('2020-01-02')],
                  'close': [1.0]}).set_index(['ticker', 'date']).to_parquet(path)
    return path


def test_fehlender_kurs_cache_bricht_ab(tmp_path):
    with pytest.raises(FileNotFoundError, match='Kurs-Cache'):
        report(_master(tmp_path), tmp_path / 'q.xlsx', tmp_path / 'fehlt.parquet')


def test_xlsx_leer_und_na_text_sind_verschiedene_zeilen(tmp_path, capsys):
    xlsx = tmp_path / 'q.xlsx'
    pd.DataFrame({'a': ['x', 'x', 'x'], 'b': [None, 'N/A', 'N/A']}).to_excel(
        xlsx, index=False)
    report(_master(tmp_path), xlsx, _cache(tmp_path))
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if 'identische Zeilen' in l)
    assert line.split()[-1] == '1'
