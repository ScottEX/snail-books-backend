"""Admin routes — user management (Rowan-Lan only)."""

import re
from flask import Blueprint, request, jsonify, session
from shared.db import get_db
from shared.auth import login_required

admin_bp = Blueprint('admin', __name__)

ADMIN_USER_ID = '64'


def _require_admin():
    """Return (user_id, error_response) — error_response is None if admin."""
    uid = str(session.get('user_id', ''))
    if uid != ADMIN_USER_ID:
        return None, (jsonify({'status': 'error', 'message': 'Forbidden'}), 403)
    return uid, None


def _pinyin_initials(s: str) -> str:
    """Extract pinyin initials from Chinese characters using a simple mapping.
    Falls back to the original character for non-Chinese chars."""
    # Common character → pinyin initial mapping (covers most common surnames)
    _PINYIN = {
        '阿': 'a', '爱': 'a', '安': 'a', '暗': 'a', '昂': 'a', '奥': 'a',
        '巴': 'b', '白': 'b', '班': 'b', '包': 'b', '贝': 'b', '边': 'b', '波': 'b', '步': 'b',
        '才': 'c', '蔡': 'c', '曹': 'c', '岑': 'c', '常': 'c', '陈': 'c', '成': 'c', '程': 'c', '迟': 'c', '崔': 'c',
        '大': 'd', '戴': 'd', '邓': 'd', '丁': 'd', '董': 'd', '杜': 'd', '段': 'd',
        '范': 'f', '方': 'f', '房': 'f', '费': 'f', '冯': 'f', '傅': 'f', '富': 'f',
        '高': 'g', '葛': 'g', '宫': 'g', '龚': 'g', '古': 'g', '顾': 'g', '关': 'g', '郭': 'g',
        '海': 'h', '韩': 'h', '郝': 'h', '何': 'h', '贺': 'h', '洪': 'h', '侯': 'h', '胡': 'h', '华': 'h', '黄': 'h', '霍': 'h',
        '纪': 'j', '贾': 'j', '简': 'j', '江': 'j', '姜': 'j', '蒋': 'j', '金': 'j',
        '康': 'k', '孔': 'k', '寇': 'k', '匡': 'k', '邝': 'k',
        '赖': 'l', '蓝': 'l', '雷': 'l', '黎': 'l', '李': 'l', '利': 'l', '梁': 'l', '廖': 'l', '林': 'l', '凌': 'l', '刘': 'l', '柳': 'l', '龙': 'l', '卢': 'l', '陆': 'l', '吕': 'l', '罗': 'l', '骆': 'l',
        '马': 'm', '麦': 'm', '毛': 'm', '梅': 'm', '孟': 'm', '米': 'm', '苗': 'm', '莫': 'm', '牟': 'm',
        '倪': 'n', '年': 'n', '聂': 'n', '宁': 'n', '牛': 'n',
        '欧': 'o', '区': 'o',
        '潘': 'p', '庞': 'p', '裴': 'p', '彭': 'p', '皮': 'p', '蒲': 'p',
        '戚': 'q', '齐': 'q', '钱': 'q', '乔': 'q', '秦': 'q', '邱': 'q', '屈': 'q', '全': 'q',
        '任': 'r', '荣': 'r', '阮': 'r', '芮': 'r',
        '沙': 's', '单': 's', '商': 's', '邵': 's', '沈': 's', '盛': 's', '施': 's', '石': 's', '史': 's', '舒': 's', '司': 's', '宋': 's', '苏': 's', '孙': 's',
        '谈': 't', '谭': 't', '汤': 't', '唐': 't', '陶': 't', '田': 't', '童': 't', '涂': 't',
        '万': 'w', '汪': 'w', '王': 'w', '韦': 'w', '魏': 'w', '温': 'w', '文': 'w', '翁': 'w', '吴': 'w', '伍': 'w', '武': 'w',
        '席': 'x', '夏': 'x', '向': 'x', '萧': 'x', '谢': 'x', '徐': 'x', '许': 'x', '薛': 'x',
        '严': 'y', '颜': 'y', '杨': 'y', '姚': 'y', '叶': 'y', '易': 'y', '殷': 'y', '尹': 'y', '应': 'y', '尤': 'y', '于': 'y', '余': 'y', '俞': 'y', '袁': 'y', '岳': 'y', '云': 'y',
        '曾': 'z', '翟': 'z', '詹': 'z', '张': 'z', '章': 'z', '赵': 'z', '郑': 'z', '钟': 'z', '周': 'z', '朱': 'z', '诸': 'z', '祝': 'z', '庄': 'z', '卓': 'z', '宗': 'z', '邹': 'z', '左': 'z',
    }
    result = []
    for ch in s:
        result.append(_PINYIN.get(ch, ch.lower()))
    return ''.join(result)


@admin_bp.route('/admin/users')
@login_required
def list_users():
    """List users with search, status filter, and pagination (admin only)."""
    _, err = _require_admin()
    if err:
        return err

    search = request.args.get('search', '').strip()
    status = request.args.get('status', '').strip()  # '' = all, 'normal', 'disabled'
    page = max(1, int(request.args.get('page', '1')))
    per_page = min(100, max(1, int(request.args.get('per_page', '20'))))

    with get_db() as db:
        where_clauses = []
        params = []

        if search:
            # Search by username (exact or pinyin initials)
            pinyin_search = _pinyin_initials(search)
            where_clauses.append(
                '(LOWER(username) LIKE ? OR LOWER(_pinyin(username)) LIKE ?)'
            )
            params.extend([f'%{search.lower()}%', f'%{pinyin_search.lower()}%'])

        if status == 'normal':
            where_clauses.append('is_disabled = 0')
        elif status == 'disabled':
            where_clauses.append('is_disabled = 1')

        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

        # Count total
        total = db.execute(
            f'SELECT COUNT(*) FROM users WHERE {where_sql}', params
        ).fetchone()[0]

        # Fetch page
        offset = (page - 1) * per_page
        rows = db.execute(
            f'''SELECT id, username, email, is_disabled, created_at
                FROM users
                WHERE {where_sql}
                ORDER BY id DESC
                LIMIT ? OFFSET ?''',
            params + [per_page, offset]
        ).fetchall()

        # Parse avatar URL (check if file exists)
        users = []
        for row in rows:
            avatar = _build_avatar(row['id'])
            users.append({
                'id': row['id'],
                'username': row['username'],
                'email': row['email'] or '',
                'is_disabled': bool(row['is_disabled']),
                'created_at': row['created_at'] or '',
                'avatar': avatar,
            })

    return jsonify({
        'status': 'ok',
        'data': users,
        'total': total,
        'page': page,
        'per_page': per_page,
    })


@admin_bp.route('/admin/users/<int:user_id>/toggle')
@login_required
def toggle_user_status(user_id):
    """Toggle user disabled status (admin only)."""
    _, err = _require_admin()
    if err:
        return err

    if str(user_id) == ADMIN_USER_ID:
        return jsonify({'status': 'error', 'message': '不能禁用管理员'}), 400

    with get_db() as db:
        row = db.execute(
            'SELECT is_disabled FROM users WHERE id=?', (user_id,)
        ).fetchone()
        if not row:
            return jsonify({'status': 'error', 'message': '用户不存在'}), 404

        new_val = 0 if row['is_disabled'] else 1
        db.execute('UPDATE users SET is_disabled=? WHERE id=?', (new_val, user_id))
        db.commit()

    return jsonify({'status': 'ok', 'is_disabled': bool(new_val)})


def _build_avatar(user_id):
    """Return avatar URL or empty string."""
    import os
    avatar_dir = os.environ.get(
        'BG_DIR',
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'user-images'),
    )
    avatar_dir = os.path.join(avatar_dir, 'avatars')
    file_path = os.path.join(avatar_dir, f'{user_id}.jpg')
    if os.path.isfile(file_path):
        return f'/api/users/avatar?user_id={user_id}'
    return ''


def _register_pinyin_function():
    """Register the _pinyin SQLite function for server-side pinyin search."""
    import sqlite3
    try:
        with get_db() as db:
            db.create_function('_pinyin', 1, _pinyin_initials)
    except Exception:
        pass  # Already registered or DB not ready


_register_pinyin_function()
