"""Baut den Deployment-Snapshot des Dashboards aus dem Master-CSV.

EIGENSTAENDIGES Skript, keine Pipeline-Stage: es liest den fertigen Master
(``data/processed/trades_master_complete.csv``) und schreibt einen
eingefrorenen Datenstand fuer die oeffentliche Streamlit-Instanz:

  - ``data/deploy/trades_snapshot.parquet``   (Daten, gleiche Spalten wie der Master)
  - ``data/deploy/price_history.parquet``     (Kurs-Cache — ohne ihn bleiben alle
    Kursverlauf-Charts im Deployment leer)
  - ``data/deploy/reference/``                (Referenztabellen, die die App liest —
    ohne sie bleiben die Gremien-Tabellen im Member-Detail ohne Sektoren)
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
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import settings  # noqa: E402
from utils.reference_data import TABLES as REFERENCE_TABLES  # noqa: E402

# Dtype-Vertrag: dieselben Listen, die auch der CSV-Loader des Dashboards
# verwendet (config/settings.py). So liest die App aus dem Snapshot garantiert
# dieselben Typen wie aus dem Master-CSV.
DATE_COLS = list(settings.TRADES_DATE_COLUMNS)
STRING_COLS = list(settings.TRADES_STRING_COLUMNS)


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


def _copy_reference(reference_dir: Path, target: Path) -> int:
    """Kopiert die von ``ReferenceData`` gelesenen Tabellen nach ``target``."""
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for name in REFERENCE_TABLES:
        shutil.copy2(reference_dir / f'{name}.csv', target / f'{name}.csv')
    return len(REFERENCE_TABLES)


def build_snapshot(source: Path, target: Path, manifest: Path,
                   price_cache: Path | None = None,
                   reference_dir: Path | None = None) -> dict:
    """Liest den Master, schreibt Snapshot + Manifest, liefert die Kennzahlen.

    ``price_cache``: Kurs-Cache, der neben den Snapshot gelegt wird. Das
    Dashboard zeichnet daraus die Kursverlaeufe in den Detail-Popups; fehlt
    er, zeigen die Popups nur den Hinweis 'keine Kursdaten'.

    ``reference_dir``: Referenztabellen (``settings.REFERENCE_DATA_PATH``),
    die als Ordner ``reference/`` neben den Snapshot gelegt werden.
    """
    if not source.exists():
        raise FileNotFoundError(f'Master-CSV nicht gefunden: {source}')

    # Nur auf Spalten anwenden, die der Master wirklich hat: eine fehlende
    # Datumsspalte wuerde read_csv sonst abbrechen lassen, statt den Snapshot
    # ohne sie zu bauen.
    header = pd.read_csv(source, nrows=0).columns
    df = pd.read_csv(
        source,
        parse_dates=[c for c in DATE_COLS if c in header],
        dtype={c: 'string' for c in STRING_COLS if c in header},
        low_memory=False,
    )
    mem_before = int(df.memory_usage(deep=True).sum())

    for col in _categorical_columns(df):
        df[col] = df[col].astype('category')
    mem_after = int(df.memory_usage(deep=True).sum())

    # Datenstand = juengstes Disclosure-Datum; das ist der Anker, an dem auch
    # die Zeitfenster des Dashboards haengen.
    data_as_of = None
    if 'disclosure_date' in df.columns:
        latest = pd.to_datetime(df['disclosure_date'], errors='coerce').max()
        if pd.notna(latest):
            data_as_of = latest.date().isoformat()

    # Kurs-Cache neben den Snapshot legen, damit ein einziger Ordner
    # (data/deploy/) alles enthaelt, was der Server braucht.
    target.parent.mkdir(parents=True, exist_ok=True)
    price_bytes = None
    if price_cache is not None and price_cache.is_file():
        price_target = target.parent / price_cache.name
        shutil.copy2(price_cache, price_target)
        price_bytes = int(price_target.stat().st_size)

    # Snapshot, Referenztabellen und Manifest erst vollstaendig als
    # temporaere Dateien vorbereiten, dann gemeinsam umbenennen: ein
    # abgebrochener Lauf hinterlaesst sonst ein halbes Parquet oder neue
    # Trades neben den Referenztabellen und dem Manifest des alten Stands.
    manifest.parent.mkdir(parents=True, exist_ok=True)
    reference_target = target.parent / Path(settings.DEPLOY_REFERENCE_PATH).name
    tmp_target = target.with_suffix(target.suffix + '.tmp')
    tmp_reference = reference_target.with_name(reference_target.name + '.tmp')
    tmp_manifest = manifest.with_suffix(manifest.suffix + '.tmp')
    try:
        df.to_parquet(tmp_target, compression='zstd', index=False)
        reference_tables = None
        if reference_dir is not None:
            reference_tables = _copy_reference(reference_dir, tmp_reference)

        info = {
            'built_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'source_file': source.name,
            'data_as_of': data_as_of,
            'rows': int(len(df)),
            'columns': int(len(df.columns)),
            'source_bytes': int(source.stat().st_size),
            'snapshot_bytes': int(tmp_target.stat().st_size),
            'memory_bytes_plain': mem_before,
            'memory_bytes_snapshot': mem_after,
            'price_cache_bytes': price_bytes,
            'reference_tables': reference_tables,
        }
        tmp_manifest.write_text(
            json.dumps(info, indent=2, sort_keys=True), encoding='utf-8'
        )

        tmp_target.replace(target)
        if reference_tables is not None:
            shutil.rmtree(reference_target, ignore_errors=True)
            tmp_reference.rename(reference_target)
        tmp_manifest.replace(manifest)
    finally:
        tmp_target.unlink(missing_ok=True)
        shutil.rmtree(tmp_reference, ignore_errors=True)
        tmp_manifest.unlink(missing_ok=True)
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
    parser.add_argument(
        '--price-cache', type=Path,
        default=BASE_DIR / settings.POST_TRADE_PRICE_CACHE,
        help=f'Kurs-Cache (Default: {settings.POST_TRADE_PRICE_CACHE})',
    )
    parser.add_argument(
        '--reference', type=Path,
        default=BASE_DIR / settings.REFERENCE_DATA_PATH,
        help=f'Referenztabellen (Default: {settings.REFERENCE_DATA_PATH})',
    )
    parser.add_argument(
        '--skip-price-cache', action='store_true',
        help='Kurs-Cache nicht mitkopieren (Kursverlaeufe bleiben dann leer)',
    )
    args = parser.parse_args()

    try:
        info = build_snapshot(
            args.source, args.target, args.manifest,
            price_cache=None if args.skip_price_cache else args.price_cache,
            reference_dir=args.reference,
        )
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Snapshot geschrieben: {args.target}")
    print(f"  Datenstand   : {info['data_as_of']}  ({info['rows']} Trades, "
          f"{info['columns']} Spalten)")
    print(f"  Dateigroesse : {info['source_bytes'] / 1e6:.0f} MB CSV -> "
          f"{info['snapshot_bytes'] / 1e6:.1f} MB Parquet")
    print(f"  Arbeitsspeicher: {info['memory_bytes_plain'] / 1e6:.0f} MB -> "
          f"{info['memory_bytes_snapshot'] / 1e6:.0f} MB")
    if info['price_cache_bytes']:
        print(f"  Kurs-Cache   : {info['price_cache_bytes'] / 1e6:.0f} MB "
              f"mitkopiert")
    else:
        print('  Kurs-Cache   : NICHT mitkopiert — Kursverlauf-Charts bleiben '
              'im Deployment leer')
    print(f"  Referenz     : {info['reference_tables']} Tabellen mitkopiert")


if __name__ == '__main__':
    main()
