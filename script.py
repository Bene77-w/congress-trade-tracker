import csv

input_pfad = 'data/processed/delisted_trades_stage.csv'
output_pfad = 'data/processed/ticker_liste_index.csv'

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
