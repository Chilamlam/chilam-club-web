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
import importlib
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
        "horizons": {"5": {"n": 900, "status": "ok", "alpha_median": 0.83,
                           "direction_accuracy": 0.56}},
        "discrimination": {}, "daily_alpha": []}},
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
ck("+0.83%" in md, "5日超额中位数 +0.83% 原样透出")
ck("56%" in md, "跑赢基准比例 56% 原样透出")
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
ck("未配置任何推送渠道" in SRC_DAILY, "全渠道未配时给出明确提示而非报错")
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

# 页面接线
ck("from page_digest import render_digest_page" in SRC_APP, "app.py 已 import 摘要页")
ck('"📮 收盘摘要"' in SRC_APP, "侧边栏已加入「收盘摘要」入口")
ck("render_digest_page()" in SRC_APP, "路由已接入摘要页渲染")
i_menu = SRC_APP.find('"📮 收盘摘要"')
i_dash = SRC_APP.find('"🛸 全市场看板"')
ck(0 < i_dash < i_menu, "摘要入口紧随全市场看板（第 2 位，最高可见度）")
ck("auth.is_vip()" in SRC_PAGE, "摘要页对推送订阅做 VIP 判定")
ck("内容在这里始终免费可看" in SRC_PAGE, "明确「锁投递不锁内容」")

# 工作流接线
wf = open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"),
          encoding="utf-8").read()
for step in ("Run Sentiment Derived", "Run Scorecard", "Run Closing Digest"):
    ck(step in wf, f"工作流含步骤「{step}」")
i_mon = wf.find("Run Market Monitor")
i_sent = wf.find("Run Sentiment Derived")
i_score = wf.find("Run Scorecard")
i_dig = wf.find("Run Closing Digest")
i_fresh = wf.find("Show data freshness")
ck(i_mon < i_sent, "Sentiment 排在 Market Monitor 之后（依赖 limit_ladder.json）")
ck(i_sent < i_score < i_dig < i_fresh, "顺序 Sentiment → Scorecard → Digest → 自检")
ck(wf.count("continue-on-error: true") >= 11,
   f"所有跑批步骤均带 continue-on-error（实际 {wf.count('continue-on-error: true')} 处）")
ck("derived.json" in wf and "performance.json" in wf and "latest.json" in wf,
   "新鲜度自检覆盖三个新产物")
ck("backfill_days" in wf, "workflow_dispatch 支持首次历史回溯入参")

print("-" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("\n失败项：")
    for m in FAIL:
        print("  ❌ " + m)
    sys.exit(1)
print("✅ 全部通过")
