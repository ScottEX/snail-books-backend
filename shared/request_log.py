"""Request logging — unified structured access log with Trace ID, slow detection, and PII masking.

Output goes to stderr (captured by gunicorn --capture-output).

Format:
  YYYY-MM-DD HH:MM:SS.mmm LEVEL [TID:trace_id] [ACCESS] uid=X u=Y ip=Z METHOD /path → STATUS SIZE DURATION | ua=... [req=...] [resp=...] [SLOW]

  - LEVEL: INFO for 2xx/3xx, WARN for 4xx, ERROR for 5xx, SLOW for >1000ms
  - Trace ID: 12-char hex string, generated per request, carried through all log lines
  - req/resp body: only for mutations (POST/PUT/DELETE/PATCH) or errors
  - Sensitive fields (password, token, secret, etc.) are masked as ***

Static files (/expense-imgs/, /user-images/, root HTML) are skipped.
"""

import sys
import json
import time
import uuid
from datetime import datetime, timezone

from flask import request, g


# ── Config ──

MAX_REQ_BODY = 500           # max chars for request body
MAX_RESP_BODY = 300          # max chars for response body
SLOW_THRESHOLD_MS = 1000     # mark requests as SLOW if they exceed this
SKIP_PREFIXES = (
    '/expense-imgs/',
    '/user-images/',
)
SKIP_EXACT = ('/', '/favicon.ico')

# Sensitive fields to mask in request/response bodies
_SENSITIVE_FIELDS = frozenset({
    'password', 'passwd', 'pwd', 'pass',
    'token', 'secret', 'api_key', 'access_token',
    'refresh_token', 'key', 'private_key',
    'flask_secret_key',
})


# ── Helpers ──

def _truncate(s, maxlen):
    if len(s) > maxlen:
        return s[:maxlen] + '…'
    return s


def _generate_trace_id() -> str:
    """12-char hex trace ID (shorter than full UUID, enough for log correlation)."""
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    """Timestamp like '2026-06-20 12:29:40.432' in local time (Beijing)."""
    # Use UTC+8 for consistency with server logs
    dt = datetime.now(timezone.utc).replace(tzinfo=None)
    # Add 8 hours for Beijing time
    from datetime import timedelta
    dt = dt + timedelta(hours=8)
    return dt.strftime('%Y-%m-%d %H:%M:%S.') + f'{dt.microsecond // 1000:03d}'


_SENSITIVE_MASK = '***'


def _mask_sensitive(data):
    """Recursively mask sensitive fields in a dict/list."""
    if isinstance(data, dict):
        return {
            k: _SENSITIVE_MASK if k.lower() in _SENSITIVE_FIELDS else _mask_sensitive(v)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [_mask_sensitive(item) for item in data]
    return data


def _level_for_status(status_code: int) -> str:
    if status_code >= 500:
        return 'ERROR'
    if status_code >= 400:
        return 'WARN'
    return 'INFO'


# ── Hooks ──

def _capture_request():
    """Called before each request. Stores request metadata + generates trace ID."""
    g._req_start = time.time()
    g._req_body = None
    g._req_skip = True
    g.trace_id = _generate_trace_id()

    path = request.path

    # Only log /api/* and auth endpoints
    if not (path.startswith('/api/') or path in (
        '/login', '/register', '/verify', '/resend-code',
        '/forgot-password', '/reset-password', '/logout',
    )):
        return

    g._req_skip = False
    g._req_method = request.method
    g._req_path = path
    g._req_qs = request.query_string.decode('utf-8', errors='replace') or ''

    # Capture and mask request body for mutations
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        try:
            body = request.get_json(silent=True)
            if body is not None:
                masked = _mask_sensitive(body)
                g._req_body = _truncate(json.dumps(masked, ensure_ascii=False), MAX_REQ_BODY)
        except Exception:
            g._req_body = None


def _log_response(response):
    """Called after each request. Logs unified structured access line."""
    start = getattr(g, '_req_start', None)
    if start is None or getattr(g, '_req_skip', True):
        return response

    elapsed_ms = int((time.time() - start) * 1000)
    status = response.status_code
    method = getattr(g, '_req_method', request.method)
    trace_id = getattr(g, 'trace_id', '000000000000')

    # Determine log level
    is_slow = elapsed_ms >= SLOW_THRESHOLD_MS
    if is_slow:
        level = 'SLOW'
    else:
        level = _level_for_status(status)

    # User identity
    user_id = getattr(g, 'user_id', None)
    username = getattr(g, 'username', None)

    # Client info
    ip = request.headers.get('X-Real-IP', request.remote_addr or '?')
    ua = (request.user_agent.string or '?')[:150]

    # Build log line
    path = getattr(g, '_req_path', request.path)
    qs = getattr(g, '_req_qs', '')
    url = path + ('?' + qs if qs else '')

    uid = str(user_id) if user_id else '-'
    uname = str(username) if username else '-'

    # Response size
    resp_len = 0
    resp_body_str = None
    is_mutation = method in ('POST', 'PUT', 'DELETE', 'PATCH')
    is_error = status >= 400

    if response.response:
        try:
            if isinstance(response.response, list):
                resp_len = len(response.response[0]) if response.response else 0
            else:
                resp_len = len(response.response)
        except Exception:
            pass

        if True:  # always capture response body (truncated)
            try:
                raw = response.get_data(as_text=True)
                # Mask sensitive fields in response too
                try:
                    parsed = json.loads(raw)
                    masked = _mask_sensitive(parsed)
                    resp_body_str = _truncate(json.dumps(masked, ensure_ascii=False), MAX_RESP_BODY)
                except (json.JSONDecodeError, TypeError):
                    resp_body_str = _truncate(raw, MAX_RESP_BODY)
            except Exception:
                pass

    # Assemble line
    parts = [
        f'{_now_iso()} {level} [TID:{trace_id}] [ACCESS]',
        f'uid={uid} u={uname} ip={ip} {method} {url}',
        f'→ {status} {resp_len}B {elapsed_ms}ms',
    ]

    if is_slow and level != 'SLOW':
        # Still mark slow requests that are also errors
        parts[-1] += f' (>1s)'
    elif is_slow:
        parts[-1] += f' (>1s)'

    body_parts = []
    req_body = getattr(g, '_req_body', None)
    if req_body:
        body_parts.append(f'req={req_body}')
    if resp_body_str:
        body_parts.append(f'resp={resp_body_str}')

    if body_parts:
        parts.append(' | '.join(body_parts))

    parts.append(f'ua={ua}')

    line = '\t'.join(parts)
    print(line, file=sys.stderr, flush=True)

    return response
