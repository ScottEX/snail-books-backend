"""Exercise financial writes against an isolated copy of the application schema."""

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
                          'procurement_items', 'procurement_batches', 'dividends', 'partners'):
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
