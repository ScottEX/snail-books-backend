# 生产环境部署检查清单 🍜

每次部署到生产环境（8600 / www.rowanlan.xyz）后，**必须逐项完成以下检查**，全部通过才算完成。

---

## 部署前：分支合并（⚠️ 两个仓库都要检查）

```bash
# ── 1. 前端 snail-books-web ──
cd /Users/lanx/projects/snail-books-web
git checkout main && git pull gh-ssh main --no-edit
git merge develop --no-edit
git push gh-ssh main    # main 受保护，走 gh pr create + merge

# ── 2. 后端 snail-books-backend ──
cd /Users/lanx/projects/snail-books-backend
git checkout develop && git pull gh-https develop --no-edit
# 后续流程：改 config.py APP_ENV → production → PR 合并 → CI 部署 → 还原 staging
```

> ⚠️ **后端 CI 拉的是前端 main 分支构建 web bundle。**
> 前端 develop 修了但 main 没合 = 部署的还是旧前端。两个仓库的 main 都必须是最新。

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
systemctl show snail-books -p Environment | tr ' ' '\n' | grep -E 'RESEND|FLASK_SECRET|FRONTEND|APK|ADMIN|DB_PATH'
```

**必须存在以下变量（缺一不可）：**

| 变量 | 说明 | 缺失后果 |
|---|---|---|
| `RESEND_API_KEY` | 邮件发送 API key | 忘记密码/冷静期邮件**静默失败** |
| `RESEND_FROM` | 发件人地址 | 同上 |
| `FLASK_SECRET_KEY` | Session 签名密钥 | **服务启动即崩溃**（`Worker failed to boot`） |
| `FRONTEND_DIR` | 前端静态文件路径 | JS bundle 500 → 白屏 |
| `APK_ALGORITHM` | 密码加密算法 | 登录/注册失败 |
| `APK_KEY` | 密码加密密钥 | 登录/注册失败 |
| `ADMIN_USER_ID` | 管理员 user_id | 管理员注销无保护、管理员 API 鉴权失败 |

### B3. override.conf 完整性

```bash
cat /etc/systemd/system/snail-books.service.d/override.conf
```

- 文件存在且有内容
- `RESEND_API_KEY` **完整无截断**（不能用 heredoc 写入，会被截断 → 401）
- `FRONTEND_DIR` 指向 `/opt/snail-books-backend/static/web-build/dist`（不是 staging 路径）
- `ADMIN_USER_ID` 值与生产管理员实际 user_id 一致

---

## C. 数据库 Schema 对齐

### C1. users 表完整列清单

生产环境的 `users` 表**必须包含以下所有列**：

| 列名 | 类型 | 用途 |
|---|---|---|
| id | INTEGER PK | 用户 ID |
| username | TEXT UNIQUE | 用户名 |
| password | TEXT | 密码哈希 |
| email | TEXT | 邮箱 |
| verification_code | TEXT | 验证码 |
| code_expires | TIMESTAMP | 验证码过期时间 |
| is_verified | INTEGER | 是否已验证 |
| reset_code | TEXT | 重置密码码 |
| reset_expires | TIMESTAMP | 重置码过期时间 |
| signature | TEXT | 个性签名 |
| enforce_single_session | INTEGER | 单会话强制 |
| session_timeout_hours | INTEGER | 会话超时小时 |
| current_session_id | TEXT | 当前会话 ID |
| created_at | TIMESTAMP | 注册时间 |
| **is_disabled** | INTEGER | 账号禁用标识（admin API） |
| **phone** | TEXT | 手机号（admin API 编辑） |
| **role** | TEXT | 角色（admin API 编辑） |
| **remark** | TEXT | 备注（admin API 编辑） |
| **delete_scheduled** | TIMESTAMP | 注销冷静期到期时间 |
| **delete_by** | TEXT | 注销操作人（admin/self） |
| **delete_reminded** | INTEGER | 是否已发送冷静期提醒 |

粗体列是最近新增列，生产环境容易缺失。

### C2. schema 快速对比（生产 vs 代码定义）

```bash
ssh root@8.135.58.90 "python3 -c \"
import sqlite3
db = sqlite3.connect('/opt/snail-books-backend/data/snail.db')
cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
required = {'id','username','password','email','verification_code','code_expires','is_verified','reset_code','reset_expires','signature','enforce_single_session','session_timeout_hours','current_session_id','created_at','is_disabled','phone','role','remark','delete_scheduled','delete_by','delete_reminded'}
missing = required - cols
if missing:
    print(f'MISSING: {missing}')
else:
    print('users table: OK')
\""
```

### C3. 全库 schema 对比（生产 vs staging）

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

**输出必须为空。** 有差异说明生产缺字段。

### C4. 如果缺字段 — 补全

```bash
ssh root@8.135.58.90 'python3 -c "
import sqlite3
db = sqlite3.connect(\"/opt/snail-books-backend/data/snail.db\")
migrations = [
    (\"users\", \"signature TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"enforce_single_session INTEGER DEFAULT 1\"),
    (\"users\", \"session_timeout_hours INTEGER DEFAULT 1\"),
    (\"users\", \"current_session_id TEXT DEFAULT NULL\"),
    (\"users\", \"is_disabled INTEGER DEFAULT 0\"),
    (\"users\", \"phone TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"role TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"remark TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"delete_scheduled TIMESTAMP\"),
    (\"users\", \"delete_by TEXT DEFAULT \\\"\\\"\"),
    (\"users\", \"delete_reminded INTEGER DEFAULT 0\"),
    (\"transactions\", \"user_id INTEGER\"),
    (\"transactions\", \"procurement_batch_id INTEGER\"),
    (\"transactions\", \"images TEXT DEFAULT \\\"\\\"\"),
    (\"transactions\", \"date TEXT DEFAULT \\\"\\\"\"),
    (\"transactions\", \"thumb_images TEXT DEFAULT \\\"[]\\\"\"),
    (\"partners\", \"user_id INTEGER\"),
    (\"dividends\", \"user_id INTEGER\"),
    (\"dividends\", \"date TEXT DEFAULT \\\"\\\"\"),
    (\"procurement_batches\", \"user_id INTEGER\"),
    (\"procurement_batches\", \"thumb_images TEXT DEFAULT \\\"[]\\\"\"),
    (\"procurement_items\", \"user_id INTEGER\"),
    (\"procurements\", \"user_id INTEGER\"),
    (\"products\", \"user_id INTEGER\"),
    (\"products\", \"supplier TEXT DEFAULT \\\"\\\"\"),
    (\"platform_fees\", \"shangou_waimai REAL DEFAULT 0\"),
    (\"platform_fee_entries\", \"shangou_waimai REAL DEFAULT 0\"),
    (\"user_tokens\", \"session_id TEXT\"),
    (\"daily_revenue\", \"archived INTEGER DEFAULT 0\"),
    (\"reconciliations\", \"bill_date TEXT\"),
    (\"reconciliations\", \"reconciled_by TEXT\"),
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
    device_info TEXT DEFAULT \"\",
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)\"\"\")
db.commit()
print(\"Schema migration done.\")
"'
```

---

## D. 功能验证

### D0. 部署完整性检查（一站式）

```bash
# 1. 确认 nginx 指向正确端口
curl -sk -o /dev/null -w "HTTP %{http_code} Size %{size_download}\n" https://www.rowanlan.xyz/

# 2. 首页 HTML 正常加载（非 0 字节）
curl -sk https://www.rowanlan.xyz/ | head -5

# 3. API 版本端点正常
curl -s https://www.rowanlan.xyz/api/frontend-version

# 4. 登录 API 正常（返回 JSON 而非 500）
curl -s -X POST https://www.rowanlan.xyz/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"test"}'

# 5. 管理员 API 存活
curl -s https://www.rowanlan.xyz/api/admin/check
```

### D1. 管理员注销保护验证

**必须在生产环境生效**——管理员账户不能被注销：

1. 以管理员身份登录生产环境
2. 进入「个人中心」
3. 点击「注销账户」
4. 确认返回错误提示（不应进入冷静期）

等价 API 测试：
```bash
# 登录后尝试注销管理员（响应应为 400，不是 200/201）
curl -s -b cookies.txt -X POST https://www.rowanlan.xyz/api/users/<ADMIN_UID>/delete \
  -H "Content-Type: application/json"
# 预期：{"status":"error","message":"管理员账户不能注销"}
```

### D2. 普通用户注销冷静期验证

1. 以非管理员用户登录
2. 注销账户
3. 确认提示「3 天冷静期」
4. 在冷静期内重新登录，确认账户自动恢复

### D3. 用户管理后台验证（仅管理员）

1. 管理员登录后，首页应显示「用户管理」入口
2. 点击进入，列表正常加载
3. 搜索（中文/拼音/邮箱）正常
4. 状态筛选（正常/禁用）正常
5. 点击用户进入详情页
6. 编辑手机号/邮箱/角色/备注 → 保存成功
7. 点击状态徽章 → 启禁用用户

### D4. 日志无异常

```bash
ssh root@8.135.58.90 "journalctl -u snail-books --since '2 min ago' --no-pager | grep -iE 'error|500|traceback|OperationalError|KeyError|no such column'"
```

**输出必须为空。** 如果有 `no such column` / `KeyError` / `OperationalError`，回到 C 节检查 schema。

---

## E. 代码版本一致性

### E1. 生产代码版本确认

```bash
# 本地 main 分支 HEAD
git log --oneline -1 main

# GitHub Actions 最后一次生产部署
# 在 https://github.com/ScottEX/snail-books-backend/actions 查看 main 分支最新 run
```

### E2. 关键文件确认已部署

部署到生产服务器的文件清单：
- `app.py` — DB 初始化 + 迁移
- `routes/admin.py` — 管理后台 API
- `routes/profile.py` — 用户注销逻辑
- `routes/auth.py` — 认证
- `shared/auth.py` — 认证中间件 + `delete_user_cascade`
- `shared/email.py` — 邮件发送
- `shared/db.py` — 数据库连接
- `shared/i18n.json` — 多语言翻译
- `i18n_backend.py` — 后端 i18n 辅助

### E3. shared/auth.py 硬编码检查 ⚠️

```bash
grep 'ADMIN_USER_ID' /opt/snail-books-backend/shared/auth.py
```

⚠️ `shared/auth.py` 中有一行 `ADMIN_USER_ID = '64'` **硬编码**（用于 `delete_user_cascade` 数据转移），这个值**应改为读取环境变量**，否则在生产上会把被删用户的业务数据转移给不存在的 user 64。

**当前已知问题**：如果生产管理员 user_id 不是 64，则用户注销时业务数据会丢失。

---

## F. 快速一键检查脚本

保存为 `check-prod.sh`：

```bash
#!/bin/bash
set -e
HOST="root@8.135.58.90"

echo "=== A1: nginx proxy_pass ==="
ssh $HOST "grep proxy_pass /etc/nginx/sites-enabled/rowanlan.xyz"

echo "=== B1: service status ==="
ssh $HOST "systemctl is-active snail-books"

echo "=== B2: environment variables ==="
ssh $HOST "systemctl show snail-books -p Environment | tr ' ' '\n' | grep -E 'RESEND|FLASK_SECRET|FRONTEND|APK|ADMIN|DB_PATH'"

echo "=== C2: users table columns ==="
ssh $HOST "python3 -c \"
import sqlite3
db = sqlite3.connect('/opt/snail-books-backend/data/snail.db')
cols = {r[1] for r in db.execute('PRAGMA table_info(users)')}
required = {'is_disabled','phone','role','remark','delete_scheduled','delete_by','delete_reminded'}
missing = required - cols
print('MISSING:', missing) if missing else print('users: OK')
\""

echo "=== C3: full schema diff ==="
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
    print(\"  OK — schema matches\")
"'

echo "=== D1: homepage ==="
curl -sk -o /dev/null -w "HTTP %{http_code}  size %{size_download}\n" https://www.rowanlan.xyz/

echo "=== D1: API health ==="
curl -s https://www.rowanlan.xyz/api/admin/check

echo "=== D4: recent errors ==="
ssh $HOST "journalctl -u snail-books --since '2 min ago' --no-pager | grep -iE 'error|500|traceback|OperationalError|KeyError' || echo '  OK — no errors'"

echo "=== E3: auth.py ADMIN_USER_ID hardcode ==="
ssh $HOST "grep 'ADMIN_USER_ID' /opt/snail-books-backend/shared/auth.py"

echo ""
echo "=== ALL CHECKS DONE ==="
```

---

## 部署流程速查

```
1. 确认 develop 已测通 → merge 到 main
2. git push → 触发 GitHub Actions
3. 等待 CI 全绿（test ✅ → deploy-production ✅ → deploy-staging skipped）
4. 运行 check-prod.sh 或逐项检查 A→F
5. 手动验证 D1、D2、D3（管理员注销/用户注销/用户管理）
6. 全部通过 → 部署完成
```

---

## 历史踩坑记录

| 日期 | 问题 | 根因 | 检查项 |
|---|---|---|---|
| 2026-06-10 | 生产管理员注销无保护 | develop 代码未合并到 main | E2 |
| 2026-06-10 | nginx 代理到 staging | `proxy_pass` 配成 8601 | A1 |
| 2026-06-10 | 数据库缺字段白屏 | schema 未迁移 | C1/C3 |
| 2026-06-10 | 忘记密码邮件不发送 | `RESEND_API_KEY` 缺失 | B2 |
| 2026-06-10 | API key 截断 401 | heredoc 写入不完整 | B3 |
| 2026-06-08 | 服务启动即崩溃 | `FLASK_SECRET_KEY` 缺失 | B2 |
| 2026-06-08 | JS 返回 500 白屏 | `FRONTEND_DIR` 路径错误 | B2 |
| 2026-06-08 | Python 3.12 语法错误 | 全角标点/未闭合 docstring | CI build |
| 2026-06-08 | `mimetypes` 未定义 | merge 丢失 import | CI build |
| 2026-06-07 | `shared/auth.py` 硬编码 ADMIN_USER_ID='64' | 未读环境变量 | E3 |

---

**此文件放在 `docs/` 目录下，每次生产部署后对照检查。**
