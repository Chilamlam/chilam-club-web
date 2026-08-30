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


# ================= 4. 前端绑定区块 =================

def check_page() -> None:
    src = read("page_watchlist.py")
    ck("def _render_push_binding" in src, "page_watchlist 缺 _render_push_binding")
    ck("_render_push_binding(user_id)" in src, "绑定区块未被调用")
    ck("def _ensure_wxpusher_token" in src,
       "缺 secrets→环境变量 的桥（wxpusher.py 只认环境变量）")

    # 等待态不能渲染成报错
    ck('status == "waiting"' in src,
       "「暂无用户扫描」是等待态，必须与真失败分开显示（否则用户以为绑定坏了）")
    ck("st.warning(" in src[src.find("def _render_push_binding"):],
       "等待态应用 warning 而非 error")

    # 写库失败必须如实告知，并指明修复动作
    ck("init_wxpusher_column.sql" in src,
       "缺列导致的保存失败须提示执行 init_wxpusher_column.sql")

    # 跨版本兼容铁律：st.image 不得带 use_container_width / use_column_width
    for m in re.finditer(r"st\.image\(([^)]*)\)", src):
        ck("use_container_width" not in m.group(1) and "use_column_width" not in m.group(1),
           f"st.image 不得带 use_*_width（三代改名，会整页 TypeError）：{m.group(1)[:60]}")

    # 不得在前端硬编码 appToken
    ck(not re.search(r"AT_[A-Za-z0-9]{20,}", src), "前端不得硬编码 appToken")


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
               check_page, check_sql, check_live):
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
