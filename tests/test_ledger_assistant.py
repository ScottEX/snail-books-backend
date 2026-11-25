"""Regression tests for the assistant's question and expense draft endpoints."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch


class LedgerAssistantTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        root = cls.directory.name
        cls.env = patch.dict(os.environ, {
            'APP_ENV': 'staging', 'FLASK_SECRET_KEY': 'ledger-tests-only', 'DB': f'{root}/ledger.sqlite',
            'BG_DIR': f'{root}/backgrounds', 'EXPENSE_IMG_DIR': f'{root}/images',
            'INVOICE_FILE_DIR': f'{root}/invoices', 'DEEPSEEK_API_KEY': '', 'OPENAI_API_KEY': '',
        })
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        import app as app_module
        import shared.db as db_module
        from shared.config import ADMIN_USER_ID
        from shared.db import get_db
        cls.enterClassContext(patch.object(app_module, 'DB', os.environ['DB']))
        cls.enterClassContext(patch.object(db_module, 'DB_PATH', os.environ['DB']))
        app_module.init_db()
        cls.app, cls.admin_id = app_module.app, int(ADMIN_USER_ID)
        with get_db() as db:
            db.executemany(
                "INSERT INTO users (id, username, password, is_verified, created_at) VALUES (?, ?, 'fixture', 1, ?)",
                [(1, 'member', '2026-11-10 10:20:00'), (2, 'other', '2026-10-08 08:30:00'),
                 (cls.admin_id, 'admin', '2026-11-01 09:00:00')],
            )
            db.executemany(
                "INSERT INTO reconciliations (id, date, bill_date) VALUES (?, ?, ?)",
                [(1, '2026-11-22', '2026-11-21'), (2, '2026-11-23', '2026-10-31')],
            )
            db.executemany(
                "INSERT INTO procurement_batches (batch_number, date, total, settled_at) VALUES (?, ?, ?, ?)",
                [(i, '2026-11-23', i * 10, None) for i in range(1, 11)] + [
                    (11, '2026-11-23', 30, '2026-11-23 10:00:00'),
                    (12, '2026-10-01', 15, '2026-10-01 17:30:00'),
                    (13, '2026-11-24', 1000, None),
                ],
            )
            db.execute("INSERT INTO products(id,name,supplier) VALUES (101,'recorded item','特定供應商')")
            db.executemany(
                "INSERT INTO procurement_items(batch_id,product_id,subtotal) VALUES (?,101,?)",
                [(1, 2), (1, 3), (2, 7), (11, 13), (13, 120)],
            )
            db.execute("INSERT INTO daily_revenue (date, turnover, revenue, jd_revenue) VALUES ('2026-11-23', 900, 700, 100)")
            db.execute("INSERT INTO transactions (type, amount, category, account, date) VALUES ('expense', 40, 'salary', 'payCash', '2026-11-23')")
            db.execute("INSERT INTO invoice_records (type, invoice_number, date) VALUES ('vat', 'A-01', '2026-11-22')")
            db.execute("INSERT INTO dividends (partner, amount, note, date) VALUES ('Fixture', 100, 'R1', '2026-11-20 08:00:00')")

    def setUp(self):
        clock = patch('routes.data.datetime', wraps=datetime)
        clock.start().now.return_value = datetime(2026, 11, 22, 17, tzinfo=timezone.utc)
        self.addCleanup(clock.stop)

    def client(self, uid=1):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = uid
        return client

    def request(self, client, status=200, **payload):
        response = client.post('/api/ledger/ask', json={'assistant_v2': True, **payload})
        self.assertEqual(response.status_code, status, response.get_json())
        return response.get_json()

    def test_all_examples_work_without_a_provider(self):
        from shared.ledger_assistant import EXAMPLES
        client = self.client(self.admin_id)
        expected = {
            'latest_procurement': 'latest_procurement', 'procurement_summary': 'procurement_summary',
            'unsettled_procurement': 'unsettled_procurement', 'turnover': 'turnover', 'profit': 'profit',
            'salary': 'expense', 'latest_expense': 'latest_expense', 'latest_revenue': 'latest_revenue',
            'latest_reconciliation': 'latest_reconciliation', 'latest_invoice': 'latest_invoice',
            'dividends': 'dividend_amount', 'shareholders': 'shareholders', 'users': 'registered_users',
        }
        self.assertEqual(expected.keys(), EXAMPLES.keys())
        for example, metric in expected.items():
            with self.subTest(example=example):
                result = self.request(client, example=example)
                self.assertEqual(result['answer']['metric'], metric)
        self.request(self.client(), status=403, example='users')
        self.request(self.app.test_client(), status=401, example='latest_reconciliation')

    def test_latest_reconciliation_uses_submission_date(self):
        for text in ('上次对账是哪一天', '上次對帳是哪一天？', '上次对账是哪天？'):
            with self.subTest(text=text):
                result = self.request(self.client(), question=text)
                self.assertEqual(result['answer']['record']['date'], '2026-11-23')
                self.assertEqual(result['answer']['record']['id'], 2)

    def test_registrations_answer_count_names_and_dates(self):
        client = self.client(self.admin_id)
        for text in ('有多少注册用户，分别叫什么', '有多少註冊使用者，分別叫什麼？'):
            with self.subTest(text=text):
                result = self.request(client, question=text)
                answer = result['answer']
                self.assertEqual(answer['current']['amount'], 3)
                self.assertEqual(answer['registrations']['users'][0], {
                    'id': 1, 'username': 'member', 'created_at': '2026-11-10 10:20:00',
                })
                self.assertEqual([row['username'] for row in answer['registrations']['users']],
                                 ['member', 'admin', 'other'])
                self.assertIsNone(answer['registrations']['next_offset'])
        self.request(self.client(), status=403, question='有多少注册用户，分别叫什么')

    def test_registration_follow_up_keeps_period_and_admin_scope(self):
        client = self.client(self.admin_id)
        initial = self.request(client, question='本月注册用户有多少')
        self.assertNotIn('registrations', initial['answer'])
        with patch('shared.ledger_query._REGISTRATION_PAGE_SIZE', 1):
            result = self.request(client, context=initial['context'], question='分别是什么时候注册的')
            answer = result['answer']
            self.assertEqual(answer['current']['start_date'], '2026-11-01')
            self.assertEqual(answer['current']['amount'], 2)
            page = answer['registrations']
            self.assertEqual([row['username'] for row in page['users']], ['member'])
            more = self.request(client, action='registration_details', context=result['context'],
                                offset=page['next_offset'])['answer']['registrations']
            self.assertEqual([row['username'] for row in more['users']], ['admin'])
            self.assertIsNone(more['next_offset'])
        self.request(self.client(), status=422, action='registration_details', context=result['context'])
        self.request(client, status=422, action='registration_details', context=result['context'], offset=-1)

    def test_chat_executes_multiple_tools_and_inherits_registration_scope(self):
        client = self.client(self.admin_id)
        def send(request_id, text, revision=None):
            response = client.post('/api/assistant/chat', json={
                'request_id': request_id, 'text': text, 'revision': revision,
            })
            self.assertEqual(response.status_code, 200, response.get_json())
            return response.get_json()

        with patch('shared.assistant_chat.ai_configured', return_value=True), patch(
            'shared.assistant_chat.request_json',
            side_effect=[
                {'calls': [
                    {'tool': 'query_registered_users', 'metric': 'registered_user_details', 'period': 'this_month'},
                    {'tool': 'find_app_destinations', 'route': 'userManagement'},
                ], 'message': None},
                {'calls': [{'tool': 'query_registered_users', 'metric': 'registered_user_details',
                            'use_previous': True}], 'message': None},
                {'calls': [{'tool': 'find_app_destinations', 'route': 'profile'}], 'message': '可以打开个人中心。'},
            ],
        ) as provider:
            first = send('chat-test-01', '这个月注册用户有多少，名字是什么')
            second = send('chat-test-02', '他们什么时候注册的？', first['revision'])
            third = send('chat-test-03', '我想看看我的账户资料', second['revision'])
            duplicate = send('chat-test-03', '我想看看我的账户资料', second['revision'])
            self.assertEqual(provider.call_count, 3)
            inputs = json.loads(provider.call_args_list[1].args[0])
            self.assertEqual(inputs['previous_query']['period'], 'this_month')
            self.assertEqual(inputs['recent'][0]['user'], '这个月注册用户有多少，名字是什么')
            self.assertNotIn('2026-11-10 10:20:00', provider.call_args_list[1].args[0])

        for state in (first, second):
            turn = state['turns'][-1]
            self.assertEqual(turn['destinations'], ['userManagement'])
            answer = turn['answers'][0]['answer']
            self.assertEqual(answer['current']['amount'], 2)
            self.assertEqual([row['username'] for row in answer['registrations']['users']], ['member', 'admin'])
        self.assertEqual(third['turns'][-1]['destinations'], ['profile'])
        self.assertEqual(third, duplicate)
        self.assertEqual(client.get('/api/assistant/chat').get_json(), third)
        self.assertEqual(self.client().get('/api/assistant/chat').get_json(), {'revision': None, 'turns': []})
        stale = client.post('/api/assistant/chat', json={
            'revision': first['revision'], 'request_id': 'chat-stale-1', 'text': 'another device',
        })
        self.assertEqual(stale.status_code, 409)
        reset = client.delete('/api/assistant/chat', json={'revision': third['revision'], 'request_id': 'chat-reset-1'})
        self.assertEqual(reset.get_json(), {'revision': 'chat-reset-1', 'turns': []})

    def test_chat_rejects_privileged_tools_and_unknown_destinations(self):
        with patch('shared.assistant_chat.ai_configured', return_value=True), patch(
            'shared.assistant_chat.request_json',
            return_value={'calls': [
                {'tool': 'find_app_destinations', 'route': 'https://example.com'},
                {'tool': 'find_app_destinations', 'route': 'userManagement'},
                {'tool': 'query_registered_users', 'metric': 'registered_user_details'},
            ], 'message': None},
        ):
            result = self.client().post('/api/assistant/chat', json={
                'request_id': 'chat-test-denied', 'text': '给我用户名单', 'revision': None,
            })
        self.assertEqual(result.status_code, 200)
        turn = result.get_json()['turns'][0]
        self.assertEqual((turn['answers'], turn['destinations'], turn['notice']), ([], [], 'ledgerAiAdminOnly'))
        self.assertEqual(self.app.test_client().get('/api/assistant/chat').status_code, 401)

    def test_expense_draft_resolves_explicit_relative_dates_in_beijing(self):
        client = self.client()
        clock_time = datetime(2026, 11, 22, 17, tzinfo=timezone.utc)
        draft = {'amount': '86.50', 'category': 'daily', 'account': 'payWechat', 'note': '买菜'}
        cases = (
            ('我今天买菜花了86.50元，微信付的', None, '2026-11-23'),
            ('今天下午买菜花了86元', '2026-11-22', '2026-11-23'),
            ('昨天下午買菜花了86元', None, '2026-11-22'),
            ('前天买菜花了86元', None, '2026-11-21'),
            ('买菜花了86元', None, None),
            ('昨天买的菜，今天补记86元', '2026-11-22', '2026-11-22'),
            ('11月20日买的菜，今天补记86元', '2026-11-20', '2026-11-20'),
        )
        with patch('routes.transactions.ai_configured', return_value=True), patch(
            'routes.transactions.reserve_ai_request', return_value=True,
        ), patch('routes.transactions.datetime', wraps=datetime) as clock, patch(
            'shared.expense_ai.request_json',
        ) as provider:
            clock.now.side_effect = clock_time.astimezone
            for text, model_date, expected_date in cases:
                with self.subTest(text=text):
                    provider.return_value = {**draft, 'date': model_date}
                    response = client.post('/api/transactions/expense-draft', json={'text': text})
                    self.assertEqual(response.status_code, 200, response.get_json())
                    self.assertEqual(response.get_json()['draft']['date'], expected_date)
                    self.assertEqual(response.get_json()['draft']['amount'], '86.50')
            provider.return_value = {**draft, 'amount': None, 'date': None}
            response = client.post('/api/transactions/expense-draft', json={'text': '今天买菜，忘了金额'})
            self.assertEqual(response.status_code, 422)

    def test_clarify_then_compare_or_follow_up(self):
        client = self.client()
        basis = self.request(client, question='采购花了多少')
        self.assertEqual(basis['reason'], 'procurement_basis')
        period = self.request(client, action='order_value', context=basis['context'])
        self.assertEqual(period['reason'], 'period')
        current = self.request(client, action='this_month', context=period['context'])
        self.assertEqual(current['answer']['current']['amount'], 580)
        comparison = self.request(client, action='compare_last_month', context=current['context'])
        self.assertEqual(comparison['answer']['comparison']['amount'], 15)
        self.assertEqual(comparison['answer']['difference'], 565)
        last_month = self.request(client, question='上个月呢', context=comparison['context'])
        self.assertEqual(last_month['answer']['current']['amount'], 15)
        self.request(self.client(2), status=422, action='this_month', context=period['context'])
        self.request(client, status=422, action='this_month', context=period['context'] + 'x')

    def test_unsettled_details_match_the_summary(self):
        client = self.client()
        answer = self.request(client, question='还有几单没结算')
        self.assertEqual(answer['answer']['current']['records'], 10)
        first = self.request(client, action='details', context=answer['context'])
        second = self.request(client, action='details', context=answer['context'], offset=first['next_offset'])
        rows = first['records'] + second['records']
        self.assertEqual(len({row['id'] for row in rows}), 10)
        self.assertEqual(sum(row['amount'] for row in rows), answer['answer']['current']['amount'])
        self.assertTrue(all(not row['settled'] and row['date'] <= '2026-11-23' for row in rows))
        self.assertIsNone(second['next_offset'])
        self.request(client, status=422, action='details', context=answer['context'], offset=-1)

    def test_typed_provider_plan_keeps_supplier_filter(self):
        from shared.ledger_query import make_plan
        client = self.client()
        with patch('routes.data.ai_configured', return_value=True), patch(
            'shared.ledger_query.request_json',
            return_value=make_plan('unsettled_procurement', 'all_time', supplier='特定供应商'),
        ):
            answer = self.request(client, question='特定供应商的未结算货款有多少？')
        details = self.request(client, action='details', context=answer['context'])
        self.assertEqual(answer['answer']['current']['amount'], 12)
        self.assertEqual({row['id']: row['amount'] for row in details['records']}, {2: 7, 1: 5})
