"""
DB 迁移脚本：把所有中文 enum 值统一改成内部 key
配合前端 e07cf99 + e197ee5 + 后端 e68a9db 的 helper 做一次性数据清理。

⚠️ 运行前必须先做：
    cp snail.db snail.db.bak-YYYYMMDD-HHMM

⚠️ 此脚本在事务里跑，任何一步失败全部回滚。
"""

import sqlite3
import sys
from datetime import datetime

# 内部 key 表 — 与 i18nHelpers.ts 严格对齐
EXPENSE_CAT_KEYS = {
    '日常': 'daily', '房租': 'rent', '薪资': 'salary', '采购': 'goods',
    '採購': 'goods',  # zh-TW legacy
    'Procurement': 'goods',  # en legacy (pre-Phase-2)
}

INCOME_CAT_KEYS = {
    '堂食': 'dineIn', '美团外卖': 'meituan',
    '美团团购': 'meituanTuan', '京东': 'jd',
    '🛵 美团外卖': 'meituan',
    '🎫 美团团购': 'meituanTuan', '📦 京东': 'jd',
    '🍜 堂食': 'dineIn',
}

PAY_KEYS = {
    '现金': 'payCash', '微信': 'payWechat', '支付宝': 'payAlipay',
    '支付寶': 'payAlipay', '現金': 'payCash',
    'Alipay': 'payAlipay',  # en legacy
}

# ⚠️ '银行卡' (Bank Card) is excluded — user confirmed deletion per 2026-06-06 decision


def normalize_substring(s, mapping):
    """If raw value contains any legacy key (as substring), return internal key."""
    if s is None or s == '':
        return s
    if s in mapping:
        return mapping[s]
    for legacy, key in mapping.items():
        if legacy and legacy in s:
            return key
    return s


def main(db_path):
    print(f"[{datetime.now()}] Starting migration on {db_path}")
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Audit: snapshot before counts
    print("\n=== BEFORE ===")
    for tbl, col in [('transactions', 'category'), ('transactions', 'account')]:
        rows = cur.execute(f"SELECT {col}, COUNT(*) FROM transactions GROUP BY {col} ORDER BY {col}").fetchall()
        print(f"  transactions.{col}: {dict(rows)}")
    rows = cur.execute("SELECT category, payment_method, COUNT(*) FROM procurement_batches GROUP BY category, payment_method ORDER BY category").fetchall()
    pb_summary = {f"{r[0]}/{r[1]}": r[2] for r in rows}
    print(f"  procurement_batches: {pb_summary}")

    try:
        # ── 1. DELETE: '银行卡' account records (user decision 2026-06-06) ──
        cur.execute("DELETE FROM transactions WHERE account = '银行卡'")
        deleted_card = cur.rowcount
        print(f"\n[1/4] DELETE transactions WHERE account='银行卡': {deleted_card} rows")

        # ── 2. MIGRATE transactions.category (expense + income) ──
        # 先收集所有 distinct category，逐一映射更新
        cats = cur.execute("SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL").fetchall()
        cat_updates = 0
        for (raw,) in cats:
            new_val = normalize_substring(raw, {**EXPENSE_CAT_KEYS, **INCOME_CAT_KEYS})
            if new_val != raw:
                cur.execute("UPDATE transactions SET category=? WHERE category=?", (new_val, raw))
                n = cur.rowcount
                cat_updates += n
                print(f"  transactions.category: {raw!r} → {new_val!r} ({n} rows)")
        print(f"[2/4] Migrate transactions.category: {cat_updates} rows total")

        # ── 3. MIGRATE transactions.account ──
        accs = cur.execute("SELECT DISTINCT account FROM transactions WHERE account IS NOT NULL").fetchall()
        acc_updates = 0
        for (raw,) in accs:
            new_val = normalize_substring(raw, PAY_KEYS)
            if new_val != raw:
                cur.execute("UPDATE transactions SET account=? WHERE account=?", (new_val, raw))
                n = cur.rowcount
                acc_updates += n
                print(f"  transactions.account: {raw!r} → {new_val!r} ({n} rows)")
        print(f"[3/4] Migrate transactions.account: {acc_updates} rows total")

        # ── 4. MIGRATE procurement_batches (category + payment_method) ──
        # category
        pcats = cur.execute("SELECT DISTINCT category FROM procurement_batches WHERE category IS NOT NULL").fetchall()
        pb_cat_updates = 0
        for (raw,) in pcats:
            new_val = normalize_substring(raw, EXPENSE_CAT_KEYS)
            if new_val != raw:
                cur.execute("UPDATE procurement_batches SET category=? WHERE category=?", (new_val, raw))
                n = cur.rowcount
                pb_cat_updates += n
                print(f"  procurement_batches.category: {raw!r} → {new_val!r} ({n} rows)")
        # payment_method
        pmeths = cur.execute("SELECT DISTINCT payment_method FROM procurement_batches WHERE payment_method IS NOT NULL").fetchall()
        pb_pm_updates = 0
        for (raw,) in pmeths:
            new_val = normalize_substring(raw, PAY_KEYS)
            if new_val != raw:
                cur.execute("UPDATE procurement_batches SET payment_method=? WHERE payment_method=?", (new_val, raw))
                n = cur.rowcount
                pb_pm_updates += n
                print(f"  procurement_batches.payment_method: {raw!r} → {new_val!r} ({n} rows)")
        print(f"[4/4] Migrate procurement_batches: {pb_cat_updates + pb_pm_updates} rows total")

        # Audit: snapshot after
        print("\n=== AFTER ===")
        for tbl, col in [('transactions', 'category'), ('transactions', 'account')]:
            rows = cur.execute(f"SELECT {col}, COUNT(*) FROM transactions GROUP BY {col} ORDER BY {col}").fetchall()
            print(f"  transactions.{col}: {dict(rows)}")
        rows = cur.execute("SELECT category, payment_method, COUNT(*) FROM procurement_batches GROUP BY category, payment_method ORDER BY category").fetchall()
        pb_summary = {f"{r[0]}/{r[1]}": r[2] for r in rows}
        print(f"  procurement_batches: {pb_summary}")

        # Verify: any unmapped Chinese strings remaining? (Python-side check)
        issues = []
        def has_cjk(s):
            return s and any('\u4e00' <= c <= '\u9fff' for c in s)
        for tbl, col in [('transactions', 'category'), ('transactions', 'account')]:
            for (v,) in cur.execute(f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL").fetchall():
                if has_cjk(v):
                    issues.append(f"{tbl}.{col} still has CJK: {v!r}")
        for col in ['category', 'payment_method']:
            for (v,) in cur.execute(f"SELECT DISTINCT {col} FROM procurement_batches WHERE {col} IS NOT NULL").fetchall():
                if has_cjk(v):
                    issues.append(f"procurement_batches.{col} still has CJK: {v!r}")

        if issues:
            print("\n⚠️  REMAINING CJK VALUES (not migrated):")
            for i in issues:
                print(f"  {i}")
            print("Rolling back.")
            conn.rollback()
            sys.exit(1)
        else:
            print("\n✅ All CJK values migrated. Committing.")
            conn.commit()
            print(f"\nTotal changes: -{deleted_card} deleted (银行卡), {cat_updates} tx-cat, {acc_updates} tx-acc, {pb_cat_updates + pb_pm_updates} pb")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()
        print(f"\n[{datetime.now()}] Migration complete.")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python migrate.py <path-to-snail.db>")
        sys.exit(1)
    main(sys.argv[1])
