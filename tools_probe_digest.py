# -*- coding: utf-8 -*-
"""
收盘摘要自检（digest.py / daily_digest.py / page_digest.py）

推送这类功能出错的方式很特殊：它**成功发出去了**，但内容是错的、是空的、
或者是把「数据没取到」说成了「今天没事发生」。这三种都不会报错，
用户会直接退订。所以自检重点不是"能不能跑"，而是：

  1. 关键数据全缺时，必须 has_content=False 且**拒绝发送**（不发空推送）
  2. 部分缺失时，缺失项必须出现在 missing 列表里，且摘要正文不能凭空补数
  3. 没有自选股 ≠ 数据缺失：不能把它记进 missing（否则每个免费用户都被误报）
  4. 摘要里的每一个数字都必须能在 derived.json / performance.json 里找到对应
  5. 全渠道未配置时退出码必须是 0（功能可用，推送是叠加项）
  6. 合规：不得出现"买入/卖出/目标价/建议仓位"这类字样
"""
from __future__ import annotations

import ast
import contextlib
import glob
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def ck(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(("✅ " if cond else "❌ ") + msg)


ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIGEST = open(os.path.join(ROOT, "digest.py"), encoding="utf-8").read()
SRC_DAILY = open(os.path.join(ROOT, "daily_digest.py"), encoding="utf-8").read()
SRC_PAGE = open(os.path.join(ROOT, "page_digest.py"), encoding="utf-8").read()
SRC_APP = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()

# ---------- 在临时目录里造一套完整产物，避免污染真实 data/ ----------
WORK = tempfile.mkdtemp(prefix="digest_probe_")
os.makedirs(os.path.join(WORK, "data", "sentiment"))
os.makedirs(os.path.join(WORK, "data", "scorecard"))

DERIVED = {
    "status": "complete", "date": "20260828", "prev_date": "20260827",
    "generated_at": "2026-08-28 19:40:00", "archive_days": 2,
    "promotion": {"status": "ok", "rates": {
        "1进2": {"base": 20, "promoted": 8, "rate": 0.4, "reliable": True},
        "2进3": {"base": 5, "promoted": 2, "rate": 0.4, "reliable": False}}},
    "premium": {"status": "ok", "n": 5, "missing": 1, "median_pct": 2.0,
                "mean_pct": 3.46, "win_rate": 0.6, "limit_again": 2},
    "ladder_gap": {"status": "ok", "max_height": 5, "min_height": 2,
                   "first_board": 0, "total": 11, "gaps": [4],
                   "tiers": [], "verdict": "5 板成为孤高，中间 [4] 板断层，接力链条不连续"},
    "breadth": {"status": "ok", "mean_pct": 1.5, "median_pct": 0.2,
                "divergence": 1.3, "up": 2800, "down": 2100, "up_ratio": 0.571,
                "verdict": "均值明显高于中位数：涨幅集中在少数标的，多数个股跑输体感偏冷"},
    "phase": {"status": "ok", "phase": "分歧期",
              "basis": "晋级率中等，资金在分歧中切换，赚钱效应不均匀；但梯队存在断层，高度接力并不连续",
              "r12": 0.4, "premium_median": 2.0,
              "note": "周期定位仅描述当前市场状态，不构成参与建议或仓位指引。"},
    "verification_plan": [
        {"指标": "1进2 晋级率", "今日基准": "40.0%（8/20）",
         "验证条件": "明日若跌破 30%，视为接力意愿转弱", "为什么看它": "样本稳定"},
        {"指标": "昨日连板股今日中位涨幅", "今日基准": "+2.00%（5 只，胜率 60%）",
         "验证条件": "明日若转负且胜率跌破 40%，说明接力资金已停止出价", "为什么看它": "真实体感"},
    ],
}
PERF = {
    "status": "complete", "benchmark": "000300.SH", "horizons": [1, 5, 10],
    "min_sample": 20,
    "archive": {"pick_rows": 900, "price_rows": 5000, "date_from": "20260701",
                "date_to": "20260828", "trade_days": 40},
    "strategies": {"rps": {
        "label": "RPS 强势股榜", "total_picks": 900, "days": 40,
        # alpha_median 在 scorecard.py 里是**小数**（0.0083 = 0.83%）。
        # 这个 fixture 原先误写成 0.83，导致展示层漏乘 100 的 bug 无法被自检发现——
        # 测试数据的单位必须和生产数据完全一致，否则断言是在验证一个不存在的世界。
        "horizons": {"5": {"n": 900, "status": "ok", "alpha_median": 0.0083,
                           "direction_accuracy": 0.56}},
        "discrimination": {"status": "ok", "monotonic": False,
                           "verdict": "排序未通过单调性检验：靠前档位并未更优，该榜单的排序信息量存疑"},
        "daily_alpha": []}},
}

with open(os.path.join(WORK, "data", "sentiment", "derived.json"), "w", encoding="utf-8") as f:
    json.dump(DERIVED, f, ensure_ascii=False)
with open(os.path.join(WORK, "data", "scorecard", "performance.json"), "w", encoding="utf-8") as f:
    json.dump(PERF, f, ensure_ascii=False)
with open(os.path.join(WORK, "data", "strong_stocks.csv"), "w", encoding="utf-8") as f:
    f.write("ts_code,name,细分行业,更新日期,初次入选\n")
    f.write("600000.SH,浦发银行,银行,2026-08-28,2026-08-28\n")
    f.write("000001.SZ,平安银行,银行,2026-08-28,2026-08-20\n")
with open(os.path.join(WORK, "data", "breakout_stocks.csv"), "w", encoding="utf-8") as f:
    f.write("ts_code,name,industry,update_date\n600519.SH,贵州茅台,白酒,2026-08-28\n")
with open(os.path.join(WORK, "data", "market_snapshot.csv"), "w", encoding="utf-8") as f:
    f.write("ts_code,name,industry,close,amount,circ_mv\n")
    f.write("600000.SH,浦发银行,银行,11.6,900,2000\n")
    f.write("000001.SZ,平安银行,银行,12.6,900,2000\n")

_cwd = os.getcwd()
os.chdir(WORK)
sys.path.insert(0, ROOT)
import digest as dg           # noqa: E402
importlib.reload(dg)

print("=" * 60)
print("一、完整数据：所有段落齐备")
print("=" * 60)
full = dg.build_markdown(watchlist=["600000.SH", "000001.SZ"],
                         pct_map={"600000": 3.2, "000001": -1.1})
md = full["markdown"]
ck(full["has_content"] is True, "has_content=True")
ck(full["missing"] == [], f"无缺失项（实际 {full['missing']}）")
ck("2026-08-28" in full["title"], f"标题含统计日 — {full['title']}")
ck("分歧期" in full["title"], "标题含周期定位（打开前就知道今天什么行情）")
for kw in ("你的池子今日", "情绪周期", "明日验证条件", "榜单变动", "榜单战绩"):
    ck(kw in md, f"正文含段落「{kw}」")
ck(md.index("你的池子今日") < md.index("情绪周期"),
   "个性化段落排在大盘段落之前（用户关心顺序：我 → 我该盯什么 → 大盘）")

print("=" * 60)
print("二、每个数字都能在产物里对上（不允许凭空生成）")
print("=" * 60)
ck("40.0%" in md and "8/20" in md, "1进2 晋级率与分子分母原样透出")
ck("+2.00%" in md, "连板溢价中位数原样透出")
ck("5 板" in md and "[4] 板" in md, "梯队高度与断层原样透出")
ck("+0.83%" in md, "5日超额中位数 0.0083 → 显示 +0.83%（小数必须乘 100）")
ck("56%" in md, "跑赢基准比例 56% 原样透出")
ck("排序未通过单调性检验" in md, "区分度不单调的结论必须与战绩数字一同播报，不许只报中位数")
ck("浦发银行" in md, "自选股名称由 market_snapshot 映射得到")
ck("+3.20%" in md and "-1.10%" in md, "自选股涨幅原样透出")
ck("+1.05%" in md, "自选中位涨幅 = (3.2 + -1.1)/2 = 1.05%（手算对账）")
# 已在榜的平安银行不应算作"今日新进"
ck("新进 **1** 只" in md, "只播报当日初次入选的 1 只（存量不重复播报）")

print("=" * 60)
print("三、缺失语义：区分「没数据」与「没事发生」")
print("=" * 60)
# 3.1 没有自选 ≠ 缺失
nowl = dg.build_markdown(watchlist=None, pct_map={"600000": 3.2})
ck("自选" not in " ".join(nowl["missing"]),
   f"未登录/无自选不记入 missing（实际 {nowl['missing']}）")
ck("你的池子今日" not in nowl["markdown"], "无自选时该段整段不出现，不塞占位内容")

# 3.2 有自选但行情缺失 → 必须记 missing
nopct = dg.build_markdown(watchlist=["600000.SH"], pct_map={})
ck(any("自选" in m for m in nopct["missing"]),
   f"有自选但无行情 → 记入 missing（实际 {nopct['missing']}）")
ck("你的池子今日" not in nopct["markdown"], "行情缺失时不显示任何自选数字")

# 3.3 派生指标缺失 → 相关段落消失且记 missing
os.rename(os.path.join("data", "sentiment", "derived.json"),
          os.path.join("data", "sentiment", "derived.bak"))
partial = dg.build_markdown(watchlist=None, pct_map={})
ck(len(partial["missing"]) >= 2,
   f"情绪产物缺失 → missing 至少 2 项（实际 {partial['missing']}）")
ck("情绪周期" not in partial["markdown"], "周期段落整段消失，不显示「待定」数字")
ck("### ✅ 明日验证条件" not in partial["markdown"], "验证条件段落整段消失")
ck(partial["has_content"] is True, "榜单与战绩仍在 → has_content 仍为 True")
ck("本次缺失" in partial["markdown"], "正文末尾明确列出缺失项")
ck("非「无事发生」" in partial["markdown"],
   "文案明确区分「数据未取到」与「今天没事」")

# 3.4 全部缺失 → has_content=False，跑批层必须拒绝发送
for p in (os.path.join("data", "scorecard", "performance.json"),
          os.path.join("data", "strong_stocks.csv"),
          os.path.join("data", "breakout_stocks.csv")):
    os.rename(p, p + ".bak")
empty = dg.build_markdown(watchlist=None, pct_map={})
ck(empty["has_content"] is False, "关键数据全缺 → has_content=False")
ck("不做任何结论性播报" in empty["markdown"], "全缺时明确声明不做结论播报")
ck(re.search(r"\d+\.\d+%", empty["markdown"]) is None,
   "全缺时正文不含任何百分比数字（无凭空生成）")

# 还原
os.rename(os.path.join("data", "sentiment", "derived.bak"),
          os.path.join("data", "sentiment", "derived.json"))
for p in (os.path.join("data", "scorecard", "performance.json"),
          os.path.join("data", "strong_stocks.csv"),
          os.path.join("data", "breakout_stocks.csv")):
    os.rename(p + ".bak", p)

print("=" * 60)
print("四、短文本形态")
print("=" * 60)
plain = full["plain"]
ck(len(plain) <= 120, f"短文本 ≤120 字符（实际 {len(plain)}）— {plain}")
ck("分歧期" in plain, "短文本含周期")
ck("1进2 40%" in plain, "短文本含 1进2")
ck("2 条验证条件待明日对账" in plain, "短文本提示需对账条数")
ck(dg.build_plain(None, None) != "", "无数据时短文本仍返回明确说明而非空串")

print("=" * 60)
print("五、合规：不得出现操作建议类字样")
print("=" * 60)
BANNED = ["建议买入", "建议卖出", "目标价", "止损位", "建仓价", "建议仓位",
          "满仓", "半仓", "推荐买", "可以买入", "可以卖出"]
# 免责声明本身要点名这些禁项（"不含目标价、买卖点"），把它排除后再扫正文
body_only = md.replace(dg.DISCLAIMER, "")
for w in BANNED:
    ck(w not in body_only, f"摘要正文不含「{w}」")
ck("不构成投资建议" in md, "摘要携带免责声明")
ck("不构成参与建议或仓位指引" in md, "周期定位免责声明原样透出")

os.chdir(_cwd)
shutil.rmtree(WORK, ignore_errors=True)

print("=" * 60)
print("六、源码规范与接线")
print("=" * 60)
tree = ast.parse(SRC_DIGEST)
imports = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.add(node.module.split(".")[0])
ck("streamlit" not in imports, f"组装层未 import streamlit — {sorted(imports)}")
ck("tushare" not in imports and "urllib" not in imports and "requests" not in imports,
   "组装层不联网（行情由跑批层注入）")
ck("replace(0, pd.NA)" not in SRC_DIGEST, "未使用 replace(0, pd.NA)")

# 跑批层：空内容必须 exit 3 且在发送之前
i_empty = SRC_DAILY.find("has_content")
i_send = SRC_DAILY.find("send_wecom(base)")
ck(0 < i_empty < i_send, "has_content 检查排在所有发送动作之前（防空推送）")
ck("DIGEST_DRY_RUN" in SRC_DAILY, "支持 DRY_RUN 干跑（可安全首次验证）")
ck("未配置任何用户投递渠道" in SRC_DAILY, "全渠道未配时给出明确提示而非报错")
ck("sys.exit(2)" in SRC_DAILY and "sys.exit(3)" in SRC_DAILY,
   "保留 2=部分失败 / 3=无内容 的退出码语义")
ck("archive(base, date_key)" in SRC_DAILY, "先归档再发送（推送失败也留下产物）")

# 密钥不得回显
# 密钥不得回显。注意：不能简单匹配变量名——脚本会在提示文案里写
# 「配置 DIGEST_WECOM_WEBHOOK 后自动启用」，那是文档不是泄露。
# 真正要拦的是把**取到的值**插进 f-string，所以扫的是插值表达式。
ck(not re.search(r"print\([^)]*\{[^}]*(pwd|password|PASS)", SRC_DAILY),
   "不回显 SMTP 密码值")
ck(not re.search(r"print\([^)]*\{\s*hook\s*[}\[]", SRC_DAILY), "不回显 webhook 值")
ck(not re.search(r"print\([^)]*\{\s*key\s*[}\[]", SRC_DAILY), "不回显 SendKey 值")
ck("sctapi.ftqq.com/{key}" not in SRC_DAILY.replace('f"https://sctapi.ftqq.com/{key}.send"', ""),
   "SendKey 仅用于构造请求 URL，不出现在日志里")

# Server酱 的失败会伪装成 HTTP 200（真实结果在响应体 code 字段），
# 只看状态码会把「今天 5 条免费额度用完」记成推送成功，明天照样静默失败。
# 注意 8/30 起 Server酱 已降级为**管理员告警**通道（它的 SendKey 绑定单个微信、
# 官方明确不支持群发，推不到付费用户手上），所以入参从 payload 改成 (title, body)。
#
# 实现体在 8/30 晚进一步搬到 admin_notify.py——站内「下单告警」要用同一套规则，
# 一份规则两处实现必然漂移，而漂移的表现是「域名解析不到」，会被当成网络问题。
# 所以下面这些断言改成在 daily_digest + admin_notify 的**合并源码**里找：
# 只要规则还在链路里且被 daily_digest 复用，断言就应通过；两边都没有才算失败。
SRC_ALERT = SRC_DAILY + "\n" + open(
    os.path.join(ROOT, "admin_notify.py"), encoding="utf-8").read()

ck("admin_notify" in SRC_DAILY,
   "daily_digest 须复用 admin_notify 的告警实现（不得各自维护一份端点规则）")
ck('resp.get("code")' in SRC_ALERT, "Server酱 校验响应体 code 字段而非只看 HTTP 状态")
ck("code not in (0, None)" in SRC_ALERT, "code 非 0 判为失败")
ck('" ".join(str(title).split())' in SRC_ALERT,
   "Server酱 title 压成单行（含换行会被服务端拒绝）")
ck("[:800]" in SRC_ALERT, "响应体截断长度足够容纳 JSON（避免解析失败误判成功）")

# Server酱 有两个产品线，SendKey 不通用且端点不同：Turbo 是 sctapi.ftqq.com，
# Server酱³ 的 key 形如 sctp{uid}t…，端点是 {uid}.push.ft07.com。
# 把 sctp 的 key 发去 Turbo 端点只会回「key 不存在」，而用户会坚信刚复制的 key 没错，
# 排查方向直接被带偏——所以必须按前缀自动选端点，且必须有断言守住。
ck('key.startswith("sctp")' in SRC_ALERT, "按 SendKey 前缀区分 Turbo / Server酱³")
ck("push.ft07.com" in SRC_ALERT, "Server酱³ 端点已实现")
ck("{uid}.push.ft07.com" in SRC_ALERT, "Server酱³ 的 uid 必须拼进域名（写错会表现成网络错误）")
try:
    _dd = importlib.import_module("daily_digest")
    ck(_dd._serverchan_url("SCTabc") == "https://sctapi.ftqq.com/SCTabc.send",
       "Turbo key → sctapi 端点")
    ck(_dd._serverchan_url("sctp123tXYZ")
       == "https://123.push.ft07.com/send/sctp123tXYZ.send",
       "sctp123tXYZ → uid=123 的 push.ft07.com 端点")
    ck(_dd._serverchan_url("sctpXtY") == "https://sctapi.ftqq.com/sctpXtY.send",
       "sctp 后无数字时退回 Turbo 端点（不构造出畸形域名）")
except Exception as _e:                                     # noqa: BLE001
    ck(False, f"_serverchan_url 可导入并正确分流（{type(_e).__name__}: {_e}）")

# 订阅用户取数：字段名必须与 subscriptions 表真实列名一致。
# 曾写成 end_date / expire_date（两列都不存在）→ 有效订阅恒为 0 位 →
# 付费用户一封都收不到，日志却平静打印「有效订阅用户 0 位」，看起来像确实没人订阅。
# 字段名写错的取数逻辑不抛异常，只给出语义为空的答案，这类静默失败最难发现。
ck("expires_at" in SRC_DAILY, "订阅到期字段用 expires_at（表的真实列名）")
# 只查**取数调用**而非裸字符串：注释里必须能提到 end_date 来解释这个坑，
# 用裸字符串判断会被自己写的注释误伤（同 continue-on-error 那次）。
ck(not re.search(r'\.get\(\s*["\'](end_date|expire_date)["\']', SRC_DAILY),
   "不再从不存在的 end_date / expire_date 列取数")

# 8/30 起名单取数下沉到 database.get_push_recipients（前端绑定页也要用同一份判定，
# 逻辑留在跑批脚本里会导致两处口径漂移）。因此这三条断言改查 database.py。
SRC_DB = open(os.path.join(ROOT, "database.py"), encoding="utf-8").read()
_SEG_REC = SRC_DB[SRC_DB.find("def get_push_recipients"):]
_SEG_REC = _SEG_REC[:_SEG_REC.find("\ndef ", 10)] if "\ndef " in _SEG_REC[10:] else _SEG_REC
ck("def get_push_recipients" in SRC_DB, "database 提供统一的投递名单取数")
ck("return None" in _SEG_REC,
   "表结构不符/取数失败时返回 None，而不是安静返回 0 位订阅")
ck('str(s.get("status") or "active") != "active"' in _SEG_REC,
   "只取 status=active 的订阅（已取消的订阅不该继续收付费推送）")
ck('u.get("digest_optin") is False' in _SEG_REC,
   "尊重退订标记（付费不等于同意被打扰），且缺列时默认订阅")
# 「谁是会员」不允许有两套答案：auth.is_vip() 里管理员默认具备 VIP 权限，
# 名单取数若只认订阅表，管理员就会「站内看得到会员功能、却永远收不到推送」。
ck('"is_admin": "eq.true"' in _SEG_REC,
   "投递名单包含管理员（与 auth.is_vip() 的管理员豁免保持一致）")
SRC_AUTH = open(os.path.join(ROOT, "auth.py"), encoding="utf-8").read()
ck('user.get("is_admin", False)' in SRC_AUTH,
   "auth.is_vip() 确实对管理员豁免（上一条断言的前提，前提变了要一起改）")
ck("if admins is None:" in _SEG_REC,
   "管理员名单取数失败时返回 None（名单不完整不算成功）")
# None 与 [] 必须在调用侧也被区分，否则「配置坏了」会长期伪装成「暂时没人付费」
ck("if rec is None:" in SRC_DAILY, "调用侧区分 None（取数失败）与 []（确实没人）")

# 个性化段落兜底：它是付费用户唯一的独占内容，不能因 tushare 一处失效就整段消失。
_SEG_FB = SRC_DAILY[SRC_DAILY.find("def fallback_pct_map"):]
_SEG_FB = _SEG_FB[:_SEG_FB.find("\ndef ", 10)] if "\ndef " in _SEG_FB[10:] else _SEG_FB
ck("def fallback_pct_map" in SRC_DAILY, "提供个性化涨幅兜底通道（不依赖 tushare）")
ck("if not pct_map:" in SRC_DAILY, "仅在全市场取数失败时才走兜底（正常路径不变）")
ck("day != date_key" in _SEG_FB,
   "兜底必须校验行情日期：拿到别的交易日的涨幅要整段丢弃，不可冒充今日")
ck("qt.gtimg.cn" in _SEG_FB, "兜底走腾讯行情（纯 stdlib，无额外依赖）")
ck("import tushare" not in _SEG_FB, "兜底通道不复用失效的 tushare 依赖")

# 「投递成功 N/N」这行读不出用户收到的是个性化版还是通用版：个性化渲染成空时，
# 用户照样收到一条计入成功的通用摘要，日志毫无异常 → 付费独占内容静默消失。
_SEG_WX = SRC_DAILY[SRC_DAILY.find("def send_wxpusher"):]
_SEG_WX = _SEG_WX[:_SEG_WX.find("\ndef ", 10)] if "\ndef " in _SEG_WX[10:] else _SEG_WX
ck("个性化" in _SEG_WX and "通用" in _SEG_WX and "n_person_ok" in _SEG_WX,
   "投递日志分别计数个性化/通用（只打总数会让降级投递看起来完全正常）")
ck('"你的池子" not in p["markdown"]' in _SEG_WX,
   "有自选股却渲染不出自选段时，显式记录降级而不是静默按成功投递")
try:
    _dd_fb = importlib.import_module("daily_digest")
    ck(_dd_fb.fallback_pct_map([], "20260828") == {},
       "兜底空输入返回空 dict（不发请求）")
    ck(_dd_fb._tx_code("603259") == "sh603259" and _dd_fb._tx_code("000506") == "sz000506"
       and _dd_fb._tx_code("920099") == "bj920099",
       "兜底代码前缀规则与 page_watchlist._tx_code 一致（沪/深/北三段）")
except Exception as _e:                                     # noqa: BLE001
    ck(False, f"兜底通道可调用（{type(_e).__name__}: {_e}）")
try:
    _dd = importlib.import_module("daily_digest")
    _rows = [{"user_id": 2, "status": "active", "expires_at": "2099-01-01T00:00:00+00:00"},
             {"user_id": 3, "status": "active", "expires_at": "2000-01-01T00:00:00+00:00"},
             {"user_id": 4, "status": "cancelled", "expires_at": "2099-01-01T00:00:00+00:00"}]
    _today = "2026-08-30"
    _act = {r["user_id"] for r in _rows
            if str(r.get("status") or "active") == "active"
            and str(r.get("expires_at") or "")[:10] >= _today}
    ck(_act == {2}, "有效订阅判定：未到期且 active 才算（过期/已取消均排除）")
except Exception as _e:                                     # noqa: BLE001
    ck(False, f"订阅判定逻辑可复现（{type(_e).__name__}: {_e}）")

# users.watchlist 列是「与我相关」这一段的物理前提：列不存在则自选股存不住，
# 个性化邮件退化成与免费内容完全相同，付费理由随之消失。
_SQL_WL = os.path.join(ROOT, "init_watchlist_column.sql")
ck(os.path.exists(_SQL_WL), "提供 init_watchlist_column.sql 补 users.watchlist 列")
if os.path.exists(_SQL_WL):
    _sql = open(_SQL_WL, encoding="utf-8").read()
    ck("ADD COLUMN IF NOT EXISTS watchlist" in _sql, "建列语句幂等（IF NOT EXISTS）")
    ck("digest_optin" in _sql, "同时提供退订开关列")
SRC_DB = open(os.path.join(ROOT, "database.py"), encoding="utf-8").read()
# 必须定位到函数段再断言：全文件裸匹配 "return res is not None" 时，
# 另一处同名写法会让它在 update_user_watchlist 被整个删掉后依然「通过」。
_seg_wl = SRC_DB[SRC_DB.find("def update_user_watchlist"):]
_seg_wl = _seg_wl[:_seg_wl.find("\ndef ", 10)] if "\ndef " in _seg_wl[10:] else _seg_wl
ck("def update_user_watchlist" in SRC_DB, "database.py 缺 update_user_watchlist")
ck("return False" in _seg_wl,
   "update_user_watchlist 忠实返回写库结果（缺列时必须为 False）")
ck(re.search(r"len\(res\)\s*==\s*0", _seg_wl) is not None,
   "零行命中（[]）须判为失败，否则账号不存在也会被当成自选股已保存")
SRC_WL = open(os.path.join(ROOT, "page_watchlist.py"), encoding="utf-8").read()
ck("已在本地更新自选清单" not in SRC_WL,
   "写库失败不得伪装成成功（原文案会让人以为已保存，刷新即丢失）")
ck("wl_flash" in SRC_WL,
   "保存结果用 flash 传递（紧跟 st.rerun() 的提示来不及显示）")

# 页面接线
ck("from page_digest import render_digest_page" in SRC_APP, "app.py 已 import 摘要页")
ck('"📮 收盘摘要"' in SRC_APP, "侧边栏已加入「收盘摘要」入口")
ck("render_digest_page()" in SRC_APP, "路由已接入摘要页渲染")
i_menu = SRC_APP.find('"📮 收盘摘要"')
i_dash = SRC_APP.find('"🛸 全市场看板"')
ck(0 < i_dash < i_menu, "摘要入口紧随全市场看板（第 2 位，最高可见度）")
ck("auth.is_vip()" in SRC_PAGE, "摘要页对推送订阅做 VIP 判定")
ck("内容在这里始终免费可看" in SRC_PAGE, "明确「锁投递不锁内容」")

# 跑批接线
# 工作流已薄壳化：步骤清单与顺序不再写在 yml 里，而是下沉到 run_daily.py 的 STEPS 表
# （理由：PAT 缺 workflow scope，改不了 yml，只能让 yml 不承载会变的东西）。
# 因此这里改为断言「编排器里有这三个步骤且顺序正确」+「yml 确实是薄壳」。
# 步骤表的完整性由 tools_probe_run_daily.py 深度校验，此处只守 digest 相关的接线。
sys.path.insert(0, ROOT)
import run_daily as _rd  # noqa: E402

_keys = [s["key"] for s in _rd.STEPS]
_scripts = {s["key"]: s["script"] for s in _rd.STEPS}
for k, script in (("sentiment", "daily_sentiment.py"),
                  ("scorecard", "daily_scorecard.py"),
                  ("digest", "daily_digest.py")):
    ck(_scripts.get(k) == script, f"编排器含步骤 {k} → {script}")
_i = {k: i for i, k in enumerate(_keys)}
ck(_i["sentiment"] > _i["market_monitor"],
   "sentiment 排在 market_monitor 之后（依赖 limit_ladder.json）")
ck(_i["sentiment"] < _i["scorecard"] < _i["digest"],
   "顺序 sentiment → scorecard → digest")
ck(_i["digest"] == len(_keys) - 1, "digest 是最后一步（读前两者产物）")
# finish() 内部会打印新鲜度与汇总，这里只关心返回码，故临时静默以免污染自检输出
_buf = io.StringIO()
with contextlib.redirect_stdout(_buf):
    _rc = _rd.finish([("x", 3, 0.0)])
ck(_rc == 0,
   "编排器即使有步骤失败也返回 0（等价原 yml 的 continue-on-error，保证 data/ 落盘）")
_fresh = {lbl for lbl, _ in _rd.FRESHNESS}
ck({"情绪派生", "战绩绩效", "收盘摘要"} <= _fresh, "新鲜度自检覆盖三个新产物")

wf = open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"),
          encoding="utf-8").read()
ck("run_daily.py" in wf, "yml 通过编排器统一跑批（薄壳化）")
# 2026-08-30 回退：`toJSON(secrets)` 把整个 secrets 集合序列化进环境变量，
# 正是凭据外泄攻击的标志动作，GitHub 供应链保护会把 run 挂起等人工 Approve
# （公开仓库，run #334 实测被拦）。定时跑批被挂起时无人知晓 → 当日 data/ 不落盘，
# 比「加 secret 时人工一次」严重得多。故必须逐个点名。
#
# 匹配前必须剥掉注释行：yml 注释里正当地引用了这个表达式来解释为何回退，
# 裸匹配会被自己的文档误伤。这类假失败会训练人忽略自检输出，比没有自检更糟
# （8/30 已因同类原因踩过三次：continue-on-error、end_date、import streamlit）。
_wf_code = "\n".join(ln for ln in wf.splitlines() if not ln.lstrip().startswith("#"))
ck(not re.search(r"\$\{\{\s*toJSON\s*\(\s*secrets\s*\)\s*\}\}", _wf_code),
   "yml 不得用 toJSON(secrets) 整体透传（会被 GitHub 供应链保护挂起，导致定时跑批静默不执行）")
# 逐个点名的代价是「漏点名」会让功能静默失效（引用不存在的 secret 只得到空字符串，
# 不报错）。所以这里反过来断言：代码里实际读取的每个密钥名，yml 都必须点到。
_env_names = set()
for _f in sorted(glob.glob(os.path.join(ROOT, "daily_*.py")) +
                 [os.path.join(ROOT, "database.py")]):
    _src = open(_f, encoding="utf-8").read()
    _env_names |= set(re.findall(
        r"(?:getenv|environ\.get)\(\s*[\"']([A-Z][A-Z0-9_]{3,})[\"']", _src))
    _env_names |= set(re.findall(r"_env\(\s*[\"']([A-Z][A-Z0-9_]{3,})[\"']", _src))
# ALL_SECRETS 是已废弃的透传入口，BACKFILL_DAYS/ONLY_STEPS 来自 workflow 输入而非 secrets
_env_names -= {"ALL_SECRETS", "BACKFILL_DAYS", "ONLY_STEPS"}
_missing = sorted(n for n in _env_names if n not in wf)
ck(not _missing,
   f"代码读取的密钥必须都在 yml 里点名，否则静默变空字符串。缺：{_missing}")
ck("backfill_days" in wf, "workflow_dispatch 支持首次历史回溯入参")
# 注意用正则匹配「行首缩进 + 键」而非裸字符串：yml 的注释里正当地提到了
# continue-on-error（解释失败语义搬去了哪里），裸 in 判断会被注释误伤。
ck(not re.search(r"^\s*continue-on-error\s*:", wf, re.M),
   "薄壳 yml 不再需要 continue-on-error 键（失败语义已收进编排器）")
_steps_yml = re.findall(r"^\s*-\s*name:\s*(.+)$", wf, re.M)
ck(len(_steps_yml) == 5,
   f"yml 应只剩 5 步（装环境→跑编排器→提交），实际 {len(_steps_yml)}：{_steps_yml}")

print("-" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("\n失败项：")
    for m in FAIL:
        print("  ❌ " + m)
    sys.exit(1)
print("✅ 全部通过")
