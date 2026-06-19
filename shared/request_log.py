"""Request logging — captures who called what API and what was returned.

Logs every /api/* request with user identity, device info, params, and response.
Output goes to stderr (captured by gunicorn --capture-output).

Format:
  [ACCESS] uid=52 u=HermesTest ip=27.38.220.251 POST /api/transactions
           req={"amount":350,"type":"expense"} → 200 45B 12ms
           ua=Mozilla/5.0 (iPhone; CPU iPhone OS 18_7)...

Static files (/expense-imgs/, /user-images/, root HTML) are skipped.
Response body is truncated to 300 chars and only logged for mutations (POST/PUT/DELETE/PATCH)
or error status codes (4xx/5xx).
"""

import sys
import json
import time
from flask import request, g


# ── Config ──

MAX_REQ_BODY = 500          # max chars for request body
MAX_RESP_BODY = 300         # max chars for response body
SKIP_PREFIXES = (            # skip these path prefixes (static assets)
    '/expense-imgs/',
    '/user-images/',
)
SKIP_EXACT = ('/', '/favicon.ico')   # skip exact paths


# ── Helpers ──

def _truncate(s, maxlen):
    """Truncate string to maxlen, adding '…' if cut."""
    if len(s) > maxlen:
        return s[:maxlen] + '…'
    return s


# ── Hooks ──

def _capture_request():
    """Called before each request. Stores request metadata on g."""
    g._req_start = time.time()
    g._req_body = None
    g._req_skip = True

    path = request.path

    # Only log /api/* requests and auth endpoints
    if not (path.startswith('/api/') or path in ('/login', '/register', '/verify', '/resend-code', '/forgot-password', '/reset-password', '/logout')):
        return

    g._req_skip = False
    g._req_method = request.method
    g._req_path = path
    g._req_qs = request.query_string.decode('utf-8', errors='replace') or ''

    # Capture request body for mutations
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        try:
            body = request.get_json(silent=True)
            if body is not None:
                g._req_body = _truncate(json.dumps(body, ensure_ascii=False), MAX_REQ_BODY)
        except Exception:
            g._req_body = None


def _log_response(response):
    """Called after each request. Logs request + response summary."""
    start = getattr(g, '_req_start', None)
    if start is None or getattr(g, '_req_skip', True):
        return response

    elapsed_ms = int((time.time() - start) * 1000)
    status = response.status_code
    method = getattr(g, '_req_method', request.method)

    # User identity (set by login_required or auth endpoints)
    user_id = getattr(g, 'user_id', None)
    username = getattr(g, 'username', None)

    # Client info
    ip = request.headers.get('X-Real-IP', request.remote_addr or '?')
    ua = (request.user_agent.string or '?')[:150]

    # Build log line
    parts = ['[ACCESS]']

    # Who
    uid = str(user_id) if user_id else '-'
    uname = str(username) if username else '-'
    parts.append(f'uid={uid} u={uname}')

    # From where
    parts.append(f'ip={ip}')

    # What
    path = getattr(g, '_req_path', request.path)
    qs = getattr(g, '_req_qs', '')
    url = path + ('?' + qs if qs else '')
    parts.append(f'{method} {url}')

    # Request body
    body = getattr(g, '_req_body', None)
    if body:
        parts.append(f'req={body}')

    # Response
    resp_len = 0
    resp_body = None
    is_mutation = method in ('POST', 'PUT', 'DELETE', 'PATCH')
    is_error = status >= 400

    if response.response:
        resp_len = len(response.response[0]) if isinstance(response.response, list) else 0
        # Only capture response body for mutations or errors
        if is_mutation or is_error:
            try:
                raw = response.get_data(as_text=True)
                resp_body = _truncate(raw, MAX_RESP_BODY)
            except Exception:
                pass

    parts.append(f'→ {status} {resp_len}B {elapsed_ms}ms')

    if resp_body:
        parts.append(f'resp={resp_body}')

    # Device
    parts.append(f'ua={ua}')

    line = ' | '.join(parts)
    print(line, file=sys.stderr, flush=True)

    return response
