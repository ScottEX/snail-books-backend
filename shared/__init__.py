# Shared utilities — import from shared.xxx
from .db import get_db, DB_PATH
from .auth import login_required
from .i18n import t, get_lang
from .email import (
    DEV_MODE, generate_code,
    send_verification_email, send_reset_email, send_email_change_code,
)
from .rate_limit import (
    check_rate_limit, record_failed_attempt,
    check_forgot_limit, record_forgot_attempt,
)
from .validation import (
    validate_password, validate_username, validate_required, validate_email,
)
