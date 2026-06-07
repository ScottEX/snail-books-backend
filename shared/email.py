"""Email utilities — Resend HTTP API integration with dev-mode fallback."""

import os
import requests

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
DEV_MODE = not RESEND_API_KEY

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), '..', 'templates', 'email')


def _load_template(name):
    """Load an email HTML template from file and return its content."""
    path = os.path.join(_TEMPLATE_DIR, f'{name}.html')
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def _send_email(to_email, subject, body, code):
    """Send email: dev mode returns code to frontend, production uses Resend API."""
    if not RESEND_API_KEY:
        print(f"[EMAIL] Dev mode: code={code} for {to_email} ({subject})")
        return True
    try:
        r = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={'from': RESEND_FROM, 'to': [to_email], 'subject': subject, 'html': body},
            timeout=15,
        )
        if r.status_code == 200:
            resp_data = r.json()
            if resp_data.get('id'):
                print(f"[EMAIL] Sent to {to_email}: {subject}")
                return True
            print(f"[EMAIL] Resend API error: {r.text}")
            return False
        print(f"[EMAIL] Resend error {r.status_code}: {r.text}")
        return False
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False


def send_verification_email(to_email, code, lang='zh-CN'):
    subjects = {
        'zh-CN': f'【柳味探秘】您的账户注册验证码：{code}',
        'zh-TW': f'【柳味探秘】您的帳戶註冊驗證碼：{code}',
        'en': f'[LiuWei TanMi] Your Account Registration Code: {code}',
    }
    template_name = f'verify_{lang}' if lang in ('zh-CN', 'zh-TW', 'en') else 'verify_zh-CN'
    body = _load_template(template_name).format(code=code)
    subject = subjects.get(lang, subjects['zh-CN'])
    return _send_email(to_email, subject, body, code)


def send_reset_email(to_email, code, lang='zh-CN'):
    subjects = {
        'zh-CN': f'【柳味探秘】密码重置验证码：{code}',
        'zh-TW': f'【柳味探秘】密碼重置驗證碼：{code}',
        'en': f'[LiuWei TanMi] Password Reset Code: {code}',
    }
    template_name = f'reset_{lang}' if lang in ('zh-CN', 'zh-TW', 'en') else 'reset_zh-CN'
    body = _load_template(template_name).format(code=code)
    subject = subjects.get(lang, subjects['zh-CN'])
    return _send_email(to_email, subject, body, code)


def send_email_change_code(to_email, code, lang='zh-CN'):
    subjects = {
        'zh-CN': f'【柳味探秘】邮箱更换验证码：{code}',
        'en': f'[LiuWei TanMi] Email Change Code: {code}',
    }
    template_name = f'change_{lang}' if lang in ('zh-CN', 'en') else 'change_zh-CN'
    body = _load_template(template_name).format(code=code)
    subject = subjects.get(lang, subjects['zh-CN'])
    return _send_email(to_email, subject, body, code)


def generate_code():
    import secrets
    return ''.join(secrets.choice('0123456789') for _ in range(6))
