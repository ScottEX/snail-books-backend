"""Period metrics shared by the dashboard and ledger answers."""

from calendar import monthrange
from datetime import date, timedelta

from shared.money import to_decimal


def revenue_totals(db, start, end):
    row = db.execute(
        """SELECT COUNT(*) AS records, SUM(turnover) AS turnover, SUM(revenue) AS revenue,
                  SUM(jd_revenue) AS jd_revenue FROM daily_revenue WHERE date BETWEEN ? AND ?""",
        (start, end),
    ).fetchone()
    return to_decimal(row['turnover']), to_decimal(row['revenue']) + to_decimal(row['jd_revenue']), row['records']


_FEE_TOTAL = ' + '.join(f'COALESCE({column}, 0)' for column in
                        ('meituan_cashier', 'meituan_waimai', 'shangou_waimai', 'meituan_tuan'))


def _period_fees(db, start, end):
    dated = db.execute(
        f"""SELECT SUM({_FEE_TOTAL}) AS amount, COUNT(*) AS records
            FROM platform_fee_entries WHERE substr(entry_date, 1, 10) BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    fees, records = to_decimal(dated['amount']), dated['records']
    fees_complete = True
    monthly = db.execute(
        f"""WITH itemized AS (
                SELECT fee_id, SUM({_FEE_TOTAL}) AS amount FROM platform_fee_entries
                WHERE date(substr(entry_date, 1, 10)) = substr(entry_date, 1, 10)
                GROUP BY fee_id
            )
            SELECT year, month, ({_FEE_TOTAL}) AS amount, COALESCE(itemized.amount, 0) AS itemized
            FROM platform_fees LEFT JOIN itemized ON itemized.fee_id = platform_fees.id
            WHERE year * 100 + month BETWEEN ? AND ? AND month BETWEEN 1 AND 12""",
        (start.year * 100 + start.month, end.year * 100 + end.month),
    ).fetchall()
    for row in monthly:
        residual = (to_decimal(row['amount']) - to_decimal(row['itemized'])).quantize(to_decimal('0.01'))
        if not residual:
            continue
        month_start = date(row['year'], row['month'], 1)
        month_end = month_start.replace(day=monthrange(row['year'], row['month'])[1])
        if start <= month_start and end >= month_end:
            fees += residual
            records += 1
        else:
            fees_complete = False
    return fees, records, fees_complete


def period_operating_metrics(db, start, end):
    start_text, end_text = start.isoformat(), end.isoformat()
    turnover, received, revenue_records = revenue_totals(db, start_text, end_text)
    expense = db.execute(
        """SELECT SUM(amount) AS amount, COUNT(*) AS records FROM transactions
           WHERE type='expense' AND date BETWEEN ? AND ?
             AND (procurement_batch_id IS NULL OR amount <> 0)""",
        (start_text, end_text),
    ).fetchone()
    expenses = to_decimal(expense['amount'])
    fees, fee_records, complete = _period_fees(db, start, end)
    return {
        'start_date': start_text, 'end_date': end_text,
        'turnover': turnover, 'received': received, 'platform_fees': fees, 'expenses': expenses,
        'net_received': received - fees, 'operating_profit': received - fees - expenses,
        'fees_complete': complete, 'revenue_records': revenue_records, 'expense_records': expense['records'],
        'fee_records': fee_records,
    }


def dashboard_periods(today):
    yesterday = today - timedelta(days=1)
    return {'today': (today, today), 'yesterday': (yesterday, yesterday), 'this_month': (today.replace(day=1), today)}
