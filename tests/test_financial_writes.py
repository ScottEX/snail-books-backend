"""Exercise financial writes against an isolated copy of the application schema."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class FinancialWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        root = Path(directory.name)
        env = patch.dict(os.environ, {
            'APP_ENV': 'staging', 'FLASK_SECRET_KEY': 'financial-test-only', 'DB': str(root / 'staging.sqlite'),
            'BG_DIR': str(root / 'bg'), 'EXPENSE_IMG_DIR': str(root / 'img'), 'INVOICE_FILE_DIR': str(root / 'invoice'),
        })
        env.start()
        cls.addClassCleanup(env.stop)
        import app as app_module
        import shared.db as db_module
        cls.enterClassContext(patch.object(app_module, 'DB', os.environ['DB']))
        cls.enterClassContext(patch.object(db_module, 'DB_PATH', os.environ['DB']))
        app_module.init_db()
        cls.app = app_module.app

    def setUp(self):
        from shared.db import get_db
        with get_db() as db:
            for table in ('write_requests', 'transactions', 'platform_fee_entries', 'platform_fees',
                          'procurement_items', 'procurement_batches', 'dividends', 'partners', 'invoice_records', 'daily_revenue'):
                db.execute(f'DELETE FROM {table}')
            db.executemany(
                """INSERT OR IGNORE INTO users(id, username, password, is_verified) VALUES (?, ?, 'fixture', 1)""",
                [(15001, 'member-one'), (15002, 'member-two')],
            )
            db.executemany('INSERT INTO partners (name, share) VALUES (?, ?)', [('A', .5), ('B', .25), ('C', .25)])
            self.product_id = db.execute('SELECT id FROM products ORDER BY id LIMIT 1').fetchone()[0]

    def post(self, path, payload, key=None, uid=15001):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = uid
        return client.post('/api/' + path, json=payload, headers={'Idempotency-Key': key} if key is not None else {})

    def count(self, table):
        from shared.db import get_db
        with get_db() as db:
            return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def test_transaction_key_is_scoped_to_user_and_matches_payload(self):
        data = {'type': 'expense', 'amount': 15.14, 'category': 'daily', 'account': 'payCash', 'date': '2026-11-22'}
        first = self.post('transactions', data, 'tx-key')
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(self.post('transactions', dict(reversed(list(data.items()))), 'tx-key').get_json(), first.get_json())
        self.assertEqual(self.post('transactions', {**data, 'amount': 99}, 'tx-key').status_code, 409)
        self.assertEqual(self.post('transactions', data, 'tx-key', uid=15002).status_code, 200)
        self.assertEqual(self.post('transactions', data, 'next-tx').status_code, 200)
        self.assertEqual(self.post('transactions', data, 'invalid key').status_code, 400)
        self.assertEqual(self.post('transactions', data).status_code, 200)
        self.assertEqual(self.count('transactions'), 4)

    def test_fee_retry_after_partial_failure_and_concurrent_retry(self):
        from shared.db import get_db
        data = {'year': 2026, 'month': 11, 'entry_date': '2026-11-22', 'meituan_cashier': 10}
        with get_db() as db:
            db.execute("""CREATE TRIGGER fail_fee_entry BEFORE INSERT ON platform_fee_entries
                          BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        try:
            first = self.post('platform-fees/entry', data, 'fee-key')
            self.assertEqual(first.status_code, 500)
            self.assertEqual(self.count('platform_fees'), 0)
            self.assertEqual(self.count('write_requests'), 0)
        finally:
            with get_db() as db:
                db.execute('DROP TRIGGER fail_fee_entry')
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: self.post('platform-fees/entry', data, 'fee-key'), range(2)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual(responses[0].get_json(), responses[1].get_json())
        self.assertEqual(responses[0].get_json()['data']['meituan_cashier'], 10)
        self.assertEqual(self.count('platform_fee_entries'), 1)
        self.assertEqual(self.count('write_requests'), 1)

    def test_procurement_replay_keeps_batch_number_and_linked_expense(self):
        data = {'date': '2026-11-22', 'payment_method': 'payCash', 'items': [{'product_id': self.product_id, 'quantity': 2}]}
        first = self.post('procurement-batches', data, 'order-key')
        self.assertEqual(first.status_code, 200, first.get_json())
        self.assertEqual(self.post('procurement-batches', data, 'order-key').get_json(), first.get_json())
        self.assertEqual(self.count('procurement_batches'), 1)
        self.assertEqual(self.count('procurement_items'), 1)
        self.assertEqual(self.count('transactions'), 1)

    def test_dividend_respects_current_shares_and_exact_cents(self):
        from shared.db import get_db
        total = '10.03'
        preview = self.post('dividends/preview', {'total': total}).get_json()
        self.assertEqual([item['amount'] for item in preview['allocations']], [5.01, 2.51, 2.51])
        with get_db() as db:
            db.execute("UPDATE partners SET share=0.4 WHERE name='A'")
            db.execute("UPDATE partners SET share=0.35 WHERE name='B'")
        old = {'total': total, 'round': 100, 'preview': preview['preview']}
        self.assertEqual(self.post('dividends', old, 'div').get_json()['message'], 'dividendPreviewChanged')
        self.assertEqual(self.count('dividends'), 0)
        fresh = self.post('dividends/preview', {'total': total}).get_json()
        data = {**old, 'preview': fresh['preview']}
        saved = self.post('dividends', data, 'div')
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(saved.get_json(), fresh)
        self.assertEqual(self.post('dividends', data, 'div').get_json(), fresh)
        self.assertEqual(self.post('dividends', data, 'another-div').status_code, 409)
        with get_db() as db:
            rows = db.execute("SELECT amount FROM dividends WHERE note='第100次' ORDER BY id").fetchall()
            self.assertEqual([row['amount'] for row in rows], [4.01, 3.51, 2.51])
            self.assertEqual(sum(Decimal(str(row['amount'])) for row in rows), Decimal(total))
            db.execute("UPDATE partners SET share=0.41 WHERE name='A'")
        self.assertEqual(self.post('dividends/preview', {'total': total}).get_json()['message'], 'dividendInvalidShares')
        self.assertEqual(self.post('dividends/preview', {'total': '10.001'}).status_code, 400)

    def test_legacy_dividend_request_still_works(self):
        response = self.post('dividends', {'items': [{'partner': 'A', 'amount': 2.5, 'note': 'legacy'}]})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.count('dividends'), 1)

    def test_invoice_resumes_and_only_notifies_after_finalizing(self):
        from io import BytesIO
        from PIL import Image
        from shared.db import get_db

        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = 15001

        def upload(rid, filename, key, *, valid=True):
            image = BytesIO()
            if valid:
                Image.new('RGB', (2, 2)).save(image, 'PNG')
                image.seek(0)
            return client.post(f'/api/invoice-records/{rid}/file', data={'file': (image, filename)},
                               headers={'Idempotency-Key': key})

        with TemporaryDirectory() as uploads, patch('routes.invoice.INVOICE_FILE_DIR', uploads), patch(
            'routes.invoice._send_invoice_email',
        ) as send_email:
            pending = {'type': 'general', 'amount': 100, 'date': '2026-11-22', 'company': 'Example',
                       'invoice_number': '10001', 'email': 'example@example.test', 'status': 'pending',
                       'defer_notification': True}
            created = self.post('invoice-records', pending, 'invoice-create')
            self.assertEqual(created.status_code, 200, created.get_json())
            rid = created.get_json()['id']
            self.assertEqual(self.post('invoice-records', pending, 'invoice-create').get_json(), created.get_json())
            send_email.assert_not_called()
            final = {'status': 'done', 'invoice_number': '10001'}
            response = client.put(f'/api/invoice-records/{rid}', json=final)
            self.assertEqual(response.status_code, 400, response.get_json())
            finalization = {'status': 'done', 'file_path': '["other/invoice.pdf"]'}
            self.assertEqual(client.put(f'/api/invoice-records/{rid}', json=finalization).status_code, 400)
            first = upload(rid, 'first.png', 'file-1')
            self.assertEqual(first.status_code, 200, first.get_json())
            self.assertEqual(upload(rid, 'first.png', 'file-1').get_json(), first.get_json())
            self.assertEqual(upload(rid, 'second.png', 'file-2', valid=False).status_code, 400)
            stored = client.get(f'/api/invoice-records/{rid}').get_json()
            self.assertEqual(stored['status'], 'pending')
            self.assertEqual(len(json.loads(stored['file_path'])), 1)
            send_email.assert_not_called()
            second = upload(rid, 'second.png', 'file-2')
            self.assertEqual(second.status_code, 200, second.get_json())
            for _ in range(2):
                self.assertEqual(client.put(f'/api/invoice-records/{rid}', json=final).status_code, 200)
            send_email.assert_called_once()
            self.assertEqual(send_email.call_args.args[:3], ('example@example.test', 'done', '10001'))
            with get_db() as db:
                row = db.execute('SELECT status, file_path FROM invoice_records WHERE id=?', (rid,)).fetchone()
                self.assertEqual(row['status'], 'done')
                self.assertEqual(json.loads(row['file_path']), [first.get_json()['file_path'], second.get_json()['file_path']])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM invoice_records').fetchone()[0], 1)
            self.assertEqual(len(list(Path(uploads).rglob('*.jpg'))), 4)

    def test_operating_dashboard_matches_ai_and_late_month_fee_coverage(self):
        from datetime import datetime, timezone
        from shared.db import get_db

        with get_db() as db:
            db.executemany('INSERT INTO daily_revenue(date,turnover,revenue,jd_revenue) VALUES (?,?,?,?)',
                           [('2026-11-23', 200, 140, 20), ('2026-10-07', 80, 60, None)])
            db.execute("""INSERT INTO transactions(type,category,account,amount,date)
                          VALUES ('expense','daily','payCash',32,'2026-11-23')""")
            db.execute("INSERT INTO platform_fees(year,month,meituan_cashier) VALUES (2026,10,15)")
            month_id = db.execute("INSERT INTO platform_fees(year,month,meituan_cashier) VALUES (2026,11,40)").lastrowid
            db.executemany('INSERT INTO platform_fee_entries(fee_id,entry_date,meituan_cashier) VALUES (?,?,?)',
                           [(month_id, '2026-11-23', 10), (month_id, '2026-11-04', 7)])
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user_id'] = 15001
        with patch('routes.data.datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 22, 17, tzinfo=timezone.utc)
            today = client.get('/api/business-summary').get_json()['operating']['today']
            self.assertEqual(today['turnover'], 200)
            self.assertEqual(today['received'], 160)
            self.assertEqual(today['net_received'], 150)
            self.assertEqual(today['operating_profit'], 118)
            self.assertFalse(today['fees_complete'])
            for question, metric in [('今天营业额', 'turnover'), ('今天实收', 'received'),
                                     ('今天扣费后收入', 'net_received'), ('今天经营利润是多少', 'operating_profit')]:
                answer = self.post('ledger/ask', {'assistant_v2': True, 'operating_metrics': True,
                                                 'question': question}).get_json()['answer']
                self.assertEqual(answer['metric'], metric)
                self.assertEqual(answer['current']['amount'], today[metric])
            answer = self.post('ledger/ask', {'assistant_v2': True, 'operating_metrics': True,
                                             'question': '上月扣费后收入'}).get_json()['answer']['current']
            self.assertEqual(answer['amount'], 45)
            self.assertTrue(answer['fees_complete'])
            old = self.post('ledger/ask', {'assistant_v2': True, 'example': 'profit'}).get_json()['answer']
            self.assertEqual(old['metric'], 'profit')
            self.assertEqual(old['current']['amount'], 128)
