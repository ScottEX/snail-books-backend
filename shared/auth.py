"""Authentication utilities — login_required decorator, session helpers.

Works with BOTH cookie sessions (Flask session) AND Bearer tokens (iOS WKWebView).
"""

import functools
from datetime import datetime, timezone
from flask import request, session, jsonify, redirect, g
from .i18n import t
from .db import get_db


def _session_expired(expires_at_str):
    """Check if an expires_at string (YYYY-MM-DD HH:MM:SS) is past (UTC)."""
    if not expires_at_str:
        return True
    try:
        expires = datetime.strptime(expires_at_str, '%Y-%m-%d %H:%M:%S')
        return datetime.now(timezone.utc).replace(tzinfo=None) > expires
    except (ValueError, TypeError):
        try:
            expires = datetime.fromisoformat(expires_at_str)
            return datetime.now(timezone.utc).replace(tzinfo=None) > expires
        except:
            return True


def login_required(f):
    @functools.wraps(f)
    def wrap(*a, **kw):
        validated_session_id = None
        kicked = False
        expired = False

        if 'user_id' not in session:
            # Check Bearer token as fallback
            auth = request.headers.get('Authorization', '')
            if auth.startswith('Bearer '):
                token = auth[7:]
                with get_db() as db:
                    row = db.execute(
                        'SELECT user_id, session_id FROM user_tokens WHERE token=?',
                        (token,)
                    ).fetchone()
                if row:
                    uid = row['user_id']
                    with get_db() as db:
                        exists = db.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
                    if exists:
                        token_sid = row['session_id']
                        if token_sid:
                            with get_db() as db:
                                srow = db.execute(
                                    'SELECT revoked_at, expires_at FROM user_sessions WHERE session_id=?',
                                    (token_sid,)
                                ).fetchone()
                            if srow and srow['revoked_at']:
                                kicked = True
                            elif srow and _session_expired(srow['expires_at']):
                                expired = True
                            elif srow:
                                validated_session_id = token_sid
                        session['user_id'] = uid
                        if token_sid:
                            session['session_id'] = token_sid
                    else:
                        with get_db() as db:
                            db.execute('DELETE FROM user_tokens WHERE token=?', (token,))
                            db.commit()
        else:
            cookie_sid = session.get('session_id')
            if cookie_sid:
                with get_db() as db:
                    srow = db.execute(
                        'SELECT revoked_at, expires_at FROM user_sessions WHERE session_id=?',
                        (cookie_sid,)
                    ).fetchone()
                if srow and srow['revoked_at']:
                    kicked = True
                elif srow and _session_expired(srow['expires_at']):
                    expired = True
                elif srow:
                    validated_session_id = cookie_sid

        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': t('err_session_expired', g.lang), 'code': 'session_expired'}), 401
            return redirect('/login')

        if kicked:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': t('err_session_kicked', g.lang) or 'Account logged in elsewhere', 'code': 'session_kicked'}), 401
            return redirect('/login')

        if expired:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': t('err_session_expired', g.lang), 'code': 'session_expired'}), 401
            return redirect('/login')

        g.user_id = session['user_id']
        g.username = session.get('username', '')

        with get_db() as db:
            exists = db.execute('SELECT id FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not exists:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': t('err_session_expired', g.lang), 'code': 'session_expired'}), 401
            return redirect('/login')

        # SSO enforcement
        with get_db() as db:
            cur = db.execute('SELECT current_session_id FROM users WHERE id=?', (g.user_id,)).fetchone()
        if cur and cur['current_session_id']:
            request_sid = session.get('session_id')
            if request_sid != cur['current_session_id']:
                kicked = True

        if validated_session_id:
            try:
                with get_db() as db:
                    db.execute(
                        "UPDATE user_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE session_id=?",
                        (validated_session_id,)
                    )
                    db.commit()
            except:
                pass

        return f(*a, **kw)
    return wrap
