"""Rate limiting with SQLite persistent storage."""

import time

from shared.db import get_db

_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 900  # 15 minutes
_FORGOT_MAX = 3
_FORGOT_WINDOW = 900


def _ensure_table(db):
    db.execute(
        'CREATE TABLE IF NOT EXISTS rate_limits '
        '(key TEXT PRIMARY KEY, attempts INTEGER, window_start REAL)'
    )


def _check_rate_limit(key, max_attempts, window):
    """Generic rate limit check. Returns (allowed, wait_seconds)."""
    now = time.time()
    with get_db() as db:
        _ensure_table(db)
        row = db.execute(
            'SELECT attempts, window_start FROM rate_limits WHERE key=?',
            (key,)
        ).fetchone()
        if row is None or (now - row['window_start']) >= window:
            return True, 0
        if row['attempts'] >= max_attempts:
            wait = int(window - (now - row['window_start']))
            return False, wait
        return True, 0


def _record_attempt(key, window):
    """Record an attempt for the given key."""
    now = time.time()
    with get_db() as db:
        _ensure_table(db)
        row = db.execute(
            'SELECT attempts, window_start FROM rate_limits WHERE key=?',
            (key,)
        ).fetchone()
        if row is None or (now - row['window_start']) >= window:
            db.execute(
                'INSERT OR REPLACE INTO rate_limits (key, attempts, window_start) VALUES (?, 1, ?)',
                (key, now)
            )
        else:
            db.execute(
                'UPDATE rate_limits SET attempts = attempts + 1 WHERE key=?',
                (key,)
            )
        db.commit()


def check_rate_limit(ip):
    """Login rate limit (backward compat wrapper)."""
    return _check_rate_limit(f'login:{ip}', _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW)


def record_failed_attempt(ip):
    """Record a failed login attempt."""
    _record_attempt(f'login:{ip}', _RATE_LIMIT_WINDOW)


def check_forgot_limit(ip):
    """Forgot-password rate limit (independent counter)."""
    return _check_rate_limit(f'forgot:{ip}', _FORGOT_MAX, _FORGOT_WINDOW)


def record_forgot_attempt(ip):
    """Record a forgot-password attempt."""
    _record_attempt(f'forgot:{ip}', _FORGOT_WINDOW)


def reserve_expense_ai_request(user_id):
    """Atomically reserve one of ten AI requests per minute for this account."""
    now = time.time()
    cutoff = now - 60
    with get_db() as db:
        _ensure_table(db)
        result = db.execute(
            """INSERT INTO rate_limits (key, attempts, window_start) VALUES (?, 1, ?)
               ON CONFLICT(key) DO UPDATE SET
                 attempts = CASE WHEN window_start <= ? THEN 1 ELSE attempts + 1 END,
                 window_start = CASE WHEN window_start <= ? THEN excluded.window_start ELSE window_start END
               WHERE window_start <= ? OR attempts < 10""",
            (f'expense-ai:{user_id}', now, cutoff, cutoff, cutoff),
        )
        return result.rowcount == 1
