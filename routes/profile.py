"""Profile routes — user info, avatar, cover, password, email, auth prefs."""

import os, re, time
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, session, g, send_file
from werkzeug.security import generate_password_hash, check_password_hash

from shared.db import get_db
from shared.auth import login_required, schedule_delete, ADMIN_USER_ID
from shared.i18n import t
from shared.email import generate_code, send_email_change_code
from shared.validation import validate_password
from shared.config import BG_DIR

profile_bp = Blueprint('profile', __name__)

AVATAR_DIR = os.path.join(BG_DIR, 'avatars')
COVER_DIR = os.path.join(BG_DIR, 'covers')
ALLOWED_BG_EXT = {'jpg', 'jpeg', 'png', 'webp'}
MAX_BG_SIZE = 5 * 1024 * 1024


def _to_pinyin(name: str) -> str:
    """Convert Chinese name to pinyin. e.g. '蓝柳富' → 'Liu-Fu Lan'"""
    if not name:
        return ''
    try:
        from pypinyin import pinyin, Style
        parts = [p[0] for p in pinyin(name, style=Style.NORMAL)]
        if len(parts) <= 1:
            return parts[0].capitalize() if parts else ''
        # 名（连字符） + 空格 + 姓
        surname = parts[0].capitalize()
        given = '-'.join(p.capitalize() for p in parts[1:])
        return f'{given} {surname}'
    except ImportError:
        return name


def _to_traditional(name: str) -> str:
    """Convert Simplified Chinese name to Traditional. e.g. '蓝柳富' → '藍柳富'"""
    if not name:
        return ''
    try:
        from opencc import OpenCC
        return OpenCC('s2t').convert(name)
    except ImportError:
        return name


# ── User info ──

@profile_bp.route('/users/me')
@login_required
def users_me():
    with get_db() as db:
        user = db.execute(
            '''SELECT u.id, u.username, u.email, u.signature, u.real_name, u.created_at,
                      u.enforce_single_session, u.session_timeout_hours,
                      p.name as partner_name
               FROM users u
               LEFT JOIN partners p ON p.linked_user_id = u.id
               WHERE u.id=?''',
            (g.user_id,)
        ).fetchone()
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    d = dict(user)
    if d.get('real_name'):
        d['real_name_pinyin'] = _to_pinyin(d['real_name'])
        d['real_name_tw'] = _to_traditional(d['real_name'])
    if d.get('enforce_single_session') is None:
        d['enforce_single_session'] = 1
    if d.get('session_timeout_hours') is None:
        d['session_timeout_hours'] = 1
    return jsonify(d)


@profile_bp.route('/users')
@login_required
def users_list():
    with get_db() as db:
        rows = db.execute('SELECT id, username FROM users WHERE is_verified=1 ORDER BY username').fetchall()
    return jsonify([dict(r) for r in rows])


# ── Auth prefs (SSO + session timeout) ──

@profile_bp.route('/users/me/auth-prefs', methods=['GET', 'PATCH'])
@login_required
def auth_prefs():
    if request.method == 'GET':
        with get_db() as db:
            row = db.execute('SELECT enforce_single_session, session_timeout_hours FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        d = dict(row)
        if d.get('enforce_single_session') is None:
            d['enforce_single_session'] = 1
        if d.get('session_timeout_hours') is None:
            d['session_timeout_hours'] = 1
        return jsonify(d)

    # PATCH
    data = request.get_json() or {}
    enforce_sso = data.get('enforce_single_session')
    timeout_hours = data.get('session_timeout_hours')
    if enforce_sso is None and timeout_hours is None:
        return jsonify({'status': 'error', 'message': t('err_empty_fields', g.lang)}), 400
    if enforce_sso is not None and enforce_sso not in (0, 1):
        return jsonify({'status': 'error', 'message': 'enforce_single_session must be 0 or 1'}), 400
    if timeout_hours is not None:
        try:
            timeout_hours = int(timeout_hours)
        except (TypeError, ValueError):
            return jsonify({'status': 'error', 'message': 'session_timeout_hours must be an integer'}), 400
        if timeout_hours not in (1, 2, 6, 24):
            return jsonify({'status': 'error', 'message': 'session_timeout_hours must be one of 1, 2, 6, 24'}), 400

    with get_db() as db:
        if enforce_sso is not None:
            if int(enforce_sso) == 1:
                cur_sid = session.get('session_id')
                if cur_sid is None:
                    auth = request.headers.get('Authorization', '')
                    if auth.startswith('Bearer '):
                        tk = auth[7:]
                        row = db.execute('SELECT session_id FROM user_tokens WHERE token=?', (tk,)).fetchone()
                        if row and row['session_id']:
                            cur_sid = row['session_id']
                if cur_sid:
                    db.execute(
                        'UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND session_id != ?',
                        (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), g.user_id, cur_sid)
                    )
                    db.execute(
                        'DELETE FROM user_tokens WHERE user_id=? AND (session_id IS NULL OR session_id != ?)',
                        (g.user_id, cur_sid)
                    )
                db.execute('UPDATE users SET enforce_single_session=?, current_session_id=? WHERE id=?', (1, cur_sid, g.user_id))
            else:
                db.execute('UPDATE users SET enforce_single_session=?, current_session_id=NULL WHERE id=?', (0, g.user_id))
        if timeout_hours is not None:
            db.execute('UPDATE users SET session_timeout_hours=? WHERE id=?', (int(timeout_hours), g.user_id))
        db.commit()
        row = db.execute('SELECT enforce_single_session, session_timeout_hours, current_session_id FROM users WHERE id=?', (g.user_id,)).fetchone()
    d = dict(row)
    if d.get('enforce_single_session') is None:
        d['enforce_single_session'] = 1
    if d.get('session_timeout_hours') is None:
        d['session_timeout_hours'] = 1
    return jsonify({'status': 'ok', **d})


# ── Signature ──

@profile_bp.route('/users/signature', methods=['POST'])
@login_required
def update_signature():
    data = request.get_json() or {}
    signature = (data.get('signature', '') or '').strip()
    if len(signature) > 200:
        return jsonify({'status': 'error', 'message': '签名不能超过200字'}), 400
    with get_db() as db:
        db.execute('UPDATE users SET signature=? WHERE id=?', (signature, g.user_id))
        db.commit()
        from shared.audit import audit
        audit('UPDATE_SIGNATURE')
    return jsonify({'status': 'ok', 'signature': signature})


# ── Delete user ──

@profile_bp.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
def delete_user(uid):
    """Self-delete: 3-day grace period. Login within 3 days auto-restores."""
    if str(uid) != str(g.user_id):
        return jsonify({'status': 'error', 'message': '只能注销自己的账户'}), 403

    if str(uid) == ADMIN_USER_ID:
        return jsonify({'status': 'error', 'message': t('err_admin_cannot_delete', g.lang)}), 400

    with get_db() as db:
        user = db.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if not user:
            return jsonify({'status': 'error', 'message': '用户不存在'}), 404

    scheduled = schedule_delete(uid, 'self', 3)
    from shared.audit import audit
    audit('SELF_DELETE', extra=f'uid={uid}')
    return jsonify({
        'status': 'ok',
        'message': f'您的账户已进入 3 天冷静期，将于 {scheduled[:10]} 永久注销。在此期间登录即可自动恢复账户。',
        'scheduled': scheduled,
    })


# ── Avatar ──

@profile_bp.route('/users/avatar', methods=['GET'])
def get_avatar():
    """Public: get avatar by username, email, or user_id."""
    username = request.args.get('username', '')
    email = request.args.get('email', '')
    user_id = request.args.get('user_id', '')
    if not username and not email and not user_id:
        return jsonify({'status': 'error', 'message': 'username, email, or user_id required'}), 400
    with get_db() as db:
        if user_id:
            user = db.execute('SELECT id FROM users WHERE id=?', (int(user_id),)).fetchone()
        elif email:
            user = db.execute('SELECT id FROM users WHERE email=LOWER(?)', (email,)).fetchone()
        else:
            user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    if not user:
        return '', 404
    for ext in ('jpg', 'jpeg', 'png', 'webp'):
        path = os.path.join(AVATAR_DIR, f'{user["id"]}.{ext}')
        if os.path.isfile(path):
            return send_file(path, mimetype=f'image/{ext if ext != "jpg" else "jpeg"}')
    return '', 404


# ── Background (public) ──

@profile_bp.route('/users/background', methods=['GET'])
def get_background():
    """Public: get user background image by username or user_id."""
    username = request.args.get('username', '')
    user_id = request.args.get('user_id', '')
    if not username and not user_id:
        return '', 404
    with get_db() as db:
        if user_id:
            user = db.execute('SELECT id FROM users WHERE id=?', (int(user_id),)).fetchone()
        else:
            user = db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
    if not user:
        return '', 404
    bg_path = os.path.join(BG_DIR, f'home-bg-{user["id"]}.jpg')
    if os.path.isfile(bg_path):
        return send_file(bg_path, mimetype='image/jpeg')
    return '', 404


@profile_bp.route('/users/avatar', methods=['POST'])
@login_required
def upload_avatar():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': '未选择文件'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'status': 'error', 'message': '文件名为空'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return jsonify({'status': 'error', 'message': '仅支持 jpg/png/webp'}), 400
    os.makedirs(AVATAR_DIR, exist_ok=True)
    for old_ext in ('jpg', 'jpeg', 'png', 'webp'):
        old = os.path.join(AVATAR_DIR, f'{g.user_id}.{old_ext}')
        if os.path.isfile(old):
            os.remove(old)
    f.save(os.path.join(AVATAR_DIR, f'{g.user_id}.{ext}'))
    return jsonify({'status': 'ok', 'url': f'/user-images/avatars/{g.user_id}.{ext}?t={int(time.time())}'})


# ── Profile cover ──

@profile_bp.route('/profile/cover', methods=['GET', 'POST', 'DELETE'])
@login_required
def profile_cover():
    if request.method == 'GET':
        url = None
        save_path = os.path.join(COVER_DIR, f'cover-{g.user_id}.jpg')
        if os.path.exists(save_path):
            url = f'/user-images/covers/cover-{g.user_id}.jpg?t={int(os.path.getmtime(save_path))}'
        return jsonify({'url': url})

    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '未选择文件'}), 400
        f = request.files['file']
        if f.filename == '':
            return jsonify({'status': 'error', 'message': '文件名为空'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_BG_EXT:
            return jsonify({'status': 'error', 'message': '仅支持 ' + ', '.join(sorted(ALLOWED_BG_EXT)) + ' 格式'}), 400
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        if size > MAX_BG_SIZE:
            return jsonify({'status': 'error', 'message': '文件最大 5MB'}), 400
        os.makedirs(COVER_DIR, exist_ok=True)
        save_path = os.path.join(COVER_DIR, f'cover-{g.user_id}.jpg')
        f.save(save_path)
        url = f'/user-images/covers/cover-{g.user_id}.jpg?t={int(time.time())}'
        return jsonify({'status': 'ok', 'url': url})

    if request.method == 'DELETE':
        save_path = os.path.join(COVER_DIR, f'cover-{g.user_id}.jpg')
        if os.path.exists(save_path):
            os.remove(save_path)
        return jsonify({'status': 'ok'})


# ── Change password ──

@profile_bp.route('/profile/password', methods=['POST'])
@login_required
def change_password():
    data = request.get_json()
    old_pw = data.get('old_password', '') if data else ''
    new_pw = data.get('new_password', '') if data else ''
    if not old_pw or not new_pw:
        return jsonify({'status': 'error', 'message': '请填写所有字段'}), 400
    ok, err = validate_password(new_pw, g.lang)
    if not ok:
        return jsonify({'status': 'error', 'message': err}), 400
    with get_db() as db:
        user = db.execute('SELECT password FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not user or not check_password_hash(user['password'], old_pw):
            return jsonify({'status': 'error', 'message': '当前密码错误'}), 400
        db.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(new_pw), g.user_id))
        # Revoke all other sessions — keep current one (user just verified old password)
        cur_sid = session.get('session_id', '')
        db.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND session_id!=?", (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), g.user_id, cur_sid))
        db.execute("DELETE FROM user_tokens WHERE user_id=? AND (session_id IS NULL OR session_id!=?)", (g.user_id, cur_sid))
        db.commit()
        from shared.audit import audit
        audit('CHANGE_PASSWORD')
    return jsonify({'status': 'ok', 'message': '密码修改成功'})


# ── Change email ──

@profile_bp.route('/profile/email/send-code', methods=['POST'])
@login_required
def profile_email_send_code():
    data = request.get_json()
    new_email = data.get('email', '').strip() if data else ''
    if not new_email:
        return jsonify({'status': 'error', 'message': '请输入新邮箱'}), 400
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', new_email):
        return jsonify({'status': 'error', 'message': t('err_email_invalid', g.lang)}), 400
    with get_db() as db:
        existing = db.execute('SELECT id FROM users WHERE email=? AND id!=?', (new_email, g.user_id)).fetchone()
        if existing:
            return jsonify({'status': 'error', 'message': t('err_email_registered', g.lang)}), 400
        code = generate_code()
        db.execute('UPDATE users SET verification_code=?, code_expires=? WHERE id=?',
                   (code, datetime.now(timezone.utc) + timedelta(minutes=10), g.user_id))
        db.commit()
    send_email_change_code(new_email, code, g.lang)
    return jsonify({'status': 'ok', 'message': '验证码已发送'})


@profile_bp.route('/profile/email/verify', methods=['POST'])
@login_required
def profile_email_verify():
    data = request.get_json()
    new_email = data.get('email', '').strip() if data else ''
    code = data.get('code', '').strip() if data else ''
    if not new_email or not code:
        return jsonify({'status': 'error', 'message': '请填写所有字段'}), 400
    with get_db() as db:
        # Re-check for race condition: email may have been taken between send-code and verify
        existing = db.execute('SELECT id FROM users WHERE email=? AND id!=?', (new_email, g.user_id)).fetchone()
        if existing:
            return jsonify({'status': 'error', 'message': t('err_email_registered', g.lang)}), 400
        user = db.execute('SELECT verification_code, code_expires FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not user or user['verification_code'] != code:
            return jsonify({'status': 'error', 'message': t('err_wrong_code', g.lang)}), 400
        if user['code_expires'] and datetime.now(timezone.utc).replace(tzinfo=None) > datetime.fromisoformat(user['code_expires']):
            return jsonify({'status': 'error', 'message': '验证码已过期'}), 400
        db.execute('UPDATE users SET email=?, verification_code=NULL, code_expires=NULL WHERE id=?',
                   (new_email, g.user_id))
        # Revoke all other sessions — keep current one (user just verified code)
        cur_sid = session.get('session_id', '')
        db.execute("UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL AND session_id!=?", (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), g.user_id, cur_sid))
        db.execute("DELETE FROM user_tokens WHERE user_id=? AND (session_id IS NULL OR session_id!=?)", (g.user_id, cur_sid))
        db.commit()
    return jsonify({'status': 'ok', 'message': '邮箱修改成功'})
