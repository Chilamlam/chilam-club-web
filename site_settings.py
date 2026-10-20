# -*- coding: utf-8 -*-
"""站点运营开关：存在 Supabase，管理员在后台改，前台读。

为什么单独一个模块
------------------
1. **两个页面要共用同一份缓存**。前台（app.py 侧边栏）读开关，后台
   （pages/admin.py）改开关后要能立刻让前台失效。Streamlit 每次重跑都会
   重新 exec 页面脚本，写在 app.py 里的函数对象不稳定，后台页又 import
   不到它（`import app` 会再执行一次 `set_page_config` 直接报错）。
   抽成模块后，两边拿到的是同一个对象，`.clear()` 才清得掉。
2. **不能放进 database.py**：那层是纯数据访问；而「读失败该显示还是隐藏」
   是展示层策略，混进数据层会让"取数失败"和"业务判定"再次纠缠。

设计要点（都是踩过的坑）
------------------------
- **读失败 ≠ 关闭**。三条路径必须分开：读到 / 明确没配过 / 读失败。
  前两者可以静默，读失败要显式告警——否则网络抖动和管理员关店在页面上
  长得一模一样，出问题时无从判断。
- **兜底方向选「隐藏」**。表里没记录或读失败时回落代码默认值（当前为关），
  因为把已关的店铺又露出来，比少显示一个入口更糟。
- **链接只认 http/https**。后台是管理员在写，但一旦管理员账号被盗，
  开放伪协议（`javascript:`）就等于给自己埋 XSS；这里做最小校验，
  不合法就不渲染，而不是"照单全收"。
- **缓存 60 秒**，后台保存时主动清。关店是「立刻要生效」的事，不能等 TTL。
"""

from __future__ import annotations

import streamlit as st

import database as db

# ---- 三项设置（key 与 init_site_settings.sql 里的初始数据一致）----
KEY_ENABLED = "shop_entry_enabled"
KEY_LABEL = "shop_entry_label"
KEY_URL = "shop_entry_url"

SHOP_KEYS = (KEY_ENABLED, KEY_LABEL, KEY_URL)

# 代码侧默认值：表里没有记录、或读失败时回落到这里。
# 换商品**不需要**改这里 —— 在后台页改三项并保存即可；只有想改「永久默认」才动它。
SHOP_ENTRY_DEFAULT_ENABLED = False
SHOP_ENTRY_DEFAULT_LABEL = "🛒 全天候小店"
SHOP_ENTRY_DEFAULT_URL = "https://shop.chilam.club"

# 缓存秒数：后台保存会主动 clear，这里的 TTL 只是「别每次重跑都打一次数据库」。
CACHE_TTL = 60


def _truthy(value) -> bool:
    """把 DB 里的文本转成布尔。只认明确的真值，其余一律 False。

    不写 `bool(value)`：字符串 `"0"` 在 Python 里是 True，那会让「关」变成「开」。
    """
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def is_safe_url(url: str) -> bool:
    """只允许 http/https 绝对链接。

    为什么必须校验：这个 URL 会被渲染成可点击按钮。若允许 `javascript:`，
    管理员账号一旦被盗，攻击者就能给所有访客挂 XSS；`data:` 同理。
    这里是最小防线，不替代「管好管理员账号」。
    """
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def read_shop_entry() -> dict:
    """**纯读取，不缓存**（缓存包装见 `shop_entry_config`）。

    返回：`{"enabled": bool, "label": str, "url": str, "source": str,
             "err": dict|None, "warn": str}`
    `source` 取值：`"db"` / `"default"`（表里没配）/ `"db-read-failed"`
    """
    vals, err = db.get_site_settings(list(SHOP_KEYS))
    if err:
        return {
            "enabled": SHOP_ENTRY_DEFAULT_ENABLED,
            "label": SHOP_ENTRY_DEFAULT_LABEL,
            "url": SHOP_ENTRY_DEFAULT_URL,
            "source": "db-read-failed",
            "err": err,
            "warn": ("读取店铺开关失败，已按默认值（关闭）处理。"
                     "原因：{msg}（HTTP {status}）"),
        }

    if not vals:
        # 表存在但一条都没配 → 沿用代码默认，不报错
        return {
            "enabled": SHOP_ENTRY_DEFAULT_ENABLED,
            "label": SHOP_ENTRY_DEFAULT_LABEL,
            "url": SHOP_ENTRY_DEFAULT_URL,
            "source": "default",
            "err": None,
            "warn": "",
        }

    label = str(vals.get(KEY_LABEL) or SHOP_ENTRY_DEFAULT_LABEL).strip() \
        or SHOP_ENTRY_DEFAULT_LABEL
    url = str(vals.get(KEY_URL) or SHOP_ENTRY_DEFAULT_URL).strip()
    enabled = _truthy(vals.get(KEY_ENABLED))

    warn = ""
    # 开关是开的，但链接不合法 → 不渲染，并告警。宁可入口消失，
    # 也不要渲染一个可能带伪协议的按钮。
    if enabled and not is_safe_url(url):
        warn = f"后台配置的店铺链接不合法（只允许 http/https），已暂不显示入口：{url!r}"
        enabled = False

    return {
        "enabled": enabled,
        "label": label,
        "url": url,
        "source": "db",
        "err": None,
        "warn": warn,
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def shop_entry_config() -> dict:
    """带缓存的读取（前台用这个）。

    缓存的是「数据库往返」，不是「最终结论」——所以后台保存后必须
    调 `clear_shop_entry_cache()`，否则最长要等 CACHE_TTL 才生效。
    """
    return read_shop_entry()


def clear_shop_entry_cache() -> None:
    """清掉前台那份缓存。后台保存成功后调用，让改动立即对所有访客生效。"""
    try:
        shop_entry_config.clear()
    except Exception:
        # 缓存对象不可用（例如被换成了普通函数）不该让保存流程失败
        pass


def save_shop_entry(enabled: bool, label: str, url: str,
                    updated_by: str | None = None) -> tuple:
    """保存三项设置，返回 `(ok, err, warn)`。成功后会清前台缓存。

    `warn` 用于「存进去了但渲染时会被拒」的情况（例如链接非 http/https），
    让管理员当场看到，而不是等发现入口不显示再来查。
    """
    warn = ""
    if not is_safe_url(url):
        warn = f"链接不是 http/https 开头，前台会拒绝渲染：{url!r}"

    items = [
        (KEY_ENABLED, "1" if enabled else "0"),
        (KEY_LABEL, label or SHOP_ENTRY_DEFAULT_LABEL),
        (KEY_URL, url or ""),
    ]
    for k, v in items:
        ok, err = db.set_site_setting(k, v, updated_by=updated_by)
        if not ok:
            return False, err, warn
    clear_shop_entry_cache()
    return True, None, warn
