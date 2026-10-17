"""Baut data/processed/ticker_liste_index.csv aus delisted_trades_stage.csv.

Helfer-Index fuer manuelle Recherchen (organisation/CLI_Bedienungsanleitung.md,
Ticker-Review): pro Ticker der erste Treffer (aeltester Trade) mit asset_name
+ transaction_date. Reine Recherchehilfe — keine Pipeline-Funktion.

Aufruf (cwd-unabhaengig): python scripts/build_ticker_liste_index.py
Historie: lag als script.py im Repo-Root; im Debug-Intervall 4 hierher
verschoben und repo-root-verankert.
"""

import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

input_pfad = BASE_DIR / 'data' / 'processed' / 'delisted_trades_stage.csv'
output_pfad = BASE_DIR / 'data' / 'processed' / 'ticker_liste_index.csv'

# Dictionary speichert den ersten Treffer: { 'ticker': {'asset_name': '...', 'transaction_date': '...'} }
ticker_daten = {}

try:
    with open(input_pfad, mode='r', encoding='utf-8') as datei:
        reader = csv.DictReader(datei)
        
        for zeile in reader:
            ticker = zeile.get('ticker')
            
            # Falls der Ticker noch nicht im Dictionary ist (= niedrigster Index / erstes Vorkommen)
            if ticker and ticker not in ticker_daten:
                ticker_daten[ticker] = {
                    'asset_name': zeile.get('asset_name'),
                    'transaction_date': zeile.get('transaction_date')
                }

    # Schreiben der neuen CSV
    with open(output_pfad, mode='w', encoding='utf-8', newline='') as datei_neu:
        fieldnames = ['ticker', 'asset_name', 'transaction_date']
        writer = csv.DictWriter(datei_neu, fieldnames=fieldnames)
        writer.writeheader()
        
        for t, daten in ticker_daten.items():
            # Zeile zusammenbauen
            row = {'ticker': t}
            row.update(daten)
            writer.writerow(row)

    print(f"Erfolg! Datei gespeichert unter: {output_pfad}")
    print(f"Anzahl der Zeilen (ohne Header): {len(ticker_daten)}")

except FileNotFoundError:
    print(f"Datei nicht gefunden: {input_pfad}")
except Exception as e:
    print(f"Fehler: {e}")
