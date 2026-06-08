#!/usr/bin/env python3
"""Daily Revenue API tests — test env"""
import json, sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

BASE = "http://8.135.58.90:8601"
S = {'t': ''}

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
    print(f"OK login uid={d.get('user_id')}")
else:
    print(f"XX login: {s} {d}"); sys.exit(1)

today = "2026-06-09"

# ============ 1. Create ============
print("\n=== 1. Create ===")
s, d = req("POST", "/api/daily-revenue", {
    "date": today, "revenue": 5000.00, "turnover": 5200.00, "jd_revenue": 300.00, "note": "test day"
})
t("1.1 create", s, d, lambda x: x.get("status")=="ok")
rec_id = d.get("data", {}).get("id") if d.get("data") else None

# Duplicate date
s, d = req("POST", "/api/daily-revenue", {
    "date": today, "revenue": 1000, "turnover": 1000
})
t("1.2 duplicate date ->409", s, d, lambda x: s==409)

# Missing fields
s, d = req("POST", "/api/daily-revenue", {"revenue": 100})
t("1.3 missing turnover ->400", s, d, lambda x: s==400)

s, d = req("POST", "/api/daily-revenue", {"turnover": 100})
t("1.4 missing date ->400", s, d, lambda x: s==400)

# ============ 2. Get List ============
print("\n=== 2. Get List ===")
s, d = req("GET", "/api/daily-revenue")
t("2.1 all records", s, d, lambda x: "records" in x and "total" in x)

s, d = req("GET", "/api/daily-revenue", params={"date": today})
t("2.2 by date", s, d, lambda x: "records" in x)

s, d = req("GET", "/api/daily-revenue", params={"year": 2026, "month": 6})
t("2.3 by year/month", s, d, lambda x: "records" in x)

s, d = req("GET", "/api/daily-revenue", params={"year": 2026})
t("2.4 by year only", s, d, lambda x: "records" in x)

s, d = req("GET", "/api/daily-revenue", params={"date_from": "2026-06-01", "date_to": "2026-06-30"})
t("2.5 date range", s, d, lambda x: "records" in x)

s, d = req("GET", "/api/daily-revenue", params={"days": 30})
t("2.6 last 30 days", s, d, lambda x: "totals" in x)

s, d = req("GET", "/api/daily-revenue", params={"page": 1, "per_page": 5})
t("2.7 pagination", s, d, lambda x: "records" in x and "pages" in x)

# ============ 3. Last 7 ============
print("\n=== 3. Last 7 Days ===")
s, d = req("GET", "/api/daily-revenue/last-7")
t("3.1 last 7 days", s, d, lambda x: "records" in x and len(x["records"])==7)
t("3.2 has status field", s, d, lambda x: all("status" in r for r in x["records"]))

# ============ 4. Totals ============
print("\n=== 4. Totals ===")
s, d = req("GET", "/api/daily-revenue/total")
t("4.1 totals", s, d, lambda x: "total_revenue" in x and "total_turnover" in x)

# ============ 5. Update ============
print("\n=== 5. Update ===")
if rec_id:
    s, d = req("PUT", f"/api/daily-revenue/{rec_id}", {"revenue": 5500.00, "note": "updated"})
    t("5.1 update", s, d, lambda x: x.get("status")=="ok")

s, d = req("PUT", "/api/daily-revenue/99999", {"revenue": 100})
t("5.2 nonexistent ->404", s, d, lambda x: s==404)

s, d = req("PUT", f"/api/daily-revenue/{rec_id}", {})
t("5.3 no fields ->400", s, d, lambda x: s==400)

# ============ 6. Delete ============
print("\n=== 6. Delete ===")
if rec_id:
    s, d = req("DELETE", f"/api/daily-revenue/{rec_id}")
    t("6.1 delete", s, d, lambda x: x.get("status")=="ok")

# ============ 7. Business Summary ============
print("\n=== 7. Business Summary ===")
s, d = req("GET", "/api/business-summary")
t("7.1 summary", s, d, lambda x: "actual_received" in x and "cash_on_hand" in x)

# ============ 8. Auth Required ============
print("\n=== 8. Auth Required ===")
sv = S['t']; S['t'] = ''
s, d = req("GET", "/api/daily-revenue")
t("8.1 no auth", s, d, lambda x: s in (401,403))
s, d = req("POST", "/api/daily-revenue", {"date": today, "turnover": 100})
t("8.2 create no auth", s, d, lambda x: s in (401,403))
S['t'] = sv

# ============ Result ============
print(f"\n{'='*50}")
print(f"  PASS: {p}    FAIL: {f}")
print(f"{'='*50}")
sys.exit(0 if f == 0 else 1)
