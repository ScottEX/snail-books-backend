"""Transaction routes — CRUD for income/expense records with pagination."""

import json
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, g
from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t
from shared.validation import validate_required
from shared.config import EXPENSE_IMG_DIR

tx_bp = Blueprint('transactions', __name__)


@tx_bp.route('/transactions', methods=['GET', 'POST'])
@login_required
def transactions():
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'type', 'amount', 'category', 'account')
        if missing:
            return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            if data['type'] not in ('income', 'expense'):
                return jsonify({'status': 'error', 'message': t('err_invalid_type', g.lang)}), 400
            if data['category'] not in ('daily', 'rent', 'salary', 'goods'):
                return jsonify({'status': 'error', 'message': t('err_invalid_category', g.lang)}), 400
            if data['account'] not in ('payCash', 'payWechat', 'payAlipay'):
                return jsonify({'status': 'error', 'message': t('err_invalid_account', g.lang)}), 400
            db.execute(
                'INSERT INTO transactions (type,amount,category,account,note,images,thumb_images,date,user_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (data['type'], data['amount'], data['category'], data['account'],
                 data.get('note', ''),
                 json.dumps(data.get('images', [])),
                 json.dumps(data.get('thumb_images', [])),
                 data.get('date', ''),
                 g.user_id,
                 datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
            db.commit()
        return jsonify({'status': 'ok'})

    # GET with pagination
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    tx_type = request.args.get('type')
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    category = request.args.get('category')

    where = []
    params = []
    if tx_type:
        where.append('t.type=?')
        params.append(tx_type)
    if date_from:
        where.append('t.date >= ?')
        params.append(date_from)
    if date_to:
        where.append('t.date <= ?')
        params.append(date_to)
    if category:
        cats = [c.strip() for c in category.split(',') if c.strip()]
        if cats:
            placeholders = ','.join(['?' for _ in cats])
            where.append(f't.category IN ({placeholders})')
            params.extend(cats)

    where_sql = ' AND '.join(where) if where else '1=1'

    with get_db() as db:
        count = db.execute(f'SELECT COUNT(*) FROM transactions t WHERE {where_sql}', params).fetchone()[0]
        total_all = db.execute("SELECT COUNT(*) FROM transactions WHERE type='expense'").fetchone()[0]
        pages = max(1, (count + per_page - 1) // per_page)
        offset = (page - 1) * per_page
        rows = db.execute(
            f'SELECT t.*, pb.batch_number AS proc_batch_number, '
            f'pb.settled_at AS proc_settled_at, pb.settled_by AS proc_settled_by, '
            f'su.username AS proc_settled_by_username, ir.status AS invoice_status FROM transactions t '
            f'LEFT JOIN procurement_batches pb ON t.procurement_batch_id = pb.id '
            f'LEFT JOIN users su ON pb.settled_by = su.id '
            f'LEFT JOIN invoice_records ir ON ir.procurement_batch_id = pb.id '
            f'WHERE {where_sql} ORDER BY t.date DESC, t.created_at DESC, t.id DESC LIMIT ? OFFSET ?',
            params + [per_page, offset]
        ).fetchall()
    return jsonify({
        'transactions': [dict(r) for r in rows],
        'page': page, 'pages': pages, 'total': count, 'per_page': per_page,
        'total_all': total_all,
    })


@tx_bp.route('/transactions/<int:id>', methods=['DELETE', 'PUT'])
@login_required
def transaction_by_id(id):
    if request.method == 'PUT':
        data = request.get_json()

        # Validate required fields
        amount = data.get('amount')
        if amount is not None:
            try:
                amount = float(amount)
                if amount == 0:
                    return jsonify({'status': 'error', 'message': t('err_amount_positive', g.lang)}), 400
            except (TypeError, ValueError):
                return jsonify({'status': 'error', 'message': t('err_amount_invalid', g.lang)}), 400

        category = data.get('category')
        if category is not None and category not in ('daily', 'rent', 'salary', 'goods'):
            return jsonify({'status': 'error', 'message': t('err_invalid_category', g.lang)}), 400

        account = data.get('account')
        if account is not None and account not in ('payCash', 'payWechat', 'payAlipay'):
            return jsonify({'status': 'error', 'message': t('err_invalid_account', g.lang)}), 400

        date = data.get('date')
        if date is not None and not date:
            return jsonify({'status': 'error', 'message': t('err_missing_date', g.lang)}), 400

        with get_db() as db:
            existing = db.execute('SELECT * FROM transactions WHERE id=?', (id,)).fetchone()
            if not existing:
                return jsonify({'status': 'error', 'message': t('err_not_found', g.lang)}), 404

            # Snapshot old image URLs for orphan file cleanup
            old_urls = set()
            for col in ('images', 'thumb_images'):
                raw = existing[col]
                if raw:
                    try:
                        arr = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(arr, list):
                            old_urls.update(arr)
                    except (json.JSONDecodeError, TypeError):
                        pass

            fields = []
            values = []
            for key in ('amount', 'category', 'account', 'note', 'date', 'images', 'thumb_images'):
                if key in data:
                    if key in ('images', 'thumb_images'):
                        fields.append(f'{key}=?')
                        values.append(json.dumps(data[key]))
                    else:
                        fields.append(f'{key}=?')
                        values.append(data[key])
            if not fields:
                return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields='fields')}), 400
            values.append(id)
            db.execute(f'UPDATE transactions SET {", ".join(fields)} WHERE id=?', values)

            # Sync images to linked procurement batch (if images changed)
            if 'images' in data or 'thumb_images' in data:
                # Build update values for procurement batch
                pb_images = data.get('images', json.loads(existing['images']) if existing['images'] else [])
                pb_thumbs = data.get('thumb_images', json.loads(existing['thumb_images']) if existing['thumb_images'] else [])
                pb_values = (
                    json.dumps(pb_images) if isinstance(pb_images, list) else pb_images,
                    json.dumps(pb_thumbs) if isinstance(pb_thumbs, list) else pb_thumbs,
                )

                # Prefer procurement_batch_id for reliable linkage (P1-Z)
                pb_id = existing['procurement_batch_id']
                if pb_id:
                    db.execute(
                        "UPDATE procurement_batches SET images=?, thumb_images=? WHERE id=?",
                        pb_values + (pb_id,)
                    )
                else:
                    # Fallback: match by OLD category/date/total/payment_method
                    old_cat = existing['category'] or ''
                    old_date = existing['date'] or ''
                    old_amount = existing['amount']
                    old_account = existing['account'] or ''
                    db.execute(
                        "UPDATE procurement_batches SET images=?, thumb_images=? WHERE category=? AND date=? AND total=? AND payment_method=?",
                        pb_values + (old_cat, old_date, old_amount, old_account)
                    )

            db.commit()

            # Before deleting orphan files, collect all URLs still referenced by procurement_batches
            proc_urls = set()
            pb_rows = db.execute('SELECT images, thumb_images FROM procurement_batches').fetchall()
            for pb in pb_rows:
                for col_name in ('images', 'thumb_images'):
                    raw = pb[col_name]
                    if raw:
                        try:
                            arr = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(arr, list):
                                proc_urls.update(arr)
                        except (json.JSONDecodeError, TypeError):
                            pass

            # Delete orphan image files no longer referenced
            new_urls = set()
            for key in ('images', 'thumb_images'):
                if key in data and isinstance(data[key], list):
                    new_urls.update(data[key])
            for url in (old_urls - new_urls):
                if url.startswith('/expense-imgs/'):
                    # Only delete if not referenced by any procurement batch
                    if url in proc_urls:
                        continue
                    rel = url[len('/expense-imgs/'):]
                    fp = os.path.normpath(os.path.join(EXPENSE_IMG_DIR, rel))
                    if fp.startswith(EXPENSE_IMG_DIR) and os.path.isfile(fp):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass

            updated = db.execute('SELECT * FROM transactions WHERE id=?', (id,)).fetchone()
        return jsonify({'status': 'ok', 'transaction': dict(updated)})

    # DELETE
    with get_db() as db:
        row = db.execute('SELECT images, thumb_images FROM transactions WHERE id=?', (id,)).fetchone()
        if row:
            # Collect all URLs still referenced by procurement_batches before cleanup
            proc_urls = set()
            pb_rows = db.execute('SELECT images, thumb_images FROM procurement_batches').fetchall()
            for pb in pb_rows:
                for col_name in ('images', 'thumb_images'):
                    raw = pb[col_name]
                    if raw:
                        try:
                            arr = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(arr, list):
                                proc_urls.update(arr)
                        except (json.JSONDecodeError, TypeError):
                            pass

            # Clean up orphan image files
            for col in ('images', 'thumb_images'):
                raw = row[col]
                if raw:
                    try:
                        urls = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(urls, list):
                            for url in urls:
                                if url.startswith('/expense-imgs/'):
                                    # Only delete if not referenced by any procurement batch
                                    if url in proc_urls:
                                        continue
                                    rel = url[len('/expense-imgs/'):]
                                    fp = os.path.normpath(os.path.join(EXPENSE_IMG_DIR, rel))
                                    if fp.startswith(EXPENSE_IMG_DIR) and os.path.isfile(fp):
                                        try:
                                            os.remove(fp)
                                        except OSError:
                                            pass
                    except (json.JSONDecodeError, TypeError):
                        pass
        db.execute('DELETE FROM transactions WHERE id=?', (id,))
        db.commit()
    return jsonify({'status': 'ok'})
