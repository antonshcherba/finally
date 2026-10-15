"""FinAlly FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.storage import Database
from app.market import PriceCache, create_stream_router, start_market_data
from app.services import ServiceError, Services

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_INTERVAL = 30.0


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


async def _snapshot_loop(services: Services) -> None:
    while True:
        await asyncio.sleep(SNAPSHOT_INTERVAL)
        try:
            services.record_snapshot()
        except Exception:
            logger.exception("snapshot failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _load_dotenv()
    db = Database()
    db.init()
    cache = app.state.cache
    tickers = set(db.get_watchlist()) | {p["ticker"] for p in db.get_positions()}
    source = await start_market_data(cache, sorted(tickers))
    services = Services(db, cache, source)
    app.state.services = services
    task = asyncio.create_task(_snapshot_loop(services))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await source.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.cache = PriceCache()

    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, e: ServiceError):
        return error_response(e.code, e.message, e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        fields = {str(x["loc"][-1]) for x in e.errors() if x.get("loc")}
        if "quantity" in fields:
            return error_response("invalid_quantity", "Quantity must be a number", 400)
        if "side" in fields:
            return error_response("invalid_side", "side must be 'buy' or 'sell'", 400)
        if "ticker" in fields:
            return error_response("invalid_ticker", "Ticker must be 1-5 letters", 400)
        return error_response("invalid_request", "Invalid request body", 400)

    from app.routes import router

    app.include_router(create_stream_router(app.state.cache))
    app.include_router(router)

    static_dir = Path(os.environ.get("STATIC_DIR") or ROOT / "frontend" / "out")
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
