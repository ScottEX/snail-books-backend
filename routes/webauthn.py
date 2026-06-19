"""WebAuthn routes — Face ID / Touch ID passwordless login."""

import base64
import json
import os

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session, g

from shared.db import get_db
from shared.auth import login_required
from shared.i18n import t

webauthn_bp = Blueprint('webauthn', __name__)

RP_ID = 'test.rowanlan.xyz'
RP_NAME = '柳味探秘'
ORIGIN = 'https://test.rowanlan.xyz'


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64url_decode(s: str) -> bytes:
    s = s + '=' * (4 - len(s) % 4) if len(s) % 4 else s
    return base64.urlsafe_b64decode(s)


# ═══════════════════════════════════════════════
#  Login — begin / complete
# ═══════════════════════════════════════════════

@webauthn_bp.route('/webauthn/login/begin', methods=['POST'])
def login_begin():
    """Start Face ID login: return challenge + allowed credentials."""
    from webauthn import generate_authentication_options
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        AuthenticatorTransport,
    )

    # If the frontend sends a credential_id hint, use it; otherwise list
    # credentials for the specified username. If no username either, list
    # all credentials in the DB (discoverable flow — first-time login).
    allow_credentials = []
    data = request.get_json(silent=True) or {}
    credential_hint = data.get('credential_id', '')
    username_hint = data.get('username', '').strip()

    if credential_hint:
        allow_credentials.append(
            PublicKeyCredentialDescriptor(
                id=_b64url_decode(credential_hint),
                transports=[AuthenticatorTransport.INTERNAL],
            )
        )
    else:
        with get_db() as db:
            if username_hint:
                rows = db.execute(
                    '''SELECT wc.credential_id FROM webauthn_credentials wc
                       JOIN users u ON u.id = wc.user_id
                       WHERE u.username = ? OR u.email = ?''',
                    (username_hint, username_hint)
                ).fetchall()
            else:
                rows = db.execute(
                    'SELECT credential_id FROM webauthn_credentials'
                ).fetchall()
        for row in rows:
            allow_credentials.append(
                PublicKeyCredentialDescriptor(
                    id=_b64url_decode(row['credential_id']),
                    transports=[AuthenticatorTransport.INTERNAL],
                )
            )

    challenge_bytes = os.urandom(32)
    options = generate_authentication_options(
        rp_id=RP_ID,
        challenge=challenge_bytes,
        allow_credentials=allow_credentials if allow_credentials else None,
        user_verification='required',
    )

    session['webauthn_challenge'] = _b64url(challenge_bytes)
    session.modified = True

    return jsonify({
        'status': 'ok',
        'challenge': _b64url(options.challenge),
        'allowCredentials': [
            {'id': _b64url(c.id), 'type': 'public-key',
             'transports': [t.value for t in (c.transports or [])]}
            for c in (options.allow_credentials or [])
        ],
        'rpId': RP_ID,
        'timeout': options.timeout or 60000,
        'userVerification': 'required',
    })


@webauthn_bp.route('/webauthn/login/complete', methods=['POST'])
def login_complete():
    """Verify Face ID assertion and log the user in."""
    from webauthn import verify_authentication_response
    from webauthn.helpers.structs import AuthenticationCredential, AuthenticatorAssertionResponse

    data = request.get_json() or {}
    raw_id = data.get('id', '')
    client_data_json = data.get('clientDataJSON', '')
    authenticator_data = data.get('authenticatorData', '')
    signature = data.get('signature', '')
    user_handle = data.get('userHandle', '')

    if not all([raw_id, client_data_json, authenticator_data, signature]):
        return jsonify({'status': 'error', 'message': 'Missing credential data'}), 400

    challenge_b64 = session.get('webauthn_challenge', '')
    if not challenge_b64:
        return jsonify({'status': 'error', 'message': 'No challenge in session'}), 400

    try:
        credential_id_bytes = _b64url_decode(raw_id)
    except Exception:
        return jsonify({'status': 'error', 'message': 'Invalid credential ID'}), 400

    with get_db() as db:
        row = db.execute(
            'SELECT u.id, u.username, wc.credential_id, wc.public_key, wc.sign_count '
            'FROM webauthn_credentials wc '
            'JOIN users u ON u.id = wc.user_id '
            'WHERE wc.credential_id = ?',
            (raw_id,)
        ).fetchone()

        if not row:
            return jsonify({'status': 'error', 'message': 'Unknown credential'}), 400

        user_id = row['id']
        username = row['username']
        stored_pk_pem = row['public_key']
        stored_sign_count = row['sign_count'] or 0

    try:
        verify_authentication_response(
            credential=AuthenticationCredential(
                id=raw_id,
                raw_id=credential_id_bytes,
                response=AuthenticatorAssertionResponse(
                    client_data_json=_b64url_decode(client_data_json),
                    authenticator_data=_b64url_decode(authenticator_data),
                    signature=_b64url_decode(signature),
                    user_handle=_b64url_decode(user_handle) if user_handle else None,
                ),
            ),
            expected_challenge=_b64url_decode(challenge_b64),
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            credential_public_key=stored_pk_pem,
            credential_current_sign_count=0,  # Apple platform authenticator always returns 0
            require_user_verification=True,
        )
    except Exception as e:
        # Don't expose internal details
        return jsonify({'status': 'error', 'message': f'Verification failed: {str(e)}'}), 400

    # Log the user in (same session creation logic as password login)
    import secrets
    from datetime import timedelta

    with get_db() as db:
        user = db.execute(
            'SELECT id, username, enforce_single_session, session_timeout_hours, is_disabled, is_verified '
            'FROM users WHERE id = ?', (user_id,)
        ).fetchone()

        if not user:
            return jsonify({'status': 'error', 'message': 'User not found'}), 400
        if int(user['is_disabled'] or 0):
            return jsonify({'status': 'error', 'message': '账户已被禁用，请联系管理员'}), 403

        enforce_sso = int(user['enforce_single_session']) if user['enforce_single_session'] is not None else 1
        timeout_hours = int(user['session_timeout_hours']) if user['session_timeout_hours'] else 1
        if timeout_hours < 1:
            timeout_hours = 1

        new_session_id = secrets.token_hex(16)
        expires_at = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=timeout_hours)).strftime('%Y-%m-%d %H:%M:%S')
        device_info = (request.user_agent.string or '')[:200]

        if enforce_sso:
            db.execute(
                "UPDATE user_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id)
            )
            db.execute(
                "DELETE FROM user_tokens WHERE user_id=? AND (session_id IS NULL OR session_id IN (SELECT session_id FROM user_sessions WHERE user_id=? AND revoked_at IS NOT NULL))",
                (user_id, user_id)
            )
            db.execute('UPDATE users SET current_session_id=? WHERE id=?', (new_session_id, user_id))

        db.execute(
            'INSERT INTO user_sessions (user_id, session_id, device_info, expires_at) VALUES (?,?,?,?)',
            (user_id, new_session_id, device_info, expires_at)
        )
        db.execute('UPDATE users SET last_login_at=? WHERE id=?',
                   (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), user_id))
        session['user_id'] = user_id
        session['username'] = username
        session['session_id'] = new_session_id
        g.user_id = user_id
        g.username = username

        db.execute("DELETE FROM user_tokens WHERE created_at < ?", ((datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d %H:%M:%S'),))
        token = secrets.token_hex(32)
        db.execute('INSERT INTO user_tokens (user_id, token, session_id) VALUES (?,?,?)', (user_id, token, new_session_id))
        db.commit()

    from shared.audit import audit
    audit('LOGIN_WEBATHN', user_id=user_id, username=username)

    session.pop('webauthn_challenge', None)

    return jsonify({'status': 'ok', 'token': token, 'username': username, 'user_id': user_id})


# ═══════════════════════════════════════════════
#  Register — begin / complete
# ═══════════════════════════════════════════════

@webauthn_bp.route('/webauthn/register/begin', methods=['GET'])
@login_required
def register_begin():
    """Start Face ID registration: return challenge."""
    from webauthn import generate_registration_options
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        AuthenticatorAttachment,
        UserVerificationRequirement,
        ResidentKeyRequirement,
    )

    challenge_bytes = os.urandom(32)

    # user_id in WebAuthn must be ≤ 64 bytes
    user_id_bytes = str(g.user_id).encode()

    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_id_bytes,
        user_name=g.username,
        challenge=challenge_bytes,
        authenticator_selection=AuthenticatorSelectionCriteria(
            authenticator_attachment=AuthenticatorAttachment.PLATFORM,
            user_verification=UserVerificationRequirement.REQUIRED,
            resident_key=ResidentKeyRequirement.REQUIRED,
        ),
    )

    session['webauthn_register_challenge'] = _b64url(challenge_bytes)
    session.modified = True

    return jsonify({
        'status': 'ok',
        'challenge': _b64url(options.challenge),
        'rp': {
            'name': RP_NAME,
            'id': RP_ID,
        },
        'user': {
            'id': _b64url(user_id_bytes),
            'name': g.username,
            'displayName': g.username,
        },
        'pubKeyCredParams': [
            {'alg': -7, 'type': 'public-key'},   # ES256
            {'alg': -257, 'type': 'public-key'}, # RS256
        ],
        'timeout': options.timeout or 60000,
        'authenticatorSelection': {
            'authenticatorAttachment': 'platform',
            'userVerification': 'required',
            'residentKey': 'required',
        },
        'attestation': 'none',
    })


@webauthn_bp.route('/webauthn/register/complete', methods=['POST'])
@login_required
def register_complete():
    """Verify Face ID attestation and store credential."""
    from webauthn import verify_registration_response
    from webauthn.helpers.structs import RegistrationCredential, AuthenticatorAttestationResponse

    data = request.get_json() or {}
    raw_id = data.get('id', '')
    client_data_json = data.get('clientDataJSON', '')
    attestation_object = data.get('attestationObject', '')

    if not all([raw_id, client_data_json, attestation_object]):
        return jsonify({'status': 'error', 'message': 'Missing credential data'}), 400

    challenge_b64 = session.get('webauthn_register_challenge', '')
    if not challenge_b64:
        return jsonify({'status': 'error', 'message': 'No challenge in session'}), 400

    try:
        credential_id_bytes = _b64url_decode(raw_id)
    except Exception:
        return jsonify({'status': 'error', 'message': 'Invalid credential ID'}), 400

    try:
        verification = verify_registration_response(
            credential=RegistrationCredential(
                id=raw_id,
                raw_id=credential_id_bytes,
                response=AuthenticatorAttestationResponse(
                    client_data_json=_b64url_decode(client_data_json),
                    attestation_object=_b64url_decode(attestation_object),
                ),
            ),
            expected_challenge=_b64url_decode(challenge_b64),
            expected_rp_id=RP_ID,
            expected_origin=ORIGIN,
            require_user_verification=True,
        )
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Registration failed: {str(e)}'}), 400

    # Store credential
    cred_id_b64 = _b64url(verification.credential_id)
    pk_pem = verification.credential_public_key
    sign_count = verification.sign_count

    with get_db() as db:
        # One user, one credential (replace if already exists)
        db.execute('DELETE FROM webauthn_credentials WHERE user_id = ?', (g.user_id,))
        db.execute(
            'INSERT INTO webauthn_credentials (user_id, credential_id, public_key, sign_count) '
            'VALUES (?, ?, ?, ?)',
            (g.user_id, cred_id_b64, pk_pem, sign_count)
        )
        db.commit()

    session.pop('webauthn_register_challenge', None)

    return jsonify({'status': 'ok', 'message': t('msg_webauthn_bound', g.lang)})


# ═══════════════════════════════════════════════
#  Public check — does a user have WebAuthn?
# ═══════════════════════════════════════════════

@webauthn_bp.route('/webauthn/check', methods=['GET'])
def check_credential():
    """Public endpoint: check if a user (or any user) has a WebAuthn credential."""
    username = request.args.get('username', '').strip()

    with get_db() as db:
        if username:
            user = db.execute(
                'SELECT id, username FROM users WHERE username = ? OR email = ?',
                (username, username)
            ).fetchone()
            if not user:
                return jsonify({'has_credential': False, 'username': username})

            row = db.execute(
                'SELECT id FROM webauthn_credentials WHERE user_id = ?',
                (user['id'],)
            ).fetchone()
            return jsonify({
                'has_credential': bool(row),
                'username': user['username'],
            })
        else:
            row = db.execute(
                'SELECT u.username FROM webauthn_credentials wc '
                'JOIN users u ON u.id = wc.user_id '
                'ORDER BY wc.id LIMIT 1'
            ).fetchone()
            if row:
                return jsonify({'has_credential': True, 'username': row['username']})
            return jsonify({'has_credential': False, 'username': None})


# ═══════════════════════════════════════════════
#  Status / Delete
# ═══════════════════════════════════════════════

@webauthn_bp.route('/webauthn/status', methods=['GET'])
@login_required
def status():
    """Whether current user has a WebAuthn credential, and its ID for client-side deletion signals."""
    with get_db() as db:
        row = db.execute(
            'SELECT id, credential_id FROM webauthn_credentials WHERE user_id = ?',
            (g.user_id,)
        ).fetchone()
    return jsonify({
        'status': 'ok',
        'has_credential': bool(row),
        'credential_id': row['credential_id'] if row else None,
    })


@webauthn_bp.route('/webauthn/credentials', methods=['DELETE'])
@login_required
def delete_credential():
    """Unbind Face ID."""
    with get_db() as db:
        db.execute('DELETE FROM webauthn_credentials WHERE user_id = ?', (g.user_id,))
        db.commit()
    return jsonify({'status': 'ok', 'message': t('msg_webauthn_unbound', g.lang)})
