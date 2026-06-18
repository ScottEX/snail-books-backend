#!/usr/bin/env python3
"""🍜 蓝姐 · 记账系统"""

import sqlite3, os, functools, re, json, time, mimetypes
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
from shared.config import FLASK_SECRET_KEY, FRONTEND_DIR, EXPENSE_IMG_DIR, BG_DIR, DB

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY
app.permanent_session_lifetime = timedelta(hours=24)

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


MAINTENANCE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>柳味探秘</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #FBF7F4;
    display: flex; justify-content: center; align-items: center;
    min-height: 100dvh; padding: 24px;
  }
  .card {
    text-align: center; max-width: 320px;
  }
  .icon {
    font-size: 48px; margin-bottom: 20px;
    animation: pulse 1.8s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: .4; transform: scale(.96); }
    50% { opacity: 1; transform: scale(1); }
  }
  h1 {
    font-size: 18px; font-weight: 600; color: #5C3D2E;
    margin-bottom: 8px;
  }
  p {
    font-size: 14px; color: #8B7355; line-height: 1.6;
  }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🐌</div>
  <h1>稍等片刻，马上就好</h1>
  <p>页面正在更新中，请刷新试试</p>
</div>
</body>
</html>"""


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
        resp = make_response(send_file(index_path, mimetype='text/html'))
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return MAINTENANCE_HTML, 503, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/', defaults={'path': ''})
def serve_spa_root(path):
    index_path = os.path.join(FRONTEND_DIR, 'index.html')
    if os.path.isfile(index_path):
        resp = make_response(send_file(index_path, mimetype='text/html'))
        resp.headers['Cache-Control'] = 'no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp
    return MAINTENANCE_HTML, 503, {'Content-Type': 'text/html; charset=utf-8'}



# ═══════════════════════════════════════════════════════════
#  Database
# ═══════════════════════════════════════════════════════════

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
                signature TEXT DEFAULT '',
                real_name TEXT DEFAULT '',
                enforce_single_session INTEGER DEFAULT 0,
                session_timeout_hours INTEGER DEFAULT 24,
                current_session_id TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                delete_scheduled TIMESTAMP,
                delete_by TEXT DEFAULT '',
                delete_reminded INTEGER DEFAULT 0,
                reviewed INTEGER DEFAULT 0,
                is_disabled INTEGER DEFAULT 0,
                last_login_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS user_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                token TEXT NOT NULL UNIQUE,
                session_id TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
                       CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                session_id TEXT NOT NULL UNIQUE,
                device_info TEXT DEFAULT '',
                expires_at TIMESTAMP,
                revoked_at TIMESTAMP,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
 CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                account TEXT NOT NULL,
                note TEXT DEFAULT '',
                images TEXT DEFAULT '[]',
                thumb_images TEXT DEFAULT '[]',
                date TEXT DEFAULT '',
                procurement_batch_id INTEGER,
                user_id INTEGER REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS dividends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT DEFAULT '',
                date TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                share REAL NOT NULL,
                investment REAL NOT NULL DEFAULT 0,
                init_capital REAL NOT NULL DEFAULT 0,
                init_date TEXT DEFAULT '',
                add_date TEXT DEFAULT '',
                status TEXT DEFAULT '',
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id),
                linked_user_id INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                price REAL NOT NULL DEFAULT 0,
                supplier TEXT DEFAULT '',
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS procurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_name TEXT,
                quantity REAL,
                unit TEXT DEFAULT '',
                unit_price REAL,
                total REAL,
                note TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id),
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
                user_id INTEGER REFERENCES users(id),
                settled_at TIMESTAMP DEFAULT NULL,
                settled_by INTEGER REFERENCES users(id) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS procurement_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER REFERENCES procurement_batches(id),
                product_id INTEGER,
                product_name TEXT,
                spec TEXT DEFAULT '',
                quantity REAL,
                unit_price REAL,
                subtotal REAL,
                user_id INTEGER REFERENCES users(id),
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
                bill_date TEXT DEFAULT '',
                reconciled_by TEXT DEFAULT '',
                user_id INTEGER REFERENCES users(id),
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
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
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
            CREATE TABLE IF NOT EXISTS invoice_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id),
                procurement_batch_id INTEGER,
                type TEXT NOT NULL CHECK(type IN ('vat','general')),
                company TEXT NOT NULL DEFAULT '',
                tax_id TEXT DEFAULT '',
                amount REAL NOT NULL DEFAULT 0,
                date TEXT NOT NULL,
                invoice_number TEXT DEFAULT '',
                email TEXT DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('pending','done')) DEFAULT 'pending',
                file_path TEXT DEFAULT '',
                file_type TEXT DEFAULT '',
                file_size INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        # Ensure email uniqueness at DB level (P2-FF)
        try:
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')
        except sqlite3.OperationalError:
            pass
        # One procurement batch can only have one invoice record
        try:
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_batch ON invoice_records(procurement_batch_id) WHERE procurement_batch_id IS NOT NULL')
        except sqlite3.OperationalError:
            pass
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
            db.execute("ALTER TABLE procurement_batches ADD COLUMN settled_at TIMESTAMP DEFAULT NULL")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE procurement_batches ADD COLUMN settled_by INTEGER REFERENCES users(id) DEFAULT NULL")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE procurements ADD COLUMN unit TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE dividends ADD COLUMN date TEXT DEFAULT ''")
        except:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN is_disabled INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN reviewed INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN remark TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN real_name TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN delete_scheduled TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN delete_by TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN delete_reminded INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE invoice_records ADD COLUMN note TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE partners ADD COLUMN init_capital REAL NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE partners ADD COLUMN init_date TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE partners ADD COLUMN add_date TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            db.execute("ALTER TABLE partners ADD COLUMN linked_user_id INTEGER REFERENCES users(id)")
        except sqlite3.OperationalError:
            pass


init_db()


# ═══════════════════════════════════════════════════════════
#  Blueprint registration
# ═══════════════════════════════════════════════════════════

from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.data import data_bp
from routes.invoice import invoice_bp
from routes.partners import bp as partners_bp
from routes.procurement import procurement_bp
from routes.profile import profile_bp
from routes.settings import settings_bp
from routes.transactions import tx_bp

# Auth routes at root level (login, register, verify, etc.)
app.register_blueprint(auth_bp)
# All other routes under /api
app.register_blueprint(admin_bp, url_prefix='/api')
app.register_blueprint(data_bp, url_prefix='/api')
app.register_blueprint(invoice_bp, url_prefix='/api')
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


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8600, debug=False)