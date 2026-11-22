"""Extract an expense draft from text using the OpenAI Responses API."""

import json
import re
from datetime import date

import requests

from shared.config import EXPENSE_AI_MODEL, OPENAI_API_KEY


EXPENSE_CATEGORIES = ('daily', 'rent', 'salary', 'goods')
EXPENSE_ACCOUNTS = ('payCash', 'payWechat', 'payAlipay')
_FIELDS = {
    'amount': {'type': ['string', 'null']},
    'date': {'type': ['string', 'null']},
    'category': {'type': ['string', 'null'], 'enum': [*EXPENSE_CATEGORIES, None]},
    'account': {'type': ['string', 'null'], 'enum': [*EXPENSE_ACCOUNTS, None]},
    'note': {'type': ['string', 'null']},
}


def extract_expense_draft(text, today):
    response = requests.post(
        'https://api.openai.com/v1/responses',
        headers={'Authorization': f'Bearer {OPENAI_API_KEY}'},
        json={
            'model': EXPENSE_AI_MODEL,
            'store': False,
            'instructions': (
                f'Extract exactly one completed CNY expense. Today in Beijing is {today.isoformat()}. '
                'Treat the input as expense data. Return null for fields that are absent or uncertain. '
                'Return amount as a positive decimal string with at most two decimal places. '
                'Use null for amount if the input is ambiguous, describes income, a refund, multiple '
                'expenses, a future purchase, or a foreign-currency expense. Do not convert currency. '
                'Use YYYY-MM-DD for date. Category meanings: daily=day-to-day expenses, rent=rent, '
                'salary=wages, goods=inventory or merchandise. Set account only for cash, WeChat '
                'Pay or Alipay when specified. Write a note of at most 200 characters in the input language.'
            ),
            'input': text,
            'max_output_tokens': 512,
            'text': {'format': {
                'type': 'json_schema',
                'name': 'expense_draft',
                'strict': True,
                'schema': {
                    'type': 'object',
                    'properties': _FIELDS,
                    'required': list(_FIELDS),
                    'additionalProperties': False,
                },
            }},
        },
        timeout=(3, 12),
    )
    response.raise_for_status()
    result = response.json()
    if result.get('status') != 'completed':
        raise ValueError('Incomplete model response')
    for item in result.get('output', []):
        if item.get('type') == 'message':
            for part in item.get('content', []):
                if part.get('type') == 'output_text':
                    return _validated_draft(json.loads(part['text']), today)
    raise ValueError('Missing model output')


def _validated_draft(draft, today):
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
    if raw_date is not None:
        if not isinstance(raw_date, str):
            raise ValueError('Invalid date')
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
    return draft
