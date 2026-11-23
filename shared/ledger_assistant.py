"""Structured assistant examples and short-lived actions on validated ledger plans."""

import re
from datetime import date

from flask import current_app, g
from itsdangerous import BadData, URLSafeTimedSerializer

from shared.app_search import keyword_variants
from shared.ledger_query import (
    answer_ledger_question, latest_question_plan, make_plan, procurement_answer_records,
)

EXAMPLES = {
    'latest_procurement': make_plan('latest_procurement', 'all_time'),
    'procurement_summary': make_plan('procurement_summary', 'this_month'),
    'unsettled_procurement': make_plan('unsettled_procurement', 'all_time'),
    'turnover': make_plan('turnover', 'last_week'),
    'profit': make_plan('profit', 'today'),
    'salary': make_plan('expense', 'this_month', category='salary'),
    'latest_expense': make_plan('latest_expense', 'all_time'),
    'latest_revenue': make_plan('latest_revenue', 'all_time'),
    'latest_reconciliation': make_plan('latest_reconciliation', 'all_time'),
    'latest_invoice': make_plan('latest_invoice', 'all_time'),
    'dividends': make_plan('dividend_amount', 'all_time'),
    'shareholders': make_plan('shareholders', 'all_time'),
    'users': make_plan('registered_users', 'all_time'),
}
_PERIOD_ACTIONS = ('this_month', 'last_month')
_ORDER_METRICS = ('procurement_summary', 'unsettled_procurement')
_PAGE_SIZE = 8


def _serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='ledger-assistant-actions')


def _pack(plan, today, actions):
    return _serializer().dumps({
        'uid': str(g.user_id), 'plan': plan, 'date': today.isoformat(), 'actions': actions,
    })


def read_context(token):
    if not isinstance(token, str) or len(token) > 2400:
        return None
    try:
        context = _serializer().loads(token, max_age=1800)
    except BadData:
        return None
    return context if context['uid'] == str(g.user_id) else None


def question_action(question):
    text = re.sub(r'\s+', '', question.lower()).rstrip('？?。.!！')
    for variant in keyword_variants(text):
        if variant in ('上月', '上个月', '上个月呢', 'lastmonth', 'whataboutlastmonth'):
            return 'last_month'
        if variant in ('本月', '这个月', '这个月呢', '本月呢', 'thismonth', 'whataboutthismonth'):
            return 'this_month'
        if variant in ('和上月比', '和上个月比', '跟上月比', '与上月比', 'comparetolastmonth', 'comparewithlastmonth'):
            return 'compare_last_month'
    return None


def direct_question(question):
    known = latest_question_plan(question)
    if known:
        return known
    text = re.sub(r'\s+', '', question.lower()).rstrip('？?。.!！')
    for variant in keyword_variants(text):
        if variant in ('还有几单没结算', '还有几单进货没结算', '还有几单没清账',
                       'howmanypurchaseordersarestillunsettled'):
            return EXAMPLES['unsettled_procurement']
        match = re.fullmatch(r'(本月|这个月|上月|上个月)?采购(?:一共|总共)?(?:花了|用了)多少(?:钱)?', variant)
        if match:
            period = None
            if match[1]:
                period = 'last_month' if match[1].startswith('上') else 'this_month'
            return clarification('procurement_basis', make_plan(None, period), ('order_value', 'posted_expense'))
    if text == 'howmuchdidwespendonpurchases':
        return clarification('procurement_basis', make_plan(None, None), ('order_value', 'posted_expense'))
    return None


def clarification(reason, plan, actions):
    # The route attaches the account-bound context before sending the options.
    return {'reason': reason, 'plan': plan, 'actions': actions}


def clarification_response(choice, today):
    actions = choice['actions']
    return {'status': 'clarify', 'reason': choice['reason'], 'actions': actions,
            'context': _pack(choice['plan'], today, actions)}


def _month_actions(plan, today, *, is_admin):
    actions = []
    for period in _PERIOD_ACTIONS:
        candidate = {**plan, 'period': period, 'start_date': None, 'end_date': None, 'compare_period': None}
        if answer_ledger_question(candidate, today, is_admin=is_admin, record_queries=True, validate_only=True):
            actions.append(period)
    return actions


def answer_response(plan, today, *, is_admin):
    answer = answer_ledger_question(plan, today, is_admin=is_admin, record_queries=True)
    if answer is None:
        if isinstance(plan, dict) and plan.get('period') is None and plan.get('compare_period') is None:
            actions = _month_actions(plan, today, is_admin=is_admin)
            if actions:
                return clarification_response(clarification('period', plan, actions), today)
        return None

    actions = []
    if 'current' in answer:
        actions = [period for period in _month_actions(plan, today, is_admin=is_admin) if period != plan['period']]
        if plan['period'] == 'this_month' and plan['compare_period'] is None and 'last_month' in actions:
            actions.insert(0, 'compare_last_month')
        if answer['metric'] in _ORDER_METRICS and answer['current']['records']:
            actions.append('details')
    return {'status': 'ok', 'answer': answer, 'actions': actions,
            'context': _pack(plan, today, actions) if actions else None}


def apply_action(context, action, payload, *, is_admin):
    plan = context['plan']
    today = date.fromisoformat(context['date'])
    if action == 'details':
        offset = payload.get('offset', 0)
        if type(offset) is not int or offset < 0 or offset > 100_000:
            return None
        records = procurement_answer_records(plan, today, offset, _PAGE_SIZE + 1)
        return {'status': 'details', 'records': records[:_PAGE_SIZE],
                'next_offset': offset + _PAGE_SIZE if len(records) > _PAGE_SIZE else None}
    if action in ('order_value', 'posted_expense'):
        plan = make_plan('procurement_summary', plan['period']) if action == 'order_value' else make_plan(
            'expense', plan['period'], category='goods',
        )
    elif action == 'compare_last_month':
        plan = {**plan, 'compare_period': 'last_month'}
    else:
        plan = {**plan, 'period': action, 'start_date': None, 'end_date': None, 'compare_period': None}
    return answer_response(plan, today, is_admin=is_admin)
