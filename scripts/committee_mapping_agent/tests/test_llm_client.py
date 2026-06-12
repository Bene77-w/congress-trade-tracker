"""Phase-0-Tests: call_json (Repair-Loop, Schema-Reject), Key-Fehler, Tool-Loop."""
import json

import pytest

from scripts.committee_mapping_agent import config, llm_client
from scripts.committee_mapping_agent.llm_client import (
    LLMValidationError,
    MissingAPIKeyError,
    call_json,
    call_with_tools,
    get_client,
)
from scripts.committee_mapping_agent.tests.conftest import (
    FakeClient,
    make_response,
    make_tool_call,
)

SCHEMA = {
    "type": "object",
    "properties": {"sector": {"type": "string"}, "rank": {"type": "integer"}},
    "required": ["sector", "rank"],
    "additionalProperties": False,
}


@pytest.fixture(autouse=True)
def _reset_usage():
    llm_client.USAGE_LOG.clear()
    yield
    llm_client.USAGE_LOG.clear()


# ---------------------------------------------------------------------------
# call_json
# ---------------------------------------------------------------------------
def test_call_json_valid_first_try():
    client = FakeClient([make_response(json.dumps({"sector": "Energy", "rank": 1}))])
    out = call_json("mapping", "sys", "user", SCHEMA, client=client)
    assert out == {"sector": "Energy", "rank": 1}
    assert len(client.calls) == 1
    # JSON-Mode muss gesetzt sein.
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    # 'mapping' hat effort gesetzt -> Thinking-Kwargs muessen mitgehen.
    assert client.calls[0]["reasoning_effort"] == config.MODELS["mapping"][1]
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}


def test_call_json_repairs_broken_json():
    client = FakeClient([
        make_response("{ kaputtes json ohne quotes"),
        make_response(json.dumps({"sector": "Energy", "rank": 1})),
    ])
    out = call_json("mapping", "sys", "user", SCHEMA, client=client)
    assert out["sector"] == "Energy"
    assert len(client.calls) == 2
    # Repair-Reprompt muss die Fehlerdiagnose enthalten.
    repair_msgs = client.calls[1]["messages"]
    assert any("ungueltig" in m["content"] for m in repair_msgs if m["role"] == "user")


def test_call_json_repairs_schema_violation():
    client = FakeClient([
        make_response(json.dumps({"sector": "Energy"})),            # rank fehlt
        make_response(json.dumps({"sector": "Energy", "rank": 2})),
    ])
    out = call_json("mapping", "sys", "user", SCHEMA, client=client)
    assert out == {"sector": "Energy", "rank": 2}
    assert len(client.calls) == 2


def test_call_json_raises_after_exhausting_retries():
    bad = make_response(json.dumps({"sector": "Energy"}))  # rank fehlt dauerhaft
    client = FakeClient([bad, bad, bad])  # 1 + max_repair_retries(2) = 3 Calls
    with pytest.raises(LLMValidationError):
        call_json("mapping", "sys", "user", SCHEMA, client=client,
                  max_repair_retries=2)
    assert len(client.calls) == 3


def test_call_json_no_effort_omits_thinking(monkeypatch):
    # effort=None -> keine Thinking-Kwargs (Rolle hier explizit gestubbt,
    # damit der Test von der echten MODELS-Config unabhaengig ist).
    monkeypatch.setitem(config.MODELS, "research", ("deepseek-v4-flash", None))
    client = FakeClient([make_response(json.dumps({"sector": "X", "rank": 0}))])
    call_json("research", "sys", "user", SCHEMA, client=client)
    assert "reasoning_effort" not in client.calls[0]
    assert "extra_body" not in client.calls[0]


def test_call_json_records_usage():
    client = FakeClient([make_response(json.dumps({"sector": "Energy", "rank": 1}))])
    call_json("mapping", "sys", "user", SCHEMA, client=client)
    assert len(llm_client.USAGE_LOG) == 1
    assert llm_client.USAGE_LOG[0]["role"] == "mapping"


# ---------------------------------------------------------------------------
# get_client / Key-Handling
# ---------------------------------------------------------------------------
def test_get_client_missing_key_raises(monkeypatch):
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    # load_dotenv soll den Key nicht aus der echten .env nachladen.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    llm_client._CLIENT = None
    with pytest.raises(MissingAPIKeyError):
        get_client(force_new=True)


# ---------------------------------------------------------------------------
# call_with_tools
# ---------------------------------------------------------------------------
def test_tool_loop_executes_handler_then_finishes():
    client = FakeClient([
        make_response(tool_calls=[make_tool_call("c1", "web_search", {"query": "x"})]),
        make_response(content="Fertige Zusammenfassung."),
    ])
    seen = {}

    def web_search(args):
        seen["query"] = args["query"]
        return "Suchergebnis-Text"

    final, messages = call_with_tools(
        "research",
        [{"role": "user", "content": "recherchiere"}],
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_handlers={"web_search": web_search},
        client=client,
    )
    assert final == "Fertige Zusammenfassung."
    assert seen["query"] == "x"
    # tool_choice muss "auto" sein (DeepSeek-Constraint).
    assert all(c.get("tool_choice") == "auto" for c in client.calls)
    # Tool-Ergebnis muss als tool-Message in der Historie liegen.
    assert any(m["role"] == "tool" and m["content"] == "Suchergebnis-Text"
               for m in messages)


def test_tool_loop_handler_exception_is_fed_back():
    client = FakeClient([
        make_response(tool_calls=[make_tool_call("c1", "boom", {})]),
        make_response(content="ok trotzdem"),
    ])

    def boom(args):
        raise ValueError("kaputt")

    final, messages = call_with_tools(
        "research", [{"role": "user", "content": "x"}],
        tools=[], tool_handlers={"boom": boom}, client=client,
    )
    assert final == "ok trotzdem"
    assert any(m["role"] == "tool" and "kaputt" in m["content"] for m in messages)


def test_tool_loop_respects_max_turns():
    # Modell will immer weiter Tools rufen -> nach max_turns Zwangsabschluss.
    def always_tool(**kwargs):
        return make_response(tool_calls=[make_tool_call("c", "noop", {})])
    # max_turns=2 Schleifen-Calls + 1 finaler Abschluss-Call = 3 Responses.
    client = FakeClient([always_tool, always_tool, make_response(content="Schluss")])

    final, _ = call_with_tools(
        "research", [{"role": "user", "content": "x"}],
        tools=[], tool_handlers={"noop": lambda a: "n"}, client=client,
        max_turns=2,
    )
    assert final == "Schluss"
    assert len(client.calls) == 3
