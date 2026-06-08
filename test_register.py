#!/usr/bin/env python3
"""Register tests — no auth needed"""
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
        ok, detail = status < 400, f"status={status}"
    if ok: p += 1; print(f"  OK {label}")
    else: f += 1; print(f"  XX {label}: {detail} | {json.dumps(data, ensure_ascii=False)[:100]}")

print("--- Register ---")

s, d = req("POST", "/register", {"username": "r1_auto", "password": "Test1234!", "email": "r1@test.com"})
t("1. normal", s, d, lambda x: x.get("status")=="ok")

s, d = req("POST", "/register", {"username": "r1_auto", "password": "Test1234!", "email": "r2@test.com"})
t("2. duplicate ->409", s, d, lambda x: s==409)

s, d = req("POST", "/register", {"username": "r3_auto", "password": "123", "email": "r3@test.com"})
t("3. weak pw ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "", "password": "Test1234!", "email": "a@test.com"})
t("4. empty username ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "r5_auto", "password": "Test1234!", "email": ""})
t("5. empty email ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "<script>xss</script>", "password": "Test1234!", "email": "xss@t.com"})
t("6. XSS username ->400", s, d, lambda x: s==400)

s, d = req("POST", "/register", {"username": "cn_test_user", "password": "Test1234!", "email": "cn@test.com"})
t("7. chinese username", s, d, lambda x: x.get("status")=="ok")

for lang in ["zh-CN","zh-TW","en"]:
    s, d = req("POST", "/register", {"username": f"wl_{lang.replace('-','')}", "password": "123", "email": f"wl{lang[:2]}@t.com"}, headers={"X-Lang": lang})
    t(f"8. weak pw i18n({lang})", s, d, lambda x: (s==400, d.get("message","")[:50]))

# P2 fix verify
print("\n--- P2: password UX fix ---")
for pw, desc in [("Abcdefgh","no digit+special"), ("Abc12345","no special"), ("Abcdefgh!","no digit")]:
    s, d = req("POST", "/register", {"username": f"p2_{pw[:4]}", "password": pw, "email": f"{pw[:4]}@t.com"})
    msg = d.get("message","")
    t(f"P2 {desc}", s, d, lambda x: ("8+" in msg or len(msg)>15, msg[:60]))

print(f"\n{'='*40}")
print(f"  PASS: {p}    FAIL: {f}")
print(f"{'='*40}")
sys.exit(0 if f == 0 else 1)
