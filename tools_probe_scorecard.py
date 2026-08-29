"""
战绩回看计算层自检（不依赖 tushare / 网络 / 真实归档）

校验口径：
1. compute_returns 的 alpha 必须等于 自身收益 - 基准同期收益（手工算例对账）
2. 未来交易日不足时对应 horizon 必须留空（NaN），禁止外推
3. 基准缺失时 summarize 必须返回 status=failed，而不是给出一个漂亮数字
4. 样本 < MIN_SAMPLE 时必须标 insufficient，并带上「基本是噪音」的说明
5. 分档区分度检验：构造单调数据必须判 monotonic=True，构造反序必须判 False
6. 计算层不得 import streamlit（托管环境下要能脱离 runtime 单测）
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
    test_no_streamlit_import()
    test_dtype_safety()
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
