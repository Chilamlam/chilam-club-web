# -*- coding: utf-8 -*-
"""
情绪派生指标「前端展示层」自检

展示层 bug 的典型形态不是崩页，而是**静默显示错误的东西**：
键名写错 → 显示「—」，看起来像"数据还没到"；缺失值补 0 → 显示「平盘」，
看起来像真的没涨没跌。这两种都不会抛异常，只能靠契约校验拦住。

本脚本做四件事：
  1. 用构造数据端到端跑出与 daily_sentiment.py **完全同构**的 payload
  2. 逐个校验 page_sentiment.py 实际读取的每一个键都在 payload 里存在
  3. 校验 _pct 对 None / NaN / 非数字 一律返回「—」而不是 0
  4. 源码级扫描：不得出现硬编码百分比/价格字面量，不得用 st.image 的过时参数
"""
from __future__ import annotations

import ast
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sentiment as sm          # noqa: E402
import page_sentiment as ps     # noqa: E402

PASS, FAIL = [], []


def ck(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(("✅ " if cond else "❌ ") + msg)


# ---------- 构造两日归档（与 tools_probe_sentiment.py 同一算例） ----------
def build_payload() -> dict:
    d1, d2 = "20260827", "20260828"
    rows = []
    for i in range(20):                      # D1 首板 20 只
        rows.append({"date": d1, "code": f"A{i:02d}", "name": f"甲{i}",
                     "industry": "测试", "limit_times": 1, "first_time": "09:31"})
    for i in range(5):                       # D1 2 板 5 只
        rows.append({"date": d1, "code": f"B{i:02d}", "name": f"乙{i}",
                     "industry": "测试", "limit_times": 2, "first_time": "09:35"})
    rows.append({"date": d1, "code": "C00", "name": "丙0",
                 "industry": "测试", "limit_times": 3, "first_time": "09:40"})

    for i in range(8):                       # D2：8 只首板晋级 2 板
        rows.append({"date": d2, "code": f"A{i:02d}", "name": f"甲{i}",
                     "industry": "测试", "limit_times": 2, "first_time": "09:32"})
    for i in range(2):                       # D2：2 只 2 板晋级 3 板
        rows.append({"date": d2, "code": f"B{i:02d}", "name": f"乙{i}",
                     "industry": "测试", "limit_times": 3, "first_time": "09:36"})
    rows.append({"date": d2, "code": "Z00", "name": "新高",   # 5 板 → 4 板断层
                 "industry": "测试", "limit_times": 5, "first_time": "09:45"})
    hist = pd.DataFrame(rows)

    # 今日涨幅：D1 连板股（B0~B4 + C00）的表现，其中 C00 故意缺失
    pct_map = {"B00": 10.0, "B01": 10.0, "B02": 2.0, "B03": -3.5, "B04": -1.2}

    thresholds = sm.phase_thresholds(hist, before_date=d2)
    promo = sm.promotion_rates(hist, d2, d1)
    premium = sm.continuation_premium(hist, d1, pct_map)
    gap = sm.ladder_gap(hist, d2)
    breadth = sm.profit_effect(1.5, 0.2, 2800, 2100)
    phase = sm.cycle_phase(promo, gap, premium, thresholds=thresholds)
    plan = sm.verification_plan(promo, gap, premium, breadth, thresholds=thresholds)

    ok_flags = [promo.get("status") == "ok", gap.get("status") == "ok",
                breadth.get("status") == "ok"]
    status = "complete" if all(ok_flags) else ("incomplete" if any(ok_flags) else "failed")
    return {
        "status": status, "date": d2, "prev_date": d1,
        "generated_at": "2026-08-28 19:40:00", "archive_days": 2,
        "promotion": promo, "premium": premium, "ladder_gap": gap,
        "breadth": breadth, "phase": phase, "phase_thresholds": thresholds,
        "verification_plan": plan,
    }


payload = build_payload()

print("=" * 60)
print("一、payload 顶层契约")
print("=" * 60)
for k in ("status", "date", "prev_date", "generated_at", "archive_days",
          "promotion", "premium", "ladder_gap", "breadth", "phase",
          "phase_thresholds", "verification_plan"):
    ck(k in payload, f"顶层键存在：{k}")
ck(payload["status"] == "complete", f"构造算例整体 status=complete（实际 {payload['status']}）")

print("=" * 60)
print("二、展示层读取的每个子键都存在")
print("=" * 60)
r12 = payload["promotion"]["rates"]["1进2"]
for k in ("rate", "promoted", "base", "reliable"):
    ck(k in r12, f"promotion.rates['1进2'].{k} 存在")
ck(abs(r12["rate"] - 0.4) < 1e-9, f"1进2 = 8/20 = 40.0%（实际 {r12['rate'] * 100:.1f}%）")
ck(r12["reliable"] is True, "1进2 样本 20 ≥ 10 → reliable=True")

prem = payload["premium"]
for k in ("status", "median_pct", "win_rate", "n", "missing"):
    ck(k in prem, f"premium.{k} 存在")
ck(prem["n"] == 5, f"连板溢价样本 n=5（实际 {prem['n']}）")
ck(prem["missing"] == 1, f"C00 涨幅缺失被计入 missing=1（实际 {prem['missing']}）")
ck(abs(prem["median_pct"] - 2.0) < 1e-9, f"中位涨幅 2.0%（实际 {prem['median_pct']}）")

gap = payload["ladder_gap"]
for k in ("status", "max_height", "total", "tiers", "gaps", "verdict"):
    ck(k in gap, f"ladder_gap.{k} 存在")
ck(gap["gaps"] == [4], f"识别到 4 板断层（实际 {gap['gaps']}）")

br = payload["breadth"]
for k in ("status", "divergence", "mean_pct", "median_pct", "verdict"):
    ck(k in br, f"breadth.{k} 存在")
ck(abs(br["divergence"] - 1.3) < 1e-9, f"背离 1.30 个百分点（实际 {br['divergence']:.2f}）")

ph = payload["phase"]
for k in ("status", "phase", "basis", "note"):
    ck(k in ph, f"phase.{k} 存在")
ck(ph["phase"] in ps.PHASE_STYLE,
   f"周期名「{ph['phase']}」在展示层样式表中有对应图标与颜色")
ck("不构成" in ph["note"], "周期定位携带免责声明，展示层原样透出")

th = ph.get("thresholds") or {}
for k in ("method", "high", "mid", "low", "samples", "note"):
    ck(k in th, f"phase.thresholds.{k} 存在（判定基准必须随结论一起下发）")
ck(th.get("method") in ("percentile", "fallback"),
   f"阈值来源标注明确（实际 {th.get('method')}）")
ck(0 <= th.get("low", -1) < th.get("mid", -1) < th.get("high", -1),
   f"阈值严格递增且非负：{th.get('low')}/{th.get('mid')}/{th.get('high')}")
ck(abs(th.get("high", 0) - 0.45) > 1e-9,
   "发酵门槛不再是被证伪的硬编码 45%")
ck(payload["phase_thresholds"] == th,
   "顶层 phase_thresholds 与 phase.thresholds 同源（不重复计算）")

print("=" * 60)
print("二·B、判定基准行的渲染（让用户能看出档位是怎么定的）")
print("=" * 60)
cap = ps._threshold_caption(ph, th)
ck("判定基准" in cap, f"基准行含「判定基准」字样 — {cap[:50]}")
ck(re.search(r"\d", cap) is not None, "基准行含具体阈值数字（可逐日核对）")
ck(ps._threshold_caption(ph, {}) == "",
   "阈值缺失时基准行返回空串（宁可不显示，不编造）")

cap_p = ps._threshold_caption(
    {"r12": 0.242},
    {"method": "percentile", "high": 0.205, "mid": 0.157, "low": 0.105,
     "samples": 34, "window_from": "20260811", "window_to": "20261008"})
ck("20.5%" in cap_p and "15.7%" in cap_p and "10.5%" in cap_p,
   f"自适应基准行列全四档刻度 — {cap_p}")
ck("20260811" in cap_p and "20261008" in cap_p, "自适应基准行标明分位窗口区间")
ck("24.2%" in cap_p, "自适应基准行带当前晋级率，便于与刻度对照")

cap_f = ps._threshold_caption({"r12": 0.10},
                              {"method": "fallback", "high": 0.21, "mid": 0.16,
                               "low": 0.11, "samples": 7,
                               "note": "样本仅 7 个交易日（<20），暂用经验阈值"})
ck("样本仅 7" in cap_f and "经验阈值" in cap_f,
   f"样本不足时基准行如实说明，不冒充历史分位 — {cap_f}")

print("=" * 60)
print("二·C、周期区块真的把基准行渲染出来（行为验证，不是字符串扫描）")
print("=" * 60)

# 为什么必须做行为验证：上一版这里只 grep 源码里有没有 `_threshold_caption(phase`,
# 于是把渲染代码改成 `if False:` 之后断言照样通过——基准行压根没上页面，
# 字符串还在文件里。改成替换 st 接口、真调一次 _render_phase、看捕获到了什么。
_calls = []
_real_caption, _real_markdown = ps.st.caption, ps.st.markdown
try:
    ps.st.caption = lambda *a, **k: _calls.append(str(a[0]) if a else "")
    ps.st.markdown = lambda *a, **k: None
    ps._render_phase(ph, payload["date"], payload["prev_date"],
                     payload["generated_at"])
finally:
    ps.st.caption, ps.st.markdown = _real_caption, _real_markdown

ck(any("判定基准" in c for c in _calls),
   f"周期区块渲染出判定基准行（捕获 {len(_calls)} 条 caption）")
ck(any(str(ph["note"]) in c for c in _calls), "周期区块照常渲染免责声明")
ck(any("不构成" in c for c in _calls), "免责声明内容原样透出，未被基准行顶掉")

# 阈值缺失时页面不得编造基准行
ph_not = dict(ph)
ph_not["thresholds"] = {}
_calls2 = []
try:
    ps.st.caption = lambda *a, **k: _calls2.append(str(a[0]) if a else "")
    ps.st.markdown = lambda *a, **k: None
    ps._render_phase(ph_not, payload["date"], payload["prev_date"],
                     payload["generated_at"])
finally:
    ps.st.caption, ps.st.markdown = _real_caption, _real_markdown
ck(not any("判定基准" in c for c in _calls2),
   "阈值缺失时页面不显示基准行（不编造）")

plan = payload["verification_plan"]
ck(len(plan) == 4, f"验证条件 4 条（实际 {len(plan)}）")
for i, item in enumerate(plan, 1):
    for k in ("指标", "今日基准", "验证条件", "为什么看它"):
        ck(k in item, f"验证条件#{i} 含字段「{k}」")
    ck(bool(re.search(r"\d", str(item.get("今日基准", "")))),
       f"验证条件#{i} 的今日基准含具体数字（可对账）")

print("=" * 60)
print("三、_pct 缺失值语义：绝不用 0 冒充")
print("=" * 60)
ck(ps._pct(None) == "—", "_pct(None) → 「—」")
ck(ps._pct(float("nan")) == "—", "_pct(NaN) → 「—」")
ck(ps._pct("abc") == "—", "_pct(非数字) → 「—」")
ck(ps._pct(0) == "0.0%", "_pct(0) → 「0.0%」（真实的 0 仍要显示）")
ck(ps._pct(-1.234, 2, sign=True) == "-1.23%", "_pct 带符号两位小数正确")
ck(ps._pct(2.5, 1, sign=True) == "+2.5%", "_pct 正数带 + 号")

print("=" * 60)
print("四、源码规范")
print("=" * 60)
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "page_sentiment.py"), encoding="utf-8").read()

# 展示层不得自己算指标 —— 只能读 derived.json
tree = ast.parse(src)
imports = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imports.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
        imports.add(node.module.split(".")[0])
ck("tushare" not in imports and "akshare" not in imports,
   f"展示层未直连数据源 — {sorted(imports)}")
ck("requests" not in imports and "urllib" not in imports,
   "展示层不联网（数据一律来自跑批产物）")

# 硬编码数值字面量扫描：形如 "12.3%" / "1234.56" 出现在字符串里就是嫌疑
hard = re.findall(r'["\'][^"\']*?\d+\.\d+\s*%[^"\']*?["\']', src)
ck(not hard, f"无硬编码百分比字面量（发现 {hard}）")
ck("use_column_width" not in src, "未使用已废弃的 use_column_width")
ck("st.image" not in src, "本页无图片渲染，不触发 st.image 兼容坑")
ck("replace(0, pd.NA)" not in src, "未使用 replace(0, pd.NA)（pandas 3.x 铁律）")

# 判定基准必须在周期区块里真的渲染出来——由「二·C」的行为验证负责，
# 这里只保留阈值来源的静态检查（不得在展示层复制数字）
ck("0.45" not in src, "展示层不残留旧版 45% 判定阈值（阈值一律来自下发数据）")
ck("sm.PHASE_MIN_SAMPLES" in src,
   "「样本不足」的门槛引用计算层常量，不在展示层复制数字")

# app.py 接线校验
app_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "app.py"), encoding="utf-8").read()
ck("from page_sentiment import render_sentiment_block" in app_src,
   "app.py 已 import render_sentiment_block")
ck("render_sentiment_block()" in app_src, "app.py 已调用 render_sentiment_block()")
i_block = app_src.find("render_sentiment_block()")
i_ladder = app_src.find("🪜 连板情绪天梯")
ck(0 < i_block < i_ladder, "情绪结论区块排在连板天梯名单之前（先结论后明细）")

print("-" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("\n失败项：")
    for m in FAIL:
        print("  ❌ " + m)
    sys.exit(1)
print("✅ 全部通过")
