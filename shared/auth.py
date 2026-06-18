"""Authentication utilities — login_required decorator, session helpers.

Works with BOTH cookie sessions (Flask session) AND Bearer tokens (iOS WKWebView).
"""

import functools
from datetime import datetime, timezone
from flask import request, session, jsonify, redirect, g
from .i18n import t
from .db import get_db
from .config import ADMIN_USER_ID, BG_DIR, EXPENSE_IMG_DIR


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
        # Moved to cron job (P1-YY/ZZ) — see cron job "清理过期删除用户"

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
                return jsonify({'status': 'error', 'message': '账户已被禁用，请联系管理员', 'code': 'account_disabled'}), 401
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
                        "UPDATE user_sessions SET last_seen_at=? WHERE session_id=?",
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), validated_session_id)
                    )
                    db.commit()
            except:
                pass

        return f(*a, **kw)
    return wrap


# ── User deletion ──

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
    """Remove avatar, background, cover images, and expense images for a user."""
    import os

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

    # Clean up per-user expense image directory (P1-WW)
    expense_user_dir = os.path.join(EXPENSE_IMG_DIR, str(user_id))
    if os.path.isdir(expense_user_dir):
        try:
            import shutil
            shutil.rmtree(expense_user_dir)
        except OSError:
            pass


# ── Grace period deletion ──

def _format_date_for_lang(date_str, lang):
    """Format '2026-06-10 03:33:53' to locale-aware string."""
    from datetime import datetime
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
    except Exception:
        return date_str
    if lang == 'en':
        months = ['January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December']
        return f"{months[dt.month - 1]} {dt.day}, {dt.year} {dt.strftime('%H:%M:%S')}"
    # zh-CN / zh-TW
    return f"{dt.year}年{dt.month}月{dt.day}日 {dt.strftime('%H:%M:%S')}"


def _sender_for_lang(lang):
    """Return trilingual sender name for the given language."""
    import re
    from shared.email import RESEND_FROM
    NAMES = {
        'zh-CN': '柳味探秘科技团队',
        'zh-TW': '柳味探秘科技團隊',
        'en': 'Liuwei Tech Team',
    }
    name = NAMES.get(lang, NAMES['zh-CN'])
    m = re.search(r'<([^>]+)>', RESEND_FROM)
    addr = m.group(1) if m else RESEND_FROM
    return f'{name} <{addr}>'


def _deletion_email(email_type, lang, email, scheduled_str):
    """Build trilingual deletion email (subject, body)."""
    if email_type == 'admin_notify':
        t = {
            'zh-CN': ('客户账户即将永久删除',
                       f'用户 {email} 的账户将于 {scheduled_str} 被永久删除。\n\n如需保留，请前往用户管理 → 用户详情页，点击「恢复账户」按钮。'),
            'zh-TW': ('客戶帳戶即將永久刪除',
                       f'用戶 {email} 的帳戶將於 {scheduled_str} 被永久刪除。\n\n如需保留，請前往用戶管理 → 用戶詳情頁，點擊「恢復帳戶」按鈕。'),
            'en': ('Customer Account Scheduled for Deletion',
                    f'User {email}\'s account will be permanently deleted on {scheduled_str}.\n\nTo keep it, go to User Management → User Details and click "Restore Account".'),
        }
    elif email_type == 'customer_admin_deleted':
        t = {
            'zh-CN': ('账户即将被删除',
                       f'管理员已将您的账户标记删除，将于 {scheduled_str} 被永久删除。\n\n如需保留，请尽快联系管理员。'),
            'zh-TW': ('帳戶即將被刪除',
                       f'管理員已將您的帳戶標記刪除，將於 {scheduled_str} 被永久刪除。\n\n如需保留，請盡快聯繫管理員。'),
            'en': ('Account Scheduled for Deletion',
                    f'Your account has been marked for deletion by the admin and will be permanently deleted on {scheduled_str}.\n\nTo keep it, please contact the admin as soon as possible.'),
        }
    else:  # customer_self_deleted
        t = {
            'zh-CN': ('账户即将永久删除',
                       f'您的账户将于 {scheduled_str} 被永久删除。\n\n如需保留，请在冷静期内登录即可自动恢复。'),
            'zh-TW': ('帳戶即將永久刪除',
                       f'您的帳戶將於 {scheduled_str} 被永久刪除。\n\n如需保留，請在冷靜期內登入即可自動恢復。'),
            'en': ('Account Scheduled for Permanent Deletion',
                    f'Your account will be permanently deleted on {scheduled_str}.\n\nTo keep it, simply log in during the grace period to auto-restore.'),
        }
    return t.get(lang, t['zh-CN'])


def _get_lang(db, user_id):
    """Get user's language preference, default zh-CN."""
    row = db.execute(
        "SELECT value FROM user_settings WHERE user_id=? AND key='lang'",
        (user_id,),
    ).fetchone()
    return row['value'] if row else 'zh-CN'


def schedule_delete(user_id, by_who, days):
    """Mark user for deletion after a grace period (disabled + scheduled).
    Also sends immediate notification emails (trilingual)."""
    from datetime import datetime, timedelta
    from .db import get_db
    from shared.email import _send_email

    scheduled = (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db() as db:
        db.execute(
            'UPDATE users SET is_disabled=1, delete_scheduled=?, delete_by=? WHERE id=?',
            (scheduled, by_who, user_id)
        )
        db.commit()

        # Get user details for email
        user = db.execute(
            'SELECT email, username FROM users WHERE id=?', (user_id,)
        ).fetchone()
        if not user or not user['email']:
            return scheduled

        user_lang = _get_lang(db, user_id)
        user_scheduled_str = _format_date_for_lang(scheduled, user_lang)
        user_from = _sender_for_lang(user_lang)

        if by_who == 'admin':
            # Notify admin (in admin's language)
            admin_row = db.execute(
                f"SELECT email FROM users WHERE id={ADMIN_USER_ID}"
            ).fetchone()
            if admin_row and admin_row['email']:
                admin_lang = _get_lang(db, ADMIN_USER_ID)
                admin_scheduled_str = _format_date_for_lang(scheduled, admin_lang)
                admin_from = _sender_for_lang(admin_lang)
                subj, body = _deletion_email('admin_notify', admin_lang, user['email'], admin_scheduled_str)
                _send_email(admin_row['email'], subj, body, '', from_addr=admin_from)

            # Notify customer (in customer's language)
            subj, body = _deletion_email('customer_admin_deleted', user_lang, user['email'], user_scheduled_str)
            _send_email(user['email'], subj, body, '', from_addr=user_from)
        else:
            # Self-deleted → notify user
            subj, body = _deletion_email('customer_self_deleted', user_lang, user['email'], user_scheduled_str)
            _send_email(user['email'], subj, body, '', from_addr=user_from)

    return scheduled


def send_deletion_reminders():
    """Send email reminder 8 hours before scheduled deletion (trilingual)."""
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

        admin_row = db.execute(f"SELECT email FROM users WHERE id={ADMIN_USER_ID}").fetchone()
        admin_email = admin_row['email'] if admin_row else ''
        admin_lang = _get_lang(db, ADMIN_USER_ID) if admin_email else 'zh-CN'

    for user in due:
        raw_date = user['delete_scheduled'] if user['delete_scheduled'] else ''

        if user['delete_by'] == 'admin':
            # Admin deleted → remind the admin
            if not admin_email:
                continue
            scheduled_str = _format_date_for_lang(raw_date, admin_lang)
            from_addr = _sender_for_lang(admin_lang)
            subj, body = _deletion_email('admin_notify', admin_lang, user['email'], scheduled_str)
            to_email = admin_email
        else:
            # Self-deleted → remind the user
            with get_db() as db:
                user_lang = _get_lang(db, user['id'])
            scheduled_str = _format_date_for_lang(raw_date, user_lang)
            from_addr = _sender_for_lang(user_lang)
            subj, body = _deletion_email('customer_self_deleted', user_lang, user['email'], scheduled_str)
            to_email = user['email']

        if _send_email(to_email, subj, body, '', from_addr=from_addr):
            with get_db() as db:
                db.execute('UPDATE users SET delete_reminded=1 WHERE id=?', (user['id'],))
                db.commit()


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
