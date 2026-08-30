"""Baut den Deployment-Snapshot des Dashboards aus dem Master-CSV.

EIGENSTAENDIGES Skript, keine Pipeline-Stage: es liest den fertigen Master
(``data/processed/trades_master_complete.csv``) und schreibt einen
eingefrorenen Datenstand fuer die oeffentliche Streamlit-Instanz:

  - ``data/deploy/trades_snapshot.parquet``   (Daten, gleiche Spalten wie der Master)
  - ``data/deploy/snapshot_manifest.json``    (Datenstand + Herkunft, fuer die UI)

Warum ueberhaupt ein Snapshot? Der Master-CSV ist ~645 MB gross und braucht
beim Laden ~626 MB RAM — zu viel fuer eine kleine Server-Instanz und zu gross
fuer ein Git-Repo. Zwei Hebel machen daraus ~15 MB Datei / ~50 MB RAM:

  1. Parquet statt CSV (spaltenweise, komprimiert).
  2. ``category``-Dtype fuer Wiederholungsspalten: ``Insider_Sectors`` etwa
     hat nur ~200 verschiedene Werte bei ~114k Zeilen, wird im CSV aber
     114k-mal ausgeschrieben.

Der App-Loader (``app/streamlit_app.py``) nimmt den Snapshot automatisch, wenn
die Datei existiert, sonst den Master-CSV. Lokal aendert sich also nichts,
solange kein Snapshot gebaut wurde.

Lauf: source venv/bin/activate && python scripts/build_deploy_snapshot.py
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings  # noqa: E402

# Muss identisch zum Loader in app/streamlit_app.py sein — sonst liest die App
# aus dem Snapshot andere Typen als aus dem CSV.
DATE_COLS = ['transaction_date', 'disclosure_date']


def _categorical_columns(df: pd.DataFrame) -> list[str]:
    """Textspalten mit wenigen verschiedenen Werten -> Kandidaten fuer category.

    Ausgeschlossen sind die Spalten, die der Score-Recompute im Dashboard neu
    schreibt: dort koennen Werte auftauchen, die nicht in den Kategorien
    stehen, was bei category-Dtype fehlschlagen wuerde.
    """
    cap = int(getattr(settings, 'DEPLOY_SNAPSHOT_CATEGORY_MAX_UNIQUE', 2000))
    excluded = set(getattr(settings, 'DEPLOY_SNAPSHOT_CATEGORY_EXCLUDE', ()))
    out: list[str] = []
    for col in df.columns:
        if col in excluded:
            continue
        if not (pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object):
            continue
        if df[col].nunique(dropna=False) <= cap:
            out.append(col)
    return out


def build_snapshot(source: Path, target: Path, manifest: Path) -> dict:
    """Liest den Master, schreibt Snapshot + Manifest, liefert die Kennzahlen."""
    if not source.exists():
        raise SystemExit(f'Master-CSV nicht gefunden: {source}')

    df = pd.read_csv(source, parse_dates=DATE_COLS, low_memory=False)
    mem_before = int(df.memory_usage(deep=True).sum())

    for col in _categorical_columns(df):
        df[col] = df[col].astype('category')
    mem_after = int(df.memory_usage(deep=True).sum())

    target.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(target, compression='zstd', index=False)

    # Datenstand = juengstes Disclosure-Datum; das ist der Anker, an dem auch
    # die Zeitfenster des Dashboards haengen.
    data_as_of = None
    if 'disclosure_date' in df.columns:
        latest = pd.to_datetime(df['disclosure_date'], errors='coerce').max()
        if pd.notna(latest):
            data_as_of = latest.date().isoformat()

    info = {
        'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source_file': source.name,
        'data_as_of': data_as_of,
        'rows': int(len(df)),
        'columns': int(len(df.columns)),
        'source_bytes': int(source.stat().st_size),
        'snapshot_bytes': int(target.stat().st_size),
        'memory_bytes_plain': mem_before,
        'memory_bytes_snapshot': mem_after,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(info, indent=2, sort_keys=True), encoding='utf-8'
    )
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--source', type=Path,
        default=BASE_DIR / 'data' / 'processed'
        / settings.TRADES_MASTER_COMPLETE_CSV,
        help='Master-CSV (Default: data/processed/trades_master_complete.csv)',
    )
    parser.add_argument(
        '--target', type=Path,
        default=BASE_DIR / settings.DEPLOY_SNAPSHOT_PATH,
        help=f'Ziel-Parquet (Default: {settings.DEPLOY_SNAPSHOT_PATH})',
    )
    parser.add_argument(
        '--manifest', type=Path,
        default=BASE_DIR / settings.DEPLOY_SNAPSHOT_MANIFEST,
        help=f'Ziel-Manifest (Default: {settings.DEPLOY_SNAPSHOT_MANIFEST})',
    )
    args = parser.parse_args()

    info = build_snapshot(args.source, args.target, args.manifest)
    print(f"Snapshot geschrieben: {args.target}")
    print(f"  Datenstand   : {info['data_as_of']}  ({info['rows']} Trades, "
          f"{info['columns']} Spalten)")
    print(f"  Dateigroesse : {info['source_bytes'] / 1e6:.0f} MB CSV -> "
          f"{info['snapshot_bytes'] / 1e6:.1f} MB Parquet")
    print(f"  Arbeitsspeicher: {info['memory_bytes_plain'] / 1e6:.0f} MB -> "
          f"{info['memory_bytes_snapshot'] / 1e6:.0f} MB")


if __name__ == '__main__':
    main()
