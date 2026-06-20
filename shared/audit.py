"""Audit logging — unified request-level audit trail for key operations.

Usage:
    from shared.audit import audit

    # Within a @login_required endpoint (uses g.user_id, g.username, g.trace_id):
    audit('CREATE_TRANSACTION', extra='amount=350, category=食材')

    # Outside @login_required (login/register), pass explicit user info:
    audit('LOGIN', user_id=user['id'], username=user['username'])

Format:
    YYYY-MM-DD HH:MM:SS.mmm INFO [TID:trace_id] [AUDIT] user_id=X username=Y action=Z [extra]

Output goes to stderr, which gunicorn captures into its error log.
Also routed through Python standard logging (WARNING) for integration with log aggregators.
"""

import sys
import logging
from datetime import datetime, timezone, timedelta


_SENSITIVE_MASK = '***'

_ACTION_LEVELS = {
    # WARN for security-sensitive / destructive actions
    'LOGOUT': 'WARN',
    'LOGIN_WEBATHN': 'WARN',
    'SELF_DELETE': 'WARN',
    'ADMIN_DELETE_USER': 'WARN',
    'ADMIN_DISABLE_USER': 'WARN',
    'ADMIN_RESTORE_USER': 'WARN',
    'REGISTER_WEBAUTHN': 'WARN',
    'UNREGISTER_WEBAUTHN': 'WARN',
    'CHANGE_PASSWORD': 'WARN',
    'CHANGE_EMAIL': 'WARN',
    'UPDATE_AUTH_PREFS': 'WARN',
}


def _now_iso() -> str:
    """Timestamp like '2026-06-20 12:29:40.432' in Beijing time."""
    dt = datetime.now(timezone.utc).replace(tzinfo=None)
    dt = dt + timedelta(hours=8)
    return dt.strftime('%Y-%m-%d %H:%M:%S.') + f'{dt.microsecond // 1000:03d}'


def _get_trace_id():
    """Get trace_id from flask.g if available, else '000000000000'."""
    try:
        from flask import g
        return getattr(g, 'trace_id', '000000000000')
    except Exception:
        return '000000000000'


def audit(action, user_id=None, username=None, extra=None):
    """Log an audit event with unified format including trace_id and timestamp.

    If user_id/username are omitted, reads from flask.g (set by @login_required).
    """
    if user_id is None:
        try:
            from flask import g
            user_id = getattr(g, 'user_id', None)
        except Exception:
            user_id = None
    if username is None:
        try:
            from flask import g
            username = getattr(g, 'username', None)
        except Exception:
            username = None

    uid = str(user_id) if user_id else '?'
    name = str(username) if username else '?'
    trace_id = _get_trace_id()
    level = _ACTION_LEVELS.get(action, 'INFO')

    parts = [f'{_now_iso()} {level} [TID:{trace_id}] [AUDIT]']
    parts.append(f'user_id={uid} username={name} action={action}')
    if extra:
        parts.append(extra)
    msg = '\t'.join(parts)

    # Write to stderr (captured by gunicorn)
    print(msg, file=sys.stderr, flush=True)
    # Also route through standard logging
    try:
        log = logging.getLogger('app')
        if level == 'WARN':
            log.warning(msg)
        elif level == 'ERROR':
            log.error(msg)
        else:
            log.info(msg)
    except Exception:
        pass
