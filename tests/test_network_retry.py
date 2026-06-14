"""Tests fuer utils/network_retry.py — einheitliches Retry/Backoff-Schema.

Deterministisch: ``_sleep`` ist injizierbar, es wird nie real gewartet.
"""

import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import requests  # noqa: E402

from config import settings  # noqa: E402
from utils.network_retry import (  # noqa: E402
    NetworkRetryExhausted,
    is_transient_exception,
    retry_call,
)


def _http_error(code):
    """requests.HTTPError mit gesetztem response.status_code."""
    exc = requests.HTTPError(str(code))
    exc.response = types.SimpleNamespace(status_code=code)
    return exc


# ---------------------------------------------------------------------------
# Klassifikation transient vs. permanent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc, transient", [
    (requests.Timeout("Read timed out"), True),
    (requests.exceptions.ReadTimeout("Read timed out. (read timeout=30)"), True),
    (requests.exceptions.ConnectTimeout("Connection to host timed out"), True),
    (requests.ConnectionError("Connection reset by peer"), True),
    (_http_error(500), True),
    (_http_error(502), True),
    (_http_error(503), True),
    (_http_error(429), True),
    (_http_error(400), False),
    (_http_error(401), False),
    (_http_error(403), False),
    (_http_error(404), False),
    (ValueError("parse error"), False),
    (KeyError("missing field"), False),
])
def test_is_transient_exception(exc, transient):
    assert is_transient_exception(exc) is transient


# ---------------------------------------------------------------------------
# Szenario 1: kurzer Ausfall -> Recovery
# ---------------------------------------------------------------------------

def test_recovers_after_transient_then_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("Connection reset")
        return "OK"

    assert retry_call(flaky, backoffs=[0, 0, 0], _sleep=lambda s: None) == "OK"
    assert calls["n"] == 3


def test_empty_then_nonempty_recovers():
    seq = iter([[], [], [1, 2]])
    out = retry_call(
        lambda: next(seq),
        is_empty=lambda r: len(r) == 0,
        backoffs=[0, 0, 0],
        _sleep=lambda s: None,
    )
    assert out == [1, 2]


# ---------------------------------------------------------------------------
# Szenario 2: API-Timeout -> Backoff-Schema greift
# ---------------------------------------------------------------------------

def test_backoff_schedule_used_in_order():
    waits = []

    def down():
        raise requests.Timeout("Read timed out")

    with pytest.raises(NetworkRetryExhausted):
        retry_call(down, backoffs=[2, 4, 8], _sleep=waits.append)
    assert waits == [2, 4, 8]  # exponentiell wachsende Abklingzeiten, in Reihenfolge


def test_default_backoffs_from_settings():
    waits = []

    def down():
        raise requests.Timeout("timeout")

    with pytest.raises(NetworkRetryExhausted):
        retry_call(down, _sleep=waits.append)
    assert waits == settings.NETWORK_RETRY_BACKOFFS  # [2,4,8,16,32,64,128]


# ---------------------------------------------------------------------------
# Szenario 3: Run-Stop (fail-closed)
# ---------------------------------------------------------------------------

def test_sustained_failure_raises_exhausted():
    with pytest.raises(NetworkRetryExhausted):
        retry_call(lambda: (_ for _ in ()).throw(requests.Timeout("t")),
                   backoffs=[0, 0, 0], _sleep=lambda s: None)


def test_permanent_error_not_retried():
    calls = {"n": 0}

    def perm():
        calls["n"] += 1
        raise _http_error(404)

    with pytest.raises(requests.HTTPError):
        retry_call(perm, backoffs=[0, 0, 0], _sleep=lambda s: None)
    assert calls["n"] == 1  # permanent -> sofort durchgereicht, KEIN Retry


def test_empty_result_exhausts_fail_closed():
    with pytest.raises(NetworkRetryExhausted):
        retry_call(lambda: [], is_empty=lambda r: len(r) == 0,
                   backoffs=[0, 0], _sleep=lambda s: None)


def test_disabled_does_single_attempt(monkeypatch):
    monkeypatch.setattr(settings, "NETWORK_RETRY_ENABLED", False)
    calls = {"n": 0}

    def down():
        calls["n"] += 1
        raise requests.Timeout("timeout")

    with pytest.raises(NetworkRetryExhausted):
        retry_call(down, _sleep=lambda s: None)
    assert calls["n"] == 1
