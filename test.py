import yfinance as yf
import json

# YF Industries aus JSON
with open("data/mappings/yf_sectors_with_industries.json") as f:
    yf_data = json.load(f)
known = set(ind for s in yf_data["sectors"] for ind in s["industries"])

# Testticker
for ticker_sym in ["AAPL", "XOM", "JPM", "LMT", "UNH"]:
    info = yf.Ticker(ticker_sym).info
    ind = info.get("industry", "N/A")
    match = "✓" if ind in known else "✗ KEIN MATCH"
    print(f"{ticker_sym}: {ind} {match}")