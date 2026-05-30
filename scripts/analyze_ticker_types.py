"""
Analysiert Ticker-Types in trades_master_complete.csv und zeigt erkannte Typen
"""

import pandas as pd
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(os.path.join(__file__, '..')))
sys.path.insert(0, BASE_DIR)
from config import settings  # noqa: E402

# Erkannte Sicherheits-Typen vom OpenFIGI API
RECOGNIZED_SECURITY_TYPES = {
    'Stock': 'Reguläre Aktie',
    'ETF': 'Exchange Traded Fund',
    'Mutual Fund': 'Investmentfonds',
    'Depositary Receipt': 'Hinterlegungsschein (z.B. ADR)',
    'Warrant': 'Optionsschein',
    'Partnership Shares': 'Partnership-Anteile',
    'Bond/CUSIP': 'Anleihe (CUSIP-identifiziert)',
    'Bond (Maturity)': 'Anleihe (mit Verfallsdatum)',
    'T-Bill': 'US Treasury Bill',
    'SEDOL': 'UK/Irland Wertpapier',
    'Foreign Stock': 'Ausländische Aktie',
    'Index': 'Index',
    'Option': 'Derivat-Option',
    'Crypto': 'Kryptowährung',
    'SPAC Unit': 'SPAC-Einheit',
    'Other': 'Sonstiges',
}

# ============================================================================
# 1. Ticker-Types in trades_master_complete.csv
# ============================================================================

csv_file = os.path.join(BASE_DIR, 'data', 'processed', settings.TRADES_MASTER_COMPLETE_CSV)
print("=" * 80)
print("1. TICKER-TYPES IN trades_master_complete.csv")
print("=" * 80)

df = pd.read_csv(csv_file, low_memory=False)

# Ticker-Types zählen
ticker_types = df['ticker_type'].value_counts(dropna=False)
print(f"\nGesamt Trades: {len(df):,}")
print(f"Unique Ticker-Types: {ticker_types.shape[0]}\n")

for ttype, count in ticker_types.items():
    pct = (count / len(df)) * 100
    description = RECOGNIZED_SECURITY_TYPES.get(str(ttype), '???')
    ttype_str = str(ttype) if pd.notna(ttype) else '[NaN/Unknown]'
    print(f"  {ttype_str:20s} : {count:7,d} ({pct:5.2f}%)  — {description}")

# ============================================================================
# 2. Erkannte vs. Unerkannte Ticker-Typen
# ============================================================================

print("\n" + "=" * 80)
print("2. ERKANNTE vs. UNERKANNTE SICHERHEITS-TYPEN")
print("=" * 80)

recognized_count = df[df['ticker_type'].isin(RECOGNIZED_SECURITY_TYPES.keys())].shape[0]
unknown_count = df[df['ticker_type'] == 'Unknown'].shape[0]
unclassified_count = df[df['ticker_type'].isna()].shape[0]

print(f"\nErkannte Sicherheits-Typen:  {recognized_count:7,d} ({(recognized_count/len(df))*100:5.2f}%)")
print(f"Klassifiziert als Unknown:   {unknown_count:7,d} ({(unknown_count/len(df))*100:5.2f}%)")
print(f"Nicht klassifiziert (NaN):   {unclassified_count:7,d} ({(unclassified_count/len(df))*100:5.2f}%)")

# ============================================================================
# 3. Alle einzigartigen Ticker mit ihren Types
# ============================================================================

print("\n" + "=" * 80)
print("3. ALLE EINZIGARTIGEN TICKER (gruppiert nach Type)")
print("=" * 80)

unique_tickers = df[['ticker', 'ticker_type']].drop_duplicates().sort_values('ticker')
print(f"\nGesamt einzigartige Ticker: {len(unique_tickers):,}\n")

# Sortierte Ausgabe nach erkannten Typen
for ttype in sorted(RECOGNIZED_SECURITY_TYPES.keys()):
    tickers_of_type = unique_tickers[unique_tickers['ticker_type'] == ttype]
    if len(tickers_of_type) > 0:
        print(f"\n{ttype} ({len(tickers_of_type):,} Ticker):")
        ticker_list = sorted(tickers_of_type['ticker'].unique())
        # Spalten-Format (bis zu 5 pro Zeile)
        for i in range(0, len(ticker_list), 5):
            chunk = ticker_list[i:i+5]
            print(f"  {', '.join(chunk)}")

# Unknown + NaN
unknown_types = unique_tickers[~unique_tickers['ticker_type'].isin(RECOGNIZED_SECURITY_TYPES.keys())]
if len(unknown_types) > 0:
    print(f"\n[UNBEKANNT / NICHT KLASSIFIZIERT] ({len(unknown_types):,} Ticker):")
    for ttype_val in unknown_types['ticker_type'].unique():
        tickers = sorted(unknown_types[unknown_types['ticker_type'] == ttype_val]['ticker'].unique())
        ttype_str = str(ttype_val) if pd.notna(ttype_val) else 'NaN'
        print(f"\n  Typ: {ttype_str} ({len(tickers):,} Ticker)")
        for i in range(0, len(tickers), 5):
            chunk = tickers[i:i+5]
            print(f"    {', '.join(chunk)}")

# ============================================================================
# 4. Zusammenfassung
# ============================================================================

print("\n" + "=" * 80)
print("4. ERKANNTE SICHERHEITS-TYPEN (OpenFIGI)")
print("=" * 80)

print("\nDas Programm kann folgende Sicherheits-Typen erkennen:\n")
for i, (ttype, description) in enumerate(sorted(RECOGNIZED_SECURITY_TYPES.items()), 1):
    print(f"  {i:2d}. {ttype:20s} — {description}")

print("\n" + "=" * 80)
