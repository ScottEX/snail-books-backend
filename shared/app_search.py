"""Search the logged-in shared business records with a literal keyword."""

from opencc import OpenCC

from shared.db import get_db


_TRADITIONAL = OpenCC('s2t')
_SIMPLIFIED = OpenCC('t2s')

# Each query exposes only the fields needed by the result list.
_SOURCES = (
    ('expense', """
        SELECT t.id, SUBSTR(t.note, 1, 120) AS title, t.category AS detail, t.date, t.amount
        FROM transactions t WHERE t.type = 'expense' AND (
            {0} OR {1})
        ORDER BY t.date DESC, t.id DESC LIMIT 8
    """, ('t.note', 't.category')),
    ('procurement', """
        SELECT b.id, CAST(b.batch_number AS TEXT) AS title, SUBSTR(b.note, 1, 120) AS detail,
               b.date, b.total AS amount
        FROM procurement_batches b WHERE {0} OR {1} OR EXISTS (
            SELECT 1 FROM procurement_items i LEFT JOIN products p ON p.id = i.product_id
            WHERE i.batch_id = b.id AND ({2} OR {3}))
        ORDER BY b.date DESC, b.id DESC LIMIT 8
    """, ('b.batch_number', 'b.note', 'i.product_name', 'p.supplier')),
    ('invoice', """
        SELECT id, invoice_number AS title, company AS detail, date, amount
        FROM invoice_records WHERE {0} OR {1}
        ORDER BY date DESC, id DESC LIMIT 8
    """, ('invoice_number', 'company')),
    ('product', """
        SELECT id, name AS title, supplier AS detail, '' AS date, price AS amount
        FROM products WHERE {0} OR {1} OR {2}
        ORDER BY name, id LIMIT 8
    """, ('name', 'supplier', 'spec')),
    ('partner', """
        SELECT id, name AS title, '' AS detail, '' AS date, NULL AS amount
        FROM partners WHERE {0}
        ORDER BY name, id LIMIT 8
    """, ('name',)),
    ('revenue', """
        SELECT id, SUBSTR(note, 1, 120) AS title, '' AS detail, date, turnover AS amount
        FROM daily_revenue WHERE {0} OR {1}
        ORDER BY date DESC, id DESC LIMIT 8
    """, ('date', 'note')),
    ('reconciliation', """
        SELECT id, reconciled_by AS title, '' AS detail, date, diff AS amount
        FROM reconciliations WHERE {0} OR {1} OR {2}
        ORDER BY date DESC, id DESC LIMIT 8
    """, ('date', 'bill_date', 'reconciled_by')),
)


def keyword_variants(keyword):
    return tuple(dict.fromkeys((keyword, _SIMPLIFIED.convert(keyword), _TRADITIONAL.convert(keyword))))


def keyword_condition(column, variants):
    """Build a literal comparison for a column selected by server code."""
    return '(' + ' OR '.join(f'INSTR(LOWER(COALESCE({column}, \'\')), LOWER(?)) > 0' for _ in variants) + ')'


APP_SEARCH_KINDS = tuple(source[0] for source in _SOURCES)


def search_app_records(keyword, kind=None):
    variants = keyword_variants(keyword)
    with get_db() as db:
        results = []
        for source, query, columns in _SOURCES:
            if kind is not None and kind != source:
                continue
            conditions = [keyword_condition(column, variants) for column in columns]
            rows = db.execute(query.format(*conditions), variants * len(columns))
            results.extend({'kind': source, **dict(row)} for row in rows)
    return results
