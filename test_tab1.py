#!/usr/bin/env python3
"""全面测试 Tab1 对账 & 支出 API"""
import json, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://localhost:8600"
TOKEN = None

def req(method, path, data=None, params=None, headers=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json", "X-Lang": "zh-CN"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if headers:
        h.update(headers)
    r = Request(url, data=body, headers=h, method=method)
    try:
        resp = urlopen(r)
        return resp.status, json.loads(resp.read())
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except:
            return e.code, {"raw": str(e)}

def login():
    global TOKEN
    code, data = req("POST", "/login", {"username": "tester_t1", "password": "Test1234!"})
    if code == 200 and data.get("token"):
        TOKEN = data["token"]
        print(f"✅ 登录成功: {data.get('username')}")
        return True
    print(f"❌ 登录失败: {code} {data}")
    return False

passed = 0
failed = 0

def test(label, status, data, check=None):
    global passed, failed
    if check is not None:
        result = check(data)
        if isinstance(result, tuple):
            ok, detail = result
        else:
            ok = bool(result)
            detail = str(result)
    else:
        ok = status < 400
        detail = f"status={status}"
    
    if ok:
        print(f"  ✅ {label}: {detail}")
        passed += 1
        return True
    else:
        print(f"  ❌ {label}: {detail} | body={json.dumps(data, ensure_ascii=False)[:200]}")
        failed += 1
        return False

if __name__ == "__main__":
    if not login():
        sys.exit(1)
    
    # ═══════════════════════════════════════════════════════════
    # 1. 对账 (Reconciliation) API
    # ═══════════════════════════════════════════════════════════
    print("\n── 对账 ──")
    
    s, d = req("POST", "/api/reconciliations", {
        "date": "2026-06-08",
        "bill_date": "2026-06-07",
        "card_balance": 5000.50,
        "cash_balance": 200.00,
        "dine_in": 3000.00,
        "meituan": 1500.00,
        "flash_sale": 300.00,
        "jd": 200.00,
        "tuan": 100.00,
        "reconciled_by": "测试员"
    })
    ok1 = test("1.1 创建对账", s, d, lambda x: x.get("ok") and x.get("action") in ("created", "updated"))
    
    # Get list and verify calculations
    s, d = req("GET", "/api/reconciliations", params={"per_page": 5, "limit": 5})
    test("1.2 获取列表", s, d, lambda x: isinstance(x, list) or "records" in x)
    
    if isinstance(d, list) and len(d) > 0:
        rec = d[0]
        ch = rec.get("dine_in",0) + rec.get("meituan",0) + rec.get("flash_sale",0) + rec.get("jd",0) + rec.get("tuan",0)
        test("1.3 channel_total", s, d, lambda x: abs(rec["channel_total"] - ch) < 0.01)
        
        rt = rec.get("card_balance",0) + rec.get("cash_balance",0)
        test("1.4 real_total", s, d, lambda x: abs(rec["real_total"] - rt) < 0.01)
        
        expected_diff = round(rt - ch, 2)
        test("1.5 diff", s, d, lambda x: abs(rec["diff"] - expected_diff) < 0.01)
    
    # Validation tests
    s, d = req("POST", "/api/reconciliations", {"card_balance": 100})
    test("1.6 缺日期→400", s, d, lambda x: s == 400)
    
    s, d = req("POST", "/api/reconciliations", {"date": "2026/06/08", "card_balance": 100})
    test("1.7 日期格式错→400", s, d, lambda x: s == 400)
    
    s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": -100})
    test("1.8 负数拒绝→400", s, d, lambda x: s == 400)
    
    s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 1e11})
    test("1.9 超出范围→400", s, d, lambda x: s == 400)
    
    s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 100, "reconciled_by": "<script>alert(1)</script>"})
    test("1.10 XSS拒绝→400", s, d, lambda x: s == 400)
    
    s, d = req("GET", "/api/reconciliations", params={"page": 1, "per_page": 5})
    test("1.11 分页查询", s, d, lambda x: "records" in d and "total" in d and "pages" in d)
    
    s, d = req("GET", "/api/reconciliations", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})
    test("1.12 日期筛选", s, d, lambda x: s == 200)
    
    s, d = req("GET", "/api/reconciliations", params={"reconciled_by": "测试员"})
    test("1.13 录入人筛选", s, d, lambda x: s == 200)
    
    s, d = req("POST", "/api/reconciliations/clear", {})
    test("1.14 清除缺确认→400", s, d, lambda x: s == 400)
    
    # 🐛 Bug 1: 对账错误消息硬编码中文
    s, d = req("POST", "/api/reconciliations", {"card_balance": 100}, headers={"X-Lang": "en"})
    has_chinese = any(ord(c) > 127 for c in d.get("error", ""))
    test("🐛 B1: en请求返回中文", s, d, lambda x: (has_chinese, f"返回中文: '{d.get('error','')}'" if has_chinese else f"OK: '{d.get('error','')}'"))
    
    s, d = req("POST", "/api/reconciliations", {"card_balance": 100}, headers={"X-Lang": "zh-TW"})
    test("B1续: zh-TW请求", s, d, lambda x: (s == 400, f"status={s}"))
    
    # ═══════════════════════════════════════════════════════════
    # 2. 支出 (Expense/Transactions) API
    # ═══════════════════════════════════════════════════════════
    print("\n── 支出 ──")
    
    s, d = req("POST", "/api/transactions", {
        "type": "expense", "amount": 88.50, "category": "日常",
        "account": "微信", "note": "买菜", "date": "2026-06-08"
    })
    test("2.1 创建支出", s, d, lambda x: x.get("status") == "ok")
    
    s, d = req("GET", "/api/transactions", params={"type": "expense", "per_page": 5})
    test("2.2 获取列表", s, d, lambda x: "transactions" in d)
    
    if d.get("transactions"):
        exp_id = d["transactions"][0]["id"]
        s, d2 = req("DELETE", f"/api/transactions/{exp_id}")
        test("2.3 删除支出", s, d2, lambda x: d2.get("status") == "ok")
    
    s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100})
    test("2.4 缺必填→400", s, d, lambda x: s == 400)
    
    s, d = req("GET", "/api/transactions", params={"type": "expense", "page": 1, "per_page": 3})
    test("2.5 分页", s, d, lambda x: "transactions" in d and "total" in d)
    
    s, d = req("GET", "/api/transactions", params={"type": "expense", "date_from": "2026-06-01", "date_to": "2026-06-30"})
    test("2.6 日期筛选", s, d, lambda x: s == 200)
    
    s, d = req("GET", "/api/transactions", params={"type": "expense", "category": "日常"})
    test("2.7 单分类筛选", s, d, lambda x: s == 200)
    
    s, d = req("GET", "/api/transactions", params={"type": "expense", "category": "日常,薪资"})
    test("2.8 多分类筛选", s, d, lambda x: s == 200)
    
    # 🐛 Bug 2: 无效 type → 500
    s, d = req("POST", "/api/transactions", {
        "type": "invalid_type", "amount": 100, "category": "日常", "account": "微信"
    })
    test("🐛 B2: 无效type→500", s, d,
         lambda x: (x.get("status") != "error" or s == 400, f"status={s}, 期望400非500"))
    
    # 三语错误消息 (支出使用了 i18n)
    for lang in ["zh-CN", "zh-TW", "en"]:
        s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100},
                   headers={"X-Lang": lang})
        msg = d.get("message", "")
        test(f"2.9 三语({lang})", s, d, lambda x: (s == 400 and len(msg) > 0, f"msg={msg[:50]}"))
    
    # ═══════════════════════════════════════════════════════════
    # 3. 未登录保护
    # ═══════════════════════════════════════════════════════════
    print("\n── 未登录 ──")
    saved = TOKEN
    TOKEN = None
    
    s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 100})
    test("3.1 对账需登录", s, d, lambda x: s in (401, 403))
    
    s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100, "category": "日常", "account": "微信"})
    test("3.2 支出需登录", s, d, lambda x: s in (401, 403))
    
    TOKEN = saved
    
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"  ✅ 通过: {passed}  ❌ 失败: {failed}")
    print(f"{'='*60}")
    
    if failed > 0:
        sys.exit(1)
