"""Centralized configuration for snail-books-backend.

All environment-dependent values live here — no more scattered os.environ.get().

Usage:
    from shared.config import ADMIN_USER_ID, DB, FLASK_SECRET_KEY, ...

Set APP_ENV to 'production' on the production server. Default is 'staging'.
"""

import os
import secrets as _secrets

APP_ENV = os.environ.get('APP_ENV', 'staging')

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Admin (the only value that MUST differ between staging and production) ──
ADMIN_USER_ID = {
    'staging': '64',
    'production': '4',
}.get(APP_ENV, '64')

# ── WebAuthn ──
WEBAUTHN_RP_ID = {
    'staging': 'test.rowanlan.xyz',
    'production': 'www.rowanlan.xyz',
}.get(APP_ENV, 'test.rowanlan.xyz')

# ── Paths (defaults project-relative; override via env var for custom deployments) ──
DB = os.environ.get('DB', os.path.join(_PROJECT_ROOT, 'data', 'snail.db'))
FRONTEND_DIR = os.environ.get('FRONTEND_DIR', os.path.join(_PROJECT_ROOT, 'static', 'web-build', 'dist'))
EXPENSE_IMG_DIR = os.environ.get('EXPENSE_IMG_DIR', os.path.join(_PROJECT_ROOT, 'expense-imgs'))
BG_DIR = os.environ.get('BG_DIR', os.path.join(_PROJECT_ROOT, 'user-images'))
INVOICE_FILE_DIR = os.environ.get('INVOICE_FILE_DIR', os.path.join(_PROJECT_ROOT, 'invoice-files'))

# ── Secrets ──
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY')
if not FLASK_SECRET_KEY:
    _secret_file = os.path.join(_PROJECT_ROOT, 'data', '.flask_secret')
    if os.path.isfile(_secret_file):
        with open(_secret_file, 'r') as f:
            FLASK_SECRET_KEY = f.read().strip()
    elif APP_ENV != 'production':
        FLASK_SECRET_KEY = _secrets.token_hex(32)
        os.makedirs(os.path.dirname(_secret_file), exist_ok=True)
        with open(_secret_file, 'w') as f:
            f.write(FLASK_SECRET_KEY)
    else:
        if not FLASK_SECRET_KEY:
            raise RuntimeError(
                "FLASK_SECRET_KEY is required in production. "
                "Generate: python3 -c 'import secrets; print(secrets.token_hex(32))'"
            )

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
