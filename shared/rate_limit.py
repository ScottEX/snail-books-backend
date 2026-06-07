"""In-memory rate limiting (resets on process restart)."""

import time

# Login rate limiting
_login_attempts = {}  # { ip: [attempt_timestamps...] }
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 900  # 15 minutes

# Forgot-password rate limiting (independent counter)
_forgot_attempts = {}
_FORGOT_MAX = 3
_FORGOT_WINDOW = 900


def _check_rate_limit(ip, store, max_attempts, window):
    """Generic rate limit check. Returns (allowed, wait_seconds)."""
    now = time.time()
    attempts = store.get(ip, [])
    attempts = [t for t in attempts if now - t < window]
    store[ip] = attempts
    if len(attempts) >= max_attempts:
        wait = int(window - (now - attempts[0]))
        return False, wait
    return True, 0


def _record_attempt(ip, store, window):
    """Record an attempt in the given store."""
    now = time.time()
    attempts = store.get(ip, [])
    attempts = [t for t in attempts if now - t < window]
    attempts.append(now)
    store[ip] = attempts


def check_rate_limit(ip):
    """Login rate limit (backward compat wrapper)."""
    return _check_rate_limit(ip, _login_attempts, _RATE_LIMIT_MAX, _RATE_LIMIT_WINDOW)


def record_failed_attempt(ip):
    """Record a failed login attempt."""
    _record_attempt(ip, _login_attempts, _RATE_LIMIT_WINDOW)


def check_forgot_limit(ip):
    """Forgot-password rate limit (independent counter)."""
    return _check_rate_limit(ip, _forgot_attempts, _FORGOT_MAX, _FORGOT_WINDOW)


def record_forgot_attempt(ip):
    """Record a forgot-password attempt."""
    _record_attempt(ip, _forgot_attempts, _FORGOT_WINDOW)
