import pytest

from app.storage import DEFAULT_WATCHLIST, SNAPSHOT_RETENTION, Database, StorageError


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "sub" / "finally.db")
    d.init()
    return d


def test_init_seeds_and_is_idempotent(db):
    assert db.get_cash() == 10000.0
    assert db.get_watchlist() == DEFAULT_WATCHLIST
    db.remove_watchlist("AAPL")
    db.apply_trade("MSFT", "buy", 1, 100)
    db.init()
    assert "AAPL" not in db.get_watchlist()
    assert db.get_cash() == 9900.0


def test_watchlist_errors(db):
    with pytest.raises(StorageError) as e:
        db.add_watchlist("AAPL")
    assert e.value.code == "already_in_watchlist"
    with pytest.raises(StorageError) as e:
        db.remove_watchlist("ZZZ")
    assert e.value.code == "not_in_watchlist"
    db.add_watchlist("ZZZ")
    assert db.get_watchlist()[-1] == "ZZZ"


def test_buy_sell_cycle(db):
    db.apply_trade("AAPL", "buy", 10, 100)
    db.apply_trade("AAPL", "buy", 10, 200)
    p = db.get_position("AAPL")
    assert p["quantity"] == 20 and p["avg_cost"] == 150
    assert db.get_cash() == 10000 - 3000
    db.apply_trade("AAPL", "sell", 5, 160)
    p = db.get_position("AAPL")
    assert p["quantity"] == 15 and p["avg_cost"] == 150
    db.apply_trade("AAPL", "sell", 15, 160)
    assert db.get_position("AAPL") is None
    assert db.get_cash() == 7000 + 800 + 2400
    trades = db.get_trades()
    assert len(trades) == 4 and trades[0]["side"] == "sell"


def test_trade_errors_leave_state(db):
    with pytest.raises(StorageError) as e:
        db.apply_trade("AAPL", "buy", 100, 200)
    assert e.value.code == "insufficient_cash"
    with pytest.raises(StorageError) as e:
        db.apply_trade("AAPL", "sell", 1, 200)
    assert e.value.code == "insufficient_shares"
    with pytest.raises(StorageError) as e:
        db.apply_trade("AAPL", "hold", 1, 200)
    assert e.value.code == "invalid_side"
    assert db.get_cash() == 10000.0 and db.get_trades() == []


def test_fractional_and_exact_cash(db):
    db.apply_trade("AAPL", "buy", 0.5, 100)
    db.apply_trade("AAPL", "buy", 99.5, 100)
    assert db.get_cash() == 0.0


def test_snapshot_pruning(db):
    for i in range(SNAPSHOT_RETENTION + 3):
        db.add_snapshot(float(i))
    snaps = db.get_snapshots()
    assert len(snaps) == SNAPSHOT_RETENTION
    assert snaps[0]["total_value"] == 3.0
    assert snaps[-1]["total_value"] == SNAPSHOT_RETENTION + 2.0


def test_chat_messages(db):
    db.add_chat_message("user", "hi")
    acts = [{"type": "buy", "ticker": "AAPL", "status": "executed"}]
    db.add_chat_message("assistant", "done", acts)
    msgs = db.get_chat_messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["actions"] is None and msgs[1]["actions"] == acts
    for i in range(25):
        db.add_chat_message("user", str(i))
    msgs = db.get_chat_messages(20)
    assert len(msgs) == 20 and msgs[-1]["content"] == "24"


def test_reset(db):
    db.apply_trade("AAPL", "buy", 1, 100)
    db.remove_watchlist("AAPL")
    db.add_chat_message("user", "x")
    db.add_snapshot(5.0)
    db.reset()
    assert db.get_cash() == 10000.0
    assert db.get_positions() == [] and db.get_trades() == []
    assert db.get_chat_messages() == []
    assert db.get_watchlist() == DEFAULT_WATCHLIST
    assert [s["total_value"] for s in db.get_snapshots()] == [10000.0]


def test_env_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FINALLY_DB_PATH", str(tmp_path / "e.db"))
    d = Database()
    d.init()
    assert (tmp_path / "e.db").exists()
