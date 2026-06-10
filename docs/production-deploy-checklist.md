# 生产环境部署检查清单 🍜

每次部署到生产环境（8600）后，**必须逐项完成以下检查**，全部通过才算完成。

---

## A. Nginx 配置

```bash
ssh root@8.135.58.90
```

### A1. proxy_pass 指向正确端口

```bash
grep proxy_pass /etc/nginx/sites-enabled/rowanlan.xyz
```

- ✅ `proxy_pass http://127.0.0.1:8600;` —— 生产端口
- ❌ `8601` —— staging 端口，**错误！** 会导致用户命中测试数据、白屏

### A2. SSL 证书有效

```bash
openssl s_client -connect www.rowanlan.xyz:443 -servername www.rowanlan.xyz </dev/null 2>/dev/null | openssl x509 -noout -dates
```

---

## B. Systemd 服务配置

### B1. 服务状态

```bash
systemctl is-active snail-books
```

必须是 `active`，**不是** `activating` 或 `failed`。

### B2. 环境变量完整性

```bash
systemctl show snail-books -p Environment | tr ' ' '\n' | grep -E 'RESEND|FLASK_SECRET|FRONTEND|APK'
```

**必须存在以下 6 个变量（缺一不可）：**

| 变量 | 说明 | 缺失后果 |
|---|---|---|
| `RESEND_API_KEY` | 邮件发送 API key | 忘记密码/冷静期邮件**静默失败**（`email.py` 返回 True 但并不发送） |
| `RESEND_FROM` | 发件人地址 | 同上 |
| `FLASK_SECRET_KEY` | Session 签名密钥 | **服务启动即崩溃**（`Worker failed to boot`） |
| `FRONTEND_DIR` | 前端静态文件路径 | JS bundle 返回 500 → 白屏 |
| `APK_ALGORITHM` | 密码加密算法 | 登录/注册失败 |
| `APK_KEY` | 密码加密密钥 | 登录/注册失败 |

### B3. override.conf 完整性

```bash
cat /etc/systemd/system/snail-books.service.d/override.conf
```

- 文件存在且有内容
- `RESEND_API_KEY` **完整无截断**（不能用 heredoc 写入，会被截断 → 401）
- `FRONTEND_DIR` 指向 `/opt/snail-books-backend/static/web-build/dist`（不是 staging 路径）

> **⚠️ 新部署环境首次配置时**：从 staging 的 override.conf 复制，只需改 `FRONTEND_DIR` 路径。
> ```bash
> cp /etc/systemd/system/snail-books-staging.service.d/override.conf \
>    /etc/systemd/system/snail-books.service.d/override.conf
> sed -i 's|backend-staging|backend|g' /etc/systemd/system/snail-books.service.d/override.conf
> systemctl daemon-reload && systemctl restart snail-books
> ```

---

## C. 数据库 Schema 对齐

### C1. schema 对比（生产 vs staging）

```bash
ssh root@8.135.58.90 'python3 -c "
import sqlite3
prod = sqlite3.connect(\"/opt/snail-books-backend/data/snail.db\")
stag = sqlite3.connect(\"/opt/snail-books-backend-staging/data/snail.db\")
for t in sorted({r[0] for r in prod.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\")} & {r[0] for r in stag.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\")}):
    p = {r[1] for r in prod.execute(f\"PRAGMA table_info({t})\")}
    s = {r[1] for r in stag.execute(f\"PRAGMA table_info({t})\")}
    if p != s:
        print(f\"{t}: prod only={p-s}, stag only={s-p}\")
"'
```

**输出必须为空。** 如果有差异，说明生产缺字段 → 代码 `SELECT *` 访问缺失列会 KeyError → 500 → 前端白屏。

### C2. 如果缺字段 — 补全

```bash
ssh root@8.135.58.90 'python3 -c "
import sqlite3
db = sqlite3.connect(\"/opt/snail-books-backend/data/snail.db\")
migrations = [
    (\"users\", \"signature TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"enforce_single_session INTEGER DEFAULT 1\"),
    (\"users\", \"session_timeout_hours INTEGER DEFAULT 1\"),
    (\"users\", \"current_session_id TEXT DEFAULT NULL\"),
    (\"transactions\", \"user_id INTEGER\"),
    (\"transactions\", \"procurement_batch_id INTEGER\"),
    (\"partners\", \"user_id INTEGER\"),
    (\"dividends\", \"user_id INTEGER\"),
    (\"procurement_batches\", \"user_id INTEGER\"),
    (\"procurement_items\", \"user_id INTEGER\"),
    (\"procurements\", \"user_id INTEGER\"),
    (\"products\", \"user_id INTEGER\"),
    (\"platform_fees\", \"shangou_waimai REAL DEFAULT 0\"),
    (\"platform_fee_entries\", \"shangou_waimai REAL DEFAULT 0\"),
    (\"user_tokens\", \"session_id TEXT\"),
]
for t, col in migrations:
    try:
        db.execute(f\"ALTER TABLE {t} ADD COLUMN {col}\")
        print(f\"  + {t}.{col}\")
    except:
        pass
db.commit()
db.execute(\"\"\"CREATE TABLE IF NOT EXISTS user_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    session_id TEXT NOT NULL UNIQUE,
    device_info TEXT DEFAULT \\\"\\\",
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)\"\"\")
db.commit()
print(\"Schema migration done.\")
" && systemctl restart snail-books'
```

---

## D. 功能验证

### D1. 首页能正常加载

```bash
curl -sk -o /dev/null -w "%{http_code} %{size_download}" https://www.rowanlan.xyz/
```

- ✅ `200 6872`（或任何 >0 的 size）
- ❌ `200 0` —— 0 字节说明 nginx 代理到了错误端口或 Flask 没返回内容

### D2. API 健康检查

```bash
curl -s https://www.rowanlan.xyz/api/frontend-version
```

返回 JSON，不是空或 HTML。

### D3. 认证接口正常

```bash
# 测试登录
curl -s -X POST https://www.rowanlan.xyz/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}'
```

返回 JSON（即使账号不存在也应返回 JSON 错误，不是 500）。

### D4. 日志无异常

```bash
ssh root@8.135.58.90 "journalctl -u snail-books --since '2 min ago' --no-pager | grep -iE 'error|500|traceback|OperationalError|KeyError'"
```

**输出必须为空。** 如果有 `no such column` / `KeyError` / `OperationalError`，回到 C 节检查 schema。

---

## E. 快速检查脚本（一键）

把以下内容保存为 `check-prod.sh`：

```bash
#!/bin/bash
# 生产环境一键检查
set -e
HOST="root@8.135.58.90"

echo "=== A1: nginx proxy_pass ==="
ssh $HOST "grep proxy_pass /etc/nginx/sites-enabled/rowanlan.xyz"

echo "=== B1: service status ==="
ssh $HOST "systemctl is-active snail-books"

echo "=== B2: environment variables ==="
ssh $HOST "systemctl show snail-books -p Environment | grep -oP 'RESEND|FLASK_SECRET|FRONTEND|APK' | sort"

echo "=== C1: schema diff ==="
ssh $HOST 'python3 -c "
import sqlite3
prod = sqlite3.connect(\"/opt/snail-books-backend/data/snail.db\")
stag = sqlite3.connect(\"/opt/snail-books-backend-staging/data/snail.db\")
issues = 0
for t in sorted({r[0] for r in prod.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\")} & {r[0] for r in stag.execute(\"SELECT name FROM sqlite_master WHERE type=\\\"table\\\"\")}):
    p = {r[1] for r in prod.execute(f\"PRAGMA table_info({t})\")}
    s = {r[1] for r in stag.execute(f\"PRAGMA table_info({t})\")}
    if p != s:
        issues += 1
        print(f\"  MISMATCH {t}: prod only={p-s}, stag only={s-p}\")
if issues == 0:
    print(\"  OK - schema matches\")
"'

echo "=== D1: homepage ==="
curl -sk -o /dev/null -w "HTTP %{http_code}  size %{size_download}\n" https://www.rowanlan.xyz/

echo "=== D4: recent errors ==="
ssh $HOST "journalctl -u snail-books --since '2 min ago' --no-pager | grep -iE 'error|500|traceback|OperationalError|KeyError' || echo '  OK - no errors'"

echo ""
echo "=== ALL CHECKS DONE ==="
```

---

## 历史踩坑记录

| 日期 | 问题 | 根因 | 检查项 |
|---|---|---|---|
| 2026-06-10 | nginx 代理到 staging | `proxy_pass` 配成 8601 | A1 |
| 2026-06-10 | 数据库缺字段白屏 | schema 未迁移 | C1 |
| 2026-06-10 | 忘记密码邮件不发送 | `RESEND_API_KEY` 缺失 | B2 |
| 2026-06-10 | API key 截断 401 | heredoc 写入不完整 | B3 |
| 2026-06-08 | 服务启动即崩溃 | `FLASK_SECRET_KEY` 缺失 | B2 |
| 2026-06-08 | JS 返回 500 白屏 | `FRONTEND_DIR` 路径错误 | B2 |
| 2026-06-08 | Python 3.12 语法错误 | 全角标点/未闭合 docstring | CI build |
| 2026-06-08 | `mimetypes` 未定义 | merge 丢失 import | CI build |

---

**此文件放在 `docs/` 目录下，每次生产部署后对照检查。**
