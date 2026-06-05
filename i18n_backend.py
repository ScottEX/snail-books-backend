"""后端 i18n 翻译模块"""

TRANSLATIONS = {
    'zh-CN': {
        'err_empty_fields': '用户名和密码不能为空',
        'err_need_verify': '邮箱未验证，请先验证',
        'err_wrong_credentials': '用户名或密码错误',
        'err_email_required': '请输入邮箱',
        'err_pw_too_short': '密码至少 8 位',
        'err_pw_no_letter': '密码必须包含字母',
        'err_pw_no_digit': '密码必须包含数字',
        'err_pw_no_special': '密码必须包含特殊字符（如 !@#$% 等）',
        'err_username_exists': '用户名已存在',
        'err_username_invalid': '用户名仅支持字母、数字、下划线和中文，2-32位',
        'err_email_registered': '该邮箱已被注册',
        'err_code_send_failed': '验证码发送失败，请稍后再试',
        'err_empty_email_code': '请输入邮箱和验证码',
        'err_wrong_code': '验证码错误',
        'err_code_expired': '验证码已过期，请重新注册',
        'err_email_not_found': '未找到该邮箱的注册记录',
        'err_resend_failed': '验证码发送失败',
        'err_email_404': '未找到该邮箱的注册记录',
        'err_reset_code_wrong': '验证码错误',
        'err_reset_code_expired': '验证码已过期，请重新获取',
        'err_incomplete': '请填写完整信息',
        'err_missing_fields': '缺少必填字段: {fields}',
        'err_session_expired': '登录已过期，请重新登录',
        'err_session_kicked': '您的账号在其他设备登录，当前会话已退出',
        'err_too_many_attempts': '尝试次数过多，请等待 {mins} 分 {secs} 秒后再试',
        'err_email_invalid': '邮箱格式不正确',
        'msg_code_sent': '验证码已发送至 {email}，请查收',
        'msg_verify_ok': '邮箱验证成功！请登录',
        'msg_code_resent': '验证码已重新发送',
        'msg_forgot_sent': '如该邮箱已注册，验证码已发送',
        'msg_reset_ok': '密码重置成功！请使用新密码登录',
    },
    'zh-TW': {
        'err_empty_fields': '使用者名稱和密碼不能為空',
        'err_need_verify': '郵箱未驗證，請先驗證',
        'err_wrong_credentials': '使用者名稱或密碼錯誤',
        'err_email_required': '請輸入郵箱',
        'err_pw_too_short': '密碼至少 8 位',
        'err_pw_no_letter': '密碼必須包含字母',
        'err_pw_no_digit': '密碼必須包含數字',
        'err_pw_no_special': '密碼必須包含特殊字符（如 !@#$% 等）',
        'err_username_exists': '使用者名稱已存在',
        'err_username_invalid': '使用者名稱僅支援字母、數字、底線和中文，2-32位',
        'err_email_registered': '該郵箱已被註冊',
        'err_code_send_failed': '驗證碼發送失敗，請稍後再試',
        'err_empty_email_code': '請輸入郵箱和驗證碼',
        'err_wrong_code': '驗證碼錯誤',
        'err_code_expired': '驗證碼已過期，請重新註冊',
        'err_email_not_found': '未找到該郵箱的註冊記錄',
        'err_resend_failed': '驗證碼發送失敗',
        'err_email_404': '未找到該郵箱的註冊記錄',
        'err_reset_code_wrong': '驗證碼錯誤',
        'err_reset_code_expired': '驗證碼已過期，請重新獲取',
        'err_incomplete': '請填寫完整資訊',
        'err_missing_fields': '缺少必填欄位: {fields}',
        'err_session_expired': '登錄已過期，請重新登錄',
        'err_session_kicked': '您的帳號在其他裝置登入，當前工作階段已退出',
        'err_too_many_attempts': '嘗試次數過多，請等待 {mins} 分 {secs} 秒後再試',
        'err_email_invalid': '郵箱格式不正確',
        'msg_code_sent': '驗證碼已發送至 {email}，請查收',
        'msg_verify_ok': '郵箱驗證成功！請登入',
        'msg_code_resent': '驗證碼已重新發送',
        'msg_forgot_sent': '如該郵箱已註冊，驗證碼已發送',
        'msg_reset_ok': '密碼重設成功！請使用新密碼登入',
    },
    'en': {
        'err_empty_fields': 'Username and password required',
        'err_need_verify': 'Email not verified, please verify first',
        'err_wrong_credentials': 'Incorrect username or password',
        'err_email_required': 'Email is required',
        'err_pw_too_short': 'Password must be at least 8 characters',
        'err_pw_no_letter': 'Password must contain a letter',
        'err_pw_no_digit': 'Password must contain a digit',
        'err_pw_no_special': 'Password must contain a special character (e.g. !@#$%)',
        'err_username_exists': 'Username already taken',
        'err_username_invalid': 'Username must be 2-32 characters: letters, digits, underscores, or Chinese',
        'err_email_registered': 'This email is already registered',
        'err_code_send_failed': 'Failed to send verification code, please try again',
        'err_empty_email_code': 'Email and verification code required',
        'err_wrong_code': 'Incorrect verification code',
        'err_code_expired': 'Verification code expired, please register again',
        'err_email_not_found': 'No registration found for this email',
        'err_resend_failed': 'Failed to send verification code',
        'err_email_404': 'No registration found for this email',
        'err_reset_code_wrong': 'Incorrect verification code',
        'err_reset_code_expired': 'Verification code expired, please request a new one',
        'err_incomplete': 'Please fill in all fields',
        'err_missing_fields': 'Missing required fields: {fields}',
        'err_session_expired': 'Session expired, please login again',
        'err_session_kicked': 'Signed in elsewhere. This session was ended.',
        'err_too_many_attempts': 'Too many attempts, please wait {mins}m {secs}s',
        'err_email_invalid': 'Invalid email format',
        'msg_code_sent': 'Verification code sent to {email}, please check',
        'msg_verify_ok': 'Email verified! Please login',
        'msg_code_resent': 'Verification code resent',
        'msg_forgot_sent': 'If registered, a verification code has been sent',
        'msg_reset_ok': 'Password reset! Please login with your new password',
    }
}

def get_lang(request):
    """从请求头获取语言，优先级：X-Lang > Accept-Language > 默认 zh-CN"""
    x_lang = request.headers.get('X-Lang', '')
    if x_lang in TRANSLATIONS:
        return x_lang
    best = request.accept_languages.best_match(['zh-CN', 'zh-TW', 'en'], default='zh-CN')
    return best

def t(key, lang='zh-CN', **kwargs):
    """翻译单个 key"""
    msg = TRANSLATIONS.get(lang, TRANSLATIONS['zh-CN']).get(key, key)
    if kwargs:
        msg = msg.format(**kwargs)
    return msg
