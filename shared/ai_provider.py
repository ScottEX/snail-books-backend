"""JSON model responses for the ledger features."""

import json

import requests

from shared.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, EXPENSE_AI_MODEL, OPENAI_API_KEY


def ai_configured():
    return bool(DEEPSEEK_API_KEY or OPENAI_API_KEY)


def request_json(text, instructions, schema_name, fields, *, max_tokens=512, timeout=(3, 12)):
    if DEEPSEEK_API_KEY:
        output = _deepseek_output(text, instructions, fields, max_tokens, timeout)
    else:
        output = _openai_output(text, instructions, schema_name, fields, max_tokens, timeout)
    return json.loads(output)


def _deepseek_output(text, instructions, fields, max_tokens, timeout):
    expected_fields = json.dumps(dict.fromkeys(fields))
    response = requests.post(
        'https://api.deepseek.com/chat/completions',
        headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}'},
        json={
            'model': DEEPSEEK_MODEL,
            'messages': [
                {'role': 'system', 'content': (
                    f'{instructions} Return a single JSON object with exactly these keys: {expected_fields}.'
                )},
                {'role': 'user', 'content': text},
            ],
            'response_format': {'type': 'json_object'},
            'max_tokens': max_tokens,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    choice = next(iter(response.json().get('choices', [])), {})
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Incomplete model response')
    return choice['message']['content']


def _openai_output(text, instructions, schema_name, fields, max_tokens, timeout):
    response = requests.post(
        'https://api.openai.com/v1/responses',
        headers={'Authorization': f'Bearer {OPENAI_API_KEY}'},
        json={
            'model': EXPENSE_AI_MODEL,
            'store': False,
            'instructions': instructions,
            'input': text,
            'max_output_tokens': max_tokens,
            'text': {'format': {
                'type': 'json_schema',
                'name': schema_name,
                'strict': True,
                'schema': {
                    'type': 'object',
                    'properties': fields,
                    'required': list(fields),
                    'additionalProperties': False,
                },
            }},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    if result.get('status') != 'completed':
        raise ValueError('Incomplete model response')
    for item in result.get('output', []):
        if item.get('type') == 'message':
            for part in item.get('content', []):
                if part.get('type') == 'output_text':
                    return part['text']
    raise ValueError('Missing model output')
