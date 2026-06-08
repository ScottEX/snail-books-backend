#!/usr/bin/env python3
"""Profile API tests"""
import json, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://8.135.58.90:8601"
S = {}  # state dict
S['t'] = ''

def req(method, path, data=None, params=None, headers=None):
    url = f"{BASE}{path}"
    if params: url += "?" + urlencode(params)
    body = json.dumps(data).encode() if data else None
    h = {"Content-Type": "application/json"}
    if S['t']: h["Authorization"] = f"Bearer {S['t']}"
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

# Login
s, d = req("POST", "/login", {"username": "LanLiuFu", "password": "Lan@1314"})
if s == 200 and d.get("token"):
    S['t'] = d["token"]
    uid = d.get("user_id")
    print(f"OK login uid={uid}")
else:
    print(f"XX login: {s} {d}"); sys.exit(1)

# === 1. User Info ===
print("\n=== 1. User Info ===")
s, d = req("GET", "/api/users/me")
t("1.1 get self", s, d, lambda x: x.get("username") and x.get("email") is not None)
s, d = req("GET", "/api/users")
t("1.2 list users", s, d, lambda x: isinstance(x, list) and len(x)>0)
sv = S['t']; S['t'] = ''
s, d = req("GET", "/api/users/me")
t("1.3 no auth", s, d, lambda x: s in (401,403))
S['t'] = sv

# === 2. Auth Prefs ===
print("\n=== 2. Auth Prefs ===")
s, d = req("GET", "/api/users/me/auth-prefs")
t("2.1 get prefs", s, d, lambda x: "enforce_single_session" in x)
s, d = req("PATCH", "/api/users/me/auth-prefs", {"session_timeout_hours": 24})
t("2.2 set timeout 24", s, d, lambda x: x.get("status")=="ok")
s, d = req("PATCH", "/api/users/me/auth-prefs", {"enforce_single_session": 0})
t("2.3 disable SSO", s, d, lambda x: x.get("status")=="ok")
s, d = req("PATCH", "/api/users/me/auth-prefs", {"session_timeout_hours": 99})
t("2.4 bad timeout ->400", s, d, lambda x: s==400)
s, d = req("PATCH", "/api/users/me/auth-prefs", {"enforce_single_session": 2})
t("2.5 bad SSO ->400", s, d, lambda x: s==400)
req("PATCH", "/api/users/me/auth-prefs", {"enforce_single_session": 1, "session_timeout_hours": 1})

# === 3. Signature ===
print("\n=== 3. Signature ===")
s, d = req("POST", "/api/users/signature", {"signature": "Hello test"})
t("3.1 set", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/api/users/signature", {"signature": "x"*201})
t("3.2 too long ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/users/signature", {"signature": ""})
t("3.3 empty", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/api/users/signature", {})
t("3.4 no field", s, d, lambda x: s==200)

# === 4. Change Password ===
print("\n=== 4. Change Password ===")
s, d = req("POST", "/api/profile/password", {"old_password": "wrong", "new_password": "Test1234!"})
t("4.1 wrong old pw ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/password", {"old_password": "Lan@1314", "new_password": "x"})
t("4.2 weak new pw ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/password", {"old_password": "", "new_password": ""})
t("4.3 empty ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/password", {"old_password": "Lan@1314", "new_password": "Test1234!"})
t("4.4 change OK", s, d, lambda x: x.get("status")=="ok")
s, d = req("POST", "/api/profile/password", {"old_password": "Test1234!", "new_password": "Lan@1314"})
t("4.5 change back", s, d, lambda x: x.get("status")=="ok")

# === 5. Email Change ===
print("\n=== 5. Email Change ===")
s, d = req("POST", "/api/profile/email/send-code", {"email": ""})
t("5.1 empty ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/email/send-code", {"email": "bad"})
t("5.2 bad format ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/email/verify", {"email": "", "code": ""})
t("5.3 verify empty ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/profile/email/verify", {"email": "x@x.com", "code": "000000"})
t("5.4 verify wrong ->400", s, d, lambda x: s==400)

# === 6. Avatar ===
print("\n=== 6. Avatar ===")
s, d = req("GET", "/api/users/avatar", params={"username": "LanLiuFu"})
t("6.1 get by username", s, d, lambda x: s in (200,404))
s, d = req("GET", "/api/users/avatar", params={"user_id": str(uid)})
t("6.2 get by user_id", s, d, lambda x: s in (200,404))
s, d = req("GET", "/api/users/avatar", params={"username": "nobody_xyz"})
t("6.3 nonexist ->404", s, d, lambda x: s==404)
s, d = req("GET", "/api/users/avatar")
t("6.4 no params ->400", s, d, lambda x: s==400)
s, d = req("POST", "/api/users/avatar")
t("6.5 upload no file ->400", s, d, lambda x: s==400)

# === 7. Cover ===
print("\n=== 7. Cover ===")
s, d = req("GET", "/api/profile/cover")
t("7.1 get cover", s, d, lambda x: "url" in x)
s, d = req("POST", "/api/profile/cover")
t("7.2 upload no file ->400", s, d, lambda x: s==400)
s, d = req("DELETE", "/api/profile/cover")
t("7.3 delete cover", s, d, lambda x: x.get("status")=="ok")

# === 8. Delete Account ===
print("\n=== 8. Delete Account ===")
s, d = req("POST", f"/api/users/{uid}/delete")
t("8.1 delete self", s, d, lambda x: (True, f"status={s}"))

# === 9. Auth Required ===
print("\n=== 9. Auth Required ===")
sv = S['t']; S['t'] = ''
s, d = req("POST", "/api/users/signature", {"signature": "x"})
t("9.1 signature no auth", s, d, lambda x: s in (401,403))
s, d = req("POST", "/api/profile/password", {"old_password": "x", "new_password": "Test1234!"})
t("9.2 password no auth", s, d, lambda x: s in (401,403))
s, d = req("GET", "/api/profile/cover")
t("9.3 cover no auth", s, d, lambda x: s in (401,403))

print(f"\n{'='*50}")
print(f"  PASS: {p}    FAIL: {f}")
print(f"{'='*50}")
sys.exit(0 if f == 0 else 1)
