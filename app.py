#!/usr/bin/env python3
"""🍜 蓝姐 · 记账系统"""

import sqlite3, os, secrets, functools, re, json, time
from datetime import datetime, date
from contextlib import contextmanager
try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found,import]
    HAS_PIL = True
except ImportError:
    _PILImage = None  # type: ignore
    HAS_PIL = False
from flask import Flask, request, jsonify, session, redirect, g, make_response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import requests, random, string
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
IMG_DIR = os.path.join(FRONTEND_DIR, 'img')
EXPENSE_IMG_DIR = os.environ.get('EXPENSE_IMG_DIR', os.path.join(os.path.dirname(__file__), 'expense-imgs'))
# User-uploaded backgrounds - stored outside dist/ so CI deploys don't wipe them
BG_DIR = os.environ.get('BG_DIR', os.path.join(os.path.dirname(__file__), 'user-images'))

# ── Expense image serving (with permanent cache) ──
# Registered before the catch-all so /expense-imgs/ doesn't hit SPA fallback.

@app.route('/expense-imgs/<path:subpath>')
def serve_expense_image(subpath):
    """Serve expense receipt images with permanent cache headers.
    subpath format: <user_id>/<filename>
    Receipt images are immutable once uploaded — they never change.
    """
    # Path traversal guard: extract user_id/filename from subpath
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

# -- User-uploaded background serving --
@app.route('/user-images/<path:subpath>')
def serve_user_image(subpath):
    file_path = os.path.normpath(os.path.join(BG_DIR, subpath))
    if not file_path.startswith(BG_DIR) or not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    mime, _ = mimetypes.guess_type(file_path)
    resp = make_response(send_file(file_path, mimetype=mime or 'image/jpeg'))
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


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
        # Static assets with content-hash → cache forever
        no_cache = mime and mime.startswith('text/html')
        max_age = 0 if no_cache else 31536000
        resp = make_response(send_file(file_path, mimetype=mime or 'application/octet-stream'))
        if not no_cache:
            resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return resp
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


# Email config — Resend HTTP API
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
DEV_MODE = not RESEND_API_KEY  # 无 key → dev 模式：验证码返给前端

def _send_email(to_email, subject, body, code):
    """发信：无 key 时 dev mode，否则走 Resend HTTP API"""
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

def send_verification_email(to_email, code):
    body = f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">[柳味探秘] 记账系统</h2>
        <p>您正在进行记账系统验证。验证码：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">10 分钟内有效，为保障账户安全，请勿向他人泄露。</p>
    </div>'''
    return _send_email(to_email, '[柳味探秘] 邮箱验证码', body, code)

def send_reset_email(to_email, code):
    body = f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">[柳味探秘] 记账系统</h2>
        <p>您正在进行密码重置。验证码：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">10 分钟内有效，如非本人操作请忽略。</p>
    </div>'''
    return _send_email(to_email, '[柳味探秘] 重置密码', body, code)

def generate_code():
    return ''.join(random.choices(string.digits, k=6))

def validate_password(password, lang='zh-CN'):
    """返回 (bool, str)。密码强度：最少8位，必须含字母、数字和特殊字符"""
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
    """返回 (bool, str)。用户名：2-32位，字母数字下划线中文"""
    if len(username) < 2 or len(username) > 32:
        return False
    if not re.match(r'^[a-zA-Z0-9_\-\u4e00-\u9fa5]+$', username):
        return False
    return True

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
    ('张安武', 0.34, 54455.08, '完结', '董事长 | 初始¥44,200(2024-04-01) + 追加¥10,255.08(2025-01-21)'),
    ('蓝柳富', 0.33, 52853.46, '完结', '打杂 | 初始¥42,900(2024-04-01) + 追加¥9,953.46(2025-01-21)'),
    ('江宽',  0.33, 52853.46, '完结', 'CEO | 初始¥42,900(2024-04-01) + 追加¥9,953.46(2025-01-21)'),
]

DEFAULT_PRODUCTS = [
    # ── 蓝姐 → 蓝姐 (49) ──
    ('大号猪脚B14','84个/件','',490,'蓝姐'),
    ('大号猪脚B13','78个/件','',490,'蓝姐'),
    ('大号卤鸭脚','300个/件','',510,'蓝姐'),
    ('炸虎皮鸡爪（大号）','30个/包','',78,'蓝姐'),
    ('锅烧（一级超薄精品）','20斤/件','',370,'蓝姐'),
    ('爆丫丫（卤鸡蛋）','30个/包','',29,'蓝姐'),
    ('爆丫丫（卤鹌鹑蛋）','1.5kg/包','',25.5,'蓝姐'),
    ('爆丫丫（流心蛋）','180个/件','',405,'蓝姐'),
    ('金稻香米粉','25kg/件','',150,'蓝姐'),
    ('华A干米粉','25kg/件','',146,'蓝姐'),
    ('柳纯米粉','25kg/件','',151,'蓝姐'),
    ('老柳州升级版汤料','10包/件','',317.5,'蓝姐'),
    ('三合一调料包','10包/件','',345,'蓝姐'),
    ('卤香红油','4桶/件','',530,'蓝姐'),
    ('卤七寸','10条/包','',200,'蓝姐'),
    ('卤味肥肠（特级净油）','30条/包','',159,'蓝姐'),
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
    ('黄金卷（腐竹）','24盒/件','',137,'蓝姐'),
    ('豆皮（清蔓雨）','18斤/箱','',136,'蓝姐'),
    ('精品腐竹（红箱）','18斤/箱','',185,'蓝姐'),
    ('油炸腐竹','10斤/件','',125,'蓝姐'),
    ('炸花生','30斤/件','',255,'蓝姐'),
    ('老卤王','10包/件','',180,'蓝姐'),
    ('台湾风味热狗肠','8包/件','',316,'蓝姐'),
    ('原味地道肠','20包/件','',300,'蓝姐'),
    ('奥尔良琵琶鸡腿','20斤/件','',206,'蓝姐'),
    ('黄花菜','20斤/箱','',585,'蓝姐'),
    ('优奶仕（豆花粉）','20包/件','',750,'蓝姐'),
    ('黄片糖','20斤/件','',83,'蓝姐'),
    ('螺味全辣椒油（微辣）','30包/件','',450,'蓝姐'),
    ('螺味全辣椒油（中辣）','30包/件','',450,'蓝姐'),
    ('螺味全辣椒油（特辣）','30包/件','',510,'蓝姐'),
    ('香辛料调味油','5升/桶','',130,'蓝姐'),
    # ── 粉仔 (2) ──
    ('米粉','60斤/包','',170,'粉仔'),
    ('豆皮','18斤/箱','',135,'粉仔'),
    # ── 鲜禾 (4) ──
    ('米粉（绿水人家）','60斤/包','',172,'鲜禾'),
    ('豆皮（王中王）','18斤/箱','',138,'鲜禾'),
    ('白背木耳丝','20斤/件','',265,'鲜禾'),
    ('八度笋-原味','10斤×5包','',150,'鲜禾'),
    # ── 蒙方 (20) ──
    ('融水片红豆角','10斤×5包','',130,'蒙方'),
    ('原味酸笋','50斤/件','',112,'蒙方'),
    ('融水红油豆角','50斤/件','',135,'蒙方'),
    ('原味酸豆角','50斤/件','',115,'蒙方'),
    ('融水米粉','48斤/件','',124,'蒙方'),
    ('增香红油（微辣）','4桶/件','',345,'蒙方'),
    ('增香红油（中辣）','4桶/件','',370,'蒙方'),
    ('增香红油（特辣）','4桶/件','',365,'蒙方'),
    ('增香红油（魔鬼辣）','4桶/件','',400,'蒙方'),
    ('卤鸡脚','200个/件','',400,'蒙方'),
    ('魔鬼辣椒粉末','10斤/件','',145,'蒙方'),
    ('特红粉末','10斤/件','',90,'蒙方'),
    ('黄金卷（腐竹）','32盒/件','',185,'蒙方'),
    ('木耳丝（特级）','30斤/件','',335,'蒙方'),
    ('木耳丝（A级）','30斤/件','',300,'蒙方'),
    ('木耳丝（B级）','30斤/件','',280,'蒙方'),
    ('豆皮（正山）','20斤/件','',142,'蒙方'),
    ('豆皮（薄款）','20斤/件','',137,'蒙方'),
    ('豆皮（王中王）','20斤/件','',135,'蒙方'),
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
                    session['user_id'] = row['user_id']
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'status':'error','message':_t('err_session_expired', g.lang)}), 401
                return redirect('/login')
        g.user_id = session['user_id']
        g.username = session.get('username', '')
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
                date TEXT DEFAULT '',
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
                supplier TEXT DEFAULT '',
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
            -- 进货批次表（2026.5.30）
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            -- 进货明细表（2026.5.30）
            CREATE TABLE IF NOT EXISTS procurement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER REFERENCES procurement_batches(id),
                product_id INTEGER REFERENCES products(id),
                product_name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                unit_price REAL NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                subtotal REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_proc_batch_date ON procurement_batches(date);
            CREATE INDEX IF NOT EXISTS idx_proc_items_batch ON procurement_items(batch_id);
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
                bill_date TEXT,
                reconciled_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_recon_date ON reconciliations(date);
            CREATE TABLE IF NOT EXISTS platform_fees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                meituan_cashier REAL DEFAULT 0,
                meituan_waimai REAL DEFAULT 0,
                eleme_waimai REAL DEFAULT 0,
                meituan_tuan REAL DEFAULT 0,
                UNIQUE(year, month)
            );
            CREATE TABLE IF NOT EXISTS platform_fee_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fee_id INTEGER REFERENCES platform_fees(id),
                entry_date TEXT NOT NULL,
                meituan_cashier REAL DEFAULT 0,
                meituan_waimai REAL DEFAULT 0,
                eleme_waimai REAL DEFAULT 0,
                meituan_tuan REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (user_id, key),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS daily_revenue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                revenue REAL NOT NULL DEFAULT 0,
                turnover REAL NOT NULL DEFAULT 0,
                jd_revenue REAL DEFAULT 0,
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id),
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
            except:
                pass
        # Migration: archived column on daily_revenue
        try:
            db.execute('ALTER TABLE daily_revenue ADD COLUMN archived INTEGER DEFAULT 0')
        except:
            pass
        # 邮箱大小写迁移：将所有存量 email 转为小写
        db.execute("UPDATE users SET email = LOWER(email) WHERE email != LOWER(email)")
        # Seed partners
        count = db.execute('SELECT COUNT(*) FROM partners').fetchone()[0]
        if count == 0:
            for p in PARTNER_DATA:
                db.execute('INSERT INTO partners (name,share,investment,status,note) VALUES (?,?,?,?,?)', p)
        # Seed products
        count = db.execute('SELECT COUNT(*) FROM products').fetchone()[0]
        if count == 0:
            for p in DEFAULT_PRODUCTS:
                db.execute('INSERT INTO products (name,spec,unit,price,supplier) VALUES (?,?,?,?,?)',
                          (p[0],p[1],p[2],p[3],p[4]))
        db.commit()
        # Migration: add bill_date column (ignore if exists)
        try:
            db.execute('ALTER TABLE reconciliations ADD COLUMN bill_date TEXT')
        except:
            pass
        try:
            db.execute('ALTER TABLE reconciliations ADD COLUMN reconciled_by TEXT')
        except:
            pass
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN images TEXT DEFAULT ''")
        except:
            pass
        # Migration (2026.5.30): add supplier column to products
        try:
            db.execute("ALTER TABLE products ADD COLUMN supplier TEXT DEFAULT ''")
        except:
            pass
        # Migration (2026.5.30): add date column to transactions
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN date TEXT DEFAULT ''")
        except:
            pass
        # Migration (2026.6.1): add thumb_images to transactions and procurement_batches
        try:
            db.execute("ALTER TABLE transactions ADD COLUMN thumb_images TEXT DEFAULT '[]'")
        except:
            pass
        try:
            db.execute("ALTER TABLE procurement_batches ADD COLUMN thumb_images TEXT DEFAULT '[]'")
        except:
            pass
        try:
            db.execute("ALTER TABLE dividends ADD COLUMN date TEXT DEFAULT ''")
        except:
            pass

init_db()
# Auto-verify existing users (backward compat)
with get_db() as db:
    db.execute("UPDATE users SET is_verified=1 WHERE is_verified IS NULL OR is_verified=0")
    db.commit()

# ── Validation helper ──
def validate_required(data, *fields):
    """Return list of missing field names; empty if all present."""
    return [f for f in fields if data.get(f) is None]

# ====== Auth ======

# ── Email validation ──
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')

def validate_email(email):
    return bool(EMAIL_RE.match(email))

# ── Rate limiting (in-memory, resets on process restart) ──
_login_attempts = {}  # { ip: [attempt_timestamps...] }
_RATE_LIMIT_MAX = 5
_RATE_LIMIT_WINDOW = 900  # 15 minutes in seconds


def check_rate_limit(ip):
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune expired attempts
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    _login_attempts[ip] = attempts
    if len(attempts) >= _RATE_LIMIT_MAX:
        wait = int(_RATE_LIMIT_WINDOW - (now - attempts[0]))
        return False, wait
    return True, 0


def record_failed_attempt(ip):
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
    attempts.append(now)
    _login_attempts[ip] = attempts


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
            return jsonify({'status':'ok','token':token,'username':user['username']})
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
        return jsonify({'status':'error','message':_t('err_username_invalid', g.lang) or '用户名仅支持字母、数字、下划线和中文，2-32位'}), 400
    # 密码强度校验
    ok, msg = validate_password(password, g.lang)
    if not ok:
        return jsonify({'status':'error','message':msg}), 400
    with get_db() as db:
        # 检查重复（不区分验证状态）
        exists = db.execute('SELECT id FROM users WHERE username=? OR email=?',(username, email)).fetchone()
        if exists:
            return jsonify({'status':'error','message':_t('err_username_exists', g.lang)}), 409
        # 仅清理验证码已过期的未验证记录
        db.execute("DELETE FROM users WHERE (username=? OR email=?) AND is_verified=0 AND code_expires < datetime('now')",(username, email))
        code = generate_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        db.execute('INSERT INTO users (username,password,email,verification_code,code_expires,is_verified) VALUES (?,?,?,?,?,0)', (username, generate_password_hash(password), email, code, expires))
        db.commit()
        if not send_verification_email(email, code):
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
        if not send_verification_email(email, code):
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
    # 限流：IP 15分钟5次
    ip = request.remote_addr or 'unknown'
    allowed, wait = check_rate_limit(ip)
    if not allowed:
        mins = wait // 60
        secs = wait % 60
        return jsonify({'status':'error','message':_t('err_too_many_attempts', g.lang, mins=mins, secs=secs) or f'Too many attempts. Please wait {mins}m{secs}s.'}), 429
    record_failed_attempt(ip)
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
            db.execute('INSERT INTO transactions (type,amount,category,account,note,images,thumb_images) VALUES (?,?,?,?,?,?,?)',
                       (data['type'], data['amount'], data['category'], data['account'],
                        data.get('note',''),
                        json.dumps(data.get('images', [])),
                        json.dumps(data.get('thumb_images', []))))
            db.commit()
        return jsonify({'status':'ok'})
    # GET with pagination & filtering
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    tx_type = request.args.get('type')           # 'income' or 'expense'
    date_from = request.args.get('date_from')    # 'YYYY-MM-DD'
    date_to = request.args.get('date_to')
    category = request.args.get('category')      # comma-separated: '日常,房租'

    where = []
    params = []
    if tx_type:
        where.append('type=?')
        params.append(tx_type)
    if date_from:
        where.append('date(created_at) >= ?')
        params.append(date_from)
    if date_to:
        where.append('date(created_at) <= ?')
        params.append(date_to)
    if category:
        cats = [c.strip() for c in category.split(',') if c.strip()]
        if cats:
            placeholders = ','.join(['?' for _ in cats])
            where.append(f'category IN ({placeholders})')
            params.extend(cats)

    where_sql = ' AND '.join(where) if where else '1=1'

    with get_db() as db:
        count = db.execute(f'SELECT COUNT(*) FROM transactions WHERE {where_sql}', params).fetchone()[0]
        pages = max(1, (count + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.execute(
            f'SELECT * FROM transactions WHERE {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?',
            params + [per_page, offset]
        ).fetchall()
    return jsonify({
        'transactions': [dict(r) for r in rows],
        'page': page, 'pages': pages, 'total': count, 'per_page': per_page,
    })

@app.route('/api/transactions/<int:id>', methods=['DELETE'])
@login_required
def api_delete_transaction(id):
    with get_db() as db:
        db.execute('DELETE FROM transactions WHERE id=?', (id,))
        db.commit()
    return jsonify({'status':'ok'})

# ── Expense image upload ──

@app.route('/api/expenses/upload-images', methods=['POST'])
@login_required
def api_upload_expense_images():
    """Upload receipt images. Returns { images: [...], thumb_images: [...], has_thumbs: bool }.
    thumb_images[i] is the 128×128 thumbnail URL (or images[i] fallback if Pillow unavailable).
    """
    if 'files' not in request.files:
        return jsonify({'status': 'error', 'message': 'No files'}), 400
    files = request.files.getlist('files')
    if not files:
        return jsonify({'status': 'error', 'message': 'No files'}), 400
    user_id = str(g.user_id)
    user_dir = os.path.join(EXPENSE_IMG_DIR, user_id)
    os.makedirs(user_dir, exist_ok=True)
    urls = []
    thumb_urls = []
    import uuid
    for f in files:
        if f.filename == '':
            continue
        # Keep original extension, generate unique name
        ext = os.path.splitext(f.filename or 'img.jpg')[1] or '.jpg'
        safe_name = f"{uuid.uuid4().hex}{ext}"
        save_path = os.path.join(user_dir, safe_name)
        f.save(save_path)
        urls.append(f'/expense-imgs/{user_id}/{safe_name}')
        # Generate 128×128 thumbnail for list rendering (faster load, less bandwidth)
        # Graceful degradation: if Pillow fails, fall back to original image URL
        if HAS_PIL:
            try:
                thumb_name = f"{os.path.splitext(safe_name)[0]}_thumb.jpg"
                thumb_path = os.path.join(user_dir, thumb_name)
                with _PILImage.open(save_path) as img:  # type: ignore[union-attr]
                    img.thumbnail((128, 128), _PILImage.LANCZOS)  # type: ignore[union-attr]
                    # Convert to RGB if needed (PNG with alpha, etc.)
                    if img.mode in ('RGBA', 'P', 'LA'):
                        bg = _PILImage.new('RGB', img.size, (255, 255, 255))  # type: ignore[union-attr]
                        if img.mode in ('RGBA', 'LA'):
                            bg.paste(img, mask=img.split()[-1])
                        else:
                            bg.paste(img.convert('RGBA'))
                        img = bg
                    img.save(thumb_path, 'JPEG', quality=85, optimize=True)
                thumb_urls.append(f'/expense-imgs/{user_id}/{thumb_name}')
            except Exception:
                # Thumbnail generation failed (corrupt image, unsupported format, etc.)
                # Fall back to original so frontend still has something to display
                thumb_urls.append(f'/expense-imgs/{user_id}/{safe_name}')
        else:
            # Pillow not installed: fall back to original
            thumb_urls.append(f'/expense-imgs/{user_id}/{safe_name}')
    return jsonify({'status': 'ok', 'images': urls, 'thumb_images': thumb_urls, 'has_thumbs': HAS_PIL})

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
                db.execute('INSERT INTO dividends (partner,amount,note,date) VALUES (?,?,?,?)',
                    (item['partner'], item['amount'], item.get('note',''), item.get('date','')))
            db.commit()
        return jsonify({'status':'ok'})
    with get_db() as db:
        rows = db.execute('SELECT * FROM dividends ORDER BY date DESC, created_at DESC').fetchall()
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

@app.route('/api/settings/background', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_background():
    if request.method == 'GET':
        url = None
        save_path = os.path.join(BG_DIR, f'home-bg-{g.user_id}.jpg')
        if os.path.exists(save_path):
            url = f'/user-images/home-bg-{g.user_id}.jpg?t={int(os.path.getmtime(save_path))}'
        opacity = 0.55
        with get_db() as db:
            row = db.execute("SELECT value FROM user_settings WHERE user_id=? AND key='background_opacity'", (g.user_id,)).fetchone()
            if row and row['value'] is not None:
                try: opacity = float(row['value'])
                except: pass
        return jsonify({'url': url, 'opacity': opacity})

    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '未选择文件'}), 400
        f = request.files['file']
        if f.filename == '':
            return jsonify({'status': 'error', 'message': '文件名为空'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_BG_EXT:
            return jsonify({'status': 'error', 'message': f'仅支持 {", ".join(ALLOWED_BG_EXT)} 格式'}), 400
        f.seek(0, 2)
        size = f.tell()
        f.seek(0)
        if size > MAX_BG_SIZE:
            return jsonify({'status': 'error', 'message': '文件最大 5MB'}), 400
        os.makedirs(BG_DIR, exist_ok=True)
        save_path = os.path.join(BG_DIR, f'home-bg-{g.user_id}.jpg')
        f.save(save_path)
        url = f'/user-images/home-bg-{g.user_id}.jpg?t={int(time.time())}'
        return jsonify({'status': 'ok', 'url': url})

    if request.method == 'PUT':
        data = request.get_json()
        if data and 'opacity' in data:
            with get_db() as db:
                db.execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'background_opacity', ?)",
                           (g.user_id, str(data['opacity'])))
                db.commit()
        return jsonify({'status': 'ok'})

@app.route('/api/settings/lang', methods=['GET'])
@login_required
def api_get_lang():
    with get_db() as db:
        row = db.execute("SELECT value FROM user_settings WHERE user_id=? AND key='lang'", (g.user_id,)).fetchone()
    lang = row['value'] if row else 'zh-CN'
    return jsonify({'lang': lang})

@app.route('/api/settings/lang', methods=['PUT'])
@login_required
def api_save_lang():
    data = request.get_json()
    if data and 'lang' in data:
        with get_db() as db:
            db.execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'lang', ?)",
                       (g.user_id, data['lang']))
            db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/settings/theme', methods=['GET'])
@login_required
def api_get_theme():
    with get_db() as db:
        row = db.execute("SELECT value FROM user_settings WHERE user_id=? AND key='theme'", (g.user_id,)).fetchone()
    theme = row['value'] if row else 'burgundy-warm'
    return jsonify({'theme': theme})


@app.route('/api/settings/theme', methods=['PUT'])
@login_required
def api_save_theme():
    data = request.get_json()
    if data and 'theme' in data:
        with get_db() as db:
            db.execute("INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'theme', ?)",
                       (g.user_id, data['theme']))
            db.commit()
    return jsonify({'status': 'ok'})

    if request.method == 'DELETE':
        save_path = os.path.join(BG_DIR, f'home-bg-{g.user_id}.jpg')
        if os.path.exists(save_path):
            os.remove(save_path)
        return jsonify({'status': 'ok'})

@app.route('/api/partners/<int:id>', methods=['DELETE'])
@login_required
def api_delete_partner(id):
    with get_db() as db:
        db.execute('DELETE FROM partners WHERE id=?', (id,))
        db.commit()
    return jsonify({'status':'ok'})

@app.route('/api/partners/<int:id>', methods=['PUT'])
@login_required
def api_update_partner(id):
    data = request.get_json()
    missing = validate_required(data, 'share', 'investment')
    if missing:
        return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    with get_db() as db:
        db.execute('UPDATE partners SET share=?, investment=?, status=?, note=? WHERE id=?', (data['share'], data['investment'], data.get('status','进行中'), data.get('note',''), id))
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
            db.execute('INSERT INTO products (name,spec,unit,price,supplier,note) VALUES (?,?,?,?,?,?)',
                      (data['name'], data.get('spec',''), data.get('unit',''), data.get('price',0), data.get('supplier',''), data.get('note','')))
            db.commit()
        return jsonify({'status':'ok'})
    if request.method == 'PUT':
        data = request.get_json()
        missing = validate_required(data, 'name', 'id')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('UPDATE products SET name=?, spec=?, unit=?, price=?, supplier=?, note=? WHERE id=?',
                      (data['name'], data.get('spec',''), data.get('unit',''), data.get('price',0), data.get('supplier',''), data.get('note',''), data['id']))
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

# ── 进货批次 API（2026.5.30）──
@app.route('/api/procurement-batches', methods=['GET','POST'])
@login_required
def api_procurement_batches():
    """POST: 创建进货批次 + 明细"""
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'date', 'payment_method', 'items')
        if missing:
            return jsonify({'status':'error','message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({'status':'error','message': _t('err_empty_fields', g.lang)}), 400
        with get_db() as db:
            cur = db.execute('SELECT COALESCE(MAX(batch_number),0) FROM procurement_batches').fetchone()
            batch_no = cur[0] + 1
            total = 0.0
            item_rows = []
            for item in items:
                pid = item.get('product_id')
                qty = item.get('quantity', 0)
                if not pid or qty <= 0:
                    continue
                product = db.execute('SELECT * FROM products WHERE id=?', (pid,)).fetchone()
                if not product:
                    continue
                unit_price = product['price']
                subtotal = unit_price * qty
                total += subtotal
                item_rows.append((product['name'], product['spec'] or '', unit_price, qty, subtotal, pid))
            if total == 0:
                return jsonify({'status':'error','message': _t('err_empty_fields', g.lang)}), 400
            images_json = json.dumps(data.get('images', []))
            thumbs_json = json.dumps(data.get('thumb_images', []))
            cur = db.execute(
                'INSERT INTO procurement_batches (batch_number,date,payment_method,category,total,images,thumb_images,note) VALUES (?,?,?,?,?,?,?,?)',
                (batch_no, data['date'], data['payment_method'], data.get('category','采购'), round(total, 2),
                 images_json, thumbs_json, data.get('note', ''))
            )
            batch_id = cur.lastrowid
            for name, spec, up, qty, sub, pid in item_rows:
                db.execute(
                    'INSERT INTO procurement_items (batch_id,product_id,product_name,spec,unit_price,quantity,subtotal) VALUES (?,?,?,?,?,?,?)',
                    (batch_id, pid, name, spec, up, qty, round(sub, 2))
                )
            # Sync an expense transaction (with thumb_images so history list can show thumbnail)
            db.execute(
                "INSERT INTO transactions (type,amount,category,account,note,date,images,thumb_images) VALUES ('expense',?,?,?,?,?,?,?)",
                (round(total, 2), data.get('category','采购'), data['payment_method'], data.get('note',''), data['date'], images_json, thumbs_json)
            )
            db.commit()
        return jsonify({'status':'ok', 'batch_id': batch_id, 'batch_number': batch_no, 'total': round(total, 2)})
    # GET: 进货记录列表（分页）
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    with get_db() as db:
        total = db.execute('SELECT COUNT(*) FROM procurement_batches').fetchone()[0]
        rows = db.execute(
            'SELECT * FROM procurement_batches ORDER BY date DESC, id DESC LIMIT ? OFFSET ?',
            (per_page, (page - 1) * per_page)
        ).fetchall()
        batches = []
        for row in rows:
            b = dict(row)
            b['images'] = json.loads(b['images']) if b['images'] else []
            b['thumb_images'] = json.loads(b['thumb_images']) if b['thumb_images'] else []
            items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (b['id'],)).fetchall()
            b['items'] = [dict(it) for it in items]
            batches.append(b)
    return jsonify({'records': batches, 'total': total, 'pages': max(1, (total + per_page - 1) // per_page), 'page': page, 'per_page': per_page})

@app.route('/api/procurement-batches/<int:id>', methods=['GET'])
@login_required
def api_procurement_batch_detail(id):
    """单次进货详情"""
    with get_db() as db:
        row = db.execute('SELECT * FROM procurement_batches WHERE id=?', (id,)).fetchone()
        if not row:
            return jsonify({'status':'error','message':'Not found'}), 404
        b = dict(row)
        b['images'] = json.loads(b['images']) if b['images'] else []
        items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (id,)).fetchall()
        b['items'] = [dict(it) for it in items]
    return jsonify(b)

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
        month_procurement = db.execute("SELECT COALESCE(SUM(total),0) FROM procurement_batches WHERE strftime('%Y-%m', date)=?", (month_str,)).fetchone()[0]
    return jsonify({
        'today': {'income': today_income, 'expense': today_expense, 'profit': today_income - today_expense},
        'month': {'income': month_income, 'expense': month_expense, 'profit': month_income - month_expense, 'procurement': month_procurement}
    })

# ── Procurement Stats ──
@app.route('/api/procurement-stats')
@login_required
def api_procurement_stats():
    with get_db() as db:
        total_spent = db.execute("SELECT COALESCE(SUM(total),0) FROM procurement_batches").fetchone()[0]
        total_income = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income'").fetchone()[0]
        batch_count = db.execute("SELECT COUNT(*) FROM procurement_batches").fetchone()[0]
        margin_pct = round((total_income - total_spent) / total_spent * 100, 1) if total_spent > 0 else 0
    return jsonify({
        'total_spent': round(total_spent, 2),
        'total_income': round(total_income, 2),
        'batch_count': batch_count,
        'margin_pct': margin_pct
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

# ── Users list (for dropdowns etc.) ──
@app.route('/api/users')
@login_required
def api_users():
    with get_db() as db:
        rows = db.execute('SELECT id, username FROM users WHERE is_verified=1 ORDER BY username').fetchall()
    return jsonify([dict(r) for r in rows])

# ── Reconciliations ──
@app.route('/api/migrate-recon', methods=['POST'])
@login_required
def api_migrate_recon():
    """One-time migration: remove UNIQUE(date) constraint."""
    with get_db() as db:
        try:
            db.execute('PRAGMA foreign_keys = OFF')
            indexes = db.execute("PRAGMA index_list('reconciliations')").fetchall()
            result = {'indexes': [{'seq': r[0], 'name': r[1], 'unique': r[2]} for r in indexes]}
            has_unique = any(r[1].startswith('sqlite_autoindex') for r in indexes)
            if not has_unique:
                return jsonify({'message': 'Already migrated, no UNIQUE found', 'result': result})
            db.execute('DROP TABLE IF EXISTS reconciliations_new')
            db.execute('''CREATE TABLE reconciliations_new (
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
                bill_date TEXT
            )''')
            db.execute('INSERT INTO reconciliations_new SELECT id,date,card_balance,cash_balance,dine_in,meituan,flash_sale,jd,tuan,channel_total,real_total,diff,user_id,created_at,bill_date FROM reconciliations')
            n = db.execute('SELECT COUNT(*) FROM reconciliations_new').fetchone()[0]
            db.execute('DROP TABLE reconciliations')
            db.execute('ALTER TABLE reconciliations_new RENAME TO reconciliations')
            db.execute('CREATE INDEX IF NOT EXISTS idx_recon_date ON reconciliations(date)')
            db.execute('PRAGMA foreign_keys = ON')
            db.commit()
            return jsonify({'message': f'Migrated {n} rows, UNIQUE removed', 'rows': n})
        except Exception as e:
            import traceback
            return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/api/reconciliations/clear', methods=['POST'])
@login_required
def api_clear_reconciliations():
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'YES':
        return jsonify({'ok': False, 'message': '需要 confirm="YES" 二次确认'}), 400
    with get_db() as db:
        db.execute('DELETE FROM reconciliations')
        db.commit()
    return jsonify({'ok': True, 'message': 'All reconciliation records cleared'})

@app.route('/api/reconciliations', methods=['POST'])
@login_required
def api_create_reconciliation():
    data = request.get_json() or {}
    if validate_required(data, 'date'): return jsonify({'error': '缺少日期'}), 400
    date = data['date']
    # validate date format
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': '日期格式必须为 YYYY-MM-DD'}), 400
    bill_date = data.get('bill_date', date)
    if bill_date:
        try:
            datetime.strptime(bill_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': '账单日期格式必须为 YYYY-MM-DD'}), 400
    reconciled_by = data.get('reconciled_by', g.username)
    # fetch username from DB if session doesn't have it (Bearer token path)
    if not reconciled_by and g.user_id:
        with get_db() as db:
            user = db.execute('SELECT username FROM users WHERE id=?', (g.user_id,)).fetchone()
            reconciled_by = user['username'] if user else str(g.user_id)
    # only validate explicit input (not the auto-filled default)
    if 'reconciled_by' in data and not re.match(r'^[\w\u4e00-\u9fa5@.\-]{1,32}$', reconciled_by):
        return jsonify({'error': '录入人格式无效'}), 400

    balances = {}
    for field in ['card_balance','cash_balance','dine_in','meituan','flash_sale','jd','tuan']:
        raw = data.get(field)
        try:
            v = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return jsonify({'error': f'{field} 必须是有效数字'}), 400
        if v < 0:
            return jsonify({'error': f'{field} 不能为负'}), 400
        if abs(v) > 1e10:
            return jsonify({'error': f'{field} 数值超出合理范围'}), 400
        balances[field] = v

    card_balance = balances['card_balance']
    cash_balance = balances['cash_balance']
    dine_in = balances['dine_in']
    meituan = balances['meituan']
    flash_sale = balances['flash_sale']
    jd = balances['jd']
    tuan = balances['tuan']
    channel_total = round(dine_in + meituan + flash_sale + jd + tuan, 2)
    real_total = round(card_balance + cash_balance, 2)
    diff = round(real_total - channel_total, 2)

    with get_db() as db:
        # Upsert: same bill_date updates existing, otherwise inserts
        existing = db.execute(
            'SELECT id FROM reconciliations WHERE bill_date=?',
            (bill_date,)
        ).fetchone()
        if existing:
            db.execute('''UPDATE reconciliations SET
                date=?, card_balance=?, cash_balance=?, dine_in=?, meituan=?, flash_sale=?,
                jd=?, tuan=?, channel_total=?, real_total=?, diff=?, reconciled_by=?
                WHERE id=?''',
                (date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
                 channel_total, real_total, diff, reconciled_by, existing['id']))
            db.commit()
            return jsonify({'ok': True, 'action': 'updated', 'id': existing['id']}), 200
        else:
            db.execute('''INSERT INTO reconciliations
                (date, bill_date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
                 channel_total, real_total, diff, reconciled_by)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (date, bill_date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
                 channel_total, real_total, diff, reconciled_by))
            db.commit()
            new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            return jsonify({'ok': True, 'action': 'created', 'id': new_id}), 201

@app.route('/api/reconciliations', methods=['GET'])
@login_required
def api_get_reconciliations():
    # New: page-based pagination (returns { records, total, pages, ... })
    page = request.args.get('page', 0, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(1, min(per_page, 100))

    # Old: limit-based (0=all, returns plain array)
    limit = request.args.get('limit', page > 0 and 0 or 30, type=int)
    if page <= 0:
        if limit < 0:
            return jsonify({'error': 'limit 不能为负'}), 400
        elif limit > 200:
            limit = 200

    bill_date_from = request.args.get('bill_date_from', '')
    bill_date_to = request.args.get('bill_date_to', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    reconciled_by = request.args.get('reconciled_by', '')

    where = 'WHERE 1=1'
    params = []
    if bill_date_from:
        where += ' AND bill_date >= ?'
        params.append(bill_date_from)
    if bill_date_to:
        where += ' AND bill_date <= ?'
        params.append(bill_date_to)
    if date_from:
        where += ' AND date >= ?'
        params.append(date_from)
    if date_to:
        where += ' AND date <= ?'
        params.append(date_to)
    if reconciled_by:
        where += ' AND reconciled_by = ?'
        params.append(reconciled_by)

    with get_db() as db:
        if page > 0:
            # New pagination mode
            count = db.execute(f'SELECT COUNT(*) FROM reconciliations {where}', params).fetchone()[0]
            pages = max(1, (count + per_page - 1) // per_page)
            offset = (page - 1) * per_page
            rows = db.execute(
                f'SELECT * FROM reconciliations {where} ORDER BY bill_date DESC, date DESC LIMIT ? OFFSET ?',
                params + [per_page, offset]
            ).fetchall()
            return jsonify({
                'records': [dict(r) for r in rows],
                'page': page, 'pages': pages, 'total': count, 'per_page': per_page,
            })
        else:
            # Old mode: limit-based (0=all)
            if limit <= 0:
                rows = db.execute(
                    f'SELECT * FROM reconciliations {where} ORDER BY bill_date DESC, date DESC',
                    params
                ).fetchall()
            else:
                rows = db.execute(
                    f'SELECT * FROM reconciliations {where} ORDER BY bill_date DESC, date DESC LIMIT ?',
                    params + [limit]
                ).fetchall()
            return jsonify([dict(r) for r in rows])

# ── Platform Fees ──

@app.route('/api/platform-fees', methods=['GET'])
@login_required
def api_get_platform_fees():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    with get_db() as db:
        if year and month:
            row = db.execute(
                'SELECT * FROM platform_fees WHERE year=? AND month=?',
                (year, month)
            ).fetchone()
            return jsonify(dict(row) if row else {})
        rows = db.execute(
            'SELECT * FROM platform_fees ORDER BY year DESC, month DESC'
        ).fetchall()
        return jsonify([dict(r) for r in rows])

@app.route('/api/platform-fees/entry', methods=['POST'])
@login_required
def api_add_platform_fee_entry():
    data = request.get_json()
    missing = validate_required(data, 'year', 'month', 'entry_date')
    if missing:
        return jsonify({'status': 'error', 'message': f'缺少必填字段: {", ".join(missing)}'}), 400
    year = data.get('year')
    month = data.get('month')
    entry_date = data.get('entry_date')
    mc = data.get('meituan_cashier', 0)
    mw = data.get('meituan_waimai', 0)
    ew = data.get('eleme_waimai', 0)
    mt = data.get('meituan_tuan', 0)
    with get_db() as db:
        # Upsert monthly row
        db.execute('''INSERT INTO platform_fees (year, month, meituan_cashier, meituan_waimai, eleme_waimai, meituan_tuan)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(year, month) DO UPDATE SET
                      meituan_cashier=meituan_cashier+excluded.meituan_cashier,
                      meituan_waimai=meituan_waimai+excluded.meituan_waimai,
                      eleme_waimai=eleme_waimai+excluded.eleme_waimai,
                      meituan_tuan=meituan_tuan+excluded.meituan_tuan''',
                   (year, month, mc, mw, ew, mt))
        fee_id = db.execute('SELECT id FROM platform_fees WHERE year=? AND month=?', (year, month)).fetchone()['id']
        # Record the daily entry
        db.execute('''INSERT INTO platform_fee_entries (fee_id, entry_date, meituan_cashier, meituan_waimai, eleme_waimai, meituan_tuan)
                      VALUES (?,?,?,?,?,?)''',
                   (fee_id, entry_date, mc, mw, ew, mt))
        updated = db.execute('SELECT * FROM platform_fees WHERE year=? AND month=?', (year, month)).fetchone()
        return jsonify({'status': 'ok', 'data': dict(updated)})

@app.route('/api/platform-fees/<int:id>', methods=['PUT'])
@login_required
def api_update_platform_fee(id):
    data = request.get_json()
    with get_db() as db:
        db.execute('''UPDATE platform_fees SET meituan_cashier=?, meituan_waimai=?, eleme_waimai=?, meituan_tuan=?
                      WHERE id=?''',
                   (data.get('meituan_cashier', 0), data.get('meituan_waimai', 0),
                    data.get('eleme_waimai', 0), data.get('meituan_tuan', 0), id))
        return jsonify({'status': 'ok'})

# ====== Daily Revenue (每日营收) ======

@app.route('/api/daily-revenue', methods=['GET'])
@login_required
def api_get_daily_revenue():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    date = request.args.get('date', type=str)
    days = request.args.get('days', type=int)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    with get_db() as db:
        where = ''
        params = []
        if days:
            # Last N days summary
            rows = db.execute('''
                SELECT date, revenue, turnover, jd_revenue
                FROM daily_revenue
                WHERE date >= date('now', ? ) 
                ORDER BY date DESC
            ''', (f'-{days} days',)).fetchall()
            totals = {'revenue': 0, 'turnover': 0, 'jd_revenue': 0}
            for r in rows:
                totals['revenue'] += (r['revenue'] or 0)
                totals['turnover'] += (r['turnover'] or 0)
                totals['jd_revenue'] += (r['jd_revenue'] or 0)
            return jsonify({'records': [], 'total': len(rows), 'pages': 1, 'page': 1, 'per_page': per_page, 'totals': totals})
        elif date:
            where = 'WHERE dr.date=?'
            params.append(date)
        elif year and month:
            where = "WHERE substr(dr.date,1,7)=?"
            params.append(f'{year}-{month:02d}')
        elif year:
            where = "WHERE substr(dr.date,1,4)=?"
            params.append(str(year))
        base = f'''SELECT dr.*, u.username as recorded_by
                   FROM daily_revenue dr
                   LEFT JOIN users u ON dr.user_id = u.id
                   {where}'''
        count = db.execute(f'SELECT COUNT(*) FROM daily_revenue dr {where}', params).fetchone()[0]
        total_pages = max(1, (count + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.execute(
            base + ' ORDER BY dr.date DESC LIMIT ? OFFSET ?',
            params + [per_page, offset]
        ).fetchall()
        return jsonify({
            'records': [dict(r) for r in rows],
            'total': count,
            'pages': total_pages,
            'page': page,
            'per_page': per_page,
        })

@app.route('/api/daily-revenue/last-7', methods=['GET'])
@login_required
def api_last_7_days():
    """Return last 7 days with gaps filled (even days without data)."""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(7)]
    with get_db() as db:
        rows = db.execute('''
            SELECT dr.*, u.username as recorded_by
            FROM daily_revenue dr
            LEFT JOIN users u ON dr.user_id = u.id
            WHERE dr.date IN ({})
        '''.format(','.join('?' * len(dates))), dates).fetchall()
        by_date = {r['date']: dict(r) for r in rows}
        result = []
        for d in dates:
            if d in by_date:
                result.append(by_date[d])
            else:
                result.append({
                    'date': d,
                    'revenue': 0, 'turnover': 0, 'jd_revenue': 0, 'note': '',
                    'recorded_by': None,
                    'archived': 0,
                    'status': '未录入',
                })
        return jsonify({'records': result})

@app.route('/api/daily-revenue', methods=['POST'])
@login_required
def api_create_daily_revenue():
    data = request.get_json()
    missing = validate_required(data, 'date', 'turnover')
    if missing:
        return jsonify({'status': 'error', 'message': f'缺少必填字段: {", ".join(missing)}'}), 400
    date = data['date']
    revenue = float(data.get('revenue', 0))
    turnover = float(data['turnover'])
    jd_revenue = float(data.get('jd_revenue', 0))
    note = data.get('note', '')
    archived = int(data.get('archived', 0))
    with get_db() as db:
        try:
            db.execute(
                'INSERT INTO daily_revenue (date, revenue, turnover, jd_revenue, note, user_id, archived) VALUES (?,?,?,?,?,?,?)',
                (date, revenue, turnover, jd_revenue, note, g.user_id, archived)
            )
            row = db.execute('''SELECT dr.*, u.username as recorded_by
                                FROM daily_revenue dr
                                LEFT JOIN users u ON dr.user_id = u.id
                                WHERE dr.date=?''', (date,)).fetchone()
            return jsonify({'status': 'ok', 'data': dict(row)})
        except sqlite3.IntegrityError:
            return jsonify({'status': 'error', 'message': '该日期已有营收记录'}), 409

@app.route('/api/daily-revenue/<int:id>', methods=['PUT'])
@login_required
def api_update_daily_revenue(id):
    data = request.get_json()
    with get_db() as db:
        row = db.execute('SELECT * FROM daily_revenue WHERE id=?', (id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '记录不存在'}), 404
        fields = []
        params = []
        for k in ['revenue', 'turnover', 'jd_revenue', 'note', 'archived']:
            if k in data:
                fields.append(f'{k}=?')
                params.append(float(data[k]) if k != 'note' else data[k])
        if not fields:
            return jsonify({'status': 'error', 'message': '无更新字段'}), 400
        params.append(id)
        db.execute(f"UPDATE daily_revenue SET {', '.join(fields)} WHERE id=?", params)
        updated = db.execute('''SELECT dr.*, u.username as recorded_by
                                FROM daily_revenue dr
                                LEFT JOIN users u ON dr.user_id = u.id
                                WHERE dr.id=?''', (id,)).fetchone()
        return jsonify({'status': 'ok', 'data': dict(updated)})

@app.route('/api/daily-revenue/<int:id>', methods=['DELETE'])
@login_required
def api_delete_daily_revenue(id):
    with get_db() as db:
        db.execute('DELETE FROM daily_revenue WHERE id=?', (id,))
        return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8600, debug=True)