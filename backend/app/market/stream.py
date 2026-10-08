"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

PUSH_INTERVAL = 0.5  # seconds between ticks
HEARTBEAT_INTERVAL = 15.0  # comment line when nothing was sent, keeps proxies from closing
RETRY_MS = 1000  # EventSource reconnect delay


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create the SSE streaming router with a reference to the price cache.

    A fresh router is built per call, so this is safe to call more than once
    (tests, app factories).
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        """SSE endpoint for live price updates.

        Every ~500ms, one event per tracked ticker (no diffing):

            data: {"ticker": "AAPL", "price": 190.12, "previous_price": 190.05,
                   "baseline_price": 190.0, "timestamp": "<ISO>", "direction": "up"}

        EventSource reconnects automatically thanks to the retry directive.
        """
        return StreamingResponse(
            generate_price_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
            },
        )

    return router


def format_sse(data: dict) -> str:
    """One SSE message frame (default `message` event, so EventSource.onmessage fires)."""
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def generate_price_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = PUSH_INTERVAL,
    heartbeat: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Yield one SSE event per tracked ticker every `interval` seconds.

    The first tick fires immediately, so a new or reconnecting client renders
    without waiting. With nothing to send (empty cache), a `: heartbeat`
    comment goes out every `heartbeat` seconds. Stops when the client
    disconnects.

    Events repeat the latest cached update even if unchanged (Massive only
    changes each poll), so clients should flash only when `price` differs
    from the value they last rendered.
    """
    yield f"retry: {RETRY_MS}\n\n"

    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    idle = 0.0
    try:
        while not await request.is_disconnected():
            prices = price_cache.get_all()
            if prices:
                idle = 0.0
                for update in prices.values():
                    yield format_sse(update.to_sse())
            else:
                idle += interval
                if idle >= heartbeat:
                    idle = 0.0
                    yield ": heartbeat\n\n"
            await asyncio.sleep(interval)
    finally:  # runs on disconnect and on server-side cancellation alike
        logger.info("SSE client disconnected: %s", client)
