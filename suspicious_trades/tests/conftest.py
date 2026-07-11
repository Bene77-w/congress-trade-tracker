"""Offline fakes shared by the Suspicious-Trades B1 tests."""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def make_usage(
    *,
    prompt: int = 100,
    completion: int = 20,
    cache_read: int = 0,
    cache_write: int = 0,
    openai_cached: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
        prompt_tokens_details=SimpleNamespace(cached_tokens=openai_cached),
    )


def make_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_response(
    content: str = "ok",
    *,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = "stop",
    usage: Any | None = None,
) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage or make_usage(),
        model="mock/model",
    )


@pytest.fixture
def llm_settings(tmp_path):
    return {
        "AGENT_SPECS": {
            "worker": {
                "model": "anthropic/test-model",
                "effort": "high",
                "max_output_tokens": 128,
                "cache": True,
            },
            "plain": {
                "model": "deepseek/test-model",
                "effort": "none",
                "max_output_tokens": 64,
                "cache": False,
            },
        },
        "LLM_NUM_RETRIES": 2,
        "LLM_TIMEOUT_SECONDS": 60.0,
        "LLM_FALLBACK_ENABLED": False,
        "OPENROUTER_MODEL_MAP": {
            "anthropic/test-model": "openrouter/anthropic/test-model"
        },
        "PRICING": {
            "anthropic/test-model": {
                "in": 5.0,
                "out": 25.0,
                "cache_read": 0.5,
                "cache_write": 6.25,
            },
            "deepseek/test-model": {
                "in": 1.0,
                "out": 2.0,
                "cache_read": 0.1,
                "cache_write": 1.0,
            },
            "openrouter/anthropic/test-model": {
                "in": 5.0,
                "out": 25.0,
                "cache_read": 0.5,
                "cache_write": 6.25,
            },
        },
        "PRICING_PERIODS": {},
        "PRICING_TOKEN_TIERS": {},
        "COST_CAP_ENABLED": True,
        "COST_CAP_USD_PER_RUN": 20.0,
        "DATA_DIR": tmp_path,
    }


@pytest.fixture
def response_factory():
    return make_response


@pytest.fixture
def tool_call_factory():
    return make_tool_call


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(*parts: str) -> Any:
    """Liest eine eingefrorene JSON-Rohantwort aus ``tests/fixtures``."""

    with (FIXTURES_DIR / Path(*parts)).open(encoding="utf-8") as handle:
        return json.load(handle)


def load_fixture_text(*parts: str) -> str:
    """Liest eine eingefrorene Textrohantwort (z.B. RSS/XML) aus den Fixtures."""

    return (FIXTURES_DIR / Path(*parts)).read_text(encoding="utf-8")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def read_fixture():
    return load_fixture


@pytest.fixture
def read_fixture_text():
    return load_fixture_text


@pytest.fixture
def ingest_settings(tmp_path) -> dict[str, Any]:
    """Vollstaendige, netzfreie Ingestion-/Registry-Konfiguration fuer B2."""

    return {
        "INGEST_LOOKBACK_DAYS": 14,
        "INGEST_SOURCES_ENABLED": {
            "congress_gov": True,
            "federal_register": True,
            "war_gov_rss": True,
            "sam_gov": True,
        },
        "FEDREG_DOC_TYPES": ["RULE", "PRORULE"],
        "WAR_GOV_MIN_CONTRACT_USD": 50_000_000,
        "EVENT_CLOSE_AFTER_DAYS": 120,
        "PROCUREMENT_AWARD_TIMEOUT_DAYS": 180,
        "CONGRESS_CACHE_DIR": FIXTURES_DIR / "congress_gov",
        "DATA_DIR": tmp_path,
        "EVENTS_FILENAME": "events.jsonl",
        "INGEST_TIMEOUT_SECONDS": 30.0,
        "FEDERAL_REGISTER_API_URL": "https://example.invalid/fr",
        "FEDERAL_REGISTER_PER_PAGE": 200,
        "WAR_GOV_RSS_URL": "https://example.invalid/rss",
        "SAM_GOV_API_URL": "https://example.invalid/sam",
        "SAM_GOV_LIMIT": 100,
    }


# --------------------------------------------------------------------------
# B3-Fixtures: synthetischer Master, Staging-CSV, Preis-Fixture, Settings.
# --------------------------------------------------------------------------

# Feste Referenzdaten der B3-Fixtures (deterministische Handrechnungs-Goldwerte).
B3_TODAY = date(2025, 2, 1)
PRICE_START = date(2025, 1, 1)
PRICE_DAYS = 400


def _master_records() -> list[dict[str, Any]]:
    """~20 Master-Zeilen: Kauf/Verkauf, Mehrfach-Member, fehlende Sektoren."""

    rows: list[dict[str, Any]] = []
    members = [
        ("Alice Buyer", "House", "R"),
        ("Bob Seller", "Senate", "D"),
        ("Cara Multi", "House", "D"),
    ]
    # Alice: zwei Kaeufe.
    rows.append(("t1", members[0], "AAA", "Purchase", "2025-01-05", 15000, "Technology", "Software", "held"))
    rows.append(("t2", members[0], "BBB", "Purchase", "2025-01-20", 40000, "Energy", "Oil & Gas", "held"))
    # Bob: ein Verkauf, ein Kauf ohne Sektor.
    rows.append(("t3", members[1], "CCC", "Sale (Full)", "2025-01-10", 50000, "Health", "Biotech", "closed"))
    rows.append(("t4", members[1], "AAA", "Purchase", "2024-12-15", 12000, "", "", "held"))
    # Cara: drei Trades desselben Members, gemischte Richtung.
    rows.append(("t5", members[2], "BBB", "Purchase", "2025-01-25", 8000, "Energy", "Oil & Gas", "held"))
    rows.append(("t6", members[2], "CCC", "Sale (Partial)", "2025-01-28", 22000, "Health", "Biotech", "held"))
    rows.append(("t7", members[2], "DDD", "Purchase", "2025-01-30", 3000, "Finance", "Banks", "held"))
    # Fuellzeilen zur Groesse (~20).
    for i in range(8, 21):
        who = members[i % 3]
        tt = "Purchase" if i % 2 == 0 else "Sale (Full)"
        tkr = ["AAA", "BBB", "CCC"][i % 3]
        disc = (date(2025, 1, 1) + timedelta(days=i)).isoformat()
        sector = ["Technology", "Energy", "Health", ""][i % 4]
        rows.append((f"t{i}", who, tkr, tt, disc, 1000 * i, sector, sector + " Sub", "held"))

    records: list[dict[str, Any]] = []
    for trade_id, member, ticker, tt, disc, amount, sector, industry, exit_status in rows:
        name, chamber, party = member
        records.append(
            {
                "trade_id": trade_id,
                "name": name,
                "chamber": chamber,
                "Member_Party": party,
                "ticker": ticker,
                "issuer_ticker": ticker,
                "asset_name": f"{ticker} Corp",
                "transaction_type": tt,
                "transaction_date": disc,
                "disclosure_date": disc,
                "amount": str(amount),
                "sector_yf": sector,
                "industry_yf": industry,
                "Relevant_Sectors": sector,
                "committee_match": "",
                "Member_Committees_Full": f"{chamber} Committee on Finance",
                "exit_status": exit_status,
            }
        )
    return records


@pytest.fixture
def synthetic_master_df() -> pd.DataFrame:
    return pd.DataFrame(_master_records())


@pytest.fixture
def b3_settings(tmp_path, synthetic_master_df) -> dict[str, Any]:
    """Netzfreie, DATA_DIR-isolierte Settings fuer alle B3-Bausteine."""

    data_dir = tmp_path / "data_dir"
    data_dir.mkdir()
    master_path = tmp_path / "trades_master_complete.csv"
    synthetic_master_df.to_csv(master_path, index=False)
    return {
        "DATA_DIR": data_dir,
        "TRADES_MASTER_PATH": master_path,
        "PRICE_CACHE_PATH": tmp_path / "price_history.parquet",
        "STAGED_TRADES_DIRNAME": "staged_trades",
        "RUNS_DIRNAME": "runs",
        "OUTPUTS_DIRNAME": "outputs",
        "USER_HINTS_DIRNAME": "user_hints",
        "HINTS_FILENAME": "hints.jsonl",
        "RECOMMENDATIONS_FILENAME": "recommendations.csv",
        "RECOMMENDATION_DOC_FILENAME": "empfehlung.md",
        "REPORT_DOC_FILENAME": "bericht.md",
        "TRADE_SOURCE": "staging",
        "TRADE_DATE_FIELD": "disclosure_date",
        "TRADE_WINDOW_DAYS": 14,
        "EVAL_MODE": "outcome",
        "EVAL_OUTCOME_BUFFER_DAYS": 5,
        "EVAL_MAX_DAYS": 180,
        "EVAL_HORIZON_DAYS": 90,
        "SUMMARY_MAX_TOKENS": 1500,
        "USER_HINTS_ENABLED": True,
    }


def write_staging_file(
    directory: Path,
    filename: str,
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
) -> Path:
    """Schreibt eine Staging-CSV (Helfer fuer Reader-Tests)."""

    directory.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows)
    if columns is not None:
        frame = frame.reindex(columns=columns)
    path = directory / filename
    frame.to_csv(path, index=False)
    return path


@pytest.fixture
def staging_writer():
    return write_staging_file


def _build_price_lookup(closes_by_ticker: dict[str, list[float]]) -> dict[str, Any]:
    """Baut das ``{ticker: (dates64, closes64)}``-Format des Kurs-Cache nach."""

    dates = np.array(
        [np.datetime64(PRICE_START + timedelta(days=i)) for i in range(PRICE_DAYS)],
        dtype="datetime64[ns]",
    )
    lookup: dict[str, Any] = {}
    for ticker, closes in closes_by_ticker.items():
        arr = np.asarray(closes, dtype="float64")
        lookup[ticker] = (dates, arr)
    return lookup


@pytest.fixture
def price_lookup() -> dict[str, Any]:
    """3 Ticker x 400 Tage (linearer Close 100+i); Ticker DDD fehlt bewusst."""

    return _build_price_lookup(
        {
            "AAA": [100.0 + i for i in range(PRICE_DAYS)],
            "BBB": [200.0 + 2 * i for i in range(PRICE_DAYS)],
            "CCC": [50.0 + 0.5 * i for i in range(PRICE_DAYS)],
        }
    )


@pytest.fixture
def price_source(price_lookup):
    from suspicious_trades.recommendations.recommender import PriceSource

    return PriceSource(price_lookup)
