"""Shared database utilities."""

import sqlite3
from contextlib import contextmanager
from .config import DB as DB_PATH


@contextmanager
def get_db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
    finally:
        db.commit()
        db.close()
