"""SQLite storage layer. See db.py for the API.

    from app.storage import Database
    db = Database()          # path from FINALLY_DB_PATH, default <repo>/db/finally.db
    db.init()                # idempotent; call once in FastAPI lifespan
"""

from .db import (
    DEFAULT_CASH,
    DEFAULT_WATCHLIST,
    SNAPSHOT_RETENTION,
    Database,
    StorageError,
)

__all__ = ["Database", "StorageError", "DEFAULT_CASH", "DEFAULT_WATCHLIST", "SNAPSHOT_RETENTION"]
