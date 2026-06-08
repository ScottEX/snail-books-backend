#!/usr/bin/env python3
"""🍜 蓝姐 · 记账系统"""

import sqlite3, os, secrets, functools, re, json, time, mimetypes
from datetime import datetime, date
from contextlib import contextmanager
from datetime import datetime, timedelta

try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found,import]
    HAS_PIL = True
except ImportError:
    _PILImage = None  # type: ignore
    HAS_PIL = False

from flask import Flask, request, jsonify, session, g, make_response, send_file
from i18n_backend import get_lang, t as _t

app = Flask(__name__)
_secret = os.environ.get('FLASK_SECRET_KEY')
if not _secret:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is required -- generate with: python3 -c 'import secrets; print(secrets.token_hex(32))'")
app.secret_key = _secret
app.permanent_session_lifetime = timedelta(hours=24)

FRONTEND_VERSION = '1'
FRONTEND_DIR = os.environ.get(
    'FRONTEND_DIR',
    os.path.join(os.path.dirname(__file__), 'static', 'web-build', 'dist'),
)
EXPENSE_IMG_DIR = os.environ.get(
    'EXPENSE_IMG_DIR',
    os.path.join(os.path.dirname(__file__), 'expense-imgs'),
)
BG_DIR = os.environ.get(
    'BG_DIR',
    os.path.join(os.path.dirname(__file__), 'user-images'),
)
AVATAR_DIR = os.path.join(BG_DIR, 'avatars')

# ── Global i18n: every request initializes g.lang from the X-Lang header ──
@app.before_request
def _set_request_lang():
    g.lang = get_lang(request)


# ═══════════════════════════════════════════════════════════
#  Static file serving (registered before Blueprints so API
#  routes take priority via Flask's registration order)
# ═══════════════════════════════════════════════════════════

@app.route('/expense-imgs/<path:subpath>')
def serve_expense_image(subpath):
    parts = subpath.split('/', 1)
    if len(parts) != 2:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    user_id, filename = parts
    user_dir = os.path.join(EXPENSE_IMG_DIR, user_id)
    file_path = os.path.normpath(os.path.join(user_dir, filename))
    if not file_path.startswith(user_dir) or not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    mime, _ = mimetypes.guess_type(file_path)
    resp = make_response(send_file(file_path, mimetype=mime or 'image/jpeg'))
    resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return resp


@app.route('/user-images/<path:subpath>')
def serve_user_image(subpath):
    file_path = os.path.normpath(os.path.join(BG_DIR, subpath))
    if not file_path.startswith(BG_DIR) or not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    mime, _ = mimetypes.guess_type(file_path)
    resp = make_response(send_file(file_path, mimetype=mime or 'image/jpeg'))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@app.route('/<path:path>')
def serve_spa_static(path):
    if path.startswith('api/'):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(file_path):
        mime, _ = mimetypes.guess_type(file_path)
        no_cache = mime and mime.startswith('text/html')
        resp = make_response(send_file(file_path, mimetype=mime or 'application/octet-stream'))
        if not no_cache:
            resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.isfile(index_path):
        return send_file(index_path, mimetype='text/html')
    return jsonify({'status': 'error', 'message': 'Frontend not built'}), 503


@app.route('/', defaults={'path': ''})
def serve_spa_root(path):
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.isfile(index_path):
        return send_file(index_path, mimetype='text/html')
    return jsonify({'status': 'error', 'message': 'Frontend not built'}), 503


# Email config -- Resend HTTP API
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
DEV_MODE = not RESEND_API_KEY  # 无 key → dev 模式:验证码返给前端

def _send_email(to_email, subject, body, code):
    """发信:无 key 时 dev mode,否则走 Resend HTTP API"""
    if not RESEND_API_KEY:
        print(f"[EMAIL] Dev mode: code={code} for {to_email} ({subject})")
        return True
    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            json={'from': RESEND_FROM, 'to': [to_email], 'subject': subject, 'html': body},
            timeout=15
        )
        if r.status_code == 200:
            resp_data = r.json()
            if resp_data.get('id'):
                print(f"[EMAIL] Sent to {to_email}: {subject}")
                return True
            print(f"[EMAIL] Resend API error: {r.text}")
            return False
        print(f"[EMAIL] Resend error {r.status_code}: {r.text}")
        return False
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False

def send_verification_email(to_email, code, lang='zh-CN'):
    templates = {
        'zh-CN': {
            'subject': f'【柳味探秘】您的账户注册验证码:{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好!您正在注册柳味探秘科技账户,以下是您的电子邮箱验证码:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">验证码有效期为 10 分钟.请在注册页面输入此验证码以完成身份验证.</p>
        <p style="color:#9C9A95;font-size:12px">提示:如果这不是您本人的操作,可能是其他用户不小心输入了您的邮箱,您可以安全地忽略此邮件,您的账户不会受到任何影响.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技团队</p>
    </div>'''
        },
        'zh-TW': {
            'subject': f'【柳味探秘】您的帳戶註冊驗證碼:{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好!您正在註冊柳味探秘科技帳戶,以下是您的電子郵箱驗證碼:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">驗證碼有效期為 10 分鐘.請在註冊頁面輸入此驗證碼以完成身份驗證.</p>
        <p style="color:#9C9A95;font-size:12px">提示:如果這不是您本人的操作,可能是其他用戶不小心輸入了您的郵箱,您可以安全地忽略此郵件,您的帳戶不會受到任何影響.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技團隊</p>
    </div>'''
        },
        'en': {
            'subject': f'[LiuWei TanMi] Your Account Registration Code: {code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">LiuWei TanMi</h2>
        <p>Hello! You are registering a LiuWei TanMi account. Here is your email verification code:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">This code is valid for 10 minutes. Please enter it on the registration page to complete verification.</p>
        <p style="color:#9C9A95;font-size:12px">Note: If this wasn't you, someone may have accidentally entered your email. You can safely ignore this message -- your account will not be affected.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">LiuWei TanMi Team</p>
    </div>'''
        },
    }
    t = templates.get(lang, templates['zh-CN'])
    return _send_email(to_email, t['subject'], t['body'], code)

def send_reset_email(to_email, code, lang='zh-CN'):
    templates = {
        'zh-CN': {
            'subject': f'【柳味探秘】密码重置验证码:{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好!您正在为柳味探秘科技账户重置密码,以下是您的验证码:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">验证码有效期为 10 分钟.请在重置密码页面输入此验证码以完成操作.</p>
        <p style="color:#9C9A95;font-size:12px">提示:如果这不是您本人的操作,您可以安全地忽略此邮件,您的账户不会受到任何影响.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技团队</p>
    </div>'''
        },
        'zh-TW': {
            'subject': f'【柳味探秘】密碼重置驗證碼:{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好!您正在為柳味探秘科技帳戶重置密碼,以下是您的驗證碼:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">驗證碼有效期為 10 分鐘.請在重置密碼頁面輸入此驗證碼以完成操作.</p>
        <p style="color:#9C9A95;font-size:12px">提示:如果這不是您本人的操作,您可以安全地忽略此郵件,您的帳戶不會受到任何影響.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技團隊</p>
    </div>'''
        },
        'en': {
            'subject': f'[LiuWei TanMi] Password Reset Code: {code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">LiuWei TanMi</h2>
        <p>Hello! You are resetting your LiuWei TanMi account password. Here is your verification code:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">This code is valid for 10 minutes. Please enter it on the password reset page to complete the process.</p>
        <p style="color:#9C9A95;font-size:12px">Note: If this wasn't you, you can safely ignore this message -- your account will not be affected.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">LiuWei TanMi Team</p>
    </div>'''
        },
    }
    t = templates.get(lang, templates['zh-CN'])
    return _send_email(to_email, t['subject'], t['body'], code)

def generate_code():
    return ''.join(random.choices(string.digits, k=6))

def validate_password(password, lang='zh-CN'):
    """返回 (bool, str).密码强度:最少8位,必须含字母,数字和特殊字符"""
    if len(password) < 8:
        return False, _t('err_pw_too_short', lang)
    if not re.search(r'[A-Za-z]', password):
        return False, _t('err_pw_no_letter', lang)
    if not re.search(r'[0-9]', password):
        return False, _t('err_pw_no_digit', lang)
    if not re.search(r'[!@#$%^&*(),.?\":{}|<>]', password):
        return False, _t('err_pw_no_special', lang)
    return True, ''

def validate_username(username):
    """返回 (bool, str).用户名:2-32位,字母数字下划线中文"""
    if len(username) < 2 or len(username) > 32:
        return False
    if not re.match(r'^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$', username):
        return False
    return True

DB = os.environ.get('DB', os.path.join(os.path.dirname(__file__), 'data', 'snail.db'))


@contextmanager
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
    finally:
        db.commit()
        db.close()


# ── Seed data ──

PARTNER_DATA = [
    ('张安武', 0.34, 54455.08, '完结', '董事长 | 初始¥44,200(2024-04-01) + 追加¥10,255.08(2025-01-21)'),
    ('蓝柳富', 0.33, 52853.46, '完结', '打杂 | 初始¥42,900(2024-04-01) + 追加¥9,953.46(2025-01-21)'),
    ('江宽',  0.33, 52853.46, '完结', 'CEO | 初始¥42,900(2024-04-01) + 追加¥9,953.46(2025-01-21)'),
]

DEFAULT_PRODUCTS = [
    # ── 蓝姐 → 蓝姐 (49) ──
    ('大号猪脚B14','84个/件','',490,'蓝姐'),
    ('大号猪脚B13','78个/件','',490,'蓝姐'),
    ('大号卤鸭脚','300个/件','',510,'蓝姐'),
    ('炸虎皮鸡爪(大号)','30个/包','',78,'蓝姐'),
    ('锅烧(一级超薄精品)','20斤/件','',370,'蓝姐'),
    ('爆丫丫(卤鸡蛋)','30个/包','',29,'蓝姐'),
    ('爆丫丫(卤鹌鹑蛋)','1.5kg/包','',25.5,'蓝姐'),
    ('爆丫丫(流心蛋)','180个/件','',405,'蓝姐'),
    ('金稻香米粉','25kg/件','',150,'蓝姐'),
    ('华A干米粉','25kg/件','',146,'蓝姐'),
    ('柳纯米粉','25kg/件','',151,'蓝姐'),
    ('老柳州升级版汤料','10包/件','',317.5,'蓝姐'),
    ('三合一调料包','10包/件','',345,'蓝姐'),
    ('卤香红油','4桶/件','',530,'蓝姐'),
    ('卤七寸','10条/包','',200,'蓝姐'),
    ('卤味肥肠(特级净油)','30条/包','',159,'蓝姐'),
    ('卤味鸭胗','30个/包','',84,'蓝姐'),
    ('卤牛肚','1kg/包','',109,'蓝姐'),
    ('牛杂串','100串/包','',82,'蓝姐'),
    ('豆腐串','100串/件','',48,'蓝姐'),
    ('纯米醋','20包/件','',9.6,'蓝姐'),
    ('老坛酸笋丝','20斤/件','',56,'蓝姐'),
    ('老坛酸豆角','20斤/件','',52,'蓝姐'),
    ('熬汤筒骨','10kg/件','',58,'蓝姐'),
    ('青柠猪皮','3斤/包','',22.5,'蓝姐'),
    ('香辣猪肺','3斤/包','',42,'蓝姐'),
    ('爆丫丫干捞酱','20包/件','',350,'蓝姐'),
    ('爆丫丫秘制炒肉沫','15包/件','',540,'蓝姐'),
    ('爆丫丫秘制炒螺肉','10包/件','',600,'蓝姐'),
    ('牛筋丸','20斤/件','',239,'蓝姐'),
    ('广味腊肠','10斤/箱','',116,'蓝姐'),
    ('天然之宝螺肉','9kg/件','',86,'蓝姐'),
    ('木耳丝','10kg/件','',244,'蓝姐'),
    ('黄金卷(腐竹)','24盒/件','',137,'蓝姐'),
    ('豆皮(清蔓雨)','18斤/箱','',136,'蓝姐'),
    ('精品腐竹(红箱)','18斤/箱','',185,'蓝姐'),
    ('油炸腐竹','10斤/件','',125,'蓝姐'),
    ('炸花生','30斤/件','',255,'蓝姐'),
    ('老卤王','10包/件','',180,'蓝姐'),
    ('台湾风味热狗肠','8包/件','',316,'蓝姐'),
    ('原味地道肠','20包/件','',300,'蓝姐'),
    ('奥尔良琵琶鸡腿','20斤/件','',206,'蓝姐'),
    ('黄花菜','20斤/箱','',585,'蓝姐'),
    ('优奶仕(豆花粉)','20包/件','',750,'蓝姐'),
    ('黄片糖','20斤/件','',83,'蓝姐'),
    ('螺味全辣椒油(微辣)','30包/件','',450,'蓝姐'),
    ('螺味全辣椒油(中辣)','30包/件','',450,'蓝姐'),
    ('螺味全辣椒油(特辣)','30包/件','',510,'蓝姐'),
    ('香辛料调味油','5升/桶','',130,'蓝姐'),
    # ── 粉仔 (2) ──
    ('米粉','60斤/包','',170,'粉仔'),
    ('豆皮','18斤/箱','',135,'粉仔'),
    # ── 鲜禾 (4) ──
    ('米粉(绿水人家)','60斤/包','',172,'鲜禾'),
    ('豆皮(王中王)','18斤/箱','',138,'鲜禾'),
    ('白背木耳丝','20斤/件','',265,'鲜禾'),
    ('八度笋-原味','10斤x5包','',150,'鲜禾'),
    # ── 蒙方 (20) ──
    ('融水片红豆角','10斤x5包','',130,'蒙方'),
    ('原味酸笋','50斤/件','',112,'蒙方'),
    ('融水红油豆角','50斤/件','',135,'蒙方'),
    ('原味酸豆角','50斤/件','',115,'蒙方'),
    ('融水米粉','48斤/件','',124,'蒙方'),
    ('增香红油(微辣)','4桶/件','',345,'蒙方'),
    ('增香红油(中辣)','4桶/件','',370,'蒙方'),
    ('增香红油(特辣)','4桶/件','',365,'蒙方'),
    ('增香红油(魔鬼辣)','4桶/件','',400,'蒙方'),
    ('卤鸡脚','200个/件','',400,'蒙方'),
    ('魔鬼辣椒粉末','10斤/件','',145,'蒙方'),
    ('特红粉末','10斤/件','',90,'蒙方'),
    ('黄金卷(腐竹)','32盒/件','',185,'蒙方'),
    ('木耳丝(特级)','30斤/件','',335,'蒙方'),
    ('木耳丝(A级)','30斤/件','',300,'蒙方'),
    ('木耳丝(B级)','30斤/件','',280,'蒙方'),
    ('豆皮(正山)','20斤/件','',142,'蒙方'),
    ('豆皮(薄款)','20斤/件','',137,'蒙方'),
    ('豆皮(王中王)','20斤/件','',135,'蒙方'),
    ('干石螺肉','5斤/件','',225,'蒙方'),
    # ── 桂螺帮 (1) ──
    ('桂螺帮螺蛳粉1.2-1.4','60斤/件','',160,'桂螺帮'),
]

def login_required(f):
    @functools.wraps(f)
    def wrap(*a, **kw):
        if 'user_id' not in session:
            # Check Bearer token as fallback
            auth = request.headers.get('Authorization','')
            if auth.startswith('Bearer '):
                token = auth[7:]
                with get_db() as db:
                    row = db.execute('SELECT user_id FROM user_tokens WHERE token=?', (token,)).fetchone()
                if row:
                    uid = row['user_id']
                    # Verify user still exists (may have been deleted)
                    with get_db() as db:
                        exists = db.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
                    if exists:
                        session['user_id'] = uid
                    else:
                        # User deleted -- clean orphan token
                        with get_db() as db:
                            db.execute('DELETE FROM user_tokens WHERE token=?', (token,))
                            db.commit()
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'status':'error','message':_t('err_session_expired', g.lang)}), 401
                return redirect('/login')
        g.user_id = session['user_id']
        g.username = session.get('username', '')
        # Verify user still exists (may have been deleted from DB)
        with get_db() as db:
            exists = db.execute('SELECT id FROM users WHERE id=?', (g.user_id,)).fetchone()
        if not exists:
            session.clear()
            if request.path.startswith('/api/'):
                return jsonify({'status':'error','message':_t('err_session_expired', g.lang)}), 401
            return redirect('/login')
        return f(*a, **kw)
    return wrap

@contextmanager
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try: yield db
    finally:
        db.commit()
        db.close()

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                email TEXT,
                verification_code TEXT,
                code_expires TIMESTAMP,
                is_verified INTEGER DEFAULT 0,
                reset_code TEXT,
                reset_expires TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                account TEXT NOT NULL,
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dividends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                share REAL NOT NULL,
                investment REAL NOT NULL DEFAULT 0,
                status TEXT DEFAULT '',
                note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                price REAL NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS procurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_name TEXT,
                quantity REAL,
                unit TEXT DEFAULT '',
                unit_price REAL,
                total REAL,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS procurement_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_number INTEGER NOT NULL DEFAULT 0,
                date TEXT NOT NULL,
                payment_method TEXT NOT NULL DEFAULT '微信',
                category TEXT DEFAULT '采购',
                total REAL NOT NULL DEFAULT 0,
                images TEXT DEFAULT '[]',
                thumb_images TEXT DEFAULT '[]',
                note TEXT DEFAULT '',
                payment_method TEXT DEFAULT '',
                supplier TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                batch_number INTEGER NOT NULL DEFAULT 1 CHECK(batch_number > 0),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS procurement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER REFERENCES procurement_batches(id),
                product_id INTEGER,
                product_name TEXT,
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                quantity REAL,
                unit_price REAL,
                total REAL,
                supplier TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS procurement_cart (
                product_id INTEGER PRIMARY KEY,
                product_name TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reconciliations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                card_balance REAL NOT NULL DEFAULT 0,
                cash_balance REAL NOT NULL DEFAULT 0,
                dine_in REAL NOT NULL DEFAULT 0,
                meituan REAL NOT NULL DEFAULT 0,
                flash_sale REAL NOT NULL DEFAULT 0,
                jd REAL NOT NULL DEFAULT 0,
                tuan REAL NOT NULL DEFAULT 0,
                channel_total REAL NOT NULL DEFAULT 0,
                real_total REAL NOT NULL DEFAULT 0,
                diff REAL NOT NULL DEFAULT 0,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS platform_fees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                meituan_cashier REAL DEFAULT 0,
                meituan_waimai REAL DEFAULT 0,
                shangou_waimai REAL DEFAULT 0,
                meituan_tuan REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(year, month)
            );
            CREATE TABLE IF NOT EXISTS platform_fee_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fee_id INTEGER REFERENCES platform_fees(id),
                entry_date TEXT NOT NULL,
                meituan_cashier REAL DEFAULT 0,
                meituan_waimai REAL DEFAULT 0,
                shangou_waimai REAL DEFAULT 0,
                meituan_tuan REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (user_id, key),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS daily_revenue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                revenue REAL NOT NULL DEFAULT 0,
                turnover REAL NOT NULL DEFAULT 0,
                jd_revenue REAL DEFAULT 0,
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                archived INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Migrations (safe to re-run)
        for col, col_type in [
            ('email','TEXT'),('verification_code','TEXT'),('code_expires','TIMESTAMP'),
            ('is_verified','INTEGER DEFAULT 0'),('reset_code','TEXT'),('reset_expires','TIMESTAMP')
        ]:
            try:
                db.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')
            except sqlite3.OperationalError:
                pass  # column already exists
        try:
            db.execute('ALTER TABLE daily_revenue ADD COLUMN archived INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass  # column already exists
        db.execute("UPDATE users SET email = LOWER(email) WHERE email != LOWER(email)")
        count = db.execute('SELECT COUNT(*) FROM partners').fetchone()[0]
        if count == 0:
            for p in PARTNER_DATA:
                db.execute('INSERT INTO partners (name,share,investment,status,note) VALUES (?,?,?,?,?)', p)
        count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        if count == 0:
            for p in DEFAULT_PRODUCTS:
                db.execute('INSERT INTO products (name,spec,unit,price,supplier) VALUES (?,?,?,?,?)',
                           (p[0],p[1],p[2],p[3],p[4]))
        db.commit()
        try:
            db.execute('ALTER TABLE reconciliations ADD COLUMN bill_date TEXT')
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute('ALTER TABLE reconciliations ADD COLUMN reconciled_by TEXT')
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN images TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE products ADD COLUMN supplier TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN date TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN thumb_images TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE procurement_batches ADD COLUMN thumb_images TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE dividends ADD COLUMN date TEXT DEFAULT ''")
        except:
            pass

init_db()


# ═══════════════════════════════════════════════════════════
#  Blueprint registration
# ═══════════════════════════════════════════════════════════

# Auth routes kept inline in app.py — stable, not audited for migration
from routes.data import data_bp
from routes.partners import bp as partners_bp
from routes.procurement import procurement_bp
from routes.profile import profile_bp
from routes.settings import settings_bp
from routes.transactions import tx_bp

app.register_blueprint(data_bp, url_prefix='/api')
app.register_blueprint(partners_bp, url_prefix='/api')
app.register_blueprint(procurement_bp, url_prefix='/api')
app.register_blueprint(profile_bp, url_prefix='/api')
app.register_blueprint(settings_bp, url_prefix='/api')
app.register_blueprint(tx_bp, url_prefix='/api')


# ── Global error handlers -- return JSON for API routes ──
@app.errorhandler(500)
def handle_500(e):
    import logging
    log = logging.getLogger('app')
    log.exception('Unhandled 500: %s', e)
    lang = g.get('lang', 'zh-CN') if hasattr(g, 'lang') else 'zh-CN'
    return jsonify({'status': 'error', 'message': _t('err_internal', lang)}), 500


@app.errorhandler(404)
def handle_404(e):
    # Only return JSON for /api/* routes; let the SPA handle frontend routing
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    return e.get_response()

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


@app.route('/login', methods=['GET','POST'])
def login_page():
    if request.method == 'GET':
        # Serve SPA entry point (React handles the login UI)
        index_path = os.path.join(FRONTEND_DIR, 'index.html')
        if os.path.isfile(index_path):
            return send_file(index_path, mimetype='text/html')
        return jsonify({'status':'error','message':'Frontend not built'}), 503
    # POST: JSON login API
    data = request.get_json()
    username = data.get('username','').strip()
    password = data.get('password','')
    remember = data.get('remember', False)
    if not username or not password:
        return jsonify({'status':'error','message':_t('err_empty_fields', g.lang)}), 400
    # Rate limit check
    ip = request.remote_addr or 'unknown'
    allowed, wait = check_rate_limit(ip)
    if not allowed:
        mins = wait // 60
        secs = wait % 60
        return jsonify({'status':'error','message':_t('err_too_many_attempts', g.lang, mins=mins, secs=secs) or f'Too many attempts. Please wait {mins}m{secs}s.'}), 429
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE username=? OR email=? OR email=?',(username, username, username.lower())).fetchone()
        if user and check_password_hash(user['password'], password):
            if not user['is_verified']:
                return jsonify({'status':'error','message':_t('err_need_verify', g.lang),'need_verify':True,'email':user['email']}), 403
            session.permanent = True
            if remember:
                app.permanent_session_lifetime = timedelta(days=30)
            else:
                app.permanent_session_lifetime = timedelta(hours=24)
            session['user_id'] = user['id']
            session['username'] = user['username']
            # 清理 90 天前的旧 token
            db.execute("DELETE FROM user_tokens WHERE created_at < datetime('now', '-90 days')")
            token = secrets.token_hex(32)
            db.execute('INSERT INTO user_tokens (user_id, token) VALUES (?,?)', (user['id'], token))
            db.commit()
            return jsonify({'status':'ok','token':token,'username':user['username'],'user_id':user['id']})
    record_failed_attempt(ip)
    return jsonify({'status':'error','message':_t('err_wrong_credentials', g.lang)}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username','').strip()
    password = data.get('password','')
    email = data.get('email','').strip().lower()
    if not username or not password:
        return jsonify({'status':'error','message':_t('err_empty_fields', g.lang)}), 400
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    if not validate_email(email):
        return jsonify({'status':'error','message':_t('err_email_invalid', g.lang) or 'Invalid email format'}), 400
    # 用户名格式校验
    if not validate_username(username):
        return jsonify({'status':'error','message':_t('err_username_invalid', g.lang) or '用户名仅支持字母,数字,下划线和中文,2-32位'}), 400
    # 密码强度校验
    ok, msg = validate_password(password, g.lang)
    if not ok:
        return jsonify({'status':'error','message':msg}), 400
    with get_db() as db:
        # 检查重复:已验证用户 → 拒绝;未验证用户 → 覆盖重注册
        exists = db.execute('SELECT id, is_verified FROM users WHERE username=? OR email=?',(username, email)).fetchone()
        if exists:
            if exists['is_verified']:
                return jsonify({'status':'error','message':_t('err_username_exists', g.lang)}), 409
            # 未验证:删除旧记录(可能是验证码填错后重注册)
            db.execute('DELETE FROM users WHERE id=?', (exists['id'],))
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('INSERT INTO users (username,password,email,verification_code,code_expires,is_verified) VALUES (?,?,?,?,?,0)', (username, generate_password_hash(password), email, code, expires))
        db.commit()
        if not send_verification_email(email, code, g.lang):
            return jsonify({'status':'error','message':_t('err_code_send_failed', g.lang)}), 500
    resp = {'status':'ok','message':_t('msg_code_sent', g.lang, email=email),'email':email}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp), 201

@app.route('/verify', methods=['POST'])
def verify_email():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    code = data.get('code','').strip()
    if not email or not code:
        return jsonify({'status':'error','message':_t('err_empty_email_code', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND verification_code=? AND is_verified=0', (email, code)).fetchone()
        if not user:
            return jsonify({'status':'error','message':_t('err_wrong_code', g.lang)}), 401
        if datetime.utcnow() > datetime.fromisoformat(user['code_expires']):
            return jsonify({'status':'error','message':_t('err_code_expired', g.lang)}), 410
        db.execute('UPDATE users SET is_verified=1, verification_code=NULL, code_expires=NULL WHERE id=?', (user['id'],))
        db.commit()
    return jsonify({'status':'ok','message':_t('msg_verify_ok', g.lang)})

@app.route('/resend-code', methods=['POST'])
def resend_code_route():
    data = request.get_json()
    email = data.get('email','').strip().lower()
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=0',(email,)).fetchone()
        if not user:
            return jsonify({'status':'ok','message':_t('msg_code_resent', g.lang)})
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('UPDATE users SET verification_code=?, code_expires=? WHERE id=?',(code, expires, user['id']))
        db.commit()
        if not send_verification_email(email, code, g.lang):
            return jsonify({'status':'error','message':_t('err_resend_failed', g.lang)}), 500
    resp = {'status':'ok','message':_t('msg_code_resent', g.lang)}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp)

# ====== 忘记密码 / 重置密码 ======

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    """发送重置密码验证码到已注册邮箱"""
    data = request.get_json()
    email = data.get('email','').strip().lower()
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    if not validate_email(email):
        return jsonify({'status':'error','message':_t('err_email_invalid', g.lang) or 'Invalid email format'}), 400
    # 限流:IP 15分钟3次(独立计数器,不与登录共享)
    ip = request.remote_addr or 'unknown'
    allowed, wait = check_forgot_limit(ip)
    if not allowed:
        mins = wait // 60
        secs = wait % 60
        return jsonify({'status':'error','message':_t('err_too_many_attempts', g.lang, mins=mins, secs=secs) or f'Too many attempts. Please wait {mins}m{secs}s.'}), 429
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=1',(email,)).fetchone()
        if not user:
            # 邮箱未注册 → 记录失败 + 统一返回(防探测)
            record_forgot_attempt(ip)
            return jsonify({'status':'ok','message':_t('msg_forgot_sent', g.lang),'email':email})
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('UPDATE users SET reset_code=?, reset_expires=? WHERE id=?',(code, expires, user['id']))
        db.commit()
        if not send_reset_email(email, code, g.lang):
            return jsonify({'status':'error','message':_t('err_code_send_failed', g.lang)}), 500
    resp = {'status':'ok','message':_t('msg_code_sent', g.lang, email=email),'email':email}
    if DEV_MODE:
        resp['dev_code'] = code
    return jsonify(resp)

@app.route('/reset-password', methods=['POST'])
def reset_password():
    """用验证码重置密码"""
    data = request.get_json()
    email = data.get('email','').strip().lower()
    code = data.get('code','').strip()
    new_password = data.get('password','')
    if not email or not code or not new_password:
        return jsonify({'status':'error','message':_t('err_incomplete', g.lang)}), 400
    ok, msg = validate_password(new_password, g.lang)
    if not ok:
        return jsonify({'status':'error','message':msg}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND reset_code=? AND is_verified=1',(email, code)).fetchone()
        if not user:
            return jsonify({'status':'error','message':_t('err_wrong_code', g.lang)}), 401
        if datetime.utcnow() > datetime.fromisoformat(user['reset_expires']):
            return jsonify({'status':'error','message':_t('err_reset_code_expired', g.lang)}), 410
        db.execute('UPDATE users SET password=?, reset_code=NULL, reset_expires=NULL WHERE id=?',
                   (generate_password_hash(new_password), user['id']))
        db.commit()
    return jsonify({'status':'ok','message':_t('msg_reset_ok', g.lang)})

# ====== End Auth ======

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return jsonify({'status':'ok'})

@app.route('/logout', methods=['GET'])
def logout_get():
    """GET /logout -- reject with 405 to prevent CSRF and SPA catch-all hijack."""
    return jsonify({'status':'error','message':'Use POST /logout'}), 405


# — API routes moved to blueprints (routes/) —

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8600, debug=False)
# 
# 
# 
# 
# fix: double /api prefix on procurement routes
# deploy: viewport fix
# deploy: position fix
# deploy: View fix
