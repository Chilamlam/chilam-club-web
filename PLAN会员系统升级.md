# Chilam Club 会员系统升级方案

## 背景

当前 `chilam-club-web` 是一个纯展示型的 Streamlit 投资驾驶舱，两个 VIP 页面（强势股 / 投机套利 / 投资作业本）没有任何访问控制。

**目标**：加上邮箱登录 + VIP 订阅体系，管理员可在后台管理用户和订阅。

---

## 技术选型

| 层级 | 选型 | 理由 |
|---|---|---|
| 数据库 | **SQLite** (SQLAlchemy ORM) | 项目规模小，无需额外服务；后续可平滑迁移 PostgreSQL |
| 认证 | **JWT** (PyJWT) + bcrypt | 标准做法，Session 存 Streamlit `st.session_state`，JWT 用于 API 鉴权 |
| 邮件验证 | 首次版本**跳過**，后续可接入 SendGrid / 飞书云邮箱 |
| 后台管理 | 同 Streamlit 内，增设 `/admin` 入口，需 `is_admin=True` |

---

## 数据模型

```sql
-- 用户表
users: id, email, password_hash, is_admin, created_at

-- VIP 订阅表
subscriptions: id, user_id, plan_name, status, start_at, expires_at
```

订阅状态：`active` / `cancelled` / `expired`

---

## 文件改造计划

### 新增文件
```
auth.py              # 登录/注册/JWT 逻辑
models.py            # SQLAlchemy 模型定义
database.py          # DB 初始化和 CRUD 封装
admin.py             # 后台管理页面
pages/auth.py        # 登录注册 UI（Streamlit page）
pages/dashboard.py   # 会员 Dashboard（展示订阅状态）
```

### 改造文件
```
app.py               # 加 auth 中间件，保护 VIP 页面路由
requirements.txt     # + bcrypt, PyJWT, SQLAlchemy
```

---

## 页面与权限对照

| 页面 | 访问要求 |
|---|---|
| 全市场看板 | 公开 |
| 黄金分割预测 | 公开 |
| 使用说明文档 | 公开 |
| 强势股 / 投机套利 / 投资作业本 | **需 VIP 订阅且未过期** |
| 后台管理 | **需 admin=True** |

未登录用户访问 VIP 页面 → 跳转登录  
已登录但无 VIP → 展示订阅提示页，可一键联系管理员

---

## 实现顺序

1. **基础骨架** — `models.py` + `database.py`（建表 + CRUD）
2. **认证模块** — `auth.py`（注册/登录/JWT/密码加密）
3. **登录页** — `pages/auth.py`（邮箱 + 密码表单）
4. **会员 Dashboard** — `pages/dashboard.py`（显示订阅状态 / 到期日）
5. **权限拦截** — `app.py` 入口加 auth 中间件，保护 VIP 路由
6. **后台管理** — `admin.py`（管理员查看用户列表 / 手动开通订阅）

---

## 关键实现细节

### 认证流程
```
注册 → bcrypt(password) → 写入 users 表
登录 → bcrypt(verify) → 返回 JWT → 存 st.session_state["token"]
```

### VIP 鉴权装饰器
```python
def require_vip(func):
    def wrapper(*args, **kwargs):
        if not check_vip(st.session_state.get("user_id")):
            st.warning("请先订阅 VIP")
            st.stop()
        return func(*args, **kwargs)
    return wrapper
```

### 订阅过期检查
```python
def is_vip_active(user_id):
    sub = db.get_active_subscription(user_id)
    return sub and sub.expires_at > datetime.now()
```

### Admin 后台
- 用户列表（含邮箱 / 注册时间 / VIP 状态）
- 手动新建订阅（输入邮箱 + 选择套餐 + 设置到期日）
- 查看订阅详情

---

## 订阅套餐

| 套餐 | 时长 |
|---|---|
| 月度 VIP | 1 个月 |
| 季度 VIP | 3 个月 |
| 年度 VIP | 12 个月 |

---

## 邮件验证

- **首次版本**：注册即生效（跳过验证）
- **后续扩展**：接入 SendGrid / 飞书云邮箱，支持验证链接

---

## Admin 初始账号

- 首次部署时自动创建：`chilam` / `chilam666`
- 密码 bcrypt 加密存储

---

## 收款方案

### 套餐定价

| 套餐 | 国内（人民币） | 海外（美元） |
|---|---|---|
| 月度 VIP | ¥25 | $25 |
| 季度 VIP | ¥60 | $60 |
| 年度 VIP | ¥200 | $200 |

### 支付渠道（全部保留）

| 渠道 | 适用场景 | 接入方式 |
|---|---|---|
| **微信支付** | 国内用户 | 直连（微信支付商家版）+ 聚合平台（备用） |
| **支付宝** | 国内用户 | 直连（支付宝当面付）+ 聚合平台（备用） |
| **Stripe** | 海外用户 | Stripe Checkout / Payment Links |

### 接入策略
- **Stripe**：代码先行，配置好 API Key 后即可启用
- **微信/支付宝**：直连方案（已有企业对公账户）；同时预留聚合平台（如 Paddle/LemonSqueezy）切换接口，一旦直连审核受阻可快速切换
- **支付回调**：Webhook 接收支付结果通知，写入 subscriptions 表激活 VIP

### 数据模型扩展

```sql
-- 支付记录表
payments: id, user_id, plan_name, amount, currency, payment_method, status, transaction_id, paid_at, created_at
```

支付状态：`pending` / `completed` / `failed` / `refunded`

---

## 验证方式

1. 新注册账号登录后，VIP 页面应显示"未订阅"
2. 后台手动开通后，刷新页面立即解锁
3. 订阅过期后，页面重新锁定
4. Admin 账户可进入后台，普通用户不能

---

## 预计改动量

- 新增 5 个文件，约 600 行代码
- 改造 `app.py`，约 80 行
- 无破坏性修改，原有功能保持不变

---

**下一步**：确认技术方案后，开始实现。先做第 1-2 步（数据模型 + 认证），还是你有其他优先级？