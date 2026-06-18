"""Partner & dividend routes with expense image upload."""

import os
import uuid
from datetime import datetime
from flask import Blueprint, request, jsonify, g

# Expire store for share links
EXPENSE_IMG_DIR = os.path.join(os.path.dirname(__file__), '..', 'expense_imgs')
ALLOWED_IMG_EXT = {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t
from shared.validation import validate_required
from shared.config import EXPENSE_IMG_DIR

# ── Pillow availability (for image thumbnail generation) ──
try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found]
    HAS_PIL = True
except ImportError:
    _PILImage = None  # type: ignore
    HAS_PIL = False


bp = Blueprint('partners', __name__)

# ═══════════════════════════════════════════════════════════
#  Expense image upload
# ═══════════════════════════════════════════════════════════

@bp.route('/expenses/upload-images', methods=['POST'])
@login_required
def upload_expense_images():
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
    for f in files:
        if f.filename == '':
            continue
        # Keep original extension, generate unique name
        ext = os.path.splitext(f.filename or 'img.jpg')[1] or '.jpg'
        if ext.lower() not in ALLOWED_IMG_EXT:
            continue
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


# ═══════════════════════════════════════════════════════════
#  Partners CRUD
# ═══════════════════════════════════════════════════════════

@bp.route('/partners')
@login_required
def list_partners():
    with get_db() as db:
        rows = db.execute("""SELECT p.*, COALESCE(SUM(d.amount),0) as total_dividends
                             FROM partners p
                             LEFT JOIN dividends d ON d.partner = p.name
                             GROUP BY p.id""").fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route('/partners/<int:id>', methods=['DELETE'])
@login_required
def delete_partner(id):
    with get_db() as db:
        db.execute('DELETE FROM partners WHERE id=?', (id,))
        db.commit()
    return jsonify({'status': 'ok'})


@bp.route('/partners/<int:id>', methods=['PUT'])
@login_required
def update_partner(id):
    data = request.get_json()
    missing = validate_required(data, 'share', 'investment')
    if missing:
        return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
    with get_db() as db:
        db.execute('UPDATE partners SET share=?, investment=?, status=?, note=? WHERE id=?',
                   (data['share'], data['investment'], data.get('status', '进行中'), data.get('note', ''), id))
        db.commit()
    return jsonify({'status': 'ok'})


# ═══════════════════════════════════════════════════════════
#  Dividends CRUD
# ═══════════════════════════════════════════════════════════

@bp.route('/dividends', methods=['GET', 'POST'])
@login_required
def dividends():
    if request.method == 'POST':
        data = request.get_json()
        items = data.get('items', [data])  # support single item or array
        for item in items:
            missing = validate_required(item, 'partner', 'amount')
            if missing:
                return jsonify({'status': 'error', 'message': t('err_missing_fields', g.lang, fields=', '.join(missing))}), 400
        with get_db() as db:
            for item in items:
                db.execute('INSERT INTO dividends (partner,amount,note,date,user_id) VALUES (?,?,?,?,?)',
                           (item['partner'], item['amount'], item.get('note', ''), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), g.user_id))
            db.commit()
        return jsonify({'status': 'ok'})
    with get_db() as db:
        rows = db.execute('SELECT * FROM dividends ORDER BY date DESC, created_at DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@bp.route('/dividends/<int:id>', methods=['DELETE'])
@login_required
def delete_dividend(id):
    with get_db() as db:
        db.execute('DELETE FROM dividends WHERE id=?', (id,))
        db.commit()
    return jsonify({'status': 'ok'})
