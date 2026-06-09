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

        # Clean up any expired scheduled deletions (lightweight, rare)
        cleanup_expired_deletions()
        send_deletion_reminders()

        with get_db() as db:
            user = db.execute('SELECT id, is_disabled FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not user:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': t('err_session_expired', g.lang), 'code': 'session_expired'}), 401
            return redirect('/login')

        if user['is_disabled']:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': '账户已被禁用，请联系管理员', 'code': 'account_disabled'}), 403
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


# ── User deletion ──

ADMIN_USER_ID = '64'


def delete_user_cascade(user_id):
    """Delete user: transfer business data to admin, remove personal data + files."""
    import os
    from .db import get_db

    with get_db() as db:
        # 1. Transfer business data to admin
        business_tables = [
            'transactions', 'dividends', 'products',
            'procurements', 'procurement_batches', 'procurement_items',
            'reconciliations', 'daily_revenue', 'partners',
        ]
        for table in business_tables:
            db.execute(f'UPDATE {table} SET user_id=? WHERE user_id=?',
                       (ADMIN_USER_ID, user_id))

        # 2. Delete personal data
        db.execute('DELETE FROM user_tokens WHERE user_id=?', (user_id,))
        db.execute('DELETE FROM user_sessions WHERE user_id=?', (user_id,))
        db.execute('DELETE FROM user_settings WHERE user_id=?', (user_id,))

        # 3. Delete user
        db.execute('DELETE FROM users WHERE id=?', (user_id,))
        db.commit()

    # 4. Delete disk files
    _delete_user_files(user_id)


def _delete_user_files(user_id):
    """Remove avatar, background, and cover images for a user."""
    import os
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BG_DIR = os.environ.get('BG_DIR', os.path.join(PROJECT_ROOT, 'user-images'))

    files_to_remove = [
        os.path.join(BG_DIR, 'avatars', f'{user_id}.jpg'),
        os.path.join(BG_DIR, 'covers', f'cover-{user_id}.jpg'),
        os.path.join(BG_DIR, f'home-bg-{user_id}.jpg'),
    ]
    for path in files_to_remove:
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


# ── Grace period deletion ──

def schedule_delete(user_id, by_who, days):
    """Mark user for deletion after a grace period (disabled + scheduled)."""
    from datetime import datetime, timedelta
    from .db import get_db

    scheduled = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as db:
        db.execute(
            'UPDATE users SET is_disabled=1, delete_scheduled=?, delete_by=? WHERE id=?',
            (scheduled, by_who, user_id)
        )
        db.commit()
    return scheduled


def cancel_delete(user_id):
    """Cancel scheduled deletion, re-enable user."""
    from .db import get_db

    with get_db() as db:
        db.execute(
            "UPDATE users SET is_disabled=0, delete_scheduled=NULL, delete_by='', delete_reminded=0 WHERE id=?",
            (user_id,)
        )
        db.commit()


def cleanup_expired_deletions():
    """Delete users whose grace period has expired. Call periodically."""
    import os
    from .db import get_db
    from datetime import datetime

    with get_db() as db:
        expired = db.execute(
            "SELECT id FROM users WHERE delete_scheduled IS NOT NULL AND delete_scheduled <= datetime('now', 'localtime')"
        ).fetchall()

    for row in expired:
        delete_user_cascade(row['id'])


def send_deletion_reminders():
    """Send email reminder 8 hours before scheduled deletion."""
    from .db import get_db
    from shared.email import _send_email

    with get_db() as db:
        due = db.execute(
            """SELECT id, email, delete_scheduled, delete_by FROM users
               WHERE delete_scheduled IS NOT NULL
                 AND delete_reminded = 0
                 AND delete_scheduled <= datetime('now', 'localtime', '+8 hours')
                 AND delete_scheduled > datetime('now', 'localtime')"""
        ).fetchall()

    for user in due:
        scheduled_date = user['delete_scheduled'][:10] if user['delete_scheduled'] else ''
        by_who = '管理员' if user['delete_by'] == 'admin' else '您'
        subject = '账户即将永久删除'
        body = f'您的账户将于 {scheduled_date} 被{by_who}永久删除。如需保留账户，请尽快登录或联系管理员。'
        if _send_email(user['email'], subject, body, ''):
            with get_db() as db:
                db.execute('UPDATE users SET delete_reminded=1 WHERE id=?', (user['id'],))
                db.commit()
