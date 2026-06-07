"""Auth routes — login, register, verify, forgot, reset, logout."""

import secrets
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, session, g
from werkzeug.security import generate_password_hash, check_password_hash

from shared.db import get_db
from shared.i18n import t
from shared.auth import login_required
from shared.email import DEV_MODE, generate_code, send_verification_email, send_reset_email
from shared.rate_limit import check_rate_limit, record_failed_attempt, check_forgot_limit, record_forgot_attempt
from shared.validation import validate_password, validate_username, validate_required, validate_email

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    from flask import current_app
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    remember = data.get('remember', False)
    if not username or not password:
        return jsonify({'status': 'error', 'message': t('err_empty_fields', g.lang)}), 400

    ip = request.remote_addr or 'unknown'
    allowed, wait = check_rate_limit(ip)
    if not allowed:
        mins = wait // 60
        secs = wait % 60
        return jsonify({'status': 'error', 'message': t('err_too_many_attempts', g.lang, mins=mins, secs=secs) or f'Too many attempts. Please wait {mins}m{secs}s.'}), 429

    with get_db() as db:
        user = db.execute(
            'SELECT * FROM users WHERE username=? OR email=?',
            (username, username.lower())
        ).fetchone()
        if user and check_password_hash(user['password'], password):
            if not user['is_verified']:
                return jsonify({'status': 'error', 'message': t('err_need_verify', g.lang), 'need_verify': True, 'email': user['email']}), 403

            enforce_sso = int(user['enforce_single_session']) if user['enforce_single_session'] is not None else 1
            timeout_hours = int(user['session_timeout_hours']) if user['session_timeout_hours'] else 1
            if timeout_hours < 1:
                timeout_hours = 1
            session.permanent = True
            # Cookie lifetime: use the 24h process default (set at app init).
            # Per-user timeout is enforced authoritatively in login_required via
            # user_sessions.expires_at. Do NOT mutate app.permanent_session_lifetime
            # here — it's process-global and clobbers multi-user isolation.
            new_session_id = secrets.token_hex(16)
            expires_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=timeout_hours)).strftime('%Y-%m-%d %H:%M:%S')
            device_info = (request.user_agent.string or '')[:200]

            if enforce_sso:
                db.execute(
                    "UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL",
                    (user['id'],)
                )
                db.execute(
                    "DELETE FROM user_tokens WHERE user_id=? AND (session_id IS NULL OR session_id IN (SELECT session_id FROM user_sessions WHERE user_id=? AND revoked_at IS NOT NULL))",
                    (user['id'], user['id'])
                )
                db.execute('UPDATE users SET current_session_id=? WHERE id=?', (new_session_id, user['id']))

            db.execute(
                'INSERT INTO user_sessions (user_id, session_id, device_info, expires_at) VALUES (?,?,?,?)',
                (user['id'], new_session_id, device_info, expires_at)
            )
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['session_id'] = new_session_id

            db.execute("DELETE FROM user_tokens WHERE created_at < datetime('now', '-90 days')")
            token = secrets.token_hex(32)
            db.execute('INSERT INTO user_tokens (user_id, token, session_id) VALUES (?,?,?)', (user['id'], token, new_session_id))
            db.commit()
            return jsonify({'status': 'ok', 'token': token, 'username': user['username'], 'user_id': user['id']})

    record_failed_attempt(ip)
    return jsonify({'status': 'error', 'message': t('err_wrong_credentials', g.lang)}), 401


@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip().lower()
    if not username or not password:
        return jsonify({'status': 'error', 'message': t('err_empty_fields', g.lang)}), 400
    if not email:
        return jsonify({'status': 'error', 'message': t('err_email_required', g.lang)}), 400
    if not validate_email(email):
        return jsonify({'status': 'error', 'message': t('err_email_invalid', g.lang) or 'Invalid email format'}), 400
    if not validate_username(username):
        return jsonify({'status': 'error', 'message': t('err_username_invalid', g.lang) or '用户名仅支持字母、数字、下划线和中文，2-32位'}), 400
    ok, msg = validate_password(password, g.lang)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 400

    with get_db() as db:
        exists = db.execute('SELECT id, is_verified FROM users WHERE username=? OR email=?', (username, email)).fetchone()
        if exists:
            if exists['is_verified']:
                return jsonify({'status': 'error', 'message': t('err_username_exists', g.lang)}), 409
            db.execute('DELETE FROM users WHERE id=?', (exists['id'],))

        code = generate_code()
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        db.execute(
            'INSERT INTO users (username,password,email,verification_code,code_expires,is_verified) VALUES (?,?,?,?,?,0)',
            (username, generate_password_hash(password), email, code, expires)
        )
        db.commit()
        if not send_verification_email(email, code, g.lang):
            return jsonify({'status': 'error', 'message': t('err_code_send_failed', g.lang)}), 500

    resp = {'status': 'ok', 'message': t('msg_code_sent', g.lang, email=email), 'email': email}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp), 201


@auth_bp.route('/verify', methods=['POST'])
def verify_email():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    if not email or not code:
        return jsonify({'status': 'error', 'message': t('err_empty_email_code', g.lang)}), 400
    with get_db() as db:
        user = db.execute(
            'SELECT * FROM users WHERE email=? AND verification_code=? AND is_verified=0',
            (email, code)
        ).fetchone()
        if not user:
            return jsonify({'status': 'error', 'message': t('err_wrong_code', g.lang)}), 401
        if datetime.now(timezone.utc).replace(tzinfo=None) > datetime.fromisoformat(user['code_expires']):
            return jsonify({'status': 'error', 'message': t('err_code_expired', g.lang)}), 410
        db.execute('UPDATE users SET is_verified=1, verification_code=NULL, code_expires=NULL WHERE id=?', (user['id'],))
        db.commit()
    return jsonify({'status': 'ok', 'message': t('msg_verify_ok', g.lang)})


@auth_bp.route('/resend-code', methods=['POST'])
def resend_code():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'status': 'error', 'message': t('err_email_required', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=0', (email,)).fetchone()
        if not user:
            return jsonify({'status': 'ok', 'message': t('msg_code_resent', g.lang)})
        code = generate_code()
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        db.execute('UPDATE users SET verification_code=?, code_expires=? WHERE id=?', (code, expires, user['id']))
        db.commit()
        if not send_verification_email(email, code, g.lang):
            return jsonify({'status': 'error', 'message': t('err_resend_failed', g.lang)}), 500
    resp = {'status': 'ok', 'message': t('msg_code_resent', g.lang)}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp)


@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'status': 'error', 'message': t('err_email_required', g.lang)}), 400
    if not validate_email(email):
        return jsonify({'status': 'error', 'message': t('err_email_invalid', g.lang) or 'Invalid email format'}), 400
    ip = request.remote_addr or 'unknown'
    allowed, wait = check_forgot_limit(ip)
    if not allowed:
        mins = wait // 60
        secs = wait % 60
        return jsonify({'status': 'error', 'message': t('err_too_many_attempts', g.lang, mins=mins, secs=secs) or f'Too many attempts. Please wait {mins}m{secs}s.'}), 429

    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=1', (email,)).fetchone()
        if not user:
            record_forgot_attempt(ip)
            return jsonify({'status': 'ok', 'message': t('msg_forgot_sent', g.lang), 'email': email})
        code = generate_code()
        expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
        db.execute('UPDATE users SET reset_code=?, reset_expires=? WHERE id=?', (code, expires, user['id']))
        db.commit()
        if not send_reset_email(email, code, g.lang):
            return jsonify({'status': 'error', 'message': t('err_code_send_failed', g.lang)}), 500
    resp = {'status': 'ok', 'message': t('msg_code_sent', g.lang, email=email), 'email': email}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp)


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    new_password = data.get('password', '')
    if not email or not code or not new_password:
        return jsonify({'status': 'error', 'message': t('err_incomplete', g.lang)}), 400
    ok, msg = validate_password(new_password, g.lang)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND reset_code=? AND is_verified=1', (email, code)).fetchone()
        if not user:
            return jsonify({'status': 'error', 'message': t('err_wrong_code', g.lang)}), 401
        if datetime.now(timezone.utc).replace(tzinfo=None) > datetime.fromisoformat(user['reset_expires']):
            return jsonify({'status': 'error', 'message': t('err_reset_code_expired', g.lang)}), 410
        db.execute('UPDATE users SET password=?, reset_code=NULL, reset_expires=NULL WHERE id=?',
                   (generate_password_hash(new_password), user['id']))
        db.commit()
    return jsonify({'status': 'ok', 'message': t('msg_reset_ok', g.lang)})


@auth_bp.route('/logout', methods=['POST'])
@login_required
def logout():
    sid = session.get('session_id')
    if sid:
        try:
            with get_db() as db:
                db.execute('DELETE FROM user_sessions WHERE session_id=?', (sid,))
                db.commit()
        except:
            pass
    if sid:
        try:
            with get_db() as db:
                db.execute('DELETE FROM user_tokens WHERE session_id=?', (sid,))
                db.commit()
        except:
            pass
    session.clear()
    return jsonify({'status': 'ok'})


@auth_bp.route('/logout', methods=['GET'])
def logout_get():
    """GET /logout — reject with 405 to prevent CSRF."""
    return jsonify({'status': 'error', 'message': 'Use POST /logout'}), 405
