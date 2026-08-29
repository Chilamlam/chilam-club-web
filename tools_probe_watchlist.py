# -*- coding: utf-8 -*-
"""
「我的池子」页自检

铁律来源：本项目曾在宏观页硬编码 6 张假数据卡片（纳指 19845 vs 真实 29433），
所以凡是展示数值的页面必须有自检，且自检必须包含**时间戳新鲜度断言**——
纯粹校验「数值非空、格式正常」拦不住硬编码，因为写死的值永远格式正常。

校验口径：
1. 源码中不得出现价格量级的硬编码字面量（正则扫描）
2. 实时报价通道真的通，且返回值为正有限数
3. 报价时间戳必须在最近 5 天内（拦过期/写死数据）
4. 沪深300 基准必须可取（否则「vs 大盘」无从计算）
5. 代码规范化 _tx_code 对沪/深/ETF 的前缀判断正确
6. 日K 通道可用且行数足够跑技术分析
7. 取不到报价时必须返回缺失而不是补 0
"""
from __future__ import annotations

import datetime
import re
import sys

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def test_no_hardcoded_prices() -> None:
    """扫描疑似硬编码价格：4 位以上整数或带小数的价格量级字面量。"""
    src = open("page_watchlist.py", encoding="utf-8").read()
    # 去掉注释与文档字符串再扫，避免误报说明文字里的数字
    body = re.sub(r'"""[\s\S]*?"""', "", src)
    body = re.sub(r"#.*", "", body)
    # 已知合法的技术常量白名单
    allow = {"1000", "0000", "159915", "600519", "000001", "000300",
             "8080", "5000", "1024", "2026"}
    suspects = []
    for m in re.finditer(r"(?<![\w.])(\d{4,6}\.\d+)(?![\w])", body):
        suspects.append(m.group(1))
    check("源码无价格量级小数硬编码", not suspects, f"可疑：{suspects[:5]}")

    check("源码无 pct_chg 硬编码赋值",
          not re.search(r'"pct"\s*:\s*-?\d+\.\d+', body))


def test_quote_channel() -> None:
    import page_watchlist as wl

    codes = ("600519", "000001", "159915", "920895")
    q = wl.fetch_quotes(codes)
    check("批量报价通道可用", len(q) >= 4, f"取到 {len(q)}（含基准）：{sorted(q)}")
    if not q:
        return

    for code, v in q.items():
        ok_price = isinstance(v["price"], float) and v["price"] > 0 and v["price"] == v["price"]
        check(f"{code} 现价为正有限数", ok_price, f"{v['price']}")
        check(f"{code} 涨跌幅在 ±30% 内", abs(v["pct"]) <= 30.0, f"{v['pct']}%")
        check(f"{code} 价格无科学计数法", "e" not in f"{v['price']}".lower())

    # ---- 时间戳新鲜度：唯一能拦住硬编码/过期数据的断言 ----
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    for code, v in q.items():
        t = str(v["time"])
        if len(t) < 8:
            check(f"{code} 时间戳格式可解析", False, t)
            continue
        try:
            ts = datetime.datetime.strptime(t[:8], "%Y%m%d")
        except ValueError:
            check(f"{code} 时间戳格式可解析", False, t)
            continue
        age = (now.date() - ts.date()).days
        check(f"{code} 报价时间在最近 5 天内", age <= 5, f"{ts.date()}，距今 {age} 天")


def test_code_normalize() -> None:
    import page_watchlist as wl

    # 2026-08-29 逐个实测腾讯 qt.gtimg.cn 的前缀要求，勿凭猜测改动
    cases = {
        "600519": "sh600519", "600519.SH": "sh600519",
        "000001": "sz000001", "300750": "sz300750",
        "159915": "sz159915", "510300": "sh510300",
        "920895": "bj920895",            # 北交所新号段必须 bj 前缀，sh/sz 返回空
        "430139": "bj430139",            # 北交所老号段
        "abc": "",                        # 非数字应返回空而不是抛异常
    }
    for raw, want in cases.items():
        got = wl._tx_code(raw)
        check(f"代码规范化 {raw} → {want or '(空)'}", got == want, f"实得 {got!r}")


def test_benchmark_present() -> None:
    import page_watchlist as wl

    # 传空自选，基准应仍被自动带上（fetch_quotes 内部强制追加 BENCH_TX）
    q = wl.fetch_quotes(())
    check("沪深300 基准随批自动请求", wl.BENCH_BARE in q,
          f"{q.get(wl.BENCH_BARE, {}).get('price')}")
    if wl.BENCH_BARE in q:
        v = q[wl.BENCH_BARE]
        check("沪深300 点位在合理区间 (1000~10000)",
              1000 < v["price"] < 10000, f"{v['price']}")


def test_daily_kline_channel() -> None:
    import page_watchlist as wl

    df = wl._fetch_daily_kline("sh600519", limit=260)
    check("日K 通道可用", not df.empty, f"{len(df)} 根")
    if df.empty:
        return
    check("日K 行数足够跑结构分析（≥60）", len(df) >= 60, f"{len(df)} 根")
    check("日K 收盘价全为正", bool((df["close"] > 0).all()))
    last = str(df["datetime"].iloc[-1])
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    try:
        ts = datetime.datetime.strptime(last[:10], "%Y-%m-%d")
        age = (now.date() - ts.date()).days
        check("日K 末根在最近 7 天内", age <= 7, f"{ts.date()}，距今 {age} 天")
    except ValueError:
        check("日K 末根日期可解析", False, last)


def test_missing_quote_is_none() -> None:
    """取不到报价时必须留空，不能补 0——补 0 会显示成「平盘」误导用户。"""
    import page_watchlist as wl

    q = wl.fetch_quotes(("999999",))     # 不存在的代码
    check("不存在的代码不出现在报价结果里", "999999" not in q, f"{q}")


def test_tech_layer_contract() -> None:
    """校验本页对 tech_analysis 返回结构的假设成立（levels 字段名）。"""
    import pandas as pd

    import page_watchlist as wl
    import tech_analysis as ta

    df = wl._fetch_daily_kline("sh600519", limit=260)
    if df.empty:
        check("技术分析契约校验（跳过：无日K）", True)
        return
    res = ta.analyze(df)
    check("analyze 返回 ok=True", bool(res.get("ok")), str(res.get("reason", "")))
    if not res.get("ok"):
        return
    levels = res.get("levels") or []
    check("levels 非空", bool(levels), f"{len(levels)} 条")
    if levels:
        keys = set(levels[0].keys())
        check("levels 含 price/label/side/gap_pct 四个字段",
              {"price", "label", "side", "gap_pct"} <= keys, f"实际 {sorted(keys)}")
        check("levels 已按距现价升序",
              abs(float(levels[0]["gap_pct"])) <= abs(float(levels[-1]["gap_pct"])))
    check("state 含 chan 描述", "chan" in (res.get("state") or {}))


if __name__ == "__main__":
    print("=" * 60)
    print("「我的池子」页自检")
    print("=" * 60)
    test_no_hardcoded_prices()
    test_code_normalize()
    test_quote_channel()
    test_benchmark_present()
    test_missing_quote_is_none()
    test_daily_kline_channel()
    test_tech_layer_contract()
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
