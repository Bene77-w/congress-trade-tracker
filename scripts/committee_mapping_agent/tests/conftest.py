"""Gemeinsame Test-Fixtures/Fakes fuer den Committee-Mapping-Agent.

Bildet die OpenAI-Response-Struktur so weit nach, wie llm_client sie nutzt:
  response.choices[0].message.content
  response.choices[0].message.tool_calls[*].id / .function.name / .function.arguments
  response.usage.model_dump()
"""
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional


def make_usage(prompt: int = 10, completion: int = 5) -> SimpleNamespace:
    """Minimales usage-Objekt mit model_dump() (wie das OpenAI-SDK)."""
    data = {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}
    return SimpleNamespace(model_dump=lambda: data)


def make_tool_call(call_id: str, name: str, arguments: Dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_response(content: str = "", tool_calls: Optional[List[Any]] = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=make_usage())


class FakeCompletions:
    def __init__(self, scripted):
        # scripted: Liste von Response-Objekten ODER Callables(**kwargs)->Response
        self._scripted = list(scripted)
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("FakeCompletions: keine weiteren Responses scripted")
        item = self._scripted.pop(0)
        return item(**kwargs) if callable(item) else item


class FakeClient:
    """Drop-in fuer den OpenAI-Client: client.chat.completions.create(...)."""
    def __init__(self, scripted: List[Any]):
        self.chat = SimpleNamespace(completions=FakeCompletions(scripted))

    @property
    def calls(self):
        return self.chat.completions.calls
