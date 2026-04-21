"""
Klassifiziert alle Ticker in congress-trading-all.xlsx nach Wertpapierart.
Nutzt Regex-basierte Erkennung gemäß wertpapiere_ticker_referenz.md.
"""

import re
import openpyxl
from collections import defaultdict
from pathlib import Path

XLSX_PATH = Path(__file__).parent.parent / "data" / "raw" / "congress-trading-all.xlsx"


def classify_ticker(ticker: str) -> str:
    t = ticker.strip().upper()

    if not t or t == '--':
        return 'LEER/UNGUELTIG'

    if re.match(r'^\^', t):
        return 'YIELD_INDEX'
    if re.match(r'^9128[A-Z0-9]{5}$', t):
        return 'CUSIP_TREASURY'
    if re.match(r'^[A-Z0-9]{9}$', t):
        return 'CUSIP'
    if re.match(r'^[A-Z]{2}[A-Z0-9]{9}[0-9]$', t):
        return 'ISIN'
    if re.match(r'^[A-Z]+\d{6}[CP]\d{8}$', t):
        return 'OPTION'
    if re.match(r'^(CRYPTO:)?[A-Z]+-USDT?$', t) or t.startswith('CRYPTO:'):
        return 'CRYPTO'
    if t in ('BTC', 'ETH', 'BITCOIN', 'SOLANA', 'RIPPLE', 'ETHEREUM'):
        return 'CRYPTO'
    if re.match(r'^[A-Z]{4}X$', t):
        return 'MUTUAL_FUND'
    # Warrant: endet auf -W, aber NICHT -PA, -PB etc. (Vorzugsaktien)
    if re.match(r'^[A-Z]{3,}-W$', t):
        return 'WARRANT'
    if re.match(r'^[A-Z]{1,4}[FGHJKMNQUVXZ]\d{2}$', t):
        return 'FUTURE'

    return 'LOOKUP_REQUIRED'


def main():
    print(f"Lade {XLSX_PATH} ...")
    wb = openpyxl.load_workbook(XLSX_PATH, read_only=True)
    ws = wb['Sheet1']

    # Sammle alle Ticker mit ihrem Quiver-TickerType
    ticker_data = []  # (ticker, quiver_type)
    for row in ws.iter_rows(min_row=2, values_only=True):
        ticker = row[0]
        quiver_type = row[1] if row[1] else ''
        if ticker:
            ticker_data.append((str(ticker).strip(), quiver_type.strip()))

    print(f"Total Trades: {len(ticker_data)}")
    print()

    # Klassifiziere jeden Ticker
    results = defaultdict(lambda: {'count': 0, 'examples': set(), 'quiver_types': defaultdict(int)})

    for ticker, quiver_type in ticker_data:
        category = classify_ticker(ticker)
        results[category]['count'] += 1
        if len(results[category]['examples']) < 8:
            results[category]['examples'].add(ticker)
        results[category]['quiver_types'][quiver_type or '(leer)'] += 1

    # Ausgabe
    print("=" * 90)
    print(f"{'Wertpapierart':<22} {'Anzahl':>8}  {'Anteil':>7}  Beispiel-Ticker")
    print("=" * 90)

    for category, data in sorted(results.items(), key=lambda x: -x[1]['count']):
        pct = data['count'] / len(ticker_data) * 100
        examples = ', '.join(sorted(data['examples']))
        print(f"{category:<22} {data['count']:>8}  {pct:>6.1f}%  {examples}")

    print("=" * 90)
    print()

    # Detail: Quiver-TickerType pro Kategorie
    print("Detail: Quiver-TickerType-Verteilung pro Regex-Kategorie")
    print("-" * 70)
    for category, data in sorted(results.items(), key=lambda x: -x[1]['count']):
        print(f"\n  {category} ({data['count']}x):")
        for qt, cnt in sorted(data['quiver_types'].items(), key=lambda x: -x[1]):
            print(f"    {qt or '(leer)':<25} {cnt:>6}")


if __name__ == '__main__':
    main()
