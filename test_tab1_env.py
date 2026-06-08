#!/usr/bin/env python3
"""Tab1 对账 & 支出 完整测试 — 测试环境 8.135.58.90:8601"""
import json, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://8.135.58.90:8601"
TOKEN = ""

def req(method, path, data=None, params=None, headers=None):
    url = f"{BASE}{path}"
    if params: url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json"}
    if TOKEN: h["Authorization"] = f"Bearer {TOKEN}"
    if headers: h.update(headers)
    try:
        resp = urlopen(Request(url, data=body, headers=h, method=method))
        return resp.status, json.loads(resp.read())
    except HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {"raw": str(e)}

p = f = 0

def t(label, status, data, check=None):
    global p, f
    if check:
        r = check(data)
        ok, detail = (r if isinstance(r, tuple) else (bool(r), str(r)))
    else:
        ok, detail = status < 400, f"status={status}"
    if ok: p += 1; print(f"  ✅ {label}")
    else: f += 1; print(f"  ❌ {label}: {detail} | {json.dumps(data, ensure_ascii=False)[:150]}")

# ── Login ──
code, d = req("POST", "/login", {"username": "LanLiuFu", "password": "Lan@1314"})
if code == 200 and d.get("token"):
    TOKEN = d["token"]
    print(f"✅ 登录: {d.get('username')}")
else:
    print(f"❌ 登录失败: {code} {d}")
    sys.exit(1)

# ═══════════════════ 1. 对账 ═══════════════════
print("\n── 对账 ──")

s, d = req("POST", "/api/reconciliations", {
    "date": "2026-06-08", "bill_date": "2026-06-07",
    "card_balance": 5000.50, "cash_balance": 200.00,
    "dine_in": 3000, "meituan": 1500, "flash_sale": 300, "jd": 200, "tuan": 100,
    "reconciled_by": "tester"
})
t("1.1 创建对账", s, d, lambda x: x.get("ok"))

s, d = req("GET", "/api/reconciliations", params={"per_page": 5, "limit": 5})
t("1.2 获取列表", s, d, lambda x: isinstance(x, list) or "records" in x)
if isinstance(d, list) and len(d) > 0:
    r = d[0]
    ch = r["dine_in"]+r["meituan"]+r["flash_sale"]+r["jd"]+r["tuan"]
    rt = r["card_balance"]+r["cash_balance"]
    ed = round(rt-ch, 2)
    t("1.3 channel_total", s, d, lambda x: abs(r["channel_total"]-ch)<0.01)
    t("1.4 real_total", s, d, lambda x: abs(r["real_total"]-rt)<0.01)
    t("1.5 diff", s, d, lambda x: abs(r["diff"]-ed)<0.01)

s, d = req("POST", "/api/reconciliations", {"card_balance": 100})
t("1.6 缺日期→400", s, d, lambda x: s==400)

s, d = req("POST", "/api/reconciliations", {"date": "2026/06/08", "card_balance": 100})
t("1.7 格式错→400", s, d, lambda x: s==400)

s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": -100})
t("1.8 负数→400", s, d, lambda x: s==400)

s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 1e11})
t("1.9 超大→400", s, d, lambda x: s==400)

s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 100, "reconciled_by": "<script>"})
t("1.10 XSS→400", s, d, lambda x: s==400)

s, d = req("GET", "/api/reconciliations", params={"page": 1, "per_page": 5})
t("1.11 分页", s, d, lambda x: "records" in d and "total" in d)

s, d = req("GET", "/api/reconciliations", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})
t("1.12 日期筛选", s, d, lambda x: s==200)

s, d = req("GET", "/api/reconciliations", params={"reconciled_by": "tester"})
t("1.13 录入人筛选", s, d, lambda x: s==200)

# 🐛 B1
s, d = req("POST", "/api/reconciliations", {"card_balance": 100}, headers={"X-Lang": "en"})
has_cn = any(ord(c) > 127 for c in d.get("error", ""))
t("🐛B1 en→中文", s, d, lambda x: (has_cn, f"'{d.get('error','')}'"))

# ═══════════════════ 2. 支出 ═══════════════════
print("\n── 支出 ──")

s, d = req("POST", "/api/transactions", {
    "type": "expense", "amount": 88.50, "category": "日常", "account": "微信", "note": "测试", "date": "2026-06-08"
})
t("2.1 创建支出", s, d, lambda x: d.get("status") == "ok")

s, d = req("GET", "/api/transactions", params={"type": "expense", "per_page": 5})
t("2.2 获取列表", s, d, lambda x: "transactions" in d)
if d.get("transactions"):
    eid = d["transactions"][0]["id"]
    s2, d2 = req("DELETE", f"/api/transactions/{eid}")
    t("2.3 删除支出", s2, d2, lambda x: d2.get("status") == "ok")

s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100})
t("2.4 缺必填→400", s, d, lambda x: s==400)

s, d = req("GET", "/api/transactions", params={"type": "expense", "page": 1, "per_page": 3})
t("2.5 分页", s, d, lambda x: "transactions" in d)

s, d = req("GET", "/api/transactions", params={"type": "expense", "date_from": "2026-06-01", "date_to": "2026-06-30"})
t("2.6 日期筛选", s, d, lambda x: s==200)

# 🐛 B2
s, d = req("GET", "/api/transactions", params={"type": "expense", "category": "日常"})
t("🐛B2 分类筛选→500", s, d, lambda x: (s==200, f"status={s}"))

# 🐛 B3
s, d = req("POST", "/api/transactions", {"type": "invalid", "amount": 100, "category": "日常", "account": "微信"})
t("🐛B3 无效type→500", s, d, lambda x: (s==400, f"status={s}"))

# 三语
for lang, exp in [("zh-CN","缺少"), ("zh-TW","缺少"), ("en","Missing")]:
    s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100}, headers={"X-Lang": lang})
    t(f"2.7 三语({lang})", s, d, lambda x: (s==400 and len(d.get("message",""))>0, d.get("message","")[:40]))

# ═══════════════════ 3. 安全 ═══════════════════
print("\n── 安全 ──")
saved = TOKEN; TOKEN = ""
s, d = req("POST", "/api/reconciliations", {"date": "2026-06-08", "card_balance": 100})
t("3.1 对账需登录", s, d, lambda x: s in (401,403))
s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100, "category": "日常", "account": "微信"})
t("3.2 支出需登录", s, d, lambda x: s in (401,403))
TOKEN = saved

# ═══════════════════ 4. P2 修复验证 ═══════════════════
print("\n── P2修复 密码校验 ──")
for pw, label in [("Abcdefgh","缺数字+特殊"), ("Abc12345","缺特殊"), ("Abcdefgh!","缺数字")]:
    s, d = req("POST", "/register", {"username": f"p2test_{pw[:4]}", "password": pw, "email": f"{pw[:4]}@t.com"})
    msg = d.get("message","")
    t(f"P2 {label}", s, d, lambda x: ("须" in msg or "8+" in msg, msg[:50]))

# ═══════════════════ 结果 ═══════════════════
print(f"\n{'='*50}")
print(f"  ✅ {p}  ❌ {f}")
print(f"{'='*50}")
sys.exit(0 if f == 0 else 1)
