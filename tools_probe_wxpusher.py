"""
WxPusher 推送通道自检（不联网也能跑，联网时额外做真实接口断言）

为什么必须有这个自检：推送这条链的失败几乎全是**静默的**——
UID 不存在、appToken 写错、顶层返回 success=true 而单个 UID 失败、
Server酱 配额耗尽仍返 HTTP 200。没有一条会抛异常，日志看起来全是绿的。
所以这里的断言重点不是「函数能跑」，而是「失败必须被判成失败」。

联网断言只在 WXPUSHER_APP_TOKEN 存在时执行，缺凭据时跳过而不算失败
（CI 上没配 token 也应该能跑通结构性断言）。

退出码：0 全通过 / 1 有断言失败
"""
from __future__ import annotations

import json
import os
import re
import sys

FAIL: list[str] = []
N = 0


def ck(cond: bool, label: str) -> None:
    global N
    N += 1
    if not cond:
        FAIL.append(label)


def read(p: str) -> str:
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# ================= 1. wxpusher.py 结构 =================

def check_module() -> None:
    src = read("wxpusher.py")
    # 必须匹配「行首的 import 语句」而不是子串：文档里合法地写着
    # 「不 import streamlit」，用子串匹配会被自己的注释误伤，
    # 而这类假失败会训练人忽略自检输出——比没有自检更糟。
    ck(re.search(r"(?m)^\s*(import streamlit|from streamlit\b)", src) is None,
       "wxpusher.py 不得 import streamlit（否则无法脱 runtime 单测）")

    ck("MAX_UIDS_PER_CALL = 2000" in src, "单次 UID 上限须为官方值 2000")
    for fn in ("def app_token", "def create_qrcode", "def scan_uid",
               "def send_to_uids", "def list_followers"):
        ck(fn in src, f"wxpusher.py 缺 {fn}")

    # 逐 UID 校验是本模块存在的核心理由
    ck(re.search(r"it\s*or\s*\{\}\)\.get\(\"code\"\)\s*==\s*1000", src) is not None,
       "send_to_uids 必须逐条校验 data[].code == 1000（顶层 code=1000 时单 UID 仍可能失败）")
    ck("无投递明细" in src,
       "顶层成功但缺 data[] 时必须按失败处理，不能假装送达")

    # 凭据只从环境变量取，且不得出现在日志里
    ck('os.getenv("WXPUSHER_APP_TOKEN")' in src, "appToken 必须从环境变量读取")
    for m in re.finditer(r"print\(([^\n]*)\)", src):
        seg = m.group(1)
        ck("tok" not in seg and "app_token" not in seg,
           f"日志不得回显 appToken：{seg[:60]}")

    # HTTPError 不兜底 —— 4xx 是语义错误，重试只会掩盖它
    i_http = src.find("urllib.error.HTTPError")
    i_curl = src.find("_curl(url, body)")
    ck(0 < i_http < i_curl,
       "HTTPError 分支必须在 curl 兜底之前 return（服务端明确拒绝不该被当成网络抖动重试）")

    # 空内容拒发
    ck("拒绝发送" in src, "空内容必须拒发（绝不推「今天没有数据」）")
    # summary 强制单行
    ck("def _clean_summary" in src and '" ".join' in src,
       "通知栏摘要必须强制单行（含换行会被服务端拒绝或显示错乱）")


# ================= 2. daily_digest.py 渠道分工 =================

def check_digest() -> None:
    src = read("daily_digest.py")

    ck("def send_wxpusher" in src, "daily_digest 缺 send_wxpusher（用户投递主通道）")
    ck("def recipients" in src, "daily_digest 缺 recipients()")
    ck("def subscriber_emails" not in src, "旧的 subscriber_emails 应已被 recipients 取代")

    # Server酱 必须降级为管理员告警：签名改成 (title, body) 就不可能再被当成用户通道用
    ck(re.search(r"def send_serverchan\(title: str, body: str\)", src) is not None,
       "send_serverchan 签名须为 (title, body) —— 表明它发的是告警而非摘要投递")
    ck("def _admin_alert" in src, "缺 _admin_alert（运维告警组装）")
    # 告警只在有问题时发，否则白耗 5 条/天配额
    ck(re.search(r"if not problems:\s*\n\s*return", src) is not None,
       "无问题时必须不发告警（免费版仅 5 条/天）")
    # 「无人可投」必须算问题
    ck("有效订阅 0 位" in src, "「有效订阅 0 位」必须触发告警——这是最易被忽略的失败形态")

    # 主流程渠道顺序：wxpusher 在 email 之前，且 serverchan 不在投递列表里
    m = re.search(r"for f in \(send_wxpusher\(.*?send_email\(.*?\)\)", src, re.S)
    ck(m is not None, "主流程投递列表须为 (send_wxpusher, send_wecom, send_email)")
    if m:
        ck("send_serverchan" not in m.group(0),
           "send_serverchan 不得出现在用户投递列表里")

    # configured 判定必须包含 WXPUSHER_APP_TOKEN，且不再把 SERVERCHAN 当投递渠道
    m2 = re.search(r"configured = any\(_env\(k\) for k in \(([^)]*)\)", src, re.S)
    ck(m2 is not None, "缺 configured 判定")
    if m2:
        seg = m2.group(1)
        ck("WXPUSHER_APP_TOKEN" in seg, "configured 须包含 WXPUSHER_APP_TOKEN")
        ck("DIGEST_SERVERCHAN_KEY" not in seg,
           "configured 不得包含 DIGEST_SERVERCHAN_KEY（它不是用户投递渠道）")

    # 邮件不得与微信重复轰炸
    ck("not r.get(\"wxpusher_uid\")" in src,
       "邮件只发给未绑微信的用户（同一条摘要收两遍会导致退订）")

    # 个性化必须一人一份
    ck(re.search(r"dg\.build_markdown\(watchlist=wl", src) is not None,
       "有自选股的用户必须单独渲染个性化正文")

    # 文档必须写明 Server酱 不能群发，避免下次又被配成用户通道
    ck("群发不支持" in src or "群发：不支持" in src,
       "模块文档须明确 Server酱 不支持群发，防止再次误用")


# ================= 3. database.py 名单取数 =================

def check_database() -> None:
    src = read("database.py")
    for fn in ("def update_user_wxpusher_uid", "def get_user_wxpusher_uid",
               "def get_push_recipients"):
        ck(fn in src, f"database.py 缺 {fn}")

    # None 与 [] 必须区分：前者是取数坏了，后者是真没人
    seg = src[src.find("def get_push_recipients"):]
    seg = seg[:seg.find("\ndef ", 10)] if "\ndef " in seg[10:] else seg
    ck("return None" in seg,
       "get_push_recipients 取数失败必须返回 None（与「确实没人订阅」的 [] 区分）")
    ck("expires_at" in seg, "到期判断必须用 expires_at（实测列名，不是 end_date）")
    # 只查「代码里真的去读这两个字段」，不能用裸子串——文档里合法地写着
    # 「不是 end_date」，裸匹配会被自己的注释误伤（这个坑 8/30 已踩过两次）。
    ck(re.search(r'\.get\(\s*["\'](end_date|expire_date)["\']', seg) is None,
       "不得再读 end_date / expire_date（这两个列在 subscriptions 表里不存在）")
    ck('str(s.get("status") or "active") != "active"' in seg,
       "必须按 status=active 过滤订阅")
    ck('u.get("digest_optin") is False' in seg,
       "digest_optin 显式为 False 时必须跳过（付费不等于同意被打扰）")

    # 写库必须忠实返回失败
    seg2 = src[src.find("def update_user_wxpusher_uid"):]
    ck("return res is not None" in seg2,
       "update_user_wxpusher_uid 必须忠实返回落库结果")

    # 失败原因必须来自真实状态码，不能由调用方猜
    ck("def _supabase_request" in src and "return_error" in src,
       "_supabase_request 须支持 return_error 透出状态码（否则 409 与 PGRST204 无法区分）")
    ck("def bind_wxpusher_uid" in src, "database.py 缺 bind_wxpusher_uid（带原因的写库）")
    ck("def explain_uid_write_error" in src, "database.py 缺 explain_uid_write_error")

    seg3 = src[src.find("def explain_uid_write_error"):]
    seg3 = seg3[:seg3.find("\ndef ", 10)] if "\ndef " in seg3[10:] else seg3
    # 匹配语法结构而非裸子串：注释里合法地写着「23505 = unique_violation；
    # PostgREST 以 409 透出」，裸匹配会被自己的注释掩护，把判定逻辑删掉也不报错。
    ck(re.search(r"status\s*==\s*409", seg3) is not None,
       "唯一冲突须真的按 status==409 判定（不是只在注释里提到 409）")
    ck(re.search(r'code\s*==\s*["\']23505["\']', seg3) is not None,
       "唯一冲突须真的按 code=='23505' 判定（PostgreSQL unique_violation）")
    ck(re.search(r'code\s*==\s*["\']PGRST204["\']', seg3) is not None,
       "缺列须按 code=='PGRST204' 判定，只在这一支提示执行迁移脚本")
    ck("已经绑定在另一个账号" in seg3,
       "409 的文案必须让用户知道下一步怎么做（去那个账号解绑或换微信）")
    ck("init_wxpusher_column.sql" in seg3,
       "缺列这一支仍须给出迁移脚本名（原因对上了才该给这个动作）")

    seg4 = src[src.find("def bind_wxpusher_uid"):]
    seg4 = seg4[:seg4.find("\ndef ", 10)] if "\ndef " in seg4[10:] else seg4
    ck("return_error=True" in seg4, "bind_wxpusher_uid 须以 return_error=True 取回错误详情")
    ck("explain_uid_write_error(err)" in seg4, "bind_wxpusher_uid 须把错误翻译成用户可读文案")
    # `Prefer: return=representation` 零行命中返回 []，而 [] is not None 为真
    ck(re.search(r"len\(res\)\s*==\s*0", seg4) is not None,
       "零行命中（[]）须判为失败，否则账号不存在也会被当成绑定成功")


# ================= 4. 前端绑定区块 =================

def check_page() -> None:
    """绑定 UI 已抽到 push_binding.py，page_watchlist 只留薄封装。

    断言拆成两处读：实现细节（等待态、缺列提示、st.image 参数）跟着实现走到
    push_binding.py，调用入口留在各页面。两边都断言，才不会出现
    「组件在但没人调」或「有人调但组件被改坏」。
    """
    src = read("page_watchlist.py")
    ck("def _render_push_binding" in src, "page_watchlist 缺 _render_push_binding")
    ck("_render_push_binding(user_id)" in src, "绑定区块未被调用")
    ck("def _ensure_wxpusher_token" in src,
       "缺 secrets→环境变量 的桥（wxpusher.py 只认环境变量）")
    ck("pb.render_gate(" in src,
       "已付费用户须走 render_gate 强提示，不能只留默认折叠的 expander")

    ck(os.path.exists("push_binding.py"), "缺 push_binding.py（绑定组件）")
    if not os.path.exists("push_binding.py"):
        return
    pbs = read("push_binding.py")

    for fn in ("def ensure_app_token", "def is_bound", "def render(", "def render_gate"):
        ck(fn in pbs, f"push_binding.py 缺 {fn}")

    # 等待态不能渲染成报错
    ck('status == "waiting"' in pbs,
       "「暂无用户扫描」是等待态，必须与真失败分开显示（否则用户以为绑定坏了）")
    ck("st.warning(" in pbs[pbs.find("def render("):],
       "等待态应用 warning 而非 error")

    # 取数失败 ≠ 未绑定：误报会让用户反复重扫，最后不信任这个提示
    ck("bound is None" in pbs,
       "is_bound 返回 None（取数失败）必须与 False（确实未绑）分开处理")

    # 写库失败必须如实告知，且**原因不能是前端猜的**。
    # 曾把所有失败都写死成「管理员需执行 init_wxpusher_column.sql 补列」，
    # 而实际最常见的失败是 409 唯一冲突（同一微信绑第二个账号）——
    # 管理员照提示执行幂等迁移脚本，脚本成功，问题分毫未动。
    ck("init_wxpusher_column.sql" not in pbs,
       "前端不得写死缺列这一种原因（真实原因须由 database 按状态码给出）")
    ck("bind_wxpusher_uid(" in pbs,
       "绑定/解绑须走 bind_wxpusher_uid 以拿到真实失败原因")
    ck(pbs.count("bind_wxpusher_uid(") >= 2,
       "绑定与解绑两处都要带出原因（解绑失败时绑定仍然有效，必须说清）")

    # 同一组件在三个页面渲染，控件 key 必须隔离，否则 Streamlit 报 duplicate key
    ck("key_prefix" in pbs, "render 须支持 key_prefix 以隔离多处渲染的控件 key")

    # 付费入口必须能看到绑定，这是「收了钱没交付」的唯一防线
    dash = read(os.path.join("pages", "dashboard.py"))
    ck("push_binding" in dash, "会员中心须挂载绑定组件（刚付完钱是最该提示的位置）")
    ck("render_gate(" in dash, "会员中心须对已生效会员做未绑定强提示")

    dg_page = read("page_digest.py")
    ck("render_gate(" in dg_page, "收盘摘要页须对已生效会员做未绑定强提示")

    # 跨版本兼容铁律：st.image 不得带 use_container_width / use_column_width
    for m in re.finditer(r"st\.image\(([^)]*)\)", pbs):
        ck("use_container_width" not in m.group(1) and "use_column_width" not in m.group(1),
           f"st.image 不得带 use_*_width（三代改名，会整页 TypeError）：{m.group(1)[:60]}")

    # 不得在前端硬编码 appToken
    for fname, s in (("page_watchlist.py", src), ("push_binding.py", pbs),
                     ("pages/dashboard.py", dash)):
        ck(not re.search(r"AT_[A-Za-z0-9]{20,}", s), f"{fname} 不得硬编码 appToken")


# ================= 4.5 站内文案不得承诺未配置的邮件通道 =================

def check_no_email_promise() -> None:
    """页面文案只允许承诺已经跑通的通道。

    历史问题：`app.py` 与 `page_digest.py` 四处写「推送到邮箱」，而
    DIGEST_SMTP_* 从未进 Secrets → 邮件恒不发。付费用户守着邮箱等一封
    永远不来的信，后台日志跟「这个人没订阅」完全一样，谁都发现不了。
    """
    for fname in ("app.py", "page_digest.py"):
        src = read(fname)
        for m in re.finditer(r"[^\n]{0,40}(推到邮箱|推送到邮箱|摘要邮件|收到摘要邮件)[^\n]{0,40}", src):
            seg = m.group(0)
            # 允许出现在「说明这条路不通」的否定语境里
            benign = ("不提供" in seg or "并未配置" in seg or "恒不发" in seg
                      or "之前写" in seg or "不许" in seg)
            ck(benign, f"{fname} 仍向用户承诺邮件投递（该通道未配置）：{seg.strip()[:70]}")
        ck("微信" in src, f"{fname} 应明确投递通道是微信")


# ================= 5. 迁移 SQL =================

def check_sql() -> None:
    ck(os.path.exists("init_wxpusher_column.sql"), "缺 init_wxpusher_column.sql")
    if not os.path.exists("init_wxpusher_column.sql"):
        return
    src = read("init_wxpusher_column.sql")
    ck("ADD COLUMN IF NOT EXISTS wxpusher_uid" in src, "SQL 须建 wxpusher_uid 列")
    ck("IF NOT EXISTS" in src, "迁移脚本须可重复执行")
    ck("WHERE wxpusher_uid IS NOT NULL" in src,
       "唯一索引须跳过 NULL（否则未绑定用户互相冲突）")


# ================= 5.5 下单告警链路 =================

def check_order_alert() -> None:
    """付费链路最后一步是人工确认收款，所以「下单必须惊动站长」是硬要求。

    没有告警时，订单静静躺在 payments 表里等站长自己想起来看后台——
    那不是流程，是运气。而用户已经付了钱在等，这是收钱不发货。
    """
    ck(os.path.exists("admin_notify.py"), "缺 admin_notify.py（下单告警通道）")
    if not os.path.exists("admin_notify.py"):
        return
    an = read("admin_notify.py")

    # 跑批环境没有 streamlit runtime
    ck(re.search(r"(?m)^\s*(import streamlit|from streamlit\b)", an) is None,
       "admin_notify.py 不得 import streamlit（daily_digest 要在 Actions 里引用它）")

    for fn in ("def serverchan_url", "def send_serverchan",
               "def send_wxpusher_admins", "def notify_admins"):
        ck(fn in an, f"admin_notify.py 缺 {fn}")

    # 两个产品线端点推导
    sys.path.insert(0, ".")
    import admin_notify as anm
    ck(anm.serverchan_url("SCTabc") == "https://sctapi.ftqq.com/SCTabc.send",
       "Turbo key 应走 sctapi.ftqq.com")
    ck(anm.serverchan_url("sctp123tXYZ")
       == "https://123.push.ft07.com/send/sctp123tXYZ.send",
       "SC3 key 须把 uid 抠出来拼进域名（域名写错表现成网络错误，会带偏排查）")
    ck(anm.serverchan_url("sctpXtY") == "https://sctapi.ftqq.com/sctpXtY.send",
       "sctp 后不是数字时应回落到 Turbo 端点，不得拼出空 uid 域名")

    # 额度耗尽仍返 HTTP 200 → 必须查响应体 code
    ck('resp.get("code")' in an,
       "Server酱 成败必须看响应体 code（额度耗尽仍返 HTTP 200）")

    # 双通道：只要一条通就算送达，全失败必须如实返回 False
    ck("delivered = delivered or ok" in an,
       "notify_admins 须双通道尝试，任一成功即算送达")

    # 「站长还没绑微信」是待办不是故障
    ck("无已绑定微信的管理员" in an,
       "无管理员绑定时应给出可操作说明，而不是当成推送故障")

    # database 侧
    db = read("database.py")
    ck("def get_admin_wxpusher_uids" in db, "database 缺 get_admin_wxpusher_uids")
    seg = db[db.find("def get_admin_wxpusher_uids"):]
    ck("if res is None" in seg and "return None" in seg,
       "取数失败须返回 None，与「确实没人绑」的 [] 区分（前者要修、后者要绑）")

    # 下单处必须真的调用
    dash = read(os.path.join("pages", "dashboard.py"))
    ck("_notify_new_order(" in dash, "创建订单后未触发管理员告警")
    ck("admin_notify" in dash, "会员中心未引入 admin_notify")
    # 告警结果必须如实告诉用户：决定他要不要自己去戳管理员
    ck("last_order_alert" in dash, "告警结果须记录并展示给用户")
    ck("自动通知管理员**未成功**" in dash,
       "告警失败必须显式告知用户去手动联系，不能假装已通知")

    # daily_digest 复用同一套端点规则，避免两处漂移
    dd = read("daily_digest.py")
    ck("admin_notify" in dd, "daily_digest 应复用 admin_notify 的端点推导，避免规则两处漂移")
    ck("付费未绑微信" in dd,
       "「有效订阅但未绑微信」= 收了钱没交付，必须在名单说明里显式点出")


# ================= 5.6 确认收款链路 =================

def check_confirm_payment() -> None:
    """确认收款是付费闭环的最后一环，而且是**人工**操作——出错时没有自动兜底。

    这里最怕两种静默失败：
    1) 订单状态 PATCH 失败却照样返回 ok=True → 订阅已续期、订单仍 pending，
       管理员下次打开还看到这笔，再点一次就重复续期（收一笔钱发两次货）；
    2) 提示紧跟 st.rerun() → 失败文案根本来不及显示，管理员只看到「点了没反应」，
       而「不要重复确认」恰恰是最需要被看见的一句。
    """
    db = read("database.py")
    seg = db[db.find("def confirm_payment"):]
    seg = seg[:seg.find("\ndef ", 10)] if "\ndef " in seg[10:] else seg
    ck(re.search(r'upd\s*=\s*_supabase_request\(\s*"PATCH",\s*f"payments\?id=eq', seg)
       is not None,
       "confirm_payment 的订单状态 PATCH 必须接住返回值")
    ck(re.search(r"if\s+upd\s+is\s+None", seg) is not None,
       "订单状态没更新成功时不得仍返回 ok=True（订阅已建 + 订单仍 pending = 重复续期）")
    ck("不要重复确认" in seg,
       "部分成功的文案必须说清「已经发生了什么」+「下一步千万别做什么」")

    seg2 = db[db.find("def cancel_payment"):]
    seg2 = seg2[:seg2.find("\ndef ", 10)] if "\ndef " in seg2[10:] else seg2
    ck(re.search(r"len\(res\)\s*==\s*0", seg2) is not None,
       "cancel_payment 零行命中须判失败（订单不存在也会被当成取消成功）")

    adm = read(os.path.join("pages", "admin.py"))
    # 必须校验「写入」而不是只看名字出现过：pop 那行也含这个字符串，
    # 只判断 `"admin_flash" in adm` 的话，把两处赋值全删掉它照样通过。
    ck('st.session_state["admin_flash"] =' in adm,
       "admin.py 确认/取消结果须写入 flash（紧跟 st.rerun() 会吃掉瞬时提示）")
    dash = read(os.path.join("pages", "dashboard.py"))
    ck('st.session_state["cancel_flash"] =' in dash,
       "dashboard.py 取消结果须写入 flash")


# ================= 6. 联网真实断言 =================

def check_live() -> None:
    tok = (os.getenv("WXPUSHER_APP_TOKEN") or "").strip()
    if not tok:
        print("ℹ️ 未设 WXPUSHER_APP_TOKEN，跳过联网断言（结构断言已覆盖）")
        return
    sys.path.insert(0, ".")
    import wxpusher as wx

    url, code = wx.create_qrcode(extra="probe", valid_seconds=600)
    ck(bool(url), f"create_qrcode 应成功，实际：{code}")
    if url:
        ck(url.startswith("https://"), "二维码 URL 须为 https")
        uid, status = wx.scan_uid(code)
        ck(uid is None and status == "waiting",
           f"刚创建的二维码尚未被扫，应返回 waiting，实际：{status}")

    followers, msg = wx.list_followers()
    ck(msg == "ok", f"关注列表查询应成功，实际：{msg}")

    # 关键断言：不存在的 UID 必须被判成失败，而不是随顶层 code=1000 一起算成功
    ok, bad = wx.send_to_uids(["UID_probe_not_exist"], "probe content", "probe")
    ck(not ok and len(bad) == 1,
       f"不存在的 UID 必须记为失败，实际 ok={ok} bad={bad}")

    # 空内容拒发
    ok2, bad2 = wx.send_to_uids(["UID_x"], "   ", "s")
    ck(not ok2 and bad2 and "拒绝发送" in bad2[0], "空内容必须拒发")

    # 无收件人不算失败
    ok3, bad3 = wx.send_to_uids([], "content", "s")
    ck(not ok3 and not bad3, "无收件人应返回空成功空失败，不算错误")


def main() -> None:
    for fn in (check_module, check_digest, check_database,
               check_page, check_no_email_promise, check_sql,
               check_order_alert, check_confirm_payment, check_live):
        try:
            fn()
        except Exception as e:
            FAIL.append(f"{fn.__name__} 抛异常：{type(e).__name__}: {e}")
    print(f"\n共 {N} 项断言，失败 {len(FAIL)} 项")
    for f in FAIL:
        print(f"  ❌ {f}")
    if FAIL:
        sys.exit(1)
    print("✅ WxPusher 推送通道自检全部通过")


if __name__ == "__main__":
    main()
