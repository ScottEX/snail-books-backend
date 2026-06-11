"""Procurement routes — products CRUD, cart, procurement batches, PDF + share."""

import concurrent.futures
import json, os, time, hmac, base64, hashlib
from datetime import datetime
from flask import Blueprint, request, jsonify, g, make_response, current_app

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t as _t
from shared.validation import validate_required
from shared.config import EXPENSE_IMG_DIR

procurement_bp = Blueprint('procurement', __name__)


# ── Helper: PDF date format ──
def _format_pdf_date(d, lang):
    """PDF date format per language: CN 年月日 / EN YYYY-MM-DD."""
    if lang in ('zh-CN', 'zh-TW'):
        return f"{d.year}年{d.month}月{d.day}日"
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


# ── Products CRUD ──
@procurement_bp.route('/products', methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def api_products():
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'name')
        if missing:
            return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('INSERT INTO products (name,spec,unit,price,supplier,note) VALUES (?,?,?,?,?,?)',
                       (data['name'], data.get('spec', ''), data.get('unit', ''), data.get('price', 0), data.get('supplier', ''), data.get('note', '')))
            db.commit()
        return jsonify({'status': 'ok'})
    if request.method == 'PUT':
        data = request.get_json()
        missing = validate_required(data, 'name', 'id')
        if missing:
            return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            db.execute('UPDATE products SET name=?, spec=?, unit=?, price=?, supplier=?, note=? WHERE id=?',
                       (data['name'], data.get('spec', ''), data.get('unit', ''), data.get('price', 0), data.get('supplier', ''), data.get('note', ''), data['id']))
            db.commit()
        return jsonify({'status': 'ok'})
    if request.method == 'DELETE':
        pid = request.args.get('id')
        with get_db() as db:
            db.execute('DELETE FROM products WHERE id=?', (pid,))
            db.commit()
        return jsonify({'status': 'ok'})
    # GET
    with get_db() as db:
        rows = db.execute('SELECT * FROM products ORDER BY name').fetchall()
    return jsonify([dict(r) for r in rows])


# ── Cart CRUD ──
@procurement_bp.route('/procurement-cart', methods=['GET'])
@login_required
def api_get_cart():
    with get_db() as db:
        rows = db.execute('SELECT * FROM procurement_cart ORDER BY updated_at DESC').fetchall()
        return jsonify([dict(r) for r in rows])


@procurement_bp.route('/procurement-cart', methods=['POST'])
@login_required
def api_add_cart():
    data = request.get_json() or {}
    product_id = data.get('product_id')
    quantity = data.get('quantity', 1)
    if not product_id or quantity < 1:
        return jsonify({'status': 'error', 'message': 'product_id and quantity>=1 required'}), 400
    with get_db() as db:
        product = db.execute('SELECT name FROM products WHERE id=?', (product_id,)).fetchone()
        if not product:
            return jsonify({'status': 'error', 'message': 'product not found'}), 404
        db.execute(
            'INSERT OR REPLACE INTO procurement_cart (product_id, product_name, quantity, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)',
            (product_id, product['name'], quantity)
        )
        db.commit()
        return jsonify({'status': 'ok'})


@procurement_bp.route('/procurement-cart/<int:product_id>', methods=['DELETE'])
@login_required
def api_remove_cart_item(product_id):
    with get_db() as db:
        db.execute('DELETE FROM procurement_cart WHERE product_id=?', (product_id,))
        db.commit()
        return jsonify({'status': 'ok'})


@procurement_bp.route('/procurement-cart', methods=['DELETE'])
@login_required
def api_clear_cart():
    with get_db() as db:
        db.execute('DELETE FROM procurement_cart')
        db.commit()
        return jsonify({'status': 'ok'})


# ── Procurement batches CRUD ──
@procurement_bp.route('/procurement-batches', methods=['GET', 'POST'])
@login_required
def api_procurement_batches():
    """POST: create procurement batch + items."""
    if request.method == 'POST':
        data = request.get_json()
        missing = validate_required(data, 'date', 'payment_method', 'items')
        if missing:
            return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        items = data.get('items', [])
        if not items or not isinstance(items, list):
            return jsonify({'status': 'error', 'message': _t('err_empty_fields', g.lang)}), 400
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
                return jsonify({'status': 'error', 'message': _t('err_empty_fields', g.lang)}), 400
            images_json = json.dumps(data.get('images', []))
            thumbs_json = json.dumps(data.get('thumb_images', []))
            cur = db.execute(
                'INSERT INTO procurement_batches (batch_number,date,payment_method,category,total,images,thumb_images,note) VALUES (?,?,?,?,?,?,?,?)',
                (batch_no, data['date'], data['payment_method'], data.get('category', '采购'), round(total, 2),
                 images_json, thumbs_json, data.get('note', ''))
            )
            batch_id = cur.lastrowid
            for name, spec, up, qty, sub, pid in item_rows:
                db.execute(
                    'INSERT INTO procurement_items (batch_id,product_id,product_name,spec,unit_price,quantity,subtotal) VALUES (?,?,?,?,?,?,?)',
                    (batch_id, pid, name, spec, up, qty, round(sub, 2))
                )
            # Sync an expense transaction
            cur = db.execute(
                "INSERT INTO transactions (type,amount,category,account,note,date,images,thumb_images,procurement_batch_id) VALUES ('expense',?,?,?,?,?,?,?,?)",
                (round(total, 2), data.get('category', '采购'), data['payment_method'], data.get('note', ''), data['date'], images_json, thumbs_json, batch_id)
            )
            db.commit()
        return jsonify({'status': 'ok', 'batch_id': batch_id, 'batch_number': batch_no, 'total': round(total, 2)})

    # GET: paginated list
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


@procurement_bp.route('/procurement-batches/<int:id>', methods=['GET', 'PUT', 'DELETE'])
@login_required
def api_procurement_batch_detail(id):
    """Single procurement batch: detail, edit, delete."""
    with get_db() as db:
        row = db.execute('SELECT * FROM procurement_batches WHERE id=?', (id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': 'Not found'}), 404

        if request.method == 'DELETE':
            batch = dict(row)

            # Collect all image URLs from this batch before deletion
            orphan_candidates = set()
            for col in ('images', 'thumb_images'):
                raw = batch.get(col)
                if raw:
                    try:
                        arr = json.loads(raw) if isinstance(raw, str) else raw
                        if isinstance(arr, list):
                            orphan_candidates.update(arr)
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Also collect URLs from the matching transaction
            tx = db.execute(
                "SELECT images, thumb_images FROM transactions WHERE procurement_batch_id=?",
                (id,)
            ).fetchone()
            if tx:
                for col in ('images', 'thumb_images'):
                    raw = tx[col]
                    if raw:
                        try:
                            arr = json.loads(raw) if isinstance(raw, str) else raw
                            if isinstance(arr, list):
                                orphan_candidates.update(arr)
                        except (json.JSONDecodeError, TypeError):
                            pass

            db.execute('DELETE FROM procurement_items WHERE batch_id=?', (id,))
            db.execute(
                "DELETE FROM transactions WHERE procurement_batch_id=?",
                (id,)
            )
            db.execute('DELETE FROM procurement_batches WHERE id=?', (id,))
            db.commit()

            # Clean up orphan image files no longer referenced by any batch or transaction
            for url in orphan_candidates:
                if not url.startswith('/expense-imgs/'):
                    continue
                still_used = db.execute(
                    "SELECT 1 FROM procurement_batches WHERE images LIKE ? OR thumb_images LIKE ? LIMIT 1",
                    (f'%{url}%', f'%{url}%')
                ).fetchone()
                if not still_used:
                    still_used = db.execute(
                        "SELECT 1 FROM transactions WHERE images LIKE ? OR thumb_images LIKE ? LIMIT 1",
                        (f'%{url}%', f'%{url}%')
                    ).fetchone()
                if not still_used:
                    rel = url[len('/expense-imgs/'):]
                    fp = os.path.normpath(os.path.join(EXPENSE_IMG_DIR, rel))
                    if fp.startswith(EXPENSE_IMG_DIR) and os.path.isfile(fp):
                        try:
                            os.remove(fp)
                        except OSError:
                            pass

            _delete_cached_pdf(id)
            return jsonify({'status': 'ok'})

        if request.method == 'PUT':
            data = request.get_json()
            missing = validate_required(data, 'date', 'payment_method')
            if missing:
                return jsonify({'status': 'error', 'message': _t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
            items = data.get('items', [])
            if not items or not isinstance(items, list):
                return jsonify({'status': 'error', 'message': _t('err_empty_fields', g.lang)}), 400
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
                return jsonify({'status': 'error', 'message': _t('err_empty_fields', g.lang)}), 400
            images_json = json.dumps(data.get('images', []))
            thumbs_json = json.dumps(data.get('thumb_images', []))
            old_batch = dict(row)
            db.execute('DELETE FROM procurement_items WHERE batch_id=?', (id,))
            for name, spec, up, qty, sub, pid in item_rows:
                db.execute(
                    'INSERT INTO procurement_items (batch_id,product_id,product_name,spec,unit_price,quantity,subtotal) VALUES (?,?,?,?,?,?,?)',
                    (id, pid, name, spec, up, qty, round(sub, 2))
                )
            db.execute(
                "UPDATE procurement_batches SET date=?, payment_method=?, category=?, total=?, images=?, thumb_images=?, note=? WHERE id=?",
                (data['date'], data['payment_method'], data.get('category', '采购'),
                 round(total, 2), images_json, thumbs_json, data.get('note', ''), id)
            )
            cur = db.execute(
                "UPDATE transactions SET amount=?, category=?, account=?, note=?, date=?, images=?, thumb_images=?, procurement_batch_id=? WHERE type='expense' AND category=? AND date=? AND amount=? AND account=?",
                (round(total, 2), data.get('category', '采购'), data['payment_method'],
                 data.get('note', ''), data['date'], images_json, thumbs_json, id,
                 old_batch.get('category', '采购'), old_batch['date'], old_batch['total'], old_batch['payment_method'])
            )
            # If no matching transaction found (e.g. category mismatch), create one
            if cur.rowcount == 0:
                db.execute(
                    "INSERT INTO transactions (type,amount,category,account,note,date,images,thumb_images,procurement_batch_id) VALUES ('expense',?,?,?,?,?,?,?,?)",
                    (round(total, 2), data.get('category', '采购'), data['payment_method'],
                     data.get('note', ''), data['date'], images_json, thumbs_json, id)
                )
            db.commit()
            _delete_cached_pdf(id)
            return jsonify({'status': 'ok', 'batch_id': id, 'total': round(total, 2)})

        # GET: detail
        b = dict(row)
        b['images'] = json.loads(b['images']) if b['images'] else []
        b['thumb_images'] = json.loads(b['thumb_images']) if b['thumb_images'] else []
        items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (id,)).fetchall()
        b['items'] = [dict(it) for it in items]
        # Include operator username
        if b.get('user_id'):
            user_row = db.execute('SELECT username FROM users WHERE id=?', (b['user_id'],)).fetchone()
            b['operator'] = user_row['username'] if user_row else ''
    return jsonify(b)


# ── PDF generation (with 30s timeout) ──
PDF_TIMEOUT = 30
PDF_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'pdf_cache')


def _write_pdf_with_timeout(html, timeout=PDF_TIMEOUT):
    """Generate PDF from HTML string with timeout protection."""
    import weasyprint, logging
    log = logging.getLogger('procurement.pdf')
    start = time.time()
    def _render():
        return weasyprint.HTML(string=html).write_pdf()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_render)
            pdf_bytes = future.result(timeout=timeout)
        elapsed = time.time() - start
        log.info(f'PDF rendered in {elapsed:.1f}s ({len(pdf_bytes)} bytes)')
        return pdf_bytes
    except concurrent.futures.TimeoutError:
        elapsed = time.time() - start
        log.error(f'PDF render timed out after {elapsed:.1f}s (> {timeout}s limit)')
        raise
    except Exception as e:
        elapsed = time.time() - start
        log.exception(f'PDF render failed after {elapsed:.1f}s: {e}')
        raise RuntimeError(f'PDF render failed: {e}') from e


def _cached_pdf_path(batch_id, lang):
    return os.path.join(PDF_CACHE_DIR, f'batch_{batch_id}_{lang}.pdf')


def _get_cached_pdf(batch_id, lang):
    _cleanup_orphaned_cache()
    cache_path = _cached_pdf_path(batch_id, lang)
    if os.path.isfile(cache_path):
        with open(cache_path, 'rb') as f:
            return f.read()
    return None


def _save_cached_pdf(batch_id, pdf_bytes, lang):
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    cache_path = _cached_pdf_path(batch_id, lang)
    with open(cache_path, 'wb') as f:
        f.write(pdf_bytes)


def _delete_cached_pdf(batch_id, lang=None):
    """Delete cached PDF(s) for a batch. If lang is None, delete all language variants."""
    import glob as _glob
    pattern = os.path.join(PDF_CACHE_DIR, f'batch_{batch_id}_*.pdf') if lang is None else _cached_pdf_path(batch_id, lang)
    if lang is None:
        for p in _glob.glob(pattern):
            os.remove(p)
    elif os.path.isfile(pattern):
        os.remove(pattern)


_LAST_ORPHAN_CLEANUP = 0
_ORPHAN_CLEANUP_INTERVAL = 3600  # 1 hour between cleanup scans


def _cleanup_orphaned_cache():
    """Remove cached PDFs whose batch no longer exists in DB. Runs at most once per hour."""
    global _LAST_ORPHAN_CLEANUP
    now = time.time()
    if now - _LAST_ORPHAN_CLEANUP < _ORPHAN_CLEANUP_INTERVAL:
        return
    _LAST_ORPHAN_CLEANUP = now
    if not os.path.isdir(PDF_CACHE_DIR):
        return
    import re
    _CACHE_RE = re.compile(r'^batch_(\d+)(?:_(?:zh-CN|zh-TW|en))?\.pdf$')
    try:
        with get_db() as db:
            existing = set(r[0] for r in db.execute('SELECT id FROM procurement_batches').fetchall())
        removed = 0
        for fname in os.listdir(PDF_CACHE_DIR):
            m = _CACHE_RE.match(fname)
            if not m:
                continue
            fid = int(m.group(1))
            if fid not in existing:
                os.remove(os.path.join(PDF_CACHE_DIR, fname))
                removed += 1
        if removed:
            import logging
            logging.getLogger('procurement.pdf').info(f'Orphan cache cleanup: removed {removed} stale PDF(s)')
    except Exception:
        pass  # Never let cleanup break the request


@procurement_bp.route('/procurement-batches/<int:id>/pdf' , methods=['GET'])
@login_required
def api_procurement_batch_pdf(id):
    refresh = request.args.get('refresh', '0') == '1'
    cached = None if refresh else _get_cached_pdf(id, g.lang)
    if cached:
        with get_db() as db:
            row = db.execute('SELECT batch_number FROM procurement_batches WHERE id=?', (id,)).fetchone()
            bn = row['batch_number'] if row else id
        filename = f"procurement_{bn:04d}.pdf"
        resp = make_response(cached)
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        return resp

    with get_db() as db:
        row = db.execute('SELECT * FROM procurement_batches WHERE id=?', (id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
        b = dict(row)
        b['images'] = json.loads(b['images']) if b['images'] else []
        items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (id,)).fetchall()
        b['items'] = [dict(it) for it in items]

    # Items HTML
    items_html = ''
    for it in b['items']:
        spec = it.get('spec', '') or ''
        items_html += (
            f"<tr><td>{it['product_name']}</td>"
            f"<td>{spec}</td>"
            f"<td>¥{it['unit_price']:,.2f}</td>"
            f"<td>{it['quantity']}</td>"
            f"<td>¥{it['subtotal']:,.2f}</td></tr>"
        )

    # Images HTML
    images_html = ''
    img_dir = EXPENSE_IMG_DIR
    if b['images']:
        imgs = ''
        for img in b['images']:
            img_path = os.path.join(img_dir, img)
            if os.path.isfile(img_path):
                imgs += f'<img src="file://{os.path.abspath(img_path)}" />'
        if imgs:
            images_html = (
                '<div class="images-section">'
                f'<div class="img-label">📎 {_t("pdfImgLabel", g.lang)}</div>'
                f'<div class="images-grid">{imgs}</div>'
                '</div>'
            )

    # Date formatting
    try:
        d = datetime.strptime(b['date'], '%Y-%m-%d')
        date_str = _format_pdf_date(d, g.lang)
    except Exception:
        date_str = b.get('date', '')

    # Template
    template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'procurement_order.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    # Optional note
    _note_raw = (b.get('note') or '').strip()
    note_html = f'<div class="note">{_t("procNoteOptional", g.lang)}：{_note_raw}</div>' if _note_raw else ''

    now = datetime.now()
    html = html.format(
        batch_number=f"2026-{b['batch_number']:04d}",
        date=date_str,
        payment_method=_t(b.get('payment_method', 'payWechat'), g.lang),
        category=_t(b.get('category', 'goods'), g.lang),
        items_html=items_html,
        total=b['total'],
        images_html=images_html,
        note_html=note_html,
        batch_label_text=_t('procNowBatch', g.lang, n=b['batch_number']),
        operator=g.username,
        gen_date=_format_pdf_date(now, g.lang),
        pdf_title=_t('pdfTitle', g.lang),
        pdf_subtitle=_t('pdfSubtitle', g.lang),
        label_date=_t('pdfLabelDate', g.lang),
        label_payment=_t('pdfLabelPayment', g.lang),
        label_category=_t('pdfLabelCategory', g.lang),
        label_batch=_t('procBatchLabel', g.lang),
        col_name=_t('pdfColName', g.lang),
        col_spec=_t('pdfColSpec', g.lang),
        col_unit_price=_t('pdfColUnitPrice', g.lang),
        col_qty=_t('pdfColQty', g.lang),
        col_subtotal=_t('pdfColSubtotal', g.lang),
        total_cny=_t('pdfTotalCNY', g.lang),
        operator_label=_t('pdfOperator', g.lang),
        gen_date_label=_t('pdfGenDate', g.lang),
    )

    try:
        pdf_bytes = _write_pdf_with_timeout(html)
    except concurrent.futures.TimeoutError:
        return jsonify({'status': 'error', 'message': 'PDF生成超时，请稍后重试'}), 504
    except RuntimeError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    filename = f"procurement_{b['batch_number']:04d}.pdf"
    _save_cached_pdf(id, pdf_bytes, g.lang)
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ── Share link helpers ──
def _make_share_token(batch_id, expires_ts):
    payload = f"{batch_id}:{expires_ts}"
    sig = hmac.new(current_app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')


def _verify_share_token(token):
    try:
        raw = base64.urlsafe_b64decode(token + '=' * (4 - len(token) % 4)).decode()
        parts = raw.split(':')
        if len(parts) != 3:
            return None
        batch_id, expires_ts, sig = parts
        expected = hmac.new(current_app.secret_key.encode(), f"{batch_id}:{expires_ts}".encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_ts) < time.time():
            return None
        return int(batch_id)
    except Exception:
        return None


@procurement_bp.route('/procurement-batches/<int:id>/share-link', methods=['GET'])
@login_required
def api_share_link(id):
    """Generate 24-hour share link."""
    expires = int(time.time()) + 86400
    token = _make_share_token(id, expires)
    return jsonify({'url': f'/api/share/{token}'})


@procurement_bp.route('/share/<token>', methods=['GET'])
def api_share_pdf(token):
    batch_id = _verify_share_token(token)
    if not batch_id:
        return jsonify({'status': 'error', 'message': '链接已过期或无效'}), 410
    refresh = request.args.get('refresh', '0') == '1'
    cached = None if refresh else _get_cached_pdf(batch_id, g.lang)
    if cached:
        with get_db() as db:
            row = db.execute('SELECT batch_number FROM procurement_batches WHERE id=?', (batch_id,)).fetchone()
            bn = row['batch_number'] if row else batch_id
        filename = f"procurement_{bn:04d}.pdf"
        resp = make_response(cached)
        resp.headers['Content-Type'] = 'application/pdf'
        resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        return resp
    with get_db() as db:
        row = db.execute('SELECT * FROM procurement_batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': 'Not found'}), 404
        b = dict(row)
        b['images'] = json.loads(b['images']) if b['images'] else []
        items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (batch_id,)).fetchall()
        b['items'] = [dict(it) for it in items]
        user_row = db.execute('SELECT username FROM users WHERE id=?', (b.get('user_id'),)).fetchone()
        operator = user_row['username'] if user_row else '—'

    # Items HTML
    items_html = ''
    for it in b['items']:
        spec = it.get('spec', '') or ''
        items_html += f"<tr><td>{it['product_name']}</td><td>{spec}</td><td>¥{it['unit_price']:,.2f}</td><td>{it['quantity']}</td><td>¥{it['subtotal']:,.2f}</td></tr>"

    # Images HTML
    images_html = ''
    img_dir = EXPENSE_IMG_DIR
    if b['images']:
        imgs = ''
        for img in b['images']:
            img_path = os.path.join(img_dir, img)
            if os.path.isfile(img_path):
                imgs += f'<img src="file://{os.path.abspath(img_path)}" />'
        if imgs:
            images_html = f'<div class="images-section"><div class="img-label">📎 {_t("pdfImgLabel", g.lang)}</div><div class="images-grid">{imgs}</div></div>'

    # Date
    try:
        d = datetime.strptime(b['date'], '%Y-%m-%d')
        date_str = _format_pdf_date(d, g.lang)
    except Exception:
        date_str = b.get('date', '')

    # Template
    template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'procurement_order.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    _note_raw = (b.get('note') or '').strip()
    note_html = f'<div class="note">{_t("procNoteOptional", g.lang)}：{_note_raw}</div>' if _note_raw else ''

    now = datetime.now()
    html = html.format(
        batch_number=f"2026-{b['batch_number']:04d}",
        date=date_str,
        payment_method=_t(b.get('payment_method', 'payWechat'), g.lang),
        category=_t(b.get('category', 'goods'), g.lang),
        items_html=items_html,
        total=b['total'],
        images_html=images_html,
        note_html=note_html,
        batch_label_text=_t('procNowBatch', g.lang, n=b['batch_number']),
        operator=operator,
        gen_date=_format_pdf_date(now, g.lang),
        pdf_title=_t('pdfTitle', g.lang),
        pdf_subtitle=_t('pdfSubtitle', g.lang),
        label_date=_t('pdfLabelDate', g.lang),
        label_payment=_t('pdfLabelPayment', g.lang),
        label_category=_t('pdfLabelCategory', g.lang),
        label_batch=_t('procBatchLabel', g.lang),
        col_name=_t('pdfColName', g.lang),
        col_spec=_t('pdfColSpec', g.lang),
        col_unit_price=_t('pdfColUnitPrice', g.lang),
        col_qty=_t('pdfColQty', g.lang),
        col_subtotal=_t('pdfColSubtotal', g.lang),
        total_cny=_t('pdfTotalCNY', g.lang),
        operator_label=_t('pdfOperator', g.lang),
        gen_date_label=_t('pdfGenDate', g.lang),
    )

    try:
        pdf_bytes = _write_pdf_with_timeout(html)
    except concurrent.futures.TimeoutError:
        return jsonify({'status': 'error', 'message': 'PDF生成超时，请稍后重试'}), 504
    except RuntimeError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    filename = f"procurement_{b['batch_number']:04d}.pdf"
    _save_cached_pdf(batch_id, pdf_bytes, g.lang)
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
    return response


# ── PNG rendering from PDF ──
def _render_procurement_png(batch_id):
    try:
        import pymupdf
    except ImportError:
        return None, None
    cached_pdf = _get_cached_pdf(batch_id, g.lang)
    if cached_pdf:
        try:
            doc = pymupdf.open(stream=cached_pdf, filetype="pdf")
            page = doc[0]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
            png_bytes = pix.tobytes("png")
            doc.close()
            with get_db() as db:
                row = db.execute('SELECT batch_number FROM procurement_batches WHERE id=?', (batch_id,)).fetchone()
                bn = row['batch_number'] if row else batch_id
            return png_bytes, bn
        except Exception:
            pass
    with get_db() as db:
        row = db.execute('SELECT * FROM procurement_batches WHERE id=?', (batch_id,)).fetchone()
        if not row:
            return None, None
        b = dict(row)
        b['images'] = json.loads(b['images']) if b['images'] else []
        items = db.execute('SELECT * FROM procurement_items WHERE batch_id=? ORDER BY id', (batch_id,)).fetchall()
        b['items'] = [dict(it) for it in items]
        user_row = db.execute('SELECT username FROM users WHERE id=?', (b.get('user_id'),)).fetchone()
        operator = user_row['username'] if user_row else '—'

    items_html = ''
    for it in b['items']:
        spec = it.get('spec', '') or ''
        items_html += f"<tr><td>{it['product_name']}</td><td>{spec}</td><td>¥{it['unit_price']:,.2f}</td><td>{it['quantity']}</td><td>¥{it['subtotal']:,.2f}</td></tr>"

    images_html = ''
    img_dir = EXPENSE_IMG_DIR
    if b['images']:
        imgs = ''
        for img in b['images']:
            img_path = os.path.join(img_dir, img)
            if os.path.isfile(img_path):
                imgs += f'<img src="file://{os.path.abspath(img_path)}" />'
        if imgs:
            images_html = f'<div class="images-section"><div class="img-label">📎 {_t("pdfImgLabel", g.lang)}</div><div class="images-grid">{imgs}</div></div>'

    try:
        d = datetime.strptime(b['date'], '%Y-%m-%d')
        date_str = _format_pdf_date(d, g.lang)
    except Exception:
        date_str = b.get('date', '')

    template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'procurement_order.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html = f.read()

    _note_raw = (b.get('note') or '').strip()
    note_html = f'<div class="note">{_t("procNoteOptional", g.lang)}：{_note_raw}</div>' if _note_raw else ''

    now = datetime.now()
    html = html.format(
        batch_number=f"2026-{b['batch_number']:04d}",
        date=date_str,
        payment_method=_t(b.get('payment_method', 'payWechat'), g.lang),
        category=_t(b.get('category', 'goods'), g.lang),
        items_html=items_html,
        total=b['total'],
        images_html=images_html,
        note_html=note_html,
        batch_label_text=_t('procNowBatch', g.lang, n=b['batch_number']),
        operator=operator,
        gen_date=_format_pdf_date(now, g.lang),
        pdf_title=_t('pdfTitle', g.lang),
        pdf_subtitle=_t('pdfSubtitle', g.lang),
        label_date=_t('pdfLabelDate', g.lang),
        label_payment=_t('pdfLabelPayment', g.lang),
        label_category=_t('pdfLabelCategory', g.lang),
        label_batch=_t('procBatchLabel', g.lang),
        col_name=_t('pdfColName', g.lang),
        col_spec=_t('pdfColSpec', g.lang),
        col_unit_price=_t('pdfColUnitPrice', g.lang),
        col_qty=_t('pdfColQty', g.lang),
        col_subtotal=_t('pdfColSubtotal', g.lang),
        total_cny=_t('pdfTotalCNY', g.lang),
        operator_label=_t('pdfOperator', g.lang),
        gen_date_label=_t('pdfGenDate', g.lang),
    )

    try:
        pdf_bytes = _write_pdf_with_timeout(html)
    except (concurrent.futures.TimeoutError, RuntimeError):
        return None, None
    _save_cached_pdf(batch_id, pdf_bytes, g.lang)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes, b['batch_number']


@procurement_bp.route('/share/<token>/first-page.png', methods=['GET'])
def api_share_png(token):
    """Render page 1 of a procurement PDF to PNG (public)."""
    batch_id = _verify_share_token(token)
    if not batch_id:
        return jsonify({'status': 'error', 'message': '链接已过期或无效'}), 410
    png_bytes, batch_number = _render_procurement_png(batch_id)
    if not png_bytes:
        return jsonify({'status': 'error', 'message': '渲染失败'}), 500
    filename = f"procurement_{batch_number:04d}.png"
    response = make_response(png_bytes)
    response.headers['Content-Type'] = 'image/png'
    response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.headers['Cache-Control'] = 'private, max-age=86400'
    return response
