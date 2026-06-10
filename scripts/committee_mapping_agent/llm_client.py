"""DeepSeek-Client + robuste Call-Helfer (eigener Orchestrator).

Zwei Einstiege:
  * call_json(...)       -> JSON-Mode + clientseitige jsonschema-Validierung +
                            Repair-Reprompt-Loop. Liefert ein validiertes dict.
  * call_with_tools(...) -> manueller Tool-Loop (tool_choice="auto"), hartes
                            Turn-Limit. Liefert (final_text, messages).

DeepSeek-Eigenheiten, die hier gekapselt sind:
  * nur max_tokens (kein max_completion_tokens)
  * Thinking via extra_body={"thinking": {"type": "enabled"}} + reasoning_effort
  * tool_choice ausschliesslich "auto" (oder weglassen)
  * kein striktes json_schema -> response_format={"type": "json_object"} +
    eigene Validierung
"""
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator

from . import config

logger = logging.getLogger(__name__)

# Akkumuliert Token-Verbrauch ueber den ganzen Lauf (fuer Kosten-Report).
# Jede Liste-Eintrag: {role, model, usage(dict|None)}.
USAGE_LOG: List[Dict[str, Any]] = []

_CLIENT = None  # lazy Singleton


class MissingAPIKeyError(RuntimeError):
    """DEEPSEEK_API_KEY fehlt in der Umgebung / .env."""


class LLMValidationError(RuntimeError):
    """Antwort konnte auch nach Repair-Retries nicht valide geparst werden."""


def get_client(force_new: bool = False):
    """Erzeugt (lazy) den OpenAI-SDK-Client gegen die DeepSeek-Base-URL.

    Liest .env via python-dotenv. Wirft MissingAPIKeyError mit klarer Anleitung,
    wenn der Key fehlt — bewusst frueh, statt eines kryptischen 401 spaeter.
    """
    global _CLIENT
    if _CLIENT is not None and not force_new:
        return _CLIENT

    # .env aus dem Projekt-Root laden (Symlink im Worktree ist ok).
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(config.BASE_DIR, ".env"))
    except ImportError:  # pragma: no cover - dotenv ist Pflicht-Dependency
        pass

    api_key = os.environ.get(config.API_KEY_ENV)
    if not api_key:
        raise MissingAPIKeyError(
            f"{config.API_KEY_ENV} ist nicht gesetzt. Trage ihn in die "
            f".env im Projekt-Root ein:\n    {config.API_KEY_ENV}=sk-...\n"
            f"(Datei: {os.path.join(config.BASE_DIR, '.env')})"
        )

    from openai import OpenAI
    _CLIENT = OpenAI(
        api_key=api_key,
        base_url=config.API_BASE_URL,
        timeout=config.REQUEST_TIMEOUT,
    )
    return _CLIENT


def _completion_kwargs(role: str) -> Dict[str, Any]:
    """Baut modell-/effort-/token-Kwargs fuer eine Rolle (DeepSeek-konform)."""
    model, effort = config.model_for(role)
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": config.max_tokens_for(role),
    }
    if effort is not None:
        # Thinking-Mode explizit aktivieren + Effort steuern.
        kwargs["reasoning_effort"] = effort
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def _record_usage(role: str, model: str, response: Any) -> None:
    usage = getattr(response, "usage", None)
    USAGE_LOG.append({
        "role": role,
        "model": model,
        "usage": usage.model_dump() if hasattr(usage, "model_dump") else None,
    })


def call_json(
    role: str,
    system: str,
    user: str,
    schema: Dict[str, Any],
    *,
    client: Any = None,
    max_repair_retries: Optional[int] = None,
) -> Dict[str, Any]:
    """Ruft das Modell im JSON-Mode und validiert gegen ``schema``.

    Bei JSON-Parse- oder Schema-Fehlern wird ein Repair-Reprompt mit der
    konkreten Fehlermeldung angehaengt (bis max_repair_retries). Schlaegt auch
    das fehl -> LLMValidationError.
    """
    client = client or get_client()
    if max_repair_retries is None:
        max_repair_retries = config.MAX_REPAIR_RETRIES

    validator = Draft202012Validator(schema)
    base_kwargs = _completion_kwargs(role)
    model = base_kwargs["model"]

    # JSON-Mode verlangt das Wort "json" im Prompt -> sicherstellen.
    system_full = system.rstrip() + (
        "\n\nAntworte ausschliesslich mit einem einzigen gueltigen JSON-Objekt "
        "ohne Markdown-Codefences und ohne weiteren Text."
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_full},
        {"role": "user", "content": user},
    ]

    last_error = ""
    for attempt in range(max_repair_retries + 1):
        response = client.chat.completions.create(
            messages=messages,
            response_format={"type": "json_object"},
            **base_kwargs,
        )
        _record_usage(role, model, response)
        content = response.choices[0].message.content or ""

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            last_error = f"JSON-Parse-Fehler: {exc}"
            logger.warning("[%s] %s (Versuch %d)", role, last_error, attempt + 1)
        else:
            errors = sorted(validator.iter_errors(parsed), key=lambda e: e.path)
            if not errors:
                return parsed
            last_error = "; ".join(
                f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
                for e in errors[:8]
            )
            logger.warning("[%s] Schema-Fehler: %s (Versuch %d)",
                           role, last_error, attempt + 1)

        if attempt < max_repair_retries:
            # Repair-Reprompt: fehlerhafte Antwort + konkrete Diagnose anhaengen.
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": (
                    "Die vorige Antwort war ungueltig. Fehler: "
                    f"{last_error}\nGib das KORRIGIERTE JSON-Objekt erneut "
                    "aus — nur das JSON, nichts sonst."
                ),
            })

    raise LLMValidationError(
        f"[{role}] Antwort nach {max_repair_retries + 1} Versuchen ungueltig. "
        f"Letzter Fehler: {last_error}"
    )


def call_with_tools(
    role: str,
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    tool_handlers: Dict[str, Callable[[Dict[str, Any]], str]],
    *,
    client: Any = None,
    max_turns: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """Manueller Tool-Loop mit hartem Turn-Limit.

    ``tool_handlers`` mappt Tool-Name -> Funktion(args_dict) -> str (Ergebnis,
    das als tool-Message zurueckgespielt wird). Endet, sobald das Modell keine
    Tool-Calls mehr anfordert oder max_turns erreicht ist. Liefert
    (finaler Text, vollstaendige Message-Historie).
    """
    client = client or get_client()
    if max_turns is None:
        max_turns = config.MAX_SEARCH_TURNS

    base_kwargs = _completion_kwargs(role)
    model = base_kwargs["model"]
    messages = list(messages)  # nicht in-place mutieren

    for turn in range(max_turns):
        response = client.chat.completions.create(
            messages=messages,
            tools=tools,
            tool_choice="auto",  # DeepSeek: NUR "auto" erlaubt
            **base_kwargs,
        )
        _record_usage(role, model, response)
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        # Assistant-Message in die Historie uebernehmen (inkl. Tool-Calls).
        assistant_entry: Dict[str, Any] = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_entry)

        if not tool_calls:
            return msg.content or "", messages

        # Jeden angeforderten Tool-Call ausfuehren und Ergebnis zurueckspielen.
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            handler = tool_handlers.get(name)
            if handler is None:
                result = f"FEHLER: unbekanntes Tool '{name}'."
            else:
                try:
                    result = handler(args)
                except Exception as exc:  # Tool-Fehler nicht fatal -> ans Modell
                    logger.warning("Tool '%s' Fehler: %s", name, exc)
                    result = f"FEHLER bei Tool '{name}': {exc}"
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result),
            })

    # Turn-Budget erschoepft: letzten Stand ohne weitere Tools final abfragen.
    logger.warning("[%s] max_turns=%d erreicht — erzwinge Abschluss.",
                   role, max_turns)
    messages.append({
        "role": "user",
        "content": ("Such-Budget erschoepft. Fasse jetzt mit den bereits "
                    "gesammelten Informationen final zusammen — ohne weitere "
                    "Tool-Aufrufe."),
    })
    response = client.chat.completions.create(messages=messages, **base_kwargs)
    _record_usage(role, model, response)
    final = response.choices[0].message.content or ""
    messages.append({"role": "assistant", "content": final})
    return final, messages
