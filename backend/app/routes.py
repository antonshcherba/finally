"""REST routes (PLAN.md §8)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.services import ServiceError, Services

router = APIRouter(prefix="/api")


def _svc(request: Request) -> Services:
    return request.app.state.services


class TradeIn(BaseModel):
    ticker: Any = None
    quantity: Any = None
    side: Any = None


class TickerIn(BaseModel):
    ticker: Any = None


class ChatIn(BaseModel):
    message: str


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/portfolio")
async def portfolio(request: Request):
    return _svc(request).get_portfolio()


@router.post("/portfolio/trade")
async def trade(body: TradeIn, request: Request):
    return await _svc(request).execute_trade(body.ticker, body.side, body.quantity)


@router.get("/portfolio/history")
async def history(request: Request):
    return {"snapshots": _svc(request).db.get_snapshots()}


@router.get("/watchlist")
async def watchlist(request: Request):
    return {"watchlist": _svc(request).get_watchlist()}


@router.post("/watchlist", status_code=201)
async def watchlist_add(body: TickerIn, request: Request):
    return await _svc(request).watchlist_add(body.ticker)


@router.delete("/watchlist/{ticker}", status_code=204)
async def watchlist_remove(ticker: str, request: Request):
    await _svc(request).watchlist_remove(ticker)
    return Response(status_code=204)


@router.post("/reset")
async def reset(request: Request):
    await _svc(request).reset()
    return {"status": "ok"}


@router.post("/chat")
async def chat(body: ChatIn, request: Request):
    from app.llm import ChatServices, ChatUnavailable, handle_chat

    svc = _svc(request)
    db = svc.db

    def context() -> dict:
        p = svc.get_portfolio()
        p["watchlist"] = [
            {"ticker": w["ticker"], "price": w["price"]} for w in svc.get_watchlist()
        ]
        return p

    async def trade_fn(ticker: str, side: str, quantity: float) -> dict:
        r = await svc.execute_trade(ticker, side, quantity)
        return r["trade"]

    async def watch_add(ticker: str) -> None:
        await svc.watchlist_add(ticker)

    cs = ChatServices(
        get_context=context,
        load_history=lambda limit: db.get_chat_messages(limit),
        save_message=lambda role, content, actions=None: db.add_chat_message(
            role, content, actions
        ),
        trade=trade_fn,
        watch_add=watch_add,
        watch_remove=svc.watchlist_remove,
    )
    try:
        return await handle_chat(body.message, cs)
    except ChatUnavailable as e:
        raise ServiceError("chat_unavailable", str(e) or "Chat is not configured", 503) from e
