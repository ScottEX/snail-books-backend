"""Email utilities — Resend HTTP API integration with dev-mode fallback."""

import os, requests

RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
RESEND_FROM = os.environ.get('RESEND_FROM', 'onboarding@resend.dev')
DEV_MODE = not RESEND_API_KEY


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
    templates = {
        'zh-CN': {
            'subject': f'【柳味探秘】您的账户注册验证码：{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好！您正在注册柳味探秘科技账户，以下是您的电子邮箱验证码：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">验证码有效期为 10 分钟。请在注册页面输入此验证码以完成身份验证。</p>
        <p style="color:#9C9A95;font-size:12px">提示：如果这不是您本人的操作，可能是其他用户不小心输入了您的邮箱，您可以安全地忽略此邮件，您的账户不会受到任何影响。</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技团队</p>
    </div>'''
        },
        'zh-TW': {
            'subject': f'【柳味探秘】您的帳戶註冊驗證碼：{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好！您正在註冊柳味探秘科技帳戶，以下是您的電子郵箱驗證碼：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">驗證碼有效期為 10 分鐘。請在註冊頁面輸入此驗證碼以完成身份驗證。</p>
        <p style="color:#9C9A95;font-size:12px">提示：如果這不是您本人的操作，可能是其他用戶不小心輸入了您的郵箱，您可以安全地忽略此郵件，您的帳戶不會受到任何影響。</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技團隊</p>
    </div>'''
        },
        'en': {
            'subject': f'[LiuWei TanMi] Your Account Registration Code: {code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">LiuWei TanMi</h2>
        <p>Hello! You are registering a LiuWei TanMi account. Here is your email verification code:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">This code is valid for 10 minutes. Please enter it on the registration page to complete verification.</p>
        <p style="color:#9C9A95;font-size:12px">Note: If this wasn't you, someone may have accidentally entered your email. You can safely ignore this message — your account will not be affected.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">LiuWei TanMi Team</p>
    </div>'''
        },
    }
    t = templates.get(lang, templates['zh-CN'])
    return _send_email(to_email, t['subject'], t['body'], code)


def send_reset_email(to_email, code, lang='zh-CN'):
    templates = {
        'zh-CN': {
            'subject': f'【柳味探秘】密码重置验证码：{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好！您正在为柳味探秘科技账户重置密码，以下是您的验证码：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">验证码有效期为 10 分钟。请在重置密码页面输入此验证码以完成操作。</p>
        <p style="color:#9C9A95;font-size:12px">提示：如果这不是您本人的操作，您可以安全地忽略此邮件，您的账户不会受到任何影响。</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技团队</p>
    </div>'''
        },
        'zh-TW': {
            'subject': f'【柳味探秘】密碼重置驗證碼：{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您好！您正在為柳味探秘科技帳戶重置密碼，以下是您的驗證碼：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">驗證碼有效期為 10 分鐘。請在重置密碼頁面輸入此驗證碼以完成操作。</p>
        <p style="color:#9C9A95;font-size:12px">提示：如果這不是您本人的操作，您可以安全地忽略此郵件，您的帳戶不會受到任何影響。</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">柳味探秘科技團隊</p>
    </div>'''
        },
        'en': {
            'subject': f'[LiuWei TanMi] Password Reset Code: {code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">LiuWei TanMi</h2>
        <p>Hello! You are resetting your LiuWei TanMi account password. Here is your verification code:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">This code is valid for 10 minutes. Please enter it on the password reset page to complete the process.</p>
        <p style="color:#9C9A95;font-size:12px">Note: If this wasn't you, you can safely ignore this message — your account will not be affected.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0">
        <p style="color:#B0B0B0;font-size:11px">LiuWei TanMi Team</p>
    </div>'''
        },
    }
    t = templates.get(lang, templates['zh-CN'])
    return _send_email(to_email, t['subject'], t['body'], code)


def send_email_change_code(to_email, code, lang='zh-CN'):
    templates = {
        'zh-CN': {
            'subject': f'【柳味探秘】邮箱更换验证码：{code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">柳味探秘科技</h2>
        <p>您正在更换账户的绑定邮箱，验证码如下：</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">验证码有效期为 10 分钟。</p>
        <p style="color:#9C9A95;font-size:12px">如非本人操作，请忽略此邮件。</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0"><p style="color:#B0B0B0;font-size:11px">柳味探秘科技团队</p></div>'''
        },
        'en': {
            'subject': f'[LiuWei TanMi] Email Change Code: {code}',
            'body': f'''<div style="max-width:400px;margin:0 auto;font-family:sans-serif">
        <h2 style="color:#8B1E22">LiuWei TanMi</h2>
        <p>You are changing your account's email address. Your verification code is:</p>
        <h1 style="font-size:36px;letter-spacing:8px;color:#1C1C1C;background:#F7F5F2;padding:16px;border-radius:12px;text-align:center">{code}</h1>
        <p style="color:#9C9A95;font-size:13px">This code expires in 10 minutes.</p>
        <p style="color:#9C9A95;font-size:12px">If this wasn't you, please ignore this email.</p>
        <hr style="border:0;border-top:1px solid #EBEBEB;margin:20px 0"><p style="color:#B0B0B0;font-size:11px">LiuWei TanMi Team</p></div>'''
        },
    }
    t = templates.get(lang, templates['zh-CN'])
    return _send_email(to_email, t['subject'], t['body'], code)


def generate_code():
    import random, string
    return ''.join(random.choices(string.digits, k=6))
