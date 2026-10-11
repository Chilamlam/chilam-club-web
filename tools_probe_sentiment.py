"""
情绪派生指标计算层自检（纯离线，不依赖 tushare / 网络）

校验口径：
1. 晋级率必须按「昨日 N 板 → 今日 ≥N+1」计算，手工构造算例对账
2. 样本 <10 的高位板必须标 reliable=False（防止 1/1=100% 被当成信号）
3. 缺前一交易日时必须返回 failed，不得用单日数据硬算
4. 梯队断层必须能识别中间高度为 0 的情况
5. 连板溢价主口径为中位数，且必须统计缺失数量
6. 赚钱效应背离方向判断正确（均值>中位 → 涨幅集中）
7. 周期定位在 1进2 样本不足时必须返回 insufficient，不得猜测
8. 明日验证条件每条都必须带「今日基准值」——不带基准的判断无法对账
9. 计算层不得 import streamlit / tushare / akshare
"""
from __future__ import annotations

import ast
import os
import sys

import pandas as pd

import sentiment as sm

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def _hist() -> pd.DataFrame:
    """
    构造两日涨停归档：
      D1(20260101): 首板 20 只(A00~A19)、2板 5 只(B0~B4)、3板 1 只(C0)
      D2(20260102): A00~A07 晋级为 2 板(8/20=40%)
                    B0,B1 晋级为 3 板(2/5=40%)
                    C0 未晋级(0/1=0%)
                    另有 4 板 1 只(D0，新晋高度) → 3 板为 0，形成断层
    """
    rows = []
    for i in range(20):
        rows.append({"date": "20260101", "code": f"A{i:02d}", "name": f"甲{i}",
                     "industry": "测试", "limit_times": 1, "first_time": "09:31:00"})
    for i in range(5):
        rows.append({"date": "20260101", "code": f"B{i}", "name": f"乙{i}",
                     "industry": "测试", "limit_times": 2, "first_time": "09:31:00"})
    rows.append({"date": "20260101", "code": "C0", "name": "丙0",
                 "industry": "测试", "limit_times": 3, "first_time": "09:31:00"})

    for i in range(8):
        rows.append({"date": "20260102", "code": f"A{i:02d}", "name": f"甲{i}",
                     "industry": "测试", "limit_times": 2, "first_time": "09:35:00"})
    for i in range(2):
        rows.append({"date": "20260102", "code": f"B{i}", "name": f"乙{i}",
                     "industry": "测试", "limit_times": 3, "first_time": "09:40:00"})
    rows.append({"date": "20260102", "code": "D0", "name": "丁0",
                 "industry": "测试", "limit_times": 5, "first_time": "09:31:00"})
    for i in range(6):
        rows.append({"date": "20260102", "code": f"E{i}", "name": f"戊{i}",
                     "industry": "测试", "limit_times": 1, "first_time": "10:00:00"})
    return pd.DataFrame(rows)


def test_promotion() -> None:
    h = _hist()
    r = sm.promotion_rates(h, "20260102", "20260101")
    check("晋级率计算返回 ok", r.get("status") == "ok", str(r.get("reason", "")))
    rates = r.get("rates", {})

    check("1进2 = 8/20 = 40%",
          rates.get("1进2", {}).get("promoted") == 8
          and rates["1进2"]["base"] == 20
          and abs(rates["1进2"]["rate"] - 0.4) < 1e-9,
          str(rates.get("1进2")))
    check("1进2 样本 20 ≥10 标 reliable=True",
          rates.get("1进2", {}).get("reliable") is True)

    check("2进3 = 2/5 = 40%",
          rates.get("2进3", {}).get("promoted") == 2
          and abs(rates["2进3"]["rate"] - 0.4) < 1e-9,
          str(rates.get("2进3")))
    check("2进3 样本 5 <10 标 reliable=False",
          rates.get("2进3", {}).get("reliable") is False)

    check("3进4 = 0/1 = 0%（C0 未晋级）",
          rates.get("3进4", {}).get("promoted") == 0
          and rates["3进4"]["base"] == 1)
    check("3进4 单只样本标 reliable=False（防 100% 被当信号）",
          rates.get("3进4", {}).get("reliable") is False)


def test_promotion_missing_prev() -> None:
    h = _hist()
    r = sm.promotion_rates(h, "20260102", "20251231")   # 不存在的前一日
    check("缺前一交易日 → failed", r.get("status") == "failed", str(r.get("reason", "")))
    r2 = sm.promotion_rates(pd.DataFrame(columns=sm.LADDER_COLS), "20260102", "20260101")
    check("空归档 → failed", r2.get("status") == "failed")


def test_ladder_gap() -> None:
    h = _hist()
    g = sm.ladder_gap(h, "20260102")
    check("断层检测返回 ok", g.get("status") == "ok")
    check("最高板识别为 5", g.get("max_height") == 5, str(g.get("max_height")))
    check("识别出 4 板断层", 4 in (g.get("gaps") or []), f"gaps={g.get('gaps')}")
    check("3 板有票不算断层", 3 not in (g.get("gaps") or []))
    check("断层结论文案提示不连续", "断层" in str(g.get("verdict", "")))

    g2 = sm.ladder_gap(h, "20991231")
    check("缺当日归档 → failed", g2.get("status") == "failed")


def test_premium() -> None:
    h = _hist()
    # D1 的 2 板：B0~B4；3 板：C0 → 共 6 只 ≥2 板
    pct_map = {"B0": 10.0, "B1": 5.0, "B2": -3.0, "B3": -8.0, "C0": 2.0}  # B4 故意缺失
    p = sm.continuation_premium(h, "20260101", pct_map)
    check("连板溢价返回 ok", p.get("status") == "ok", str(p.get("reason", "")))
    check("样本 n=5（B4 缺失未计入）", p.get("n") == 5, str(p.get("n")))
    check("缺失数量 missing=1", p.get("missing") == 1, str(p.get("missing")))
    check("中位数 = 2.0", abs(p.get("median_pct", 0) - 2.0) < 1e-9, str(p.get("median_pct")))
    check("胜率 = 3/5 = 60%", abs(p.get("win_rate", 0) - 0.6) < 1e-9, str(p.get("win_rate")))
    check("再涨停统计 limit_again=1", p.get("limit_again") == 1)

    p2 = sm.continuation_premium(h, "20260101", {})
    check("涨幅全缺 → failed", p2.get("status") == "failed")


def test_profit_effect() -> None:
    a = sm.profit_effect(1.5, 0.2, 2800, 2200)
    check("均值远高于中位 → 判定涨幅集中",
          a.get("status") == "ok" and "少数" in a.get("verdict", ""), a.get("verdict", ""))
    check("背离值 = 1.3", abs(a.get("divergence", 0) - 1.3) < 1e-9)
    check("上涨占比 = 2800/5000", abs(a.get("up_ratio", 0) - 0.56) < 1e-9)

    b = sm.profit_effect(-1.0, 0.1, 1000, 4000)
    check("中位高于均值 → 判定少数大跌拖低",
          "拖低" in b.get("verdict", ""), b.get("verdict", ""))

    c = sm.profit_effect(None, 0.1, 1, 1)
    check("缺均值 → failed", c.get("status") == "failed")


def test_cycle_and_plan() -> None:
    h = _hist()
    promo = sm.promotion_rates(h, "20260102", "20260101")
    gap = sm.ladder_gap(h, "20260102")
    premium = sm.continuation_premium(h, "20260101", {"B0": 10.0, "B1": 5.0,
                                                     "B2": -3.0, "B3": -8.0, "C0": 2.0})
    breadth = sm.profit_effect(1.5, 0.2, 2800, 2200)

    ph = sm.cycle_phase(promo, gap, premium)
    check("周期定位返回 ok", ph.get("status") == "ok", str(ph.get("reason", "")))
    check("1进2=40% 且正溢价 → 发酵期（在经验阈值 21% 之上）",
          ph.get("phase") == "发酵期", str(ph.get("phase")))
    check("未传阈值时回退经验阈值并如实标注 method=fallback",
          (ph.get("thresholds") or {}).get("method") == "fallback",
          str((ph.get("thresholds") or {}).get("method")))
    check("周期结论声明不含参与建议",
          "不构成参与建议" in str(ph.get("note", "")))

    # 溢价为负时，即便晋级率高也不得判「发酵」（接力顺畅与溢价为负互相矛盾）
    neg = sm.continuation_premium(h, "20260101", {"B0": -10.0, "B1": -5.0,
                                                  "B2": -3.0, "B3": -8.0, "C0": -2.0})
    ph_neg = sm.cycle_phase(promo, gap, neg)
    check("晋级率高但连板溢价为负 → 不判发酵期",
          ph_neg.get("phase") == "分歧期", str(ph_neg.get("phase")))

    # 1进2 样本不足时必须拒绝定位
    thin = {"status": "ok", "rates": {"1进2": {"base": 3, "promoted": 3,
                                              "rate": 1.0, "reliable": False}}}
    ph2 = sm.cycle_phase(thin, gap, premium)
    check("1进2 样本不足 → insufficient（不猜测）",
          ph2.get("status") == "insufficient", str(ph2.get("reason", "")))

    plan = sm.verification_plan(promo, gap, premium, breadth)
    check("明日验证条件至少 4 条", len(plan) >= 4, f"{len(plan)} 条")
    for item in plan:
        has_base = bool(str(item.get("今日基准", "")).strip())
        check(f"「{item.get('指标')}」带今日基准值", has_base, str(item.get("今日基准")))
        check(f"「{item.get('指标')}」带可对账阈值",
              any(ch.isdigit() for ch in str(item.get("验证条件", ""))))

    # 验证条件的刻度必须与周期定位同源（同一规则两处实现必漂移）
    plan_th = sm.verification_plan(promo, gap, premium, breadth,
                                   thresholds={"high": 0.30, "mid": 0.20,
                                               "low": 0.12, "method": "percentile"})
    cond = str(plan_th[0].get("验证条件", ""))
    check("验证条件的档位刻度来自传入阈值（30/20/12）",
          "30.0%" in cond and "20.0%" in cond and "12.0%" in cond, cond)
    check("验证条件标注当前档位（40% ≥ 30% → 发酵期）",
          "发酵期" in str(plan_th[0].get("今日基准", "")),
          str(plan_th[0].get("今日基准")))

    # 验证条件的档位标注必须随阈值变化——同一 r12 在不同阈值下档位不同，
    # 若两处各算一套（硬编码），这里就会露馅
    plan_th2 = sm.verification_plan(promo, gap, premium, breadth,
                                    thresholds={"high": 0.90, "mid": 0.30,
                                                "low": 0.10, "method": "percentile"})
    check("同一 r12 在更严格的阈值下档位降级为分歧期",
          "分歧期" in str(plan_th2[0].get("今日基准", "")),
          str(plan_th2[0].get("今日基准")))


def _hist_long(m_list: list, per_day: int = 20) -> pd.DataFrame:
    """
    构造长历史归档：第 d 日 20 只首板，其中前 m_list[d-2] 只在第 d+1 日晋级为 2 板。
    于是第 d+1 日的 1进2 晋级率 = m_list[d-2] / per_day —— 晋级率序列完全可控，
    便于用手算值对账分位阈值。
    """
    rows = []
    n = len(m_list) + 1
    for d in range(1, n + 1):
        for i in range(per_day):
            rows.append({"date": f"202601{d:02d}", "code": f"S{d:02d}{i:02d}",
                         "name": f"股{i}", "industry": "测试",
                         "limit_times": 1, "first_time": "09:31:00"})
        if d == 1:
            continue
        for i in range(m_list[d - 2]):
            rows.append({"date": f"202601{d:02d}", "code": f"S{d - 1:02d}{i:02d}",
                         "name": f"股{i}", "industry": "测试",
                         "limit_times": 2, "first_time": "09:35:00"})
    return pd.DataFrame(rows)


def test_phase_adaptive() -> None:
    """
    自适应阈值的核心校验（这是修「几乎每天都判退潮/冰点」的关键）。

    最要紧的两条不是「阈值算得对不对」，而是：
      ① 阈值**只由当日之前的历史决定**——当日结果换成极值，阈值必须一字不变
      ② 阈值与真实分布**同刻度**——今日若是历史最高晋级率，就必须判发酵，
         而不是像旧阈值那样把 p90 高位判成「退潮期」
    """
    # 23 个历史日：晋级只数在 1~6 台阶间循环（→ 5%~30%），手算分位如下：
    #   sorted = [1×4, 2×4, 3×4, 4×4, 5×4, 6×3]
    #   p80 → pos=17.6 → 5  → 25%
    #   p55 → pos=12.1 → 4  → 20%
    #   p25 → pos=5.5  → 2  → 10%
    base23 = [1, 2, 3, 4, 5, 6] * 3 + [1, 2, 3, 4, 5]
    today = "20260125"

    h_high = _hist_long(base23 + [20])      # 今日 20/20 = 100%（历史最高）
    h_low = _hist_long(base23 + [0])        # 今日 0/20 = 0%（历史最低）

    s = sm.promotion_rate_series(h_high, before_date=today)
    check("晋级率序列长度 = 历史天数 - 1", len(s) == 23, str(len(s)))
    check("序列末位严格早于当日（当日不参与基准）",
          len(s) > 0 and str(s.index[-1]) < today, str(list(s.index)[-1]))

    th = sm.phase_thresholds(h_high, before_date=today)
    check("历史样本 23 ≥ 20 → 启用分位阈值",
          th["method"] == "percentile", str(th.get("method", "")))
    check("分位阈值 = 手算 p80/p55/p25 = 25%/20%/10%",
          abs(th["high"] - 0.25) < 1e-9 and abs(th["mid"] - 0.20) < 1e-9
          and abs(th["low"] - 0.10) < 1e-9,
          f"{th['high']:.3f}/{th['mid']:.3f}/{th['low']:.3f}")
    check("阈值严格单调递减且非负",
          th["high"] > th["mid"] > th["low"] >= 0)
    check("阈值窗口右端不含当日", str(th["window_to"]) < today, str(th["window_to"]))

    # ★ 方法论底线：当日结果绝不能影响阈值
    th_low = sm.phase_thresholds(h_low, before_date=today)
    check("★ 当日结果换成极值（100% vs 0%）后阈值完全不变",
          (th["high"], th["mid"], th["low"], th["samples"])
          == (th_low["high"], th_low["mid"], th_low["low"], th_low["samples"]),
          f"{th['high']:.3f} vs {th_low['high']:.3f}")

    # ★ 端到端语义：历史最高的晋级率必须判发酵，历史最低必须判冰点
    promo_high = sm.promotion_rates(h_high, today, "20260124")
    ph_high = sm.cycle_phase(promo_high, {"status": "failed"},
                             {"status": "failed"}, thresholds=th)
    check("★ 今日 100%（历史最高）→ 判发酵期",
          ph_high.get("phase") == "发酵期", f"{ph_high.get('phase')}")

    promo_low = sm.promotion_rates(h_low, today, "20260124")
    ph_low = sm.cycle_phase(promo_low, {"status": "failed"},
                            {"status": "failed"}, thresholds=th)
    check("★ 今日 0%（历史最低）→ 判冰点期",
          ph_low.get("phase") == "冰点期", f"{ph_low.get('phase')}")

    # 旧阈值下的经典误判：24.2% 在真实分布里是 p90 高位，却被判「退潮」
    check("★ 历史高位晋级率不再被判退潮（旧版 p90 判退潮的反例）",
          sm.classify_phase(0.242, th) != "退潮期",
          sm.classify_phase(0.242, th))

    # 档位边界：临界值必须归入较高档（≥ 即命中）
    bth = {"high": 0.30, "mid": 0.20, "low": 0.12, "method": "percentile"}
    for r, want in ((0.30, "发酵期"), (0.299, "分歧期"), (0.20, "分歧期"),
                    (0.199, "退潮期"), (0.12, "退潮期"), (0.119, "冰点期")):
        check(f"边界 r12={r * 100:.1f}% → {want}",
              sm.classify_phase(r, bth) == want, sm.classify_phase(r, bth))

    # 阈值本身不可信时必须回退，绝不能任由逆序阈值把全部日子滑进同一档
    check("逆序阈值（high<mid）被拒绝并回退经验阈值",
          sm.classify_phase(0.15, {"high": 0.10, "mid": 0.20, "low": 0.05})
          == "退潮期",
          sm.classify_phase(0.15, {"high": 0.10, "mid": 0.20, "low": 0.05}))
    for bad in ({"high": 1, "mid": 2, "low": 3}, {"high": "x", "mid": 1, "low": 0},
                {}, None):
        check(f"非法阈值 {bad!r:.30} → 回退且不抛异常",
              sm._normalize_thresholds(bad)["method"] == "fallback")

    # 样本不足 → 如实标注 fallback，不硬算分位
    th_short = sm.phase_thresholds(_hist_long(base23[:10]))
    check("样本 10 < 20 → 回退经验阈值",
          th_short["method"] == "fallback" and th_short["samples"] == 10,
          f"{th_short['method']} samples={th_short['samples']}")
    check("回退时说明里写明样本数与经验阈值",
          "10" in th_short["note"] and "%" in th_short["note"], th_short["note"])

    # 首板太少的噪声日不进分布
    thin = pd.DataFrame(
        [{"date": "20260126", "code": f"T{i:02d}", "name": "t", "industry": "t",
          "limit_times": 1, "first_time": "09:31:00"} for i in range(5)]
        + [{"date": "20260127", "code": f"T{i:02d}", "name": "t", "industry": "t",
            "limit_times": 2, "first_time": "09:35:00"} for i in range(5)]
    )
    h_mix = pd.concat([h_high, thin], ignore_index=True)
    idx = set(sm.promotion_rate_series(h_mix).index)
    check("前一日首板仅 5 只（<10）→ 该日不进分布",
          "20260127" not in idx, str(sorted(idx)[-3:]))

    # 空归档 / 单日归档不得崩，也不得编造阈值
    empty = pd.DataFrame(columns=sm.LADDER_COLS)
    th_empty = sm.phase_thresholds(empty)
    check("空归档 → fallback 且样本 0",
          th_empty["method"] == "fallback" and th_empty["samples"] == 0)
    check("空归档序列长度 0", len(sm.promotion_rate_series(empty)) == 0)


def test_layer_purity() -> None:
    path = os.path.join(os.path.dirname(__file__), "sentiment.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    check("计算层未 import streamlit", "streamlit" not in mods, f"{sorted(mods)}")
    check("计算层未 import tushare/akshare", not ({"tushare", "akshare"} & mods))

    src = open(path, encoding="utf-8").read()
    check("未使用 replace(0, pd.NA)", "replace(0, pd.NA)" not in src)
    check("astype 使用显式 float64", "astype(float)" not in src)


if __name__ == "__main__":
    print("=" * 60)
    print("情绪派生指标计算层自检")
    print("=" * 60)
    test_promotion()
    test_promotion_missing_prev()
    test_ladder_gap()
    test_premium()
    test_profit_effect()
    test_cycle_and_plan()
    test_phase_adaptive()
    test_layer_purity()
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
