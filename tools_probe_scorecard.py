"""
战绩回看计算层自检（不依赖 tushare / 网络 / 真实归档）

校验口径：
1. compute_returns 的 alpha 必须等于 自身收益 - 基准同期收益（手工算例对账）
2. 未来交易日不足时对应 horizon 必须留空（NaN），禁止外推
3. 基准缺失时 summarize 必须返回 status=failed，而不是给出一个漂亮数字
4. 样本 < MIN_SAMPLE 时必须标 insufficient，并带上「基本是噪音」的说明
5. 分档区分度检验：构造单调数据必须判 monotonic=True，构造反序必须判 False
6. 计算层不得 import streamlit（托管环境下要能脱离 runtime 单测）
7. beta 估计：构造 beta=2 必须估出 ≈2，构造 beta=1 必须估出 ≈1（反向验证，
   防止函数恒定输出一个数还看起来"对"）；入选日不足时必须拒绝给 beta
8. 有效样本量：独立观测数必须等于 ceil(入选日数/horizon) 而非原始记录条数；
   全胜构造的 p 值必须显著、对开构造必须不显著（双向验证 p 真的在算）
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pandas as pd

import scorecard as sc

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def _mk_prices(days: list[str], codes: dict[str, list[float]]) -> pd.DataFrame:
    rows = []
    for code, series in codes.items():
        for d, v in zip(days, series):
            rows.append({"date": d, "ts_code": code, "adj_close": float(v)})
    return pd.DataFrame(rows)


def test_alpha_math() -> None:
    days = ["20260101", "20260102", "20260103", "20260104", "20260105", "20260106"]
    prices = _mk_prices(days, {
        sc.BENCHMARK: [100, 101, 102, 103, 104, 105],   # 基准每日 +1 点
        "600000.SH": [10, 11, 12, 13, 14, 15],          # 涨得比基准快
    })
    picks = pd.DataFrame([{
        "date": "20260101", "strategy": "rps", "ts_code": "600000.SH",
        "name": "测试", "rank": 1, "bucket": "1-10", "ref_close": 10.0,
    }])
    rets = sc.compute_returns(picks, prices)
    check("收益表非空", not rets.empty)
    if rets.empty:
        return
    r = rets.iloc[0]

    exp_r1 = 11 / 10 - 1
    exp_b1 = 101 / 100 - 1
    check("T+1 alpha 与手算一致",
          abs(r["alpha_1"] - (exp_r1 - exp_b1)) < 1e-12,
          f"实得 {r['alpha_1']:.6f} 期望 {exp_r1 - exp_b1:.6f}")

    exp_r5 = 15 / 10 - 1
    exp_b5 = 105 / 100 - 1
    check("T+5 alpha 与手算一致",
          abs(r["alpha_5"] - (exp_r5 - exp_b5)) < 1e-12,
          f"实得 {r['alpha_5']:.6f} 期望 {exp_r5 - exp_b5:.6f}")

    check("未来交易日不足的 horizon 留空（禁止外推）", bool(np.isnan(r["alpha_10"])))


def test_missing_benchmark() -> None:
    days = ["20260101", "20260102"]
    prices = _mk_prices(days, {"600000.SH": [10, 11]})   # 故意不给基准
    picks = pd.DataFrame([{
        "date": "20260101", "strategy": "rps", "ts_code": "600000.SH",
        "name": "测试", "rank": 1, "bucket": "1-10", "ref_close": 10.0,
    }])
    res = sc.summarize(picks, prices)
    check("基准缺失时 status=failed", res.get("status") == "failed", res.get("reason", ""))
    rets = sc.compute_returns(picks, prices)
    check("基准缺失时收益表为空", rets.empty)


def test_insufficient_sample() -> None:
    days = [f"2026010{i}" for i in range(1, 10)] + ["20260110", "20260111", "20260112"]
    n_small = 5
    codes = {sc.BENCHMARK: [100 + i for i in range(len(days))]}
    for k in range(n_small):
        codes[f"60000{k}.SH"] = [10 + i * 0.5 for i in range(len(days))]
    prices = _mk_prices(days, codes)
    picks = pd.DataFrame([{
        "date": "20260101", "strategy": "rps", "ts_code": f"60000{k}.SH",
        "name": f"股{k}", "rank": k + 1, "bucket": "1-10", "ref_close": 10.0,
    } for k in range(n_small)])

    res = sc.summarize(picks, prices)
    blk = res["strategies"]["rps"]["horizons"]["5"]
    check(f"样本 {n_small} 条 (<{sc.MIN_SAMPLE}) 标 insufficient",
          blk.get("status") == "insufficient", blk.get("reason", ""))
    check("insufficient 时带「噪音」说明",
          "噪音" in str(blk.get("reason", "")))
    check("全部策略样本不足时顶层 status=incomplete",
          res.get("status") == "incomplete", res.get("reason", ""))


def test_bucket_monotonic() -> None:
    days = [f"202601{i:02d}" for i in range(1, 21)]
    bench = [100.0] * len(days)          # 基准不动，alpha 等于自身收益
    codes = {sc.BENCHMARK: bench}
    picks_rows = []

    # 三档，每档 20 只，涨幅递减 → 应判单调
    gains = {"1-10": 0.10, "11-30": 0.05, "31-50": 0.01}
    idx = 0
    for bucket, g in gains.items():
        for _ in range(20):
            code = f"9{idx:05d}.SZ"
            codes[code] = [10.0 * (1 + g * i / 5.0) for i in range(len(days))]
            picks_rows.append({
                "date": days[0], "strategy": "rps", "ts_code": code,
                "name": code, "rank": idx + 1, "bucket": bucket, "ref_close": 10.0,
            })
            idx += 1

    prices = _mk_prices(days, codes)
    picks = pd.DataFrame(picks_rows)
    rets = sc.compute_returns(picks, prices)
    disc = sc.bucket_monotonic(rets, 5)
    check("单调构造 → monotonic=True",
          disc.get("status") == "ok" and disc.get("monotonic") is True,
          str(disc.get("verdict", disc.get("reason", ""))))

    # 反序：把档位标签调换，应判不单调
    swap = {"1-10": "31-50", "31-50": "1-10"}
    rets2 = rets.copy()
    rets2["bucket"] = rets2["bucket"].map(lambda b: swap.get(b, b))
    disc2 = sc.bucket_monotonic(rets2, 5)
    check("反序构造 → monotonic=False",
          disc2.get("status") == "ok" and disc2.get("monotonic") is False,
          str(disc2.get("verdict", "")))

    res = sc.summarize(picks, prices)
    blk = res["strategies"]["rps"]["horizons"]["5"]
    check(f"样本 60 条 (≥{sc.MIN_SAMPLE}) 标 ok", blk.get("status") == "ok")
    check("顶层 status=complete", res.get("status") == "complete")
    check("方向正确率在 [0,1] 区间",
          0.0 <= blk.get("direction_accuracy", -1) <= 1.0,
          f"{blk.get('direction_accuracy')}")


def test_beta_estimation() -> None:
    """
    beta 估计的正反双向验证。

    这是本次新增的核心保护：alpha = ret - 1.0×bench 隐含 beta=1，
    高 beta 组合在下跌市会把放大的跌幅误记成选股能力为负。
    所以既要验证「喂 beta=2 的数据能估出 2」，也要反向验证
    「喂 beta=1 的数据不会误报成 2」——只会成功的断言等于没有断言。
    """
    days = [f"202601{i:02d}" for i in range(1, 41)]

    def _build(beta_true: float, alpha_per_step: float = 0.0):
        """构造 bench 有涨有跌、个股严格按 beta 放大的合成数据。"""
        bench = [100.0]
        # 交替涨跌，保证 bench 有方差，否则 beta 无法辨识
        steps = [0.01, -0.015, 0.02, -0.01, 0.005, -0.02, 0.015, -0.005]
        for i in range(1, len(days)):
            bench.append(bench[-1] * (1 + steps[i % len(steps)]))
        codes = {sc.BENCHMARK: bench}
        for k in range(30):
            s = [10.0]
            for i in range(1, len(days)):
                br = bench[i] / bench[i - 1] - 1
                s.append(s[-1] * (1 + beta_true * br + alpha_per_step))
            codes[f"9{k:05d}.SZ"] = s
        prices = _mk_prices(days, codes)
        picks = pd.DataFrame([
            {"date": d, "strategy": "rps", "ts_code": f"9{k:05d}.SZ",
             "name": f"股{k}", "rank": k + 1,
             "bucket": sc.rank_bucket(k + 1), "ref_close": 10.0}
            for d in days[:25] for k in range(30)
        ])
        return sc.compute_returns(picks, prices)

    rets2 = _build(beta_true=2.0)
    est2 = sc.estimate_beta(rets2, 5)
    check("beta 回归状态 ok", est2.get("status") == "ok", str(est2.get("reason", "")))
    b2 = est2.get("beta", float("nan"))
    check("构造 beta=2 → 估出 ≈2", abs(b2 - 2.0) < 0.25, f"估得 {b2:.3f}")
    check("beta=2 构造的 R² 接近 1", est2.get("r_squared", 0) > 0.9,
          f"R²={est2.get('r_squared')}")

    # 反向验证：beta=1 的数据不能被估成 2，否则说明回归压根没在算
    rets1 = _build(beta_true=1.0)
    est1 = sc.estimate_beta(rets1, 5)
    b1 = est1.get("beta", float("nan"))
    check("构造 beta=1 → 估出 ≈1（反向验证，防止恒定输出）",
          abs(b1 - 1.0) < 0.25, f"估得 {b1:.3f}")
    check("beta=1 与 beta=2 的估计值可区分",
          abs(b2 - b1) > 0.5, f"beta1={b1:.3f} beta2={b2:.3f}")

    # beta=1 时 beta 调整与原始 alpha 应基本一致；beta=2 时必须显著不同
    check("beta≈1 时 beta 调整后 alpha 与原始 alpha 接近",
          abs(est1.get("adj_alpha_median", 0) - est1.get("raw_alpha_median", 0)) < 0.02,
          f"adj={est1.get('adj_alpha_median'):.4f} raw={est1.get('raw_alpha_median'):.4f}")
    check("beta≈2 时 beta 调整确实改变了 alpha（不是原样透传）",
          abs(est2.get("adj_alpha_median", 0) - est2.get("raw_alpha_median", 0)) > 1e-6,
          f"adj={est2.get('adj_alpha_median'):.4f} raw={est2.get('raw_alpha_median'):.4f}")

    # 入选日不足时必须拒绝给 beta，而不是硬算一个数出来
    few = rets2[rets2["date"].isin(days[:5])]
    est_few = sc.estimate_beta(few, 5)
    check(f"入选日 < {sc.MIN_DAYS_BETA} 时拒绝给 beta",
          est_few.get("status") == "insufficient" and "beta" not in est_few,
          str(est_few.get("reason", "")))


def test_effective_sample() -> None:
    """
    有效样本量折算的正反双向验证。

    要防的错是「拿虚高的 n 谈稳定跑赢/跑输」：T+5 窗口逐日滚动重叠，
    同日几十只票同涨同跌，2000+ 条记录其实只有个位数独立观测。
    """
    days = [f"202601{i:02d}" for i in range(1, 41)]
    codes = {sc.BENCHMARK: [100.0 + i for i in range(len(days))]}
    for k in range(40):
        codes[f"8{k:05d}.SZ"] = [10.0 + i * 0.2 for i in range(len(days))]
    prices = _mk_prices(days, codes)

    n_days_pick = 25
    picks = pd.DataFrame([
        {"date": d, "strategy": "rps", "ts_code": f"8{k:05d}.SZ",
         "name": f"股{k}", "rank": k + 1,
         "bucket": sc.rank_bucket(k + 1), "ref_close": 10.0}
        for d in days[:n_days_pick] for k in range(40)
    ])
    rets = sc.compute_returns(picks, prices)
    eff = sc.effective_sample(rets, 5)

    check("有效样本量返回非 failed", eff.get("status") in ("ok", "insufficient"),
          str(eff.get("reason", "")))
    check("原始记录数被如实记录",
          eff.get("raw_n", 0) == len(rets.dropna(subset=["alpha_5"])),
          f"raw_n={eff.get('raw_n')} 实际={len(rets.dropna(subset=['alpha_5']))}")
    check("入选日数正确", eff.get("n_days") == n_days_pick,
          f"n_days={eff.get('n_days')} 期望={n_days_pick}")

    # 关键断言：独立观测数必须约等于 入选日数 / horizon，而不是原始条数
    expect_indep = -(-n_days_pick // 5)   # ceil(25/5)
    check("独立观测数 = ceil(入选日数 / horizon)",
          eff.get("n_independent") == expect_indep,
          f"实得 {eff.get('n_independent')} 期望 {expect_indep}")
    check("独立观测数远小于原始记录数（折算真的生效了）",
          eff.get("n_independent", 0) * 20 < eff.get("raw_n", 0),
          f"{eff.get('n_independent')} vs {eff.get('raw_n')}")
    check(f"独立观测 < {sc.MIN_INDEPENDENT} 时标 insufficient",
          eff.get("status") == "insufficient", str(eff.get("status")))
    check("insufficient 时说明「负数不等于策略失效」",
          "不等于策略失效" in str(eff.get("reason", "")))

    # 反向验证 1：p 值必须真的在算。构造全胜，p 应当显著
    fake = pd.DataFrame([
        {"date": f"202602{i:02d}", "strategy": "rps", "ts_code": "600000.SH",
         "rank": 1, "bucket": "1-10", "alpha_5": 0.05}
        for i in range(1, 41)
    ])
    eff_win = sc.effective_sample(fake, 5)
    check("全胜构造 → 胜率 100%",
          eff_win.get("direction_accuracy_independent") == 1.0,
          f"{eff_win.get('direction_accuracy_independent')}")
    check("全胜构造 → p 值显著（反向验证 p 值真的在算）",
          eff_win.get("p_value", 1.0) < 0.05, f"p={eff_win.get('p_value')}")

    # 反向验证 2：五五对开时 p 必须不显著
    mixed = pd.DataFrame([
        {"date": f"202602{i:02d}", "strategy": "rps", "ts_code": "600000.SH",
         "rank": 1, "bucket": "1-10",
         "alpha_5": 0.05 if (i // 5) % 2 == 0 else -0.05}
        for i in range(1, 41)
    ])
    eff_mix = sc.effective_sample(mixed, 5)
    check("对开构造 → p 不显著",
          not eff_mix.get("significant", True), f"p={eff_mix.get('p_value')}")

    # 二项检验本身的算例对账
    p = sc._binom_p_two_sided(3, 8)
    check("二项检验算例对账 (k=3,n=8) p≈0.7266", abs(p - 0.7265625) < 1e-9, f"{p:.7f}")
    check("二项检验算例对账 (k=8,n=8) p≈0.0078",
          abs(sc._binom_p_two_sided(8, 8) - 0.0078125) < 1e-9)


def test_no_streamlit_import() -> None:
    """
    只校验真实的 import 语句行，不做全文字符串匹配——
    文档字符串里写「禁止 import streamlit」会让全文匹配误报。
    """
    import ast

    path = os.path.join(os.path.dirname(__file__), "scorecard.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])

    check("计算层未 import streamlit", "streamlit" not in mods, f"实际依赖 {sorted(mods)}")
    check("计算层未 import tushare/akshare",
          not ({"tushare", "akshare"} & mods), f"实际依赖 {sorted(mods)}")


def test_dtype_safety() -> None:
    """pandas 3.x 铁律：不得出现 replace(0, pd.NA) 这类会把 float64 退化成 object 的写法。"""
    src = open(os.path.join(os.path.dirname(__file__), "scorecard.py"), encoding="utf-8").read()
    check("未使用 replace(0, pd.NA)", "replace(0, pd.NA)" not in src)
    check("astype 使用显式 float64 字符串",
          'astype(float)' not in src)


if __name__ == "__main__":
    print("=" * 60)
    print("战绩回看计算层自检")
    print("=" * 60)
    test_alpha_math()
    test_missing_benchmark()
    test_insufficient_sample()
    test_bucket_monotonic()
    test_beta_estimation()
    test_effective_sample()
    test_no_streamlit_import()
    test_dtype_safety()
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
