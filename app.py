#!/usr/bin/env python3
"""🍜 蓝姐螺蛳粉 · 记账系统"""

import sqlite3, os, secrets, functools, re
from datetime import datetime, date
from contextlib import contextmanager
from flask import Flask, request, jsonify, session, redirect, g, make_response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import smtplib, random, string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from i18n_backend import get_lang, t as _t

app = Flask(__name__)
# Persistent secret key (survives restarts)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'snail-books-lanxu-2026-secret-key-v1')
# Session timeout: 24 hours
app.permanent_session_lifetime = timedelta(hours=24)

# ── CORS (for iOS App cross-origin requests) ──
@ app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = request.headers.get('Origin', '*')
    response.headers['Access-Control-Allow-Credentials'] = 'true'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,X-Lang,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    return response

@ app.before_request
def handle_options():
    if request.method == 'OPTIONS':
        return make_response('', 200)

FRONTEND_VERSION = '1'
FRONTEND_DIR = os.environ.get('FRONTEND_DIR', os.path.join(os.path.dirname(__file__), '..', 'snail-books-web', 'dist'))

@ app.before_request
def detect_lang():
    g.lang = get_lang(request)


# ── SPA static file serving ──
import mimetypes

@app.route('/<path:path>')
def serve_spa_static(path):
    """Serve static files from the Expo web build dist/ directory."""
    # Let API routes take priority (they're registered first, so this only
    # fires for paths that don't match any API route)
    if path.startswith('api/'):
        return jsonify({'status':'error','message':'Not found'}), 404
    file_path = os.path.join(FRONTEND_DIR, path)
    if os.path.isfile(file_path):
        mime, _ = mimetypes.guess_type(file_path)
        return send_file(file_path, mimetype=mime or 'application/octet-stream')
    # SPA fallback: serve index.html
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.isfile(index_path):
        return send_file(index_path, mimetype='text/html')
    return jsonify({'status':'error','message':'Frontend not built'}), 503


@app.route('/', defaults={'path': ''})
def serve_spa_root(path):
    """Serve SPA entry point for root and login routes."""
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.isfile(index_path):
        return send_file(index_path, mimetype='text/html')
    return jsonify({'status':'error','message':'Frontend not built'}), 503


# Email config
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
SENDGRID_FROM = os.environ.get('SENDGRID_FROM', 'noreply@lan-noodles.com')

def _send_email(to_email, subject, body, code):
    """通用发信：无 API key 时打印到 stdout，否则走 SendGrid SMTP"""
    if not SENDGRID_API_KEY:
        print(f"[EMAIL] Dev mode: code={code} for {to_email} ({subject})")
        return True
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDGRID_FROM
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))
        with smtplib.SMTP('smtp.sendgrid.net', 587) as server:
            server.starttls()
            server.login('apikey', SENDGRID_API_KEY)
            server.send_message(msg)
        print(f"[EMAIL] Sent to {to_email}: {subject}")
        return True
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False

def send_verification_email(to_email, code):
    body = f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">蓝姐螺蛳粉 记账系统</h2>
        <p>你的验证码是：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">10 分钟内有效，请勿泄露</p>
    </div>'''
    return _send_email(to_email, '蓝姐螺蛳粉 - 邮箱验证码', body, code)

def send_reset_email(to_email, code):
    body = f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">蓝姐螺蛳粉 记账系统</h2>
        <p>你正在重置密码，验证码是：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">10 分钟内有效，如非本人操作请忽略</p>
    </div>'''
    return _send_email(to_email, '蓝姐螺蛳粉 - 重置密码', body, code)

def generate_code():
    return ''.join(random.choices(string.digits, k=6))

def validate_password(password, lang='zh-CN'):
    """返回 (bool, str)。密码强度：最少6位，必须含字母和数字"""
    if len(password) < 6:
        return False, _t('err_pw_too_short', lang)
    if not re.search(r'[A-Za-z]', password):
        return False, _t('err_pw_no_letter', lang)
    if not re.search(r'[0-9]', password):
        return False, _t('err_pw_no_digit', lang)
    return True, ''

DB = os.environ.get('DB', os.path.join(os.path.dirname(__file__), 'data', 'snail.db'))

INCOME_CATS = ['🍜 堂食', '🛵 美团外卖', '🛵 饿了吗外卖', '🎫 美团团购', '📦 京东', '🔧 其他收入']
EXPENSE_CATS = [
    '📦 原材料进货', '🏠 房租', '⚡ 水电煤气', '👨‍🍳 人工工资',
    '🔧 设备/工具', '🏗️ 装修', '📋 培训/证件', '🧹 卫生/清洁',
    '🧻 餐具/纸巾', '📦 包装/打包', '📢 广告/推广', '💊 杂项/烟酒', '📝 其他'
]
ACCOUNTS = ['💚 微信收款', '💙 支付宝收款', '💵 现金', '🏦 银行卡']

# 实际合伙人数据
PARTNER_DATA = [
    ('张安武', 0.34, 44200, '完结'),
    ('蓝柳富', 0.33, 42900, '完结'),
    ('江宽',   0.33, 42900, '完结'),
]

DEFAULT_PRODUCTS = [
    ('猪脚','大号猪脚B14','84个/件',490),('猪脚','大号猪脚B13','78个/件',490),
    ('鸭脚','大号卤鸭脚','300个/件',540),('鸡爪','炸虎皮鸡爪（大号）','30个/包',81),
    ('锅烧','锅烧（一级超薄精品）','20斤/件',370),
    ('卤蛋','爆丫丫（卤鸡蛋）','30个/包',29),('卤蛋','爆丫丫（卤鹌鹑蛋）','1.5kg/包',26),
    ('卤蛋','爆丫丫（流心蛋）','180个/件',405),
    ('米粉','金稲香米粉','25kg/件',150),('米粉','华A干米粉','25kg/件',146),
    ('米粉','柳纯米粉','25kg/件',151),
    ('螺蛳汤料','老柳州升级版汤料','10包/件',318),('螺蛳汤料','三合一调料包','10包/件',345),
    ('螺蛳汤料','卤香红油','4桶/件',530),
    ('螺蛳卤味','卤七寸','10条/包',200),('螺蛳卤味','卤味肥肠（特级净油）','30条/包',159),
    ('螺蛳卤味','卤味鸭胗','30个/包',84),('螺蛳卤味','卤牛肚','1kg/包',109),
    ('牛杂串','','100串/包',82),('豆腐串','','100串/件',48),
    ('纯米醋','','20包/件',10),('老坛酸笋丝','','20斤/件',56),
    ('老坛酸豆角','','20斤/件',53),('熬汤筒骨','','10kg/件',58),
    ('青柠猪皮','','3斤/包',23),('香辣猪肺','','3斤/包',42),
    ('干捞酱','爆丫丫干捞酱','20包/件',350),('秘制炒肉沫','爆丫丫','15包/件',540),
    ('秘制炒螺肉','爆丫丫','10包/件',600),('牛筋丸','','20斤/件',239),
    ('广味腊肠','','10斤/箱',116),
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
                    session['user_id'] = row['user_id']
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'status':'error','message':_t('err_session_expired', g.lang)}), 401
                return redirect('/login')
        g.user_id = session['user_id']
        return f(*a, **kw)
    return wrap

@contextmanager
def get_db():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try: yield db
    finally: db.close()

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
                user_id INTEGER NOT NULL REFERENCES users(id),
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
                name TEXT NOT NULL UNIQUE,
                share REAL NOT NULL,
                investment REAL NOT NULL DEFAULT 0,
                status TEXT DEFAULT '进行中',
                note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                price REAL NOT NULL DEFAULT 0,
                note TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS procurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER REFERENCES products(id),
                product_name TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                unit_price REAL NOT NULL,
                total REAL NOT NULL,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(created_at);
            CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type);
            CREATE INDEX IF NOT EXISTS idx_div_date ON dividends(created_at);
            CREATE INDEX IF NOT EXISTS idx_proc_date ON procurements(created_at);
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
                user_id INTEGER REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date)
            );
            CREATE INDEX IF NOT EXISTS idx_recon_date ON reconciliations(date);
        ''')
        # Migrations (safe to re-run)
        for col, col_type in [
            ('email','TEXT'),('verification_code','TEXT'),('code_expires','TIMESTAMP'),
            ('is_verified','INTEGER DEFAULT 0'),('reset_code','TEXT'),('reset_expires','TIMESTAMP')
        ]:
            try:
                db.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')
            except:
                pass
        # Seed partners
        count = db.execute('SELECT COUNT(*) FROM partners').fetchone()[0]
        if count == 0:
            for p in PARTNER_DATA:
                db.execute('INSERT INTO partners (name,share,investment,status) VALUES (?,?,?,?)', p)
        # Seed products
        count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        if count == 0:
            for p in DEFAULT_PRODUCTS:
                db.execute('INSERT INTO products (name,spec,unit,price) VALUES (?,?,?,?)',
                          (p[0],p[1],p[2],p[3]))
        db.commit()

init_db()
# Auto-verify existing users (backward compat)
with get_db() as db:
    db.execute("UPDATE users SET is_verified=1 WHERE is_verified IS NULL OR is_verified=0")
    db.commit()

# ── Validation helper ──
def validate_required(data, *fields):
    """Return list of missing field names; empty if all present."""
    return [f for f in fields if not data.get(f)]

# ====== Auth ======

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
    if not username or not password:
        return jsonify({'status':'error','message':_t('err_empty_fields', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE username=? OR email=?',(username, username)).fetchone()
        if user and check_password_hash(user['password'], password):
            if not user['is_verified']:
                return jsonify({'status':'error','message':_t('err_need_verify', g.lang),'need_verify':True,'email':user['email']}), 403
            session.permanent = True
            session['user_id'] = user['id']
            session['username'] = user['username']
            token = secrets.token_hex(32)
            db.execute('INSERT INTO user_tokens (user_id, token) VALUES (?,?)', (user['id'], token))
            db.commit()
            return jsonify({'status':'ok','token':token,'username':user['username']})
    return jsonify({'status':'error','message':_t('err_wrong_credentials', g.lang)}), 401

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data.get('username','').strip()
    password = data.get('password','')
    email = data.get('email','').strip()
    if not username or not password:
        return jsonify({'status':'error','message':_t('err_empty_fields', g.lang)}), 400
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    # 密码强度校验
    ok, msg = validate_password(password, g.lang)
    if not ok:
        return jsonify({'status':'error','message':msg}), 400
    with get_db() as db:
        exists = db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
        if exists:
            return jsonify({'status':'error','message':_t('err_username_exists', g.lang)}), 409
        email_exists = db.execute('SELECT id FROM users WHERE email=? AND is_verified=1',(email,)).fetchone()
        if email_exists:
            return jsonify({'status':'error','message':_t('err_email_registered', g.lang)}), 409
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('INSERT INTO users (username,password,email,verification_code,code_expires,is_verified) VALUES (?,?,?,?,?,0)', (username, generate_password_hash(password), email, code, expires))
        db.commit()
        if not send_verification_email(email, code):
            return jsonify({'status':'error','message':_t('err_code_send_failed', g.lang)}), 500
    return jsonify({'status':'ok','message':_t('msg_code_sent', g.lang, email=email),'email':email})

@app.route('/verify', methods=['POST'])
def verify_email():
    data = request.get_json()
    email = data.get('email','').strip()
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
    email = data.get('email','').strip()
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=0',(email,)).fetchone()
        if not user:
            return jsonify({'status':'error','message':_t('err_email_not_found', g.lang)}), 404
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('UPDATE users SET verification_code=?, code_expires=? WHERE id=?',(code, expires, user['id']))
        db.commit()
        if not send_verification_email(email, code):
            return jsonify({'status':'error','message':_t('err_resend_failed', g.lang)}), 500
    return jsonify({'status':'ok','message':_t('msg_code_resent', g.lang)})

# ====== 忘记密码 / 重置密码 ======

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    """发送重置密码验证码到已注册邮箱"""
    data = request.get_json()
    email = data.get('email','').strip()
    if not email:
        return jsonify({'status':'error','message':_t('err_email_required', g.lang)}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND is_verified=1',(email,)).fetchone()
        if not user:
            # 不暴露邮箱是否已注册，统一返回
            return jsonify({'status':'ok','message':_t('msg_forgot_sent', g.lang),'email':email})
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('UPDATE users SET reset_code=?, reset_expires=? WHERE id=?',(code, expires, user['id']))
        db.commit()
        if not send_reset_email(email, code):
            return jsonify({'status':'error','message':_t('err_code_send_failed', g.lang)}), 500
    return jsonify({'status':'ok','message':_t('msg_code_sent', g.lang, email=email),'email':email})

@app.route('/reset-password', methods=['POST'])
def reset_password():
    """用验证码重置密码"""
    data = request.get_json()
    email = data.get('email','').strip()
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

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# Page routes are now served by the SPA fallback (serve_spa_static / serve_spa_root).
# API routes follow below — all unchanged.

@app.route('/api/transactions', methods=['GET','POST'])
@login_required
def api_transactions():
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'type', 'amount', 'category', 'account')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('INSERT INTO transactions (type,amount,category,account,note) VALUES (?,?,?,?,?)', (data['type'], data['amount'], data['category'], data['account'], data.get('note','')))
            db.commit()
        return jsonify({'status':'ok'})
    # GET with pagination
    page = request.args.get('page', 1, type=int)
    per_page = 20
    with get_db() as db:
        count = db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
        pages = max(1, (count + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.execute('SELECT * FROM transactions ORDER BY created_at DESC LIMIT ? OFFSET ?', (per_page, offset)).fetchall()
    return jsonify({'transactions': [dict(r) for r in rows], 'page': page, 'pages': pages, 'total': count})

@app.route('/api/transactions/<int:id>', methods=['DELETE'])
@login_required
def api_delete_transaction(id):
    with get_db() as db:
        db.execute('DELETE FROM transactions WHERE id=?', (id,))
        db.commit()
    return jsonify({'status':'ok'})

@app.route('/api/partners')
@login_required
def api_partners():
    with get_db() as db:
        rows = db.execute("""SELECT p.*, COALESCE(SUM(d.amount),0) as total_dividends FROM partners p LEFT JOIN dividends d ON d.partner = p.name GROUP BY p.id""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/dividends', methods=['GET','POST'])
@login_required
def api_dividends():
    if request.method == 'POST':
        data = request.get_json()
        items = data.get('items', [data])  # support single item or array
        for item in items:
            missing = validate_required(item, 'partner', 'amount')
            if missing:
                return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            for item in items:
                db.execute('INSERT INTO dividends (partner,amount,note) VALUES (?,?,?)', (item['partner'], item['amount'], item.get('note','')))
            db.commit()
        return jsonify({'status':'ok'})
    with get_db() as db:
        rows = db.execute('SELECT * FROM dividends ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/dividends/<int:id>', methods=['DELETE'])
@login_required
def api_delete_dividend(id):
    with get_db() as db:
        db.execute('DELETE FROM dividends WHERE id=?', (id,))
        db.commit()
    return jsonify({'status':'ok'})

# ========== 设置 - 首页背景图 ==========
ALLOWED_BG_EXT = {'jpg', 'jpeg', 'png', 'webp'}
MAX_BG_SIZE = 5 * 1024 * 1024  # 5MB

@app.route('/api/settings/background', methods=['POST'])
@login_required
def api_upload_background():
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': '未选择文件'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'status': 'error', 'message': '文件名为空'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in ALLOWED_BG_EXT:
        return jsonify({'status': 'error', 'message': f'仅支持 {", ".join(ALLOWED_BG_EXT)} 格式'}), 400
    # check size
    f.seek(0, 2)
    size = f.tell()
    f.seek(0)
    if size > MAX_BG_SIZE:
        return jsonify({'status': 'error', 'message': f'文件最大 5MB'}), 400
    save_path = os.path.join(app.static_folder, 'home-bg.jpg')
    f.save(save_path)
    return jsonify({'status': 'ok'})

@app.route('/api/settings/background', methods=['DELETE'])
@login_required
def api_reset_background():
    save_path = os.path.join(app.static_folder, 'home-bg.jpg')
    if os.path.exists(save_path):
        os.remove(save_path)
    return jsonify({'status': 'ok'})

@app.route('/api/partners/<int:id>', methods=['PUT'])
@login_required
def api_update_partner(id):
    data = request.get_json()
    missing = validate_required(data, 'share', 'investment')
    if missing:
        return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    with get_db() as db:
        db.execute('UPDATE partners SET share=?, investment=?, status=? WHERE id=?', (data['share'], data['investment'], data.get('status','进行中'), id))
        db.commit()
    return jsonify({'status':'ok'})

@app.route('/api/products', methods=['GET','POST','PUT','DELETE'])
@login_required
def api_products():
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'name')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('INSERT INTO products (name,spec,unit,price,note) VALUES (?,?,?,?,?)',
                      (data['name'], data.get('spec',''), data.get('unit',''), data.get('price',0), data.get('note','')))
            db.commit()
        return jsonify({'status':'ok'})
    if request.method == 'PUT':
        data = request.get_json()
        missing = validate_required(data, 'name', 'id')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('UPDATE products SET name=?, spec=?, unit=?, price=?, note=? WHERE id=?',
                      (data['name'], data.get('spec',''), data.get('unit',''), data.get('price',0), data.get('note',''), data['id']))
            db.commit()
        return jsonify({'status':'ok'})
    if request.method == 'DELETE':
        pid = request.args.get('id')
        with get_db() as db:
            db.execute('DELETE FROM products WHERE id=?', (pid,))
            db.commit()
        return jsonify({'status':'ok'})
    # GET
    with get_db() as db:
        rows = db.execute('SELECT * FROM products ORDER BY name').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/procurements', methods=['GET','POST'])
@login_required
def api_procurements():
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'product_id', 'product_name', 'quantity', 'unit_price', 'total')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('INSERT INTO procurements (product_id,product_name,quantity,unit_price,total,note) VALUES (?,?,?,?,?,?)', (data['product_id'], data['product_name'], data['quantity'], data['unit_price'], data['total'], data.get('note','')))
            db.commit()
        return jsonify({'status':'ok'})
    with get_db() as db:
        rows = db.execute('SELECT * FROM procurements ORDER BY created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/procurements/<int:id>', methods=['DELETE'])
@login_required
def api_delete_procurement(id):
    with get_db() as db:
        db.execute('DELETE FROM procurements WHERE id=?', (id,))
        db.commit()
    return jsonify({'status':'ok'})

@app.route('/api/stats')
@login_required
def api_stats():
    with get_db() as db:
        income = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income'").fetchone()[0]
        expense = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='expense'").fetchone()[0]
        tx_count = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return jsonify({'income':income, 'expense':expense, 'count':tx_count})

# ── Summary (today + month) ──
@app.route('/api/summary')
@login_required
def api_summary():
    today_str = date.today().isoformat()
    month_str = date.today().strftime('%Y-%m')
    with get_db() as db:
        # Today
        today_income = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income' AND date(created_at)=?", (today_str,)).fetchone()[0]
        today_expense = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='expense' AND date(created_at)=?", (today_str,)).fetchone()[0]
        # Month
        month_income = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income' AND strftime('%Y-%m', created_at)=?", (month_str,)).fetchone()[0]
        month_expense = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='expense' AND strftime('%Y-%m', created_at)=?", (month_str,)).fetchone()[0]
        month_procurement = db.execute("SELECT COALESCE(SUM(total),0) FROM procurements WHERE strftime('%Y-%m', created_at)=?", (month_str,)).fetchone()[0]
    return jsonify({
        'today': {'income': today_income, 'expense': today_expense, 'profit': today_income - today_expense},
        'month': {'income': month_income, 'expense': month_expense, 'profit': month_income - month_expense, 'procurement': month_procurement}
    })

# ── Chart: 12-month trend ──
@app.route('/api/chart')
@login_required
def api_chart():
    with get_db() as db:
        rows = db.execute("""
            SELECT strftime('%Y-%m', created_at) as month,
                   COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) as income,
                   COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) as expense
            FROM transactions
            WHERE created_at >= date('now', '-12 months')
            GROUP BY month ORDER BY month
        """).fetchall()
    return jsonify([dict(r) for r in rows])

# ── Summary (today + month) ──
@app.route('/api/frontend-version')
def api_frontend_version():
    return jsonify({'version': FRONTEND_VERSION})

@app.route('/api/frontend.zip')
def api_frontend_zip():
    import io, zipfile
    buf = io.BytesIO()
    www = os.path.join(os.path.dirname(__file__), '..', 'snail-books-ios', 'www')
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(www):
            for fn in files:
                full = os.path.join(root, fn)
                arcname = os.path.relpath(full, www)
                zf.write(full, arcname)
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='frontend.zip')

# ── Reconciliations ──
@app.route('/api/reconciliations', methods=['POST'])
@login_required
def api_create_reconciliation():
    data = request.get_json() or {}
    if validate_required(data, 'date'): return jsonify({'error': '缺少日期'}), 400
    date = data['date']

    card_balance = float(data.get('card_balance', 0))
    cash_balance = float(data.get('cash_balance', 0))
    dine_in = float(data.get('dine_in', 0))
    meituan = float(data.get('meituan', 0))
    flash_sale = float(data.get('flash_sale', 0))
    jd = float(data.get('jd', 0))
    tuan = float(data.get('tuan', 0))
    channel_total = dine_in + meituan + flash_sale + jd + tuan
    real_total = card_balance + cash_balance
    diff = real_total - channel_total

    with get_db() as db:
        db.execute('''INSERT OR REPLACE INTO reconciliations
            (date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
             channel_total, real_total, diff, user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
             channel_total, real_total, diff, g.user_id))
    return jsonify({'ok': True}), 201

@app.route('/api/reconciliations', methods=['GET'])
@login_required
def api_get_reconciliations():
    limit = request.args.get('limit', 30, type=int)
    with get_db() as db:
        rows = db.execute(
            'SELECT * FROM reconciliations WHERE user_id=? ORDER BY date DESC LIMIT ?',
            (g.user_id, limit)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8600, debug=True)