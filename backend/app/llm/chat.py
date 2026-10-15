import inspect
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from .prompt import build_messages
from .schema import LLMAction, LLMResponse

log = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b:free"
HISTORY_LIMIT = 20
FALLBACK_MESSAGE = "Sorry, I couldn't process that response. Please try again."


class ChatUnavailable(Exception):
    code = "chat_unavailable"
    message = "Chat is not configured"
    status = 503


@dataclass
class ChatServices:
    get_context: Callable[[], Any]
    load_history: Callable[[int], Any]
    save_message: Callable[[str, str, list | None], Any]
    trade: Callable[[str, str, float], Any]
    watch_add: Callable[[str], Any]
    watch_remove: Callable[[str], Any]


async def _call(fn, *args):
    result = fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


def mock_response(message: str) -> LLMResponse:
    m = message.lower()
    if "buy" in m:
        return LLMResponse(message="Buying 1 share of AAPL.",
                           actions=[LLMAction(type="buy", ticker="AAPL", quantity=1)])
    if "sell" in m:
        return LLMResponse(message="Selling 1 share of AAPL.",
                           actions=[LLMAction(type="sell", ticker="AAPL", quantity=1)])
    if "remove" in m:
        return LLMResponse(message="Removing V from your watchlist.",
                           actions=[LLMAction(type="watch_remove", ticker="V")])
    if "watch" in m:
        return LLMResponse(message="Adding V to your watchlist.",
                           actions=[LLMAction(type="watch_add", ticker="V")])
    return LLMResponse(message="Mock response: I'm FinAlly. How can I help with your portfolio?")


def parse_response(raw: str | None) -> LLMResponse:
    try:
        return LLMResponse.model_validate_json(raw or "")
    except (ValidationError, ValueError):
        pass
    try:
        data = json.loads(raw or "")
        if isinstance(data, dict) and isinstance(data.get("message"), str):
            actions = []
            for a in data.get("actions") or []:
                try:
                    actions.append(LLMAction.model_validate(a))
                except ValidationError:
                    continue
            return LLMResponse(message=data["message"], actions=actions)
    except (ValueError, TypeError):
        pass
    if raw and raw.strip() and not raw.lstrip().startswith(("{", "[")):
        return LLMResponse(message=raw.strip())
    return LLMResponse(message=FALLBACK_MESSAGE)


async def _call_llm(messages: list[dict]) -> LLMResponse:
    from litellm import acompletion

    try:
        resp = await acompletion(model=MODEL, messages=messages, response_format=LLMResponse,
                                 reasoning_effort="low")
        return parse_response(resp.choices[0].message.content)
    except Exception:
        log.exception("LLM call failed")
        return LLMResponse(message=FALLBACK_MESSAGE)


def _failed(action: LLMAction, code: str, message: str) -> dict:
    out = {"type": action.type, "ticker": action.ticker, "status": "failed",
           "error": {"code": code, "message": message}}
    if action.quantity is not None:
        out["quantity"] = action.quantity
    return out


async def _execute(action: LLMAction, svc: ChatServices) -> dict:
    ticker = action.ticker.strip().upper()
    out: dict = {"type": action.type, "ticker": ticker}
    try:
        if action.type in ("buy", "sell"):
            if action.quantity is None:
                return _failed(action, "invalid_quantity", "quantity is required")
            out["quantity"] = action.quantity
            res = await _call(svc.trade, ticker, action.type, action.quantity)
            res = res or {}
            price = res.get("price", (res.get("trade") or {}).get("price"))
            if price is not None:
                out["price"] = price
        elif action.type == "watch_add":
            await _call(svc.watch_add, ticker)
        else:
            await _call(svc.watch_remove, ticker)
    except Exception as e:
        return _failed(action, getattr(e, "code", "error"), getattr(e, "message", None) or str(e))
    out["status"] = "executed"
    return out


async def handle_chat(message: str, svc: ChatServices) -> dict:
    mock = os.environ.get("LLM_MOCK", "").lower() == "true"
    if not mock and not os.environ.get("OPENROUTER_API_KEY"):
        raise ChatUnavailable()

    if mock:
        parsed = mock_response(message)
    else:
        context = await _call(svc.get_context)
        history = await _call(svc.load_history, HISTORY_LIMIT)
        parsed = await _call_llm(build_messages(context, history, message))

    results = [await _execute(a, svc) for a in parsed.actions]
    await _call(svc.save_message, "user", message, None)
    await _call(svc.save_message, "assistant", parsed.message, results)
    return {"message": parsed.message, "actions": results}
