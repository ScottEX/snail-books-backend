#!/usr/bin/env python3
"""snail-books full test — test env"""
import json, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://8.135.58.90:8601"
TOKEN = None

def req(method, path, data=None, params=None, headers=None):
    url = f"{BASE}{path}"
    if params: url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json"}
    if TOKEN: h["Authorization"] = f"Bearer {TOKEN}"
    if headers: h.update(headers)
    try:
        resp = urlopen(Request(url, data=body, headers=h, method=method))
        try: return resp.status, json.loads(resp.read())
        except: return resp.status, {}
    except HTTPError as e:
        try: return e.code, json.loads(e.read())
        except: return e.code, {}

p = f = 0
def t(label, status, data, check=None):
    global p, f
    if check:
        r = check(data)
        ok, detail = (r if isinstance(r, tuple) else (bool(r), str(r)))
    else:
        ok = status < 400; detail = f"status={status}"
    if ok: p += 1; print(f"  OK {label}")
    else: f += 1; print(f"  XX {label}: {detail} | {json.dumps(data, ensure_ascii=False)[:100]}")

def do_login(u="LanLiuFu", pw="Lan@1314"):
    global TOKEN
    s, d = req("POST", "/login", {"username": u, "password": pw})
    if s == 200:
        t = d.get("token")
        if t:
            TOKEN = t
            return True
    return False

# ============ 1. Register ============
print("=== 1. Register ===")
s, d = req("POST", "/register", {"username": "f1", "password": "Test1234!", "email": "f1@t.com"})
t("1.1 normal", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/register", {"username": "f1", "password": "Test1234!", "email": "f1b@t.com"})
t("1.2 dup unverified OK", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/register", {"username": "f2", "password": "x", "email": "f2@t.com"})
t("1.3 weak pw ->400", s, d, lambda x: s==400)
s, d = req("POST", "/register", {"username": "", "password": "Test1234!", "email": "a@t.com"})
t("1.4 empty user ->400", s, d, lambda x: s==400)
s, d = req("POST", "/register", {"username": "f3", "password": "Test1234!", "email": ""})
t("1.5 empty email ->400", s, d, lambda x: s==400)
s, d = req("POST", "/register", {"username": "<script>x</script>", "password": "Test1234!", "email": "x@t.com"})
t("1.6 XSS ->400", s, d, lambda x: s==400)
s, d = req("POST", "/register", {"username": "chinese_user", "password": "Test1234!", "email": "cn@t.com"})
t("1.7 chinese user", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/register", {"username": "f4", "password": "Test1234!", "email": "bad"})
t("1.8 bad email ->400", s, d, lambda x: s==400)

for lang in ["zh-CN","zh-TW","en"]:
    s, d = req("POST", "/register", {"username": f"i18_{lang[:2]}", "password": "x", "email": f"i{lang[:2]}@t.com"}, headers={"X-Lang": lang})
    t(f"1.9 i18n({lang})", s, d, lambda x: (s==400, d.get("message","")[:50]))

for pw, desc in [("Abcdefgh","P2 no digit+special"), ("Abc12345","P2 no special")]:
    s, d = req("POST", "/register", {"username": f"p2_{pw[:3]}", "password": pw, "email": f"{pw[:3]}@t.com"})
    msg = d.get("message","")
    t(f"1.10 {desc}", s, d, lambda x: ("8+" in msg or "xum" in msg, msg[:60]))

# ============ 2. Forgot Password ============
print("\n=== 2. Forgot Password ===")
s, d = req("POST", "/forgot-password", {"email": "f1@t.com"})
t("2.1 exists no leak", s, d, lambda x: d.get("status")=="ok")
s, d = req("POST", "/forgot-password", {"email": "nobody@x.xyz"})
t("2.2 nonexist no leak", s, d, lambda x: d.get("status")=="ok")
s, d = req("POST", "/forgot-password", {"email": ""})
t("2.3 empty ->400", s, d, lambda x: s==400)
s, d = req("POST", "/forgot-password", {"email": "bad"})
t("2.4 bad format ->400", s, d, lambda x: s==400)
s, d = req("POST", "/reset-password", {"email": "f1@t.com", "code": "000000", "password": "NewTest123!"})
t("2.5 wrong code ->401", s, d, lambda x: s==401)

# ============ 3. Login ============
print("\n=== 3. Login ===")
if not do_login():
    print("XX Cannot login, aborting"); sys.exit(1)
print("  OK logged in")

s, d = req("POST", "/login", {"username": "LanLiuFu", "password": "wrong"})
t("3.1 wrong pw", s, d, lambda x: (s in (401,429), f"status={s}"))
s, d = req("POST", "/login", {"username": "nobody_x", "password": "x"})
t("3.2 nonexistent", s, d, lambda x: (s in (401,429), f"status={s}"))
s, d = req("POST", "/login", {"username": "", "password": ""})
t("3.3 empty ->400", s, d, lambda x: s==400)
s, d = req("POST", "/login", {"username": "lanliufu", "password": "Lan@1314"})
t("3.4 case-insensitive", s, d, lambda x: (d.get("token") is not None, ""))
do_login()  # restore session after case-insensitive login revokes it

for lang in ["zh-CN","zh-TW","en"]:
    s, d = req("POST", "/login", {"username": "LanLiuFu", "password": "wrong"}, headers={"X-Lang": lang})
    t(f"3.5 i18n({lang})", s, d, lambda x: (s in (401,429), d.get("message","")[:40]))

# ============ 4. Reconciliation ============
print("\n=== 4. Reconciliation ===")
s, d = req("POST", "/api/reconciliations", {
    "date": "2026-06-09", "bill_date": "2026-06-08",
    "card_balance": 5000.50, "cash_balance": 200.00,
    "dine_in": 3000, "meituan": 1500, "flash_sale": 300, "jd": 200, "tuan": 100,
    "reconciled_by": "LanLiuFu"
})
t("4.1 create", s, d, lambda x: x.get("ok"))

s, d = req("GET", "/api/reconciliations", params={"per_page": 5, "limit": 5})
t("4.2 list", s, d, lambda x: isinstance(x, list) or "records" in x)
if isinstance(d, list) and len(d) > 0:
    r = d[0]
    ch = r["dine_in"]+r["meituan"]+r["flash_sale"]+r["jd"]+r["tuan"]
    rt = r["card_balance"]+r["cash_balance"]
    ed = round(rt-ch, 2)
    t("4.3 channel_total", s, d, lambda x: (abs(r["channel_total"]-ch)<0.01, f"{r['channel_total']} vs {ch}"))
    t("4.4 real_total", s, d, lambda x: (abs(r["real_total"]-rt)<0.01, f"{r['real_total']} vs {rt}"))
    t("4.5 diff", s, d, lambda x: (abs(r["diff"]-ed)<0.01, f"{r['diff']} vs {ed}"))

for label, body in [
    ("4.6 no date", {"card_balance": 100}),
    ("4.7 bad fmt", {"date": "bad", "card_balance": 100}),
    ("4.8 negative", {"date": "2026-06-08", "card_balance": -100}),
    ("4.9 overflow", {"date": "2026-06-08", "card_balance": 1e11}),
    ("4.10 XSS", {"date": "2026-06-08", "card_balance": 100, "reconciled_by": "<script>"}),
]:
    s, d = req("POST", "/api/reconciliations", body)
    t(f"{label} ->400", s, d, lambda x: s==400)

s, d = req("GET", "/api/reconciliations", params={"page": 1, "per_page": 3})
t("4.11 pagination", s, d, lambda x: "records" in d and "total" in d)
s, d = req("GET", "/api/reconciliations", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})
t("4.12 date filter", s, d, lambda x: s==200)
s, d = req("GET", "/api/reconciliations", params={"reconciled_by": "LanLiuFu"})
t("4.13 user filter", s, d, lambda x: s==200)

s, d = req("POST", "/api/reconciliations", {"card_balance": 100}, headers={"X-Lang": "en"})
has_cn = any(ord(c)>127 for c in d.get("error",""))
t("BUG1 en returns CN", s, d, lambda x: (has_cn, f"'{d.get('error','')}'"))

# ============ 5. Expense ============
print("\n=== 5. Expense ===")
s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 88.50, "category": "daily", "account": "WeChat", "note": "test", "date": "2026-06-09"})
t("5.1 create", s, d, lambda x: d.get("status")=="ok")
s, d = req("GET", "/api/transactions", params={"type": "expense", "per_page": 5})
t("5.2 list", s, d, lambda x: "transactions" in d)
if d.get("transactions"):
    eid = d["transactions"][0]["id"]
    s2, d2 = req("DELETE", f"/api/transactions/{eid}")
    t("5.3 delete", s2, d2, lambda x: d2.get("status")=="ok")
s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100})
t("5.4 missing ->400", s, d, lambda x: s==400)
s, d = req("GET", "/api/transactions", params={"type": "expense", "page": 1, "per_page": 3})
t("5.5 pagination", s, d, lambda x: "transactions" in d and "total" in d)
s, d = req("GET", "/api/transactions", params={"type": "expense", "date_from": "2026-06-01", "date_to": "2026-06-30"})
t("5.6 date filter", s, d, lambda x: s==200)
s, d = req("GET", "/api/transactions", params={"type": "expense", "category": "daily"})
t("BUG2 category filter", s, d, lambda x: (s==200, f"status={s}"))
s, d = req("POST", "/api/transactions", {"type": "invalid", "amount": 100, "category": "daily", "account": "WeChat"})
t("BUG3 invalid type", s, d, lambda x: (s==400, f"status={s}"))
for lang in ["zh-CN","zh-TW","en"]:
    s, d = req("POST", "/api/transactions", {"type": "expense", "amount": 100}, headers={"X-Lang": lang})
    t(f"5.7 i18n({lang})", s, d, lambda x: (s==400, d.get("message","")[:40]))

# ============ 6. Auth Required ============
print("\n=== 6. Auth Required ===")
saved = TOKEN
TOKEN = None
s, d = req("GET", "/api/reconciliations", params={"limit": 1})
t("6.1 recon no auth", s, d, lambda x: s in (401,403))
s, d = req("GET", "/api/transactions")
t("6.2 expense no auth", s, d, lambda x: s in (401,403))
TOKEN = saved

# ============ 7. Logout ============
print("\n=== 7. Logout ===")
s, d = req("GET", "/logout")
t("7.1 GET CSRF ->405", s, d, lambda x: s==405)
s, d = req("POST", "/logout")
t("7.2 POST logout", s, d, lambda x: d.get("status")=="ok")
TOKEN = None
s, d = req("GET", "/api/reconciliations", params={"limit": 1})
t("7.3 token invalid", s, d, lambda x: s in (401,403))

# ============ Result ============
print(f"\n{'='*50}")
print(f"  PASS: {p}    FAIL: {f}")
print(f"{'='*50}")
sys.exit(0 if f == 0 else 1)
