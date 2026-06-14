"""Invoice records routes — CRUD for 开票记录.

Status (status): 'pending' (待开) | 'done' (已开) — 2-state model, no rejected/voided.
File upload: pdf/jpg/png, max 10MB, stored under uploads/invoice/<user_id>/<uuid>.<ext>.
"""

import os, json, uuid, mimetypes, re
from datetime import datetime
from flask import Blueprint, request, jsonify, g, make_response, send_file
from werkzeug.utils import secure_filename

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t as _t
from shared.validation import validate_required
from shared.config import INVOICE_FILE_DIR

invoice_bp = Blueprint('invoice', __name__)

ALLOWED_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
VALID_STATUS = {'pending', 'done'}
VALID_TYPE = {'vat', 'general'}


def _ensure_user_dir(user_id):
    """Create and return the user's invoice file dir."""
    user_dir = os.path.join(INVOICE_FILE_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def _row_to_dict(row):
    """Convert sqlite row to dict, decoding nothing fancy (no JSON columns here)."""
    return dict(row)


def _validate_date(date_str, lang):
    if not date_str or not DATE_RE.match(date_str):
        return _t('err_date_format', lang)
    return None


# ── List (with filter) ──
@invoice_bp.route('/invoice-records', methods=['GET'])
@login_required
def api_invoice_records_list():
    status = request.args.get('status')  # 'pending' | 'done' | None
    type_ = request.args.get('type')      # 'vat' | 'general' | None
    batch_id = request.args.get('procurement_batch_id', type=int)
    sql = 'SELECT * FROM invoice_records WHERE 1=1'
    params = []
    if status in VALID_STATUS:
        sql += ' AND status=?'
        params.append(status)
    if type_ in VALID_TYPE:
        sql += ' AND type=?'
        params.append(type_)
    if batch_id:
        sql += ' AND procurement_batch_id=?'
        params.append(batch_id)
    sql += ' ORDER BY date DESC, id DESC'
    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
    return jsonify([_row_to_dict(r) for r in rows])


# ── Detail ──
@invoice_bp.route('/invoice-records/<int:rid>', methods=['GET'])
@login_required
def api_invoice_record_detail(rid):
    with get_db() as db:
        row = db.execute('SELECT * FROM invoice_records WHERE id=?', (rid,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 404
    return jsonify(_row_to_dict(row))


# ── Create ──
@invoice_bp.route('/invoice-records', methods=['POST'])
@login_required
def api_invoice_record_create():
    data = request.get_json() or {}
    missing = validate_required(data, 'type', 'amount', 'date')
    if missing:
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    if data['type'] not in VALID_TYPE:
        return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 400
    if not data.get('company', '').strip():
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='company')}), 400
    if not isinstance(data['amount'], (int, float)) or data['amount'] <= 0:
        return jsonify({'status': 'error', 'message': _t('err_amount_positive', g.lang)}), 400
    err = _validate_date(data['date'], g.lang)
    if err:
        return jsonify({'status': 'error', 'message': err}), 400
    status = data.get('status', 'pending')
    if status not in VALID_STATUS:
        return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 400
    # 'done' requires invoice_number
    if status == 'done' and not (data.get('invoice_number') or '').strip():
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='invoice_number')}), 400
    user_id = g.user['id'] if hasattr(g, 'user') and g.user else None
    batch_id = data.get('procurement_batch_id')
    # One batch → one invoice record (enforce uniqueness when batch_id is set)
    if batch_id:
        with get_db() as db:
            existing = db.execute(
                'SELECT id FROM invoice_records WHERE procurement_batch_id=?', (batch_id,)
            ).fetchone()
            if existing:
                return jsonify({'status': 'error', 'message': '该批次已开过发票，不能重复申请'}), 409
    # 'done' status requires invoice_number + file (file uploaded via separate endpoint)
    if status == 'done' and not (data.get('invoice_number') or '').strip():
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='invoice_number')}), 400
    # Note: file is uploaded separately, so we can't check it here. Backend accepts 'done'
    # without file at create time (file will be uploaded immediately after by front-end).
    # The PUT endpoint enforces file requirement for 'done' status.
    with get_db() as db:
        cur = db.execute(
            'INSERT INTO invoice_records (user_id, procurement_batch_id, type, company, tax_id, amount, date, invoice_number, email, status, file_path, file_type, file_size) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (
                user_id,
                data.get('procurement_batch_id'),
                data['type'],
                data['company'].strip(),
                data.get('tax_id', '').strip(),
                float(data['amount']),
                data['date'],
                (data.get('invoice_number') or '').strip(),
                (data.get('email') or '').strip(),
                status,
                '',  # file_path populated by separate upload endpoint
                '',
                0,
            )
        )
        rid = cur.lastrowid
    return jsonify({'status': 'ok', 'id': rid})


# ── Update (covers pending→done transition + invoice_number补传) ──
@invoice_bp.route('/invoice-records/<int:rid>', methods=['PUT'])
@login_required
def api_invoice_record_update(rid):
    data = request.get_json() or {}
    with get_db() as db:
        row = db.execute('SELECT * FROM invoice_records WHERE id=?', (rid,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 404
        rec = dict(row)
        # Validate partial fields
        if 'type' in data and data['type'] not in VALID_TYPE:
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 400
        if 'status' in data and data['status'] not in VALID_STATUS:
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 400
        if 'amount' in data and (not isinstance(data['amount'], (int, float)) or data['amount'] <= 0):
            return jsonify({'status': 'error', 'message': _t('err_amount_positive', g.lang)}), 400
        if 'date' in data:
            err = _validate_date(data['date'], g.lang)
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
        # If transitioning to 'done' or already 'done', invoice_number required
        new_status = data.get('status', rec['status'])
        merged_invoice_no = data.get('invoice_number', rec['invoice_number'] or '').strip()
        if new_status == 'done' and not merged_invoice_no:
            return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='invoice_number')}), 400
        # If transitioning to 'done' or already 'done', file must exist
        merged_file = data.get('file_path', rec['file_path'] or '')
        if new_status == 'done' and not merged_file:
            return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='file')}), 400
        # Apply update — only known fields
        updatable = ['procurement_batch_id', 'type', 'company', 'tax_id', 'amount', 'date',
                     'invoice_number', 'email', 'status', 'file_path', 'file_type', 'file_size']
        sets, vals = [], []
        for k in updatable:
            if k in data:
                sets.append(f'{k}=?')
                vals.append(data[k])
        if not sets:
            return jsonify({'status': 'ok'})  # no-op
        sets.append('updated_at=CURRENT_TIMESTAMP')
        vals.append(rid)
        db.execute(f'UPDATE invoice_records SET {", ".join(sets)} WHERE id=?', vals)
    return jsonify({'status': 'ok'})


# ── Delete (physical) ──
@invoice_bp.route('/invoice-records/<int:rid>', methods=['DELETE'])
@login_required
def api_invoice_record_delete(rid):
    with get_db() as db:
        row = db.execute('SELECT * FROM invoice_records WHERE id=?', (rid,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 404
        # Also remove the file from disk if exists
        rec = dict(row)
        if rec.get('file_path'):
            try:
                full = os.path.join(INVOICE_FILE_DIR, rec['file_path'])
                if os.path.isfile(full):
                    os.remove(full)
            except OSError:
                pass  # don't fail delete on filesystem error
        db.execute('DELETE FROM invoice_records WHERE id=?', (rid,))
    return jsonify({'status': 'ok'})


# ── Upload file (PDF / JPG / PNG, max 10MB) ──
@invoice_bp.route('/invoice-records/<int:rid>/file', methods=['POST'])
@login_required
def api_invoice_record_upload(rid):
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='file')}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='file')}), 400
    # Validate ext
    safe_name = secure_filename(f.filename) or 'upload'
    ext = os.path.splitext(safe_name)[1].lower()
    if ext not in ALLOWED_EXT:
        return jsonify({'status': 'error', 'message': _t('err_invoice_file_type', g.lang)}), 400
    # Validate size (seek to end)
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > MAX_FILE_SIZE:
        return jsonify({'status': 'error', 'message': _t('err_invoice_file_too_large', g.lang)}), 400
    if size == 0:
        return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields='file')}), 400
    # Detect content_type (fallback to mime by ext)
    mime, _ = mimetypes.guess_type(safe_name)
    content_type = mime or 'application/octet-stream'
    # Resolve user dir
    user_id = g.user['id'] if hasattr(g, 'user') and g.user else None
    if not user_id:
        return jsonify({'status': 'error', 'message': _t('err_need_verify', g.lang)}), 401
    user_dir = _ensure_user_dir(user_id)
    new_name = f'{uuid.uuid4().hex}{ext}'
    full_path = os.path.join(user_dir, new_name)
    f.save(full_path)
    rel_path = f'{user_id}/{new_name}'
    with get_db() as db:
        row = db.execute('SELECT id FROM invoice_records WHERE id=?', (rid,)).fetchone()
        if not row:
            # Clean up orphan file
            try:
                os.remove(full_path)
            except OSError:
                pass
            return jsonify({'status': 'error', 'message': _t('err_invoice_not_found', g.lang)}), 404
        db.execute(
            'UPDATE invoice_records SET file_path=?, file_type=?, file_size=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
            (rel_path, content_type, size, rid)
        )
    return jsonify({'status': 'ok', 'file_path': rel_path, 'file_type': content_type, 'file_size': size})


# ── Serve invoice file (for download / share preview) ──
@invoice_bp.route('/invoice-files/<int:user_id>/<path:filename>', methods=['GET'])
@login_required
def api_invoice_file_serve(user_id, filename):
    user_dir = os.path.join(INVOICE_FILE_DIR, str(user_id))
    file_path = os.path.normpath(os.path.join(user_dir, filename))
    if not file_path.startswith(user_dir) or not os.path.isfile(file_path):
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    mime, _ = mimetypes.guess_type(file_path)
    resp = make_response(send_file(file_path, mimetype=mime or 'application/octet-stream'))
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp


# ── Lite procurement batches (for invoice-record batch selector) ──
@invoice_bp.route('/procurement-batches-lite', methods=['GET'])
@login_required
def api_procurement_batches_lite():
    """Lightweight procurement batch list (id, batch_number, date, total) for the
    invoice-record drawer batch selector. Defaults to last 20, sorted by date desc.
    """
    limit = min(request.args.get('limit', 20, type=int), 100)
    with get_db() as db:
        rows = db.execute(
            'SELECT id, batch_number, date, total FROM procurement_batches '
            'ORDER BY date DESC, id DESC LIMIT ?',
            (limit,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])
