#!/usr/bin/env python3
"""Register + Forgot Password tests — no auth needed"""
import json, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://8.135.58.90:8601"

def req(method, path, data=None, params=None, headers=None):
    url = f"{BASE}{path}"
    if params: url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json"}
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
        ok = status < 400; detail = f"status={status}"
    if ok: p += 1; print(f"  OK {label}")
    else: f += 1; print(f"  XX {label}: {detail} | {json.dumps(data, ensure_ascii=False)[:100]}")

# ============ Register ============
print("=== Register ===")

s, d = req("POST", "/register", {"username": "reg_t1", "password": "Test1234!", "email": "reg1@test.com"})
t("1.1 normal", s, d, lambda x: x.get("status")=="ok" and x.get("email"))

s, d = req("POST", "/register", {"username": "reg_t1", "password": "Test1234!", "email": "reg1b@test.com"})
t("1.2 dup unverified (overwrites)", s, d, lambda x: x.get("status")=="ok")

s, d = req("POST", "/register", {"username": "reg_t2", "password": "short", "email": "reg2@test.com"})
t("1.3 weak pw ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "", "password": "Test1234!", "email": "a@t.com"})
t("1.4 empty user ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "reg_t3", "password": "Test1234!", "email": ""})
t("1.5 empty email ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "<script>xss</script>", "password": "Test1234!", "email": "x@t.com"})
t("1.6 XSS username ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "中文用户测试", "password": "Test1234!", "email": "cn@test.com"})
t("1.7 chinese username", s, d, lambda x: x.get("status")=="ok")

for lang, lcode in [("zh-CN","cn"),("zh-TW","tw"),("en","en")]:
    s, d = req("POST", "/register", {"username": f"i18n_{lcode}", "password": "x", "email": f"i{lcode}@t.com"}, headers={"X-Lang": lang})
    t(f"1.8 i18n weak pw({lang})", s, d, lambda x: (s==400, d.get("message","")[:50]))

s, d = req("POST", "/register", {"username": "reg_t4", "password": "Test1234!", "email": "notanemail"})
t("1.9 bad email format ->400", s, d, lambda x: s==400)

# P2 verify
for pw, desc in [("Abcdefgh","no digit+special"), ("Abc12345","no special")]:
    s, d = req("POST", "/register", {"username": f"p2v_{pw[:3]}", "password": pw, "email": f"{pw[:3]}@t.com"})
    msg = d.get("message","")
    t(f"1.10 P2 {desc}", s, d, lambda x: ("8+" in msg or "须" in msg, msg[:60]))

# ============ Forgot Password ============
print("\n=== Forgot Password ===")

s, d = req("POST", "/forgot-password", {"email": "reg1@test.com"})
t("2.1 exists (no leak)", s, d, lambda x: d.get("status")=="ok")

s, d = req("POST", "/forgot-password", {"email": "nobody@nowhere.xyz"})
t("2.2 nonexistent (no leak)", s, d, lambda x: d.get("status")=="ok")

s, d = req("POST", "/forgot-password", {"email": ""})
t("2.3 empty ->400", s, d, lambda x: s==400)

s, d = req("POST", "/forgot-password", {"email": "notanemail"})
t("2.4 bad format ->400", s, d, lambda x: s==400)

s, d = req("POST", "/reset-password", {"email": "reg1@test.com", "code": "000000", "password": "NewTest123!"})
t("2.5 wrong code ->401", s, d, lambda x: s==401)

s, d = req("POST", "/reset-password", {"email": "", "code": "123456", "password": "NewTest123!"})
t("2.6 empty email ->400", s, d, lambda x: s==400)

# ============ Result ============
print(f"\n{'='*40}")
print(f"  PASS: {p}    FAIL: {f}")
print(f"{'='*40}")
sys.exit(0 if f == 0 else 1)
