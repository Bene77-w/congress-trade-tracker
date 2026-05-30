"""Zentrale TickerType-Sets fuer yfinance-Routing-Entscheidungen.

Jede Stage, die yfinance konsumiert, importiert ihr Set von hier. Die
Sets sind bewusst getrennt, weil verschiedene Stages unterschiedliche
TickerType-Anforderungen haben.
"""

from utils.ticker_classifier import TickerType


# yfinance-Sektor/Industry-Lookup (utils/ticker_sector.py).
# Welche Ticker werden bei der Sektor-Anreicherung an yfinance geschickt.
SECTOR_LOOKUP_TYPES = frozenset({
    TickerType.STOCK,
    TickerType.SHARECLASS,
    TickerType.FOREIGN_STOCK,
    TickerType.ETF,
    TickerType.NUMERIC_SUFFIX,
})


# yfinance-Post-Trade-Preis-Historie (scoring/post_trade_returns.py).
# NUMERIC_SUFFIX bewusst ausgeschlossen: Reverse-Split-/Reorg-Symbole
# liefern Preise, die nicht zum heutigen Listing passen.
POST_TRADE_PRICE_TYPES = frozenset({
    TickerType.STOCK,
    TickerType.SHARECLASS,
    TickerType.FOREIGN_STOCK,
    TickerType.ETF,
})
