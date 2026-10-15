from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.llm import ChatServices, ChatUnavailable, handle_chat
from app.llm.chat import parse_response


class Err(Exception):
    code = "insufficient_cash"
    message = "Need more cash"


def make_svc(trade=None):
    saved = []
    svc = ChatServices(
        get_context=lambda: {"cash_balance": 10000},
        load_history=lambda n: [{"role": "user", "content": "hi"}],
        save_message=lambda r, c, a: saved.append((r, c, a)),
        trade=trade or AsyncMock(return_value={"price": 190.0}),
        watch_add=AsyncMock(),
        watch_remove=AsyncMock(),
    )
    return svc, saved


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")


async def test_unavailable(monkeypatch):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ChatUnavailable):
        await handle_chat("hi", make_svc()[0])


@pytest.mark.parametrize("msg,kind", [("Buy it", "buy"), ("SELL now", "sell"),
                                       ("remove that", "watch_remove"), ("watch V", "watch_add")])
async def test_mock_keywords(mock_env, msg, kind):
    svc, saved = make_svc()
    out = await handle_chat(msg, svc)
    assert out["actions"][0]["type"] == kind
    assert out["actions"][0]["status"] == "executed"
    assert [s[0] for s in saved] == ["user", "assistant"]


async def test_mock_plain(mock_env):
    out = await handle_chat("hello", make_svc()[0])
    assert out["actions"] == []


async def test_failure_inline(mock_env):
    svc, _ = make_svc(trade=AsyncMock(side_effect=Err()))
    out = await handle_chat("buy", svc)
    a = out["actions"][0]
    assert a["status"] == "failed" and a["error"]["code"] == "insufficient_cash"


async def test_real_path_uses_litellm(monkeypatch):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    content = '{"message":"ok","actions":[{"type":"buy","ticker":"msft","quantity":2}]}'
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    svc, _ = make_svc()
    with patch("litellm.acompletion", AsyncMock(return_value=resp)) as m:
        out = await handle_chat("buy msft", svc)
    msgs = m.call_args.kwargs["messages"]
    assert "FinAlly" in msgs[0]["content"] and msgs[-1]["content"] == "buy msft"
    assert out["actions"][0]["ticker"] == "MSFT" and out["actions"][0]["price"] == 190.0


async def test_llm_exception_graceful(monkeypatch):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    with patch("litellm.acompletion", AsyncMock(side_effect=RuntimeError("boom"))):
        out = await handle_chat("hi", make_svc()[0])
    assert out["actions"] == [] and out["message"]


def test_parse_malformed():
    assert parse_response("not json").message == "not json"
    assert parse_response("{bad").actions == []
    r = parse_response('{"message":"m","actions":[{"type":"nope","ticker":"X"},{"type":"watch_add","ticker":"V"}]}')
    assert len(r.actions) == 1
    assert parse_response(None).message
