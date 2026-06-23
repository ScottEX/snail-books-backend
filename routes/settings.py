"""Settings routes — background, lang, theme, stats, summary, chart, OTA."""

import os
import time
import io
import zipfile
from datetime import date, timedelta

from flask import Blueprint, request, jsonify, g, send_file

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t
from shared.config import BG_DIR
from shared.version import get_frontend_version
from shared.money import fmt_money, to_decimal

settings_bp = Blueprint('settings', __name__)

# ── Constants ──
ALLOWED_BG_EXT = {'jpg', 'jpeg', 'png', 'webp'}
MAX_BG_SIZE = 5 * 1024 * 1024  # 5MB
FRONTEND_VERSION = get_frontend_version()


# ═══════════════════════════════════════════════════════════════════════
#  Background
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/settings/background', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def background():
    if request.method == 'GET':
        url = None
        save_path = os.path.join(BG_DIR, f'home-bg-{g.user_id}.jpg')
        if os.path.exists(save_path):
            url = f'/user-images/home-bg-{g.user_id}.jpg?t={int(os.path.getmtime(save_path))}'
        opacity = 0.55
        with get_db() as db:
            row = db.execute(
                "SELECT value FROM user_settings WHERE user_id=? AND key='background_opacity'",
                (g.user_id,),
            ).fetchone()
            if row and row['value'] is not None:
                try:
                    opacity = float(row['value'])
                except Exception:
                    pass
        return jsonify({'url': url, 'opacity': opacity})

    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'status': 'error', 'message': '未选择文件'}), 400
        f = request.files['file']
        if f.filename == '':
            return jsonify({'status': 'error', 'message': '文件名为空'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in ALLOWED_BG_EXT:
            return jsonify(
                {'status': 'error', 'message': f'仅支持 {", ".join(ALLOWED_BG_EXT)} 格式'}
            ), 400
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
                db.execute(
                    "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'background_opacity', ?)",
                    (g.user_id, str(data['opacity'])),
                )
                db.commit()
        return jsonify({'status': 'ok'})

    if request.method == 'DELETE':
        # Reset to default — delete the user's custom background file.
        # The frontend then reverts to /img/bg.jpg and dispatches a
        # 'bg-changed' event so HomeScreen refreshes immediately.
        save_path = os.path.join(BG_DIR, f'home-bg-{g.user_id}.jpg')
        if os.path.exists(save_path):
            os.remove(save_path)
        return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════════
#  Language
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/settings/lang', methods=['GET'])
@login_required
def get_lang():
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM user_settings WHERE user_id=? AND key='lang'",
            (g.user_id,),
        ).fetchone()
    lang = row['value'] if row else 'zh-CN'
    return jsonify({'lang': lang})


@settings_bp.route('/settings/lang', methods=['PUT'])
@login_required
def save_lang():
    data = request.get_json()
    if not data or 'lang' not in data:
        return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields='lang')}), 400
    lang = data['lang']
    if lang not in ('zh-CN', 'zh-TW', 'en'):
        return jsonify({'status': 'error', 'message': t('err_invalid_lang', g.lang) or 'Invalid language'}), 400
    with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'lang', ?)",
                (g.user_id, lang),
            )
            db.commit()
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════════
#  Theme
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/settings/theme', methods=['GET'])
@login_required
def get_theme():
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM user_settings WHERE user_id=? AND key='theme'",
            (g.user_id,),
        ).fetchone()
    theme = row['value'] if row else 'burgundy-warm'
    return jsonify({'theme': theme})


@settings_bp.route('/settings/theme', methods=['PUT'])
@login_required
def save_theme():
    data = request.get_json()
    if not data or 'theme' not in data:
        return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields='theme')}), 400
    theme = data['theme']
    if theme not in ('burgundy-warm', 'obsidian-gold', 'deep-teal'):
        return jsonify({'status': 'error', 'message': t('err_invalid_theme', g.lang) or 'Invalid theme'}), 400
    with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, 'theme', ?)",
                (g.user_id, theme),
            )
            db.commit()
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════════════════
#  Stats
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/stats')
@login_required
def stats():
    with get_db() as db:
        row = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) AS income,"
            "       COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) AS expense,"
            "       COUNT(*) AS count FROM transactions"
        ).fetchone()
    return jsonify({'income': fmt_money(row['income']), 'expense': fmt_money(row['expense']), 'count': row['count']})


# ═══════════════════════════════════════════════════════════════════════
#  Summary (today + month)
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/summary')
@login_required
def summary():
    today_str = date.today().isoformat()
    month_str = date.today().strftime('%Y-%m')
    with get_db() as db:
        # Today — use business date, not created_at (P1-TTT)
        today_income = to_decimal(db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income' AND date=?",
            (today_str,),
        ).fetchone()[0])
        today_expense = to_decimal(db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='expense' AND date=?",
            (today_str,),
        ).fetchone()[0])
        # Month — use business date, not created_at (P1-TTT)
        month_income = to_decimal(db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='income' AND strftime('%Y-%m', date)=?",
            (month_str,),
        ).fetchone()[0])
        month_expense = to_decimal(db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='expense' AND strftime('%Y-%m', date)=?",
            (month_str,),
        ).fetchone()[0])
        month_procurement = to_decimal(db.execute(
            "SELECT COALESCE(SUM(total),0) FROM procurement_batches WHERE strftime('%Y-%m', date)=?",
            (month_str,),
        ).fetchone()[0])
    return jsonify({
        'today': {
            'income': fmt_money(today_income),
            'expense': fmt_money(today_expense),
            'profit': fmt_money(today_income - today_expense),
        },
        'month': {
            'income': fmt_money(month_income),
            'expense': fmt_money(month_expense),
            'profit': fmt_money(month_income - month_expense),
            'procurement': fmt_money(month_procurement),
        },
    })


# ═══════════════════════════════════════════════════════════════════════
#  Procurement Stats
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/procurement-stats')
@login_required
def procurement_stats():
    with get_db() as db:
        total_spent = to_decimal(db.execute(
            "SELECT COALESCE(SUM(total),0) FROM procurement_batches"
        ).fetchone()[0])
        total_income = to_decimal(db.execute(
            "SELECT COALESCE(SUM(revenue), 0) + COALESCE(SUM(jd_revenue), 0) FROM daily_revenue"
        ).fetchone()[0])
        batch_count = db.execute("SELECT COUNT(*) FROM procurement_batches").fetchone()[0]
        margin_pct = float((total_income - total_spent) / total_income * 100) if total_income > 0 else 0.0
    return jsonify({
        'total_spent': fmt_money(total_spent),
        'total_income': fmt_money(total_income),
        'batch_count': batch_count,
        'margin_pct': margin_pct,
    })


# ═══════════════════════════════════════════════════════════════════════
#  Chart — 12-month income/expense trend
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/chart')
@login_required
def chart():
    with get_db() as db:
        rows = db.execute("""
            SELECT strftime('%Y-%m', date) as month,
                   COALESCE(SUM(CASE WHEN type='income' THEN amount ELSE 0 END),0) as income,
                   COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE 0 END),0) as expense
            FROM transactions
            WHERE date >= date('now', '-12 months')
            GROUP BY month ORDER BY month
        """).fetchall()
    return jsonify([{'month': r['month'], 'income': fmt_money(r['income']), 'expense': fmt_money(r['expense'])} for r in rows])


# ═══════════════════════════════════════════════════════════════════════
#  Chart — monthly aggregated (income from daily_revenue, expense from transactions)
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/chart/monthly')
@login_required
def chart_monthly():
    with get_db() as db:
        # Monthly income from daily_revenue (revenue + jd_revenue)
        income_rows = db.execute("""
            SELECT strftime('%Y-%m', date) as month,
                   COALESCE(SUM(revenue), 0) + COALESCE(SUM(jd_revenue), 0) as income
            FROM daily_revenue
            WHERE date >= date('now', '-12 months')
            GROUP BY month ORDER BY month
        """).fetchall()

        # Monthly expense from transactions (by expense date, not creation time)
        expense_rows = db.execute("""
            SELECT strftime('%Y-%m', date) as month,
                   COALESCE(SUM(amount), 0) as expense
            FROM transactions
            WHERE type='expense' AND date >= date('now', '-12 months')
            GROUP BY month ORDER BY month
        """).fetchall()

        # Current month expense category breakdown (by expense date)
        month_str = date.today().strftime('%Y-%m')
        cat_rows = db.execute("""
            SELECT category, COALESCE(SUM(amount), 0) as total
            FROM transactions
            WHERE type='expense' AND strftime('%Y-%m', date)=?
            GROUP BY category ORDER BY total DESC
        """, (month_str,)).fetchall()

        # Daily profit (last 12 days)
        today_str = date.today().strftime('%Y-%m-%d')
        daily_income_rows = db.execute("""
            SELECT d.date,
                   COALESCE(SUM(d.revenue), 0) + COALESCE(SUM(d.jd_revenue), 0) as income
            FROM daily_revenue d
            WHERE d.date >= date('now', '-11 days')
            GROUP BY d.date
        """).fetchall()
        daily_expense_rows = db.execute("""
            SELECT t.date,
                   COALESCE(SUM(t.amount), 0) as expense
            FROM transactions t
            WHERE t.type='expense' AND t.date >= date('now', '-11 days')
            GROUP BY t.date
        """).fetchall()

    # Build 12-day date list
    daily_dates: list[str] = []
    for i in range(11, -1, -1):
        d = date.today() - timedelta(days=i)
        daily_dates.append(d.strftime('%Y-%m-%d'))

    income_dict = {r['date']: fmt_money(r['income']) for r in daily_income_rows}
    expense_dict = {r['date']: fmt_money(r['expense']) for r in daily_expense_rows}
    daily_profit = [fmt_money(income_dict.get(d, 0) - expense_dict.get(d, 0)) for d in daily_dates]
    daily_income_list = [income_dict.get(d, 0) for d in daily_dates]
    daily_expense_list = [expense_dict.get(d, 0) for d in daily_dates]

    # Build 12-month label list (oldest first)
    today = date.today()
    months = []
    y, m = today.year, today.month
    for i in range(11, -1, -1):
        mm = m - i
        yy = y
        while mm <= 0:
            mm += 12
            yy -= 1
        months.append(f'{yy}-{mm:02d}')

    income_dict = {r['month']: fmt_money(r['income']) for r in income_rows}
    expense_dict = {r['month']: fmt_money(r['expense']) for r in expense_rows}

    income_list = [income_dict.get(m, 0) for m in months]
    expense_list = [expense_dict.get(m, 0) for m in months]
    profit_list = [fmt_money(income_list[i] - expense_list[i]) for i in range(len(months))]

    return jsonify({
        'months': months,
        'income': income_list,
        'expense': expense_list,
        'profit': profit_list,
        'categories': {r['category']: fmt_money(r['total']) for r in cat_rows},
        'daily_dates': daily_dates,
        'daily_profit': daily_profit,
        'daily_income': daily_income_list,
        'daily_expense': daily_expense_list,
    })


# ═══════════════════════════════════════════════════════════════════════
#  OTA — public (no auth required)
# ═══════════════════════════════════════════════════════════════════════

@settings_bp.route('/frontend-version')
def frontend_version():
    return jsonify({'version': FRONTEND_VERSION})


@settings_bp.route('/frontend.zip')
@login_required
def frontend_zip():
    www = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        '..', 'snail-books-ios', 'www',
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(www):
            for fn in files:
                full = os.path.join(root, fn)
                arcname = os.path.relpath(full, www)
                zf.write(full, arcname)
    buf.seek(0)
    return send_file(
        buf,
        mimetype='application/zip',
        as_attachment=True,
        download_name='frontend.zip',
    )
