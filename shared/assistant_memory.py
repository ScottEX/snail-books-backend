"""Persistent, account-scoped preferences and explicit chat consent."""

import json
import re

from shared.app_search import keyword_variants
from shared.db import get_db

SETTINGS_KEY = 'assistant_preferences_v1'
_HABITS_KEY = 'assistant_habits_v1'
OPTIONS = {
    'profit_basis': ('after_fees', 'before_fees'),
    'default_period': ('today', 'this_month', 'last_month'),
}
_CONSENT = re.compile(r'记住|(?:以后|今后|往后).*(?:默认|给我|用)|默认(?:给我|用|看|查|是)|'
                      r'from now on|remember|by default|always use', re.I)
_NOT_CONSENT = re.compile(r'不(?:要|用|必)(?:再)?(?:记住|保存|默认)|别(?:记住|保存)|'
                          r'能不能|可不可以|是不是|[吗么嗎麼]\s*[？?]?$|[？?]|don.t (?:remember|save)', re.I)
_EXPLICIT_FEE = {
    'before_fees': re.compile(r'扣费前|扣手续费前|扣除平台手续费前|不扣.{0,8}手续费|before.{0,12}fees', re.I),
    'after_fees': re.compile(r'扣费后|扣完|扣.{0,8}手续费后|(?<!不)扣除.{0,8}手续费(?!前)|after.{0,12}fees|net of.{0,12}fees', re.I),
}
_EXPLICIT_PERIOD = {
    'last_month': re.compile(r'上个月|上月|last month', re.I),
    'this_month': re.compile(r'本月|这个月|this month', re.I),
    'today': re.compile(r'今天|今日|today', re.I),
}


def read_preferences(db, user_id):
    return _read(db, user_id, SETTINGS_KEY)


def _read(db, user_id, key):
    row = db.execute('SELECT value FROM user_settings WHERE user_id=? AND key=?', (user_id, key)).fetchone()
    return json.loads(row['value']) if row else {}


def preferences(user_id):
    with get_db() as db:
        return read_preferences(db, user_id)


def valid_preference(key, value):
    return isinstance(key, str) and key in OPTIONS and value in OPTIONS[key]


def save_preference(db, user_id, key, value):
    current = read_preferences(db, user_id)
    if value is None:
        current.pop(key, None)
    else:
        current[key] = value
    _save(db, user_id, SETTINGS_KEY, current)
    if key == 'profit_basis' and value is None:
        db.execute('DELETE FROM user_settings WHERE user_id=? AND key=?', (user_id, _HABITS_KEY))
    return current


def _save(db, user_id, key, data):
    db.execute(
        'INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) '
        'ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value',
        (user_id, key, json.dumps(data)),
    )


def explicit_value(text, key):
    patterns = _EXPLICIT_FEE if key == 'profit_basis' else _EXPLICIT_PERIOD
    matches = {
        value for value, pattern in patterns.items()
        if any(pattern.search(variant) for variant in keyword_variants(text))
    }
    return next(iter(matches)) if len(matches) == 1 else None


def confirmed_preference(text):
    variants = keyword_variants(text)
    if any(_NOT_CONSENT.search(variant) for variant in variants) or not any(_CONSENT.search(variant) for variant in variants):
        return None
    for key in OPTIONS:
        value = explicit_value(text, key)
        if value:
            return {'key': key, 'value': value}
    return None


def observe_habit(db, user_id, text, plan, today):
    if not plan or plan['metric'] not in ('profit', 'operating_profit'):
        return None
    key, value = 'profit_basis', explicit_value(text, 'profit_basis')
    if not value or key in read_preferences(db, user_id):
        return None
    day = today.toordinal()
    habits = {
        name: item for name, item in _read(db, user_id, _HABITS_KEY).items()
        if item['since'] > day - 30
    }
    item = habits.setdefault(value, {'since': day, 'count': 0, 'suggested': False})
    item['count'] = min(item['count'] + 1, 3)
    offer = item['count'] == 3 and not item['suggested']
    item['suggested'] = item['suggested'] or offer
    _save(db, user_id, _HABITS_KEY, habits)
    return {'key': key, 'value': value} if offer else None
