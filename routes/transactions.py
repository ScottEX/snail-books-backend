"""Transaction routes — CRUD for income/expense records with pagination."""

import json
from flask import Blueprint, request, jsonify, g
from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t
from shared.validation import validate_required

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
            db.execute(
                'INSERT INTO transactions (type,amount,category,account,note,images,thumb_images,user_id) VALUES (?,?,?,?,?,?,?,?)',
                (data['type'], data['amount'], data['category'], data['account'],
                 data.get('note', ''),
                 json.dumps(data.get('images', [])),
                 json.dumps(data.get('thumb_images', [])),
                 g.user_id)
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
        where.append('type=?')
        params.append(tx_type)
    if date_from:
        where.append('date(t.created_at) >= ?')
        params.append(date_from)
    if date_to:
        where.append('date(t.created_at) <= ?')
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
            f'SELECT t.*, pb.batch_number AS proc_batch_number FROM transactions t '
            f'LEFT JOIN procurement_batches pb ON t.procurement_batch_id = pb.id '
            f'WHERE {where_sql} ORDER BY t.created_at DESC LIMIT ? OFFSET ?',
            params + [per_page, offset]
        ).fetchall()
    return jsonify({
        'transactions': [dict(r) for r in rows],
        'page': page, 'pages': pages, 'total': count, 'per_page': per_page,
        'total_all': total_all,
    })


@tx_bp.route('/transactions/<int:id>', methods=['DELETE'])
@login_required
def delete_transaction(id):
    with get_db() as db:
        db.execute('DELETE FROM transactions WHERE id=?', (id,))
        db.commit()
    return jsonify({'status': 'ok'})
