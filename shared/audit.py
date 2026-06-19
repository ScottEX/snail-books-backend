"""Audit logging — unified request-level audit trail for key operations.

Usage:
    from shared.audit import audit

    # Within a @login_required endpoint (uses g.user_id, g.username):
    audit('CREATE_TRANSACTION', extra='amount=350, category=食材')

    # Outside @login_required (login/register), pass explicit user info:
    audit('LOGIN', user_id=user['id'], username=user['username'])

Log format:
    [AUDIT] user_id=<id> username=<name> action=<ACTION> [extra]

Output goes to stderr, which gunicorn captures into its error log.
"""

import sys
import logging


def audit(action, user_id=None, username=None, extra=None):
    """Log an audit event.

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

    parts = [f'[AUDIT] user_id={uid} username={name} action={action}']
    if extra:
        parts.append(extra)
    msg = ' '.join(parts)

    # Write to stderr (captured by gunicorn) + also try standard logging
    print(msg, file=sys.stderr, flush=True)
    try:
        logging.getLogger('app').warning(msg)
    except Exception:
        pass
