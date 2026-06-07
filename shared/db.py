"""Shared database utilities."""

import sqlite3, os
from contextlib import contextmanager

DB_PATH = os.environ.get(
    'DB',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'snail.db'),
)


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
