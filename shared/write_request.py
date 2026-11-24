"""Store successful write responses in the same transaction as their records."""

import hashlib
import json
import re

from flask import abort, g, jsonify, make_response, request

from shared.i18n import t


def start_write(db, data):
    key = request.headers.get('Idempotency-Key')
    if key is not None and not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', key):
        _reject('err_write_key_invalid', 400)

    db.execute('BEGIN IMMEDIATE')
    if key is None:
        return None, None

    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    identity = (g.user_id, request.endpoint, key)
    row = db.execute(
        'SELECT fingerprint, response FROM write_requests WHERE user_id=? AND endpoint=? AND request_key=?', identity,
    ).fetchone()
    if row:
        if row['fingerprint'] != fingerprint:
            _reject('err_write_key_conflict', 409)
        return None, jsonify(json.loads(row['response']))
    return (*identity, fingerprint), None


def finish_write(db, receipt, result):
    if receipt is not None:
        db.execute(
            'INSERT INTO write_requests (user_id, endpoint, request_key, fingerprint, response) VALUES (?,?,?,?,?)',
            (*receipt, json.dumps(result)),
        )
    return jsonify(result)


def _reject(message_key, status):
    abort(make_response(jsonify({'status': 'error', 'message': t(message_key, g.lang)}), status))
