"""Data routes — reconciliations, platform fees, daily revenue, business summary."""

import json, os, time, re
from datetime import datetime, timedelta, date
from flask import Blueprint, request, jsonify, session, g
import sqlite3

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t
from shared.validation import validate_required
from shared.config import ADMIN_USER_ID

data_bp = Blueprint('data', __name__)


# ── Server time (Beijing) ──
@data_bp.route('/server-date', methods=['GET'])
def server_date():
    """Return current Beijing date. No login required."""
    today = date.today()
    return jsonify({'date': today.isoformat()})


# ═══════════════════════════════════════════
# Reconciliations
# ═══════════════════════════════════════════

@data_bp.route('/migrate-recon', methods=['POST'])
@login_required
def migrate_recon():
    """One-time migration: remove UNIQUE(date) constraint."""
    with get_db() as db:
        try:
            db.execute('PRAGMA foreign_keys = OFF')
            indexes = db.execute("PRAGMA index_list('reconciliations')").fetchall()
            has_unique = any(r[1].startswith('sqlite_autoindex') for r in indexes)
            if not has_unique:
                return jsonify({'message': 'Already migrated, no UNIQUE found'})

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
            return jsonify({'error': 'Migration failed'}), 500


@data_bp.route('/reconciliations/clear', methods=['POST'])
@login_required
def clear_reconciliations():
    data = request.get_json(silent=True) or {}
    if data.get('confirm') != 'YES':
        return jsonify({'ok': False, 'message': t('err_recon_confirm', g.lang)}), 400
    if str(session.get('user_id', '')) != ADMIN_USER_ID:
        return jsonify({'status': 'error', 'message': '仅管理员可操作'}), 403
    with get_db() as db:
        db.execute('DELETE FROM reconciliations')
        db.commit()
        from shared.audit import audit
        audit('CLEAR_RECONCILIATIONS')
    return jsonify({'ok': True, 'message': t('msg_recon_cleared', g.lang)})


@data_bp.route('/reconciliations', methods=['POST'])
@login_required
def create_reconciliation():
    data = request.get_json() or {}
    # date is now server-submission time, not user-provided
    dt = date.today().isoformat()

    bill_date = data.get('bill_date', dt)
    if bill_date:
        try:
            datetime.strptime(bill_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': t('err_bill_date_format', g.lang)}), 400

    reconciled_by = data.get('reconciled_by', g.username)
    if not reconciled_by and g.user_id:
        with get_db() as db:
            user = db.execute('SELECT username FROM users WHERE id=?', (g.user_id,)).fetchone()
            reconciled_by = user['username'] if user else str(g.user_id)
    if 'reconciled_by' in data and not re.match(r'^[\w\u4e00-\u9fa5@.\-]{1,32}$', reconciled_by):
        return jsonify({'error': t('err_invalid_reconciled_by', g.lang)}), 400

    balances = {}
    for field in ['card_balance', 'cash_balance', 'dine_in', 'meituan', 'flash_sale', 'jd', 'tuan']:
        raw = data.get(field)
        try:
            v = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return jsonify({'error': t('err_field_not_number', g.lang, field=field)}), 400
        if v < 0:
            return jsonify({'error': t('err_field_negative', g.lang, field=field)}), 400
        if abs(v) > 1e10:
            return jsonify({'error': t('err_field_too_large', g.lang, field=field)}), 400
        balances[field] = v

    card_balance = balances['card_balance']
    cash_balance = balances['cash_balance']
    channel_total = round(sum(balances[k] for k in ['dine_in', 'meituan', 'flash_sale', 'jd', 'tuan']), 2)
    real_total = round(card_balance + cash_balance, 2)
    diff = round(real_total - channel_total, 2)

    with get_db() as db:
        existing = db.execute('SELECT id FROM reconciliations WHERE bill_date=?', (bill_date,)).fetchone()
        if existing:
            db.execute('''UPDATE reconciliations SET
                date=?, card_balance=?, cash_balance=?, dine_in=?, meituan=?, flash_sale=?,
                jd=?, tuan=?, channel_total=?, real_total=?, diff=?, reconciled_by=?
                WHERE id=?''',
                       (dt, card_balance, cash_balance, balances['dine_in'], balances['meituan'],
                        balances['flash_sale'], balances['jd'], balances['tuan'],
                        channel_total, real_total, diff, reconciled_by, existing['id']))
            db.commit()
            from shared.audit import audit
            audit('CREATE_RECONCILIATION', extra=f'date={bill_date} ¥{real_total}')
            return jsonify({'ok': True, 'action': 'updated', 'id': existing['id']}), 200
        else:
            db.execute('''INSERT INTO reconciliations
                (date, bill_date, card_balance, cash_balance, dine_in, meituan, flash_sale, jd, tuan,
                 channel_total, real_total, diff, reconciled_by, user_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (dt, bill_date, card_balance, cash_balance, balances['dine_in'], balances['meituan'],
                        balances['flash_sale'], balances['jd'], balances['tuan'],
                        channel_total, real_total, diff, reconciled_by, g.user_id,
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            db.commit()
            new_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            from shared.audit import audit
            audit('CREATE_RECONCILIATION', extra=f'date={bill_date} ¥{real_total}')
            return jsonify({'ok': True, 'action': 'created', 'id': new_id}), 201


@data_bp.route('/reconciliations', methods=['GET'])
@login_required
def get_reconciliations():
    page = request.args.get('page', 0, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(1, min(per_page, 100))
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
        where += ' AND bill_date >= ?'; params.append(bill_date_from)
    if bill_date_to:
        where += ' AND bill_date <= ?'; params.append(bill_date_to)
    if date_from:
        where += ' AND date >= ?'; params.append(date_from)
    if date_to:
        where += ' AND date <= ?'; params.append(date_to)
    if reconciled_by:
        where += ' AND reconciled_by = ?'; params.append(reconciled_by)

    with get_db() as db:
        if page > 0:
            count = db.execute(f'SELECT COUNT(*) FROM reconciliations {where}', params).fetchone()[0]
            total_all = db.execute('SELECT COUNT(*) FROM reconciliations').fetchone()[0]
            pages = max(1, (count + per_page - 1) // per_page)
            offset = (page - 1) * per_page
            rows = db.execute(
                f'SELECT * FROM reconciliations {where} ORDER BY date DESC, bill_date DESC LIMIT ? OFFSET ?',
                params + [per_page, offset]
            ).fetchall()
            return jsonify({
                'records': [dict(r) for r in rows],
                'page': page, 'pages': pages, 'total': count, 'per_page': per_page,
                'total_all': total_all,
            })
        else:
            if limit <= 0:
                rows = db.execute(
                    f'SELECT * FROM reconciliations {where} ORDER BY date DESC, bill_date DESC', params
                ).fetchall()
            else:
                rows = db.execute(
                    f'SELECT * FROM reconciliations {where} ORDER BY date DESC, bill_date DESC LIMIT ?',
                    params + [limit]
                ).fetchall()
            return jsonify([dict(r) for r in rows])


# ═══════════════════════════════════════════
# Platform Fees
# ═══════════════════════════════════════════

@data_bp.route('/platform-fees', methods=['GET'])
@login_required
def get_platform_fees():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    with get_db() as db:
        if year and month:
            row = db.execute('SELECT * FROM platform_fees WHERE year=? AND month=?', (year, month)).fetchone()
            return jsonify(dict(row) if row else {})
        rows = db.execute('SELECT * FROM platform_fees ORDER BY year DESC, month DESC').fetchall()
        return jsonify([dict(r) for r in rows])


@data_bp.route('/platform-fees/entry', methods=['POST'])
@login_required
def add_platform_fee_entry():
    data = request.get_json()
    missing = validate_required(data, 'year', 'month', 'entry_date')
    if missing:
        return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    year = data.get('year')
    month = data.get('month')
    entry_date = data.get('entry_date')
    mc = data.get('meituan_cashier', 0)
    mw = data.get('meituan_waimai', 0)
    sw = data.get('shangou_waimai', 0)
    mt = data.get('meituan_tuan', 0)
    with get_db() as db:
        db.execute('''INSERT INTO platform_fees (year, month, meituan_cashier, meituan_waimai, shangou_waimai, meituan_tuan)
                      VALUES (?,?,?,?,?,?)
                      ON CONFLICT(year, month) DO UPDATE SET
                      meituan_cashier=meituan_cashier+excluded.meituan_cashier,
                      meituan_waimai=meituan_waimai+excluded.meituan_waimai,
                      shangou_waimai=shangou_waimai+excluded.shangou_waimai,
                      meituan_tuan=meituan_tuan+excluded.meituan_tuan''',
                   (year, month, mc, mw, sw, mt))
        fee_id = db.execute('SELECT id FROM platform_fees WHERE year=? AND month=?', (year, month)).fetchone()['id']
        db.execute('''INSERT INTO platform_fee_entries (fee_id, entry_date, meituan_cashier, meituan_waimai, shangou_waimai, meituan_tuan)
                      VALUES (?,?,?,?,?,?)''',
                   (fee_id, entry_date, mc, mw, sw, mt))
        updated = db.execute('SELECT * FROM platform_fees WHERE year=? AND month=?', (year, month)).fetchone()
        from shared.audit import audit
        audit('CREATE_PLATFORM_FEE', extra=f'{year}/{month} entry={entry_date}')
        return jsonify({'status': 'ok', 'data': dict(updated)})


@data_bp.route('/platform-fees/<int:id>', methods=['PUT'])
@login_required
def update_platform_fee(id):
    data = request.get_json()
    if not data:
        return jsonify({'status': 'error', 'message': t('err_empty_fields', g.lang)}), 400
    with get_db() as db:
        db.execute('''UPDATE platform_fees SET meituan_cashier=?, meituan_waimai=?, shangou_waimai=?, meituan_tuan=?
                      WHERE id=?''',
                   (data.get('meituan_cashier', 0), data.get('meituan_waimai', 0),
                    data.get('shangou_waimai', 0), data.get('meituan_tuan', 0), id))
        from shared.audit import audit
        audit('UPDATE_PLATFORM_FEE', extra=f'id={id}')
        return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════
# Daily Revenue
# ═══════════════════════════════════════════

@data_bp.route('/daily-revenue', methods=['GET'])
@login_required
def get_daily_revenue():
    year = request.args.get('year', type=int)
    month = request.args.get('month', type=int)
    dt = request.args.get('date', type=str)
    days = request.args.get('days', type=int)
    date_from = request.args.get('date_from', type=str)
    date_to = request.args.get('date_to', type=str)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)

    with get_db() as db:
        if days:
            rows = db.execute('''SELECT date, revenue, turnover, jd_revenue
                FROM daily_revenue WHERE date >= date('now', ?) ORDER BY date DESC''',
                              (f'-{days} days',)).fetchall()
            totals = {'revenue': sum(r['revenue'] or 0 for r in rows),
                      'turnover': sum(r['turnover'] or 0 for r in rows),
                      'jd_revenue': sum(r['jd_revenue'] or 0 for r in rows)}
            return jsonify({'records': [], 'total': len(rows), 'pages': 1, 'page': 1, 'per_page': per_page, 'totals': totals})

        where_parts, params = [], []
        if dt:
            where_parts.append('dr.date=?'); params.append(dt)
        else:
            if year and month:
                where_parts.append("substr(dr.date,1,7)=?"); params.append(f'{year}-{month:02d}')
            elif year:
                where_parts.append("substr(dr.date,1,4)=?"); params.append(str(year))
        if date_from:
            where_parts.append('dr.date >= ?'); params.append(date_from)
        if date_to:
            where_parts.append('dr.date <= ?'); params.append(date_to)

        where = ('WHERE ' + ' AND '.join(where_parts)) if where_parts else ''
        base = f'SELECT dr.*, u.username as recorded_by FROM daily_revenue dr LEFT JOIN users u ON dr.user_id = u.id {where}'
        count = db.execute(f'SELECT COUNT(*) FROM daily_revenue dr {where}', params).fetchone()[0]
        total_all = db.execute('SELECT COUNT(*) FROM daily_revenue').fetchone()[0]
        total_pages = max(1, (count + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.execute(base + ' ORDER BY dr.date DESC LIMIT ? OFFSET ?', params + [per_page, offset]).fetchall()
        return jsonify({'records': [dict(r) for r in rows], 'total': count, 'pages': total_pages, 'page': page,
                        'per_page': per_page, 'total_all': total_all})


@data_bp.route('/daily-revenue/last-7')
@login_required
def last_7_days():
    today = datetime.now().date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(7)]
    with get_db() as db:
        rows = db.execute('''SELECT dr.*, u.username as recorded_by
            FROM daily_revenue dr LEFT JOIN users u ON dr.user_id = u.id
            WHERE dr.date IN (''' + ','.join('?' * len(dates)) + ')', dates).fetchall()
        by_date = {r['date']: dict(r) for r in rows}
        result = []
        for d in dates:
            if d in by_date:
                result.append(by_date[d])
            else:
                result.append({'date': d, 'revenue': 0, 'turnover': 0, 'jd_revenue': 0,
                               'note': '', 'recorded_by': None, 'archived': 0, 'status': '未录入'})
        return jsonify({'records': result})


@data_bp.route('/daily-revenue/total')
@login_required
def daily_revenue_total():
    with get_db() as db:
        row = db.execute(
            'SELECT COALESCE(SUM(revenue),0) as total_revenue, COALESCE(SUM(turnover),0) as total_turnover,'
            ' COALESCE(SUM(jd_revenue),0) as total_jd FROM daily_revenue'
        ).fetchone()
        return jsonify(dict(row))


@data_bp.route('/business-summary')
@login_required
def business_summary():
    with get_db() as db:
        rev = db.execute(
            'SELECT COALESCE(SUM(revenue),0) as total_revenue, COALESCE(SUM(turnover),0) as receivable,'
            ' COALESCE(SUM(jd_revenue),0) as total_jd FROM daily_revenue'
        ).fetchone()
        actual_received = rev['total_revenue'] + rev['total_jd']
        receivable = rev['receivable']
        discount = receivable - actual_received

        pf = db.execute(
            'SELECT COALESCE(SUM(meituan_cashier),0) + COALESCE(SUM(meituan_waimai),0) +'
            ' COALESCE(SUM(shangou_waimai),0) + COALESCE(SUM(meituan_tuan),0) as total_pf FROM platform_fees'
        ).fetchone()
        platform_fees_total = pf['total_pf']
        cumulative_revenue = actual_received - platform_fees_total

        exp = db.execute("SELECT COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE -amount END),0) as total_exp FROM transactions WHERE type IN ('expense','income')").fetchone()
        cumulative_expense = exp['total_exp']

        # Category breakdown for glass card
        cat_rows = db.execute(
            "SELECT category, COALESCE(SUM(CASE WHEN type='expense' THEN amount ELSE -amount END),0) as total"
            " FROM transactions WHERE type IN ('expense','income') GROUP BY category"
        ).fetchall()
        expense_by_category = {r['category']: r['total'] for r in cat_rows}

        pinv = db.execute('SELECT COALESCE(SUM(investment),0) as total_inv FROM partners').fetchone()
        total_investment = pinv['total_inv']
        pdiv = db.execute('SELECT COALESCE(SUM(amount),0) as total_div FROM dividends').fetchone()
        total_dividends = pdiv['total_div']
        cash_on_hand = (total_investment + cumulative_revenue) - (cumulative_expense + total_dividends)

        return jsonify({
            'actual_received': actual_received, 'receivable': receivable, 'discount': discount,
            'cumulative_revenue': cumulative_revenue, 'cumulative_expense': cumulative_expense,
            'cash_on_hand': cash_on_hand, 'total_investment': total_investment, 'total_dividends': total_dividends,
            'expense_by_category': expense_by_category,
        })


@data_bp.route('/daily-revenue', methods=['POST'])
@login_required
def create_daily_revenue():
    data = request.get_json()
    missing = validate_required(data, 'date', 'turnover')
    if missing:
        return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    dt = data['date']
    revenue = float(data.get('revenue', 0))
    turnover = float(data['turnover'])
    jd_revenue = float(data.get('jd_revenue', 0))
    note = data.get('note', '')
    archived = int(data.get('archived', 0))
    with get_db() as db:
        try:
            db.execute(
                'INSERT INTO daily_revenue (date, revenue, turnover, jd_revenue, note, user_id, archived) VALUES (?,?,?,?,?,?,?)',
                (dt, revenue, turnover, jd_revenue, note, g.user_id, archived)
            )
            row = db.execute('''SELECT dr.*, u.username as recorded_by
                FROM daily_revenue dr LEFT JOIN users u ON dr.user_id = u.id WHERE dr.date=?''', (dt,)).fetchone()
            from shared.audit import audit
            audit('CREATE_DLY_REV', extra=f'{dt} ¥{turnover}')
            return jsonify({'status': 'ok', 'data': dict(row)})
        except sqlite3.IntegrityError:
            return jsonify({'status': 'error', 'message': '该日期已有营收记录'}), 409


@data_bp.route('/daily-revenue/<int:id>', methods=['PUT'])
@login_required
def update_daily_revenue(id):
    data = request.get_json()
    with get_db() as db:
        row = db.execute('SELECT * FROM daily_revenue WHERE id=?', (id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '记录不存在'}), 404
        fields, params = [], []
        for k in ['revenue', 'turnover', 'jd_revenue', 'note', 'archived']:
            if k in data:
                fields.append(f'{k}=?')
                params.append(float(data[k]) if k != 'note' else data[k])
        if not fields:
            return jsonify({'status': 'error', 'message': '无更新字段'}), 400
        params.append(id)
        db.execute("UPDATE daily_revenue SET " + ', '.join(fields) + " WHERE id=?", params)
        updated = db.execute('''SELECT dr.*, u.username as recorded_by
            FROM daily_revenue dr LEFT JOIN users u ON dr.user_id = u.id WHERE dr.id=?''', (id,)).fetchone()
        from shared.audit import audit
        audit('UPDATE_DLY_REV', extra=f'id={id}')
        return jsonify({'status': 'ok', 'data': dict(updated)})


@data_bp.route('/daily-revenue/<int:id>', methods=['DELETE'])
@login_required
def delete_daily_revenue(id):
    with get_db() as db:
        db.execute('DELETE FROM daily_revenue WHERE id=?', (id,))
        from shared.audit import audit
        audit('DELETE_DLY_REV', extra=f'id={id}')
        return jsonify({'status': 'ok'})
