# 蓝姐螺蛳粉 · 后端开发规范

## 技术栈
- **语言**: Python 3
- **框架**: Flask
- **数据库**: SQLite（默认 `/opt/snail-books/data/snail.db`，可通过 `DB` 环境变量覆盖）
- **部署**: Docker + gunicorn，或直接 gunicorn + systemd
- **前端**: SPA 模式，serve `../snail-books-web/dist/` 下的构建产物

## API 设计

### 通用约定
- 所有 `/api/*` 路由返回 JSON，格式 `{"status": "ok|error", ...}`
- 认证所需的路由用 `@login_required` 装饰器
- 错误消息必须走 i18n 后端翻译（`_t('key', g.lang)`）

### 分页
- 列表接口每页 20 条，返回 `{items: [...], page: N, pages: N, total: N}`
- 前端用 `?page=N` 翻页

### 命名
- URL 全部小写，单词用连字符（已存在的下划线保持兼容）
- API 路由：`/api/<资源名复数>`
- 示例：`/api/transactions`、`/api/dividends`

## 数据库

### 迁移
- **绝对不要** 在 `executescript()` 里放 `ALTER TABLE ADD COLUMN`
- 正确方式：Python `try/except` 逐列添加
```python
for col, col_type in [('email','TEXT'),('is_verified','INTEGER DEFAULT 0')]:
    try:
        db.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')
    except:
        pass
```

### 查询
- `/api/partners` **必须** LEFT JOIN dividends 计算 `total_dividends`
- 不做 JOIN 时前端 `reduce` 得到 `NaN`

## 认证

### 双重通道
- Session cookie（网页版同源）
- Bearer token（iOS WKWebView 跨域）
- `login_required` 装饰器同时支持两种

### 密码规则
- 最少 6 位，必须同时包含字母和数字
- 前端 + 后端双重校验

## 部署

### Docker 部署
```bash
# 先在 snail-books-web 构建前端
cd ../snail-books-web && npm run build:web

# 构建并启动后端
docker build -t snail-books .
docker run -d -p 8600:8600 -v /opt/snail-books/data:/opt/snail-books/data snail-books
```

### VPS 直接部署
```bash
rsync -avz app.py i18n_backend.py root@8.135.58.90:/opt/snail-books/
ssh root@8.135.58.90 'systemctl restart snail-books'
```

### 重启后验证（必须全部通过）
1. `systemctl is-active snail-books` → active
2. `curl -s -o /dev/null -w '%{http_code}' localhost:8600/login` → 200
3. `curl -s localhost:8600/api/summary` → JSON（前端构建后测试 SPA 页面）

## OTA 更新
- `/api/frontend-version` 返回当前版本号
- `/api/frontend.zip` 打包 `../snail-books-ios/www/` 供 iOS 端下载更新
- iOS 端 `updater.js` 轮询版本号自动更新

## 代码规范

### 禁止事项
- `secret_key` 不要用 `secrets.token_hex(32)`（重启后 session 全部失效）
- 不要用 shell `sed` 改 Python 代码
- 不要在 VPS 上直接改代码——所有改动先在 Mac 完成再部署

### 必须事项
- 每个新路由必须加 `@login_required`
- API 错误返回带 i18n 翻译的 `message` 字段
- 部署后跑 `journalctl -u snail-books --no-pager -n 10` 确认无报错
