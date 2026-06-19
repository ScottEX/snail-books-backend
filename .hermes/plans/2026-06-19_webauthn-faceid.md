# WebAuthn Face ID 登录 — 实现方案

> **域名:** test.rowanlan.xyz（测试环境）
> **共存:** 面容登录 + 密码登录并存
> **绑定:** 登录后去设置页手动开启

---

## 架构概览

```
用户点「面容登录」
  → GET /api/webauthn/login/begin    后端返回随机 challenge
  → navigator.credentials.get()     iOS 弹 Face ID，签名 challenge
  → POST /api/webauthn/login/complete 后端验签 → 创建 session → 登录成功
```

绑定流程：
```
用户去设置页点「开启面容登录」
  → GET /api/webauthn/register/begin   后端返回 challenge + 用户信息
  → navigator.credentials.create()     iOS 弹 Face ID，生成密钥对
  → POST /api/webauthn/register/complete 后端存公钥
```

---

## 后端改动

### 文件清单

| 操作 | 路径 |
|------|------|
| 新建 | `routes/webauthn.py` |
| 修改 | `app.py`（注册 blueprint） |
| 修改 | `shared/db.py`（建表） |
| 修改 | `i18n_backend.py`（加提示语） |
| 可能需要 | `requirements.txt`（加 `webauthn` 库，或手写验签） |

### 数据库新表

```sql
CREATE TABLE IF NOT EXISTS webauthn_credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    credential_id TEXT NOT NULL UNIQUE,
    public_key TEXT NOT NULL,
    sign_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
```

### API 设计

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/webauthn/login/begin` | 无 | 开始面容登录，返回 challenge |
| POST | `/api/webauthn/login/complete` | 无 | 验签 + 建 session |
| GET | `/api/webauthn/register/begin` | login_required | 开始绑定，返回 challenge |
| POST | `/api/webauthn/register/complete` | login_required | 验签 + 存公钥 |
| GET | `/api/webauthn/status` | login_required | 是否已绑定 |
| DELETE | `/api/webauthn/credentials` | login_required | 解绑 |

### 验签方案

两种选择：

**A. 用 `webauthn` 库**（PyPI 有，1 个依赖）
- 优点：标准流程，不用手写 crypto
- 缺点：需要 `pip install webauthn`，CI 之外要手动装

**B. 手写验签**（基于 `cryptography` 库，后端已有）  
- 优点：零依赖，逻辑透明
- 缺点：大概 80 行 crypto 代码

**推荐 A**，`webauthn` 库处理 CBOR/ASN.1/签名验证细节，避免踩坑。

---

## 前端改动

### 文件清单

| 操作 | 路径 |
|------|------|
| 修改 | `src/api/client.ts`（加 5 个 API 方法） |
| 修改 | `src/screens/LoginScreen.tsx`（加「面容登录」按钮） |
| 修改 | `src/screens/profile/ProfileScreen.tsx`（加绑定开关） |
| 修改 | `src/screens/profile/useProfileForms.ts`（绑定逻辑） |
| 修改 | `src/i18n.tsx`（加中文/英文提示） |

### LoginScreen 改动

在密码输入框下方加一个按钮：
```
「🔒 面容登录」
```
点击后调用 `webauthnLogin()`：
1. 调 `/api/webauthn/login/begin` → 拿到 challenge
2. 调 `navigator.credentials.get()` → Face ID 弹窗
3. 调 `/api/webauthn/login/complete` → 拿 session
4. 成功 → 进主页；失败 → 提示

### ProfileScreen 改动

在「更换邮箱」附近加一行设置：
```
面容登录  [开关]
```
- 未绑定时点开关 → 触发绑定流程
- 已绑定时关开关 → 调 DELETE 解绑

### 浏览器兼容

只支持 iOS Safari 14+（已满足），前端判断：
```ts
const supportsWebAuthn = typeof window !== 'undefined' 
  && window.PublicKeyCredential !== undefined;
```
不支持的设备不显示面容登录按钮。

---

## 任务拆分

### 后端（4 个任务）

#### Task 1: 建表 + 加 webauthn 依赖
- `shared/db.py` 加建表 SQL
- `requirements.txt` 加 `webauthn`
- SSH 到测试环境手动 `pip install webauthn`（新 pip 包 CI 不会自动装）
- `git commit`

#### Task 2: 创建 webauthn blueprint + login 接口
- 建 `routes/webauthn.py`
- 实现 `login/begin`（生成 challenge 存 session）
- 实现 `login/complete`（验签 + 登录）
- 有未覆盖的异常走 500 handler 兜底
- `git commit`

#### Task 3: 注册/状态/删除接口
- 实现 `register/begin` + `register/complete`
- 实现 `status`
- 实现 `credentials` DELETE
- `git commit`

#### Task 4: 注册 blueprint + i18n
- `app.py` 注册 blueprint
- `i18n_backend.py` 加相关提示语
- `git commit`

### 前端（4 个任务）

#### Task 5: API 层加 webauthn 方法
- `src/api/client.ts` 加 5 个方法
- `git commit`

#### Task 6: LoginScreen 加面容登录按钮
- 加按钮 + 调 webauthn 登录流程
- 不支持的设备隐藏按钮
- `git commit`

#### Task 7: ProfileScreen 加绑定开关
- 安全设置区加「面容登录」开关行
- 绑定/解绑逻辑
- `git commit`

#### Task 8: i18n 前端文案
- `src/i18n.tsx` 三语加键
- `git commit`

---

## 验收标准

1. 在 iPhone Safari 打开 test.rowanlan.xyz
2. 密码登录 → 进设置 → 点「面容登录」开关 → Face ID 弹窗 → 绑定成功
3. 退出登录 → 登录页出现「面容登录」按钮 → 点它 → Face ID 弹窗 → 登录成功
4. 关掉开关 → 解绑 → 面容登录按钮消失

---

## 风险

- `webauthn` 库在 Python 3.12 下可能有兼容问题（需验证）
- iOS Safari WebAuthn 要求 `userVerification: "required"` 才能弹 Face ID
- 测试环境 CI 部署后，新 pip 包需手动 `pip install`（CI 只推文件，不跑 `pip install -r requirements.txt`）
- CBOR 解析需要 `cbor2` 库（`webauthn` 的依赖）
