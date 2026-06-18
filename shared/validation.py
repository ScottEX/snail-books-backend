"""Input validation utilities shared across routes."""

import re
from .i18n import t

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')


def validate_password(password, lang='zh-CN'):
    """Returns (bool, str). Password: >=8 chars, must contain letter, digit, special char."""
    ok = True
    if len(password) < 8:
        ok = False
    if not re.search(r'[A-Za-z]', password):
        ok = False
    if not re.search(r'[0-9]', password):
        ok = False
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        ok = False
    if not ok:
        return False, t('err_pw_requirements', lang)
    return True, ''


def validate_username(username):
    """Returns bool. Username: 2-32 chars, letters/digits/underscores/Chinese."""
    if len(username) < 2 or len(username) > 32:
        return False
    # Also reject if UTF-8 byte length exceeds 96 (3× worst-case for CJK)
    if len(username.encode('utf-8')) > 96:
        return False
    if not re.match(r'^[a-zA-Z0-9_\-\\u4e00-\\u9fa5]+$', username):
        return False
    return True


def validate_required(data, *fields):
    """Return list of missing field names; empty if all present."""
    return [f for f in fields if data.get(f) in (None, '')]


def validate_email(email):
    """Returns bool. Basic email format check."""
    return bool(EMAIL_RE.match(email))
