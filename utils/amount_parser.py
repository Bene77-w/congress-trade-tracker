"""
Amount-Parser fuer Congress-Trade-Disclosures.

Quiver/House/Senate liefern den Trade-Wert als String-Range. Beispiele:
  "$1,001 - $15,000"          -> (1001, 15000)
  "$15,001 - $50,000"         -> (15001, 50000)
  "Over $50,000,000"          -> (50_000_000, 50_000_000)
  "$94.91"                    -> (94, 94)             (Single-Wert)
  ""                          -> (0, 0)               (leer/NaN)

Edge-Cases:
  - "Transaction in a Spouse or Dependent Child Asset over $1,000,000"
    -> wir extrahieren alle USD-Werte -> (1_000_000, 1_000_000)
  - "$250,001-$500,000 - $100,001" (korrupt mit Doppel-Range)
    -> wir nehmen min/max ueber alle gefundenen Werte -> (100001, 500000)
  - "Over $50,000,000" (open-ended) -> konservativ (50M, 50M); Score-Logik
    behandelt das wie einen Punkt-Trade auf der Untergrenze.
  - Dezimale wie "$94.91" werden auf int abgeschnitten (Trade-Volumen wird
    in ganzen USD gefuehrt; sub-Dollar-Trades sind in der Praxis vernachlaessigbar).
"""

import re
from typing import Tuple


_AMOUNT_PATTERN = re.compile(r'\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)')


def _to_int(token: str) -> int:
    """Wandelt einen Token wie '15,001' oder '94.91' in int um (abschneiden)."""
    cleaned = token.replace(',', '')
    return int(float(cleaned))


def parse_amount_range(amount_str) -> Tuple[int, int]:
    """Extrahiert (low, high) in USD aus einem Disclosure-Amount-String.

    Args:
        amount_str: Original-String aus Quiver/House/Senate. Akzeptiert auch
            None / NaN / leerer String / int / float.

    Returns:
        (low, high) als int-Tupel. Wenn nichts geparst werden kann: (0, 0).
        Bei Single-Werten: low == high.
    """
    if amount_str is None:
        return (0, 0)

    # NaN-Check ohne pandas-Dependency (float('nan') != float('nan'))
    if isinstance(amount_str, float) and amount_str != amount_str:
        return (0, 0)

    if isinstance(amount_str, (int, float)):
        v = int(amount_str)
        return (v, v)

    s = str(amount_str).strip()
    if not s:
        return (0, 0)

    matches = _AMOUNT_PATTERN.findall(s)
    if not matches:
        return (0, 0)

    values = []
    for token in matches:
        try:
            values.append(_to_int(token))
        except (ValueError, TypeError):
            continue

    if not values:
        return (0, 0)

    return (min(values), max(values))


def midpoint_usd(amount_str) -> int:
    """Mittelwert der parse_amount_range, abgerundet auf ganze USD.

    Beispiel:
        '$1,001 - $15,000' -> (1001+15000)//2 = 8000
        '$94.91'           -> 94
        ''                 -> 0
    """
    low, high = parse_amount_range(amount_str)
    return (low + high) // 2


def max_usd(amount_str) -> int:
    """Obergrenze der parse_amount_range in ganzen USD.

    Wird vom Trade-Volume-Signal als Score-Basis genutzt: der "im
    schlimmsten Fall hat der Member bis hierhin gehandelt"-Wert.

    Beispiele:
        '$1,001 - $15,000' -> 15000
        '$15,001'          -> 15001
        'Over $50,000,000' -> 50_000_000   (open-ended, konservativ)
        ''                 -> 0
    """
    _, high = parse_amount_range(amount_str)
    return high
