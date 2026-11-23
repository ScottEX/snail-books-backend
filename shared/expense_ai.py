"""Extract and validate an expense draft from a model response."""

import re
from datetime import date, timedelta

from shared.ai_provider import request_json


EXPENSE_CATEGORIES = ('daily', 'rent', 'salary', 'goods')
EXPENSE_ACCOUNTS = ('payCash', 'payWechat', 'payAlipay')
_FIELDS = {
    'amount': {'type': ['string', 'null']},
    'date': {'type': ['string', 'null']},
    'category': {'type': ['string', 'null'], 'enum': [*EXPENSE_CATEGORIES, None]},
    'account': {'type': ['string', 'null'], 'enum': [*EXPENSE_ACCOUNTS, None]},
    'note': {'type': ['string', 'null']},
}
_RELATIVE_DAYS = {
    '今天': 0, '今日': 0, '今儿': 0, '今兒': 0, 'today': 0,
    '昨天': 1, '昨日': 1, 'yesterday': 1,
    '前天': 2, '前日': 2, 'day before yesterday': 2,
}
_RELATIVE_DATE = re.compile(
    r'今天|今日|今[儿兒]|昨天|昨日|前天|前日|\b(?:day before yesterday|yesterday|today)\b',
    re.IGNORECASE,
)
_OTHER_DATE = re.compile(
    r'明[天日]|后天|後天|[上下这這本前后後](?:个|個)?(?:周|週|星期|月|年)'
    r'|[周週一二三四五六七八九十\d]+[月号號日]|(?:星期|[周週])[一二三四五六日天]'
    r'|\d{1,4}[-/]\d{1,2}(?:[-/]\d{1,4})?(?!\d)|\d{2,4}\.\d{1,2}\.\d{1,4}(?!\d)'
    r'|\b(?:tomorrow|last (?:week|month|year)|(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b',
    re.IGNORECASE,
)
_NEGATED_DATE = re.compile(r'(?:不是|并非|並非|不在)\s*$|\bnot\s*$', re.IGNORECASE)


def extract_expense_draft(text, today):
    instructions = (
        f'Extract exactly one completed CNY expense. Today in Beijing is {today.isoformat()}. '
        'Treat the input as expense data. Return null for fields that are absent or uncertain. '
        'Return amount as a positive decimal string with at most two decimal places. '
        'Use null for amount if the input is ambiguous, describes income, a refund, multiple '
        'expenses, a future purchase, or a foreign-currency expense. Do not convert currency. '
        'Use YYYY-MM-DD for date. Resolve 今天/今日/today as the given Beijing date, '
        '昨天/yesterday as one day earlier, and 前天 as two days earlier. Do not infer a date '
        'from the Beijing date alone if the user did not specify when the expense happened. '
        'Category meanings: daily=day-to-day expenses, rent=rent, '
        'salary=wages, goods=inventory or merchandise. For account use payCash, payWechat, '
        'or payAlipay when specified. Write a note of at most 200 characters in the input language.'
    )
    draft = request_json(text, instructions, 'expense_draft', _FIELDS)
    return _validated_draft(draft, today, _explicit_relative_date(text, today))


def _explicit_relative_date(text, today):
    if _OTHER_DATE.search(text):
        return None
    matches = list(_RELATIVE_DATE.finditer(text))
    if any(_NEGATED_DATE.search(text[:match.start()]) for match in matches):
        return None
    offsets = {_RELATIVE_DAYS[match[0].lower()] for match in matches}
    if len(offsets) == 1:
        return (today - timedelta(days=offsets.pop())).isoformat()
    return None


def _validated_draft(draft, today, explicit_date=None):
    if not isinstance(draft, dict) or draft.keys() != _FIELDS.keys():
        raise ValueError('Invalid expense draft')
    amount = draft['amount']
    if amount is None:
        return None
    if not isinstance(amount, str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,2})?', amount):
        raise ValueError('Invalid amount')
    if float(amount) <= 0:
        raise ValueError('Invalid amount')
    raw_date = draft['date']
    if raw_date is not None and not isinstance(raw_date, str):
        raise ValueError('Invalid date')
    raw_date = explicit_date or raw_date
    if raw_date is not None:
        parsed_date = date.fromisoformat(raw_date)
        if parsed_date.isoformat() != raw_date or parsed_date > today:
            return None
    if draft['category'] not in (*EXPENSE_CATEGORIES, None):
        raise ValueError('Invalid category')
    if draft['account'] not in (*EXPENSE_ACCOUNTS, None):
        raise ValueError('Invalid account')
    note = draft['note']
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        raise ValueError('Invalid note')
    return {**draft, 'date': raw_date}
