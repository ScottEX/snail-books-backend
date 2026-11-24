"""Calculate a proportional dividend allocation in whole cents."""

import hashlib
import json
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from shared.money import to_decimal


class DividendAllocationError(ValueError):
    pass


def allocate_dividend(db, total):
    try:
        if not isinstance(total, (str, int, float)) or isinstance(total, bool):
            raise InvalidOperation
        amount = to_decimal(total)
        if not amount.is_finite() or not 0 < amount <= 999_999_999 or amount != amount.quantize(Decimal('0.01')):
            raise InvalidOperation
    except InvalidOperation as error:
        raise DividendAllocationError('dividendInvalidTotal') from error

    partners = db.execute('SELECT id, name, share FROM partners ORDER BY id').fetchall()
    try:
        shares = [Fraction(str(partner['share'])) for partner in partners]
    except (ValueError, ZeroDivisionError) as error:
        raise DividendAllocationError('dividendInvalidShares') from error
    if any(share < 0 for share in shares) or sum(shares) != 1:
        raise DividendAllocationError('dividendInvalidShares')

    cents = int(amount * 100)
    quotas = [cents * share for share in shares]
    payouts = [int(quota) for quota in quotas]
    remainders = sorted(range(len(partners)), key=lambda i: quotas[i] - payouts[i], reverse=True)
    for index in remainders[:cents - sum(payouts)]:
        payouts[index] += 1

    allocations = [
        {'id': partner['id'], 'name': partner['name'], 'share': partner['share'], 'amount': payout / 100}
        for partner, payout in zip(partners, payouts)
    ]
    preview = hashlib.sha256(json.dumps([cents, allocations], separators=(',', ':')).encode()).hexdigest()
    return {'total': cents / 100, 'preview': preview, 'allocations': allocations}
