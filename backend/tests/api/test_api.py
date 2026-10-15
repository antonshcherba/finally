import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "nostatic"))
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    with TestClient(create_app()) as c:
        yield c


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code


def trade(c, **kw):
    body = {"ticker": "AAPL", "quantity": 1, "side": "buy"} | kw
    return c.post("/api/portfolio/trade", json=body)


def test_health(client):
    assert client.get("/api/health").status_code == 200


def test_initial_state(client):
    p = client.get("/api/portfolio").json()
    assert p["cash_balance"] == 10000 and p["positions"] == []
    wl = client.get("/api/watchlist").json()["watchlist"]
    assert len(wl) == 10 and wl[0]["price"] and wl[0]["baseline_price"]


def test_buy_and_sell(client):
    r = trade(client, quantity=10)
    assert r.status_code == 200
    body = r.json()
    price = body["trade"]["price"]
    assert body["portfolio"]["positions"][0]["quantity"] == 10
    assert body["portfolio"]["cash_balance"] == pytest.approx(10000 - 10 * price, abs=0.01)
    r = trade(client, quantity=10, side="sell")
    assert r.json()["portfolio"]["positions"] == []
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) >= 2


@pytest.mark.parametrize("q", [0, -1, 0.00001, 1.23456, "x", None])
def test_invalid_quantity(client, q):
    err(trade(client, quantity=q), 400, "invalid_quantity")


def test_trade_errors(client):
    err(trade(client, ticker="123"), 400, "invalid_ticker")
    err(trade(client, ticker="ZZZZ"), 400, "unknown_ticker")
    err(trade(client, side="hold"), 400, "invalid_side")
    err(trade(client, quantity=1000000), 400, "insufficient_cash")
    err(trade(client, side="sell"), 400, "insufficient_shares")


def test_watchlist_crud(client):
    assert client.delete("/api/watchlist/V").status_code == 204
    err(client.delete("/api/watchlist/V"), 404, "not_in_watchlist")
    r = client.post("/api/watchlist", json={"ticker": " v "})
    assert r.status_code == 201 and r.json()["ticker"] == "V"
    err(client.post("/api/watchlist", json={"ticker": "V"}), 409, "already_in_watchlist")
    err(client.post("/api/watchlist", json={"ticker": "ZZZZ"}), 400, "unknown_ticker")


def test_position_stays_priced_after_unwatch(client):
    trade(client, ticker="V")
    client.delete("/api/watchlist/V")
    assert client.app.state.cache.get_price("V") is not None


def test_reset(client):
    trade(client, quantity=5)
    client.delete("/api/watchlist/MSFT")
    assert client.post("/api/reset").status_code == 200
    p = client.get("/api/portfolio").json()
    assert p["cash_balance"] == 10000 and p["positions"] == []
    assert len(client.get("/api/watchlist").json()["watchlist"]) == 10
    assert len(client.get("/api/portfolio/history").json()["snapshots"]) == 1


def test_chat_mock_buy(client):
    r = client.post("/api/chat", json={"message": "please buy"})
    assert r.status_code == 200, r.text
    assert r.json()["actions"][0]["status"] == "executed"
    assert client.get("/api/portfolio").json()["positions"][0]["ticker"] == "AAPL"


def test_chat_unavailable(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    err(client.post("/api/chat", json={"message": "hi"}), 503, "chat_unavailable")


def test_sse_route_registered(client):
    assert any(r.path == "/api/stream/prices" for r in client.app.routes)
