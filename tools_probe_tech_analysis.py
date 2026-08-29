"""
自动技术分析自检 —— 不需要 Streamlit runtime，直接跑数据层 + 计算层 + 渲染层。

跑法（必须用装了 pandas/plotly 的 venv，托管主解释器没装）:
  /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_tech_analysis.py

校验口径（任一不满足即计为失败）:
  1. analyze() 对 >= MIN_BARS 根 K 线必须 ok=True
  2. 笔端点必须顶底严格交替
  3. 线段端点数必须 <= 笔端点数（线段是高一级结构，不可能更碎）
  4. 中枢 ZG > ZD，且区间宽度 >= 0.3%，段数 <= 9
  5. 有 targets 时 C 点必须落在 A、B 之间
  6. 关键价位表里的价格全为正、有限
  7. 四种图层组合（无/缠论/波利/两者）渲染均不抛异常
"""
import sys
import types

_st = types.ModuleType("streamlit")


def _cache_data(*a, **kw):
    if a and callable(a[0]):
        return a[0]
    return lambda fn: fn


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_st.cache_data = _cache_data
for _fn in ("plotly_chart", "caption", "markdown", "info", "warning", "success",
            "write", "dataframe", "header", "metric", "error"):
    setattr(_st, _fn, lambda *a, **k: None)
_st.columns = lambda spec, **k: [_Ctx() for _ in (range(spec) if isinstance(spec, int) else spec)]
_st.container = lambda **k: _Ctx()
sys.modules["streamlit"] = _st

import numpy as np                                    # noqa: E402
import page_live_quote as q                           # noqa: E402
import tech_analysis as ta                            # noqa: E402
import tech_overlay as tov                            # noqa: E402

SYMBOLS = ["600519", "sh000001", "513100", "00700", "HSI", "NVDA", "IXIC", "GC", "CL"]
MODE_SETS = ([], [tov._MODE_CHAN], [tov._MODE_DINAPOLI], [tov._MODE_CHAN, tov._MODE_DINAPOLI])

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
    return cond


def probe(raw: str, period: str):
    sym = q.resolve_symbol(raw)
    quote = q.get_quote(sym)
    df = q.get_kline(sym, period)
    head = f"{raw} {period}"
    if df.empty:
        fails.append(f"{head}: K 线为空（行情通道问题，先跑 tools_probe_quote_api.py）")
        return

    res = ta.analyze(df)
    if not check(res.get("ok"), f"{head}: analyze 失败 -> {res.get('reason')}"):
        return

    pts = res["strokes"]
    alt = all(a["type"] != b["type"] for a, b in zip(pts, pts[1:]))
    check(alt, f"{head}: 笔端点顶底未严格交替")
    check(len(res["segments"]) <= len(pts) or len(pts) < 5,
          f"{head}: 线段端点 {len(res['segments'])} > 笔端点 {len(pts)}")

    for pv in res["pivots"]:
        check(pv["zg"] > pv["zd"], f"{head}: 中枢 ZG<=ZD")
        check(ta._pct(pv["zd"], pv["zg"]) >= 0.3 - 1e-9, f"{head}: 中枢区间宽度 < 0.3%")
        check(pv["legs"] <= ta._PIVOT_MAX_LEGS, f"{head}: 中枢段数 {pv['legs']} 超过封顶")

    tg = res.get("targets")
    if tg:
        lo, hi = min(tg["a"], tg["b"]), max(tg["a"], tg["b"])
        check(lo < tg["c"] < hi, f"{head}: ABC 的 C 点未落在 A、B 之间")

    for lv in res["levels"]:
        check(np.isfinite(lv["price"]) and lv["price"] > 0,
              f"{head}: 关键价位异常 {lv['label']}={lv['price']}")

    for modes in MODE_SETS:
        try:
            r = q._render_kline_chart(sym, quote, df, period, modes=modes)
            if modes and r.get("ok"):
                tov.draw_macd(df, r)
                tov.render_conclusion(r, period, modes)
        except Exception as e:
            fails.append(f"{head} modes={modes}: 渲染抛异常 {type(e).__name__}: {e}")

    pv = res["pivots"][-1] if res["pivots"] else None
    if pv:
        state = "延伸中" if pv["alive"] else f"已离开{pv['legs_after']}段"
        pvs = f"枢[{ta._n(pv['zd'])}~{ta._n(pv['zg'])}] {pv['legs']}段{state}"
    else:
        pvs = "无中枢"
    print(f"   OK   {period:6s} n={len(df):3d} 笔={len(pts):2d} 段={len(res['segments']):2d} "
          f"{pvs} 汇聚={len(res['confluence'])} 背离={len(res['divergence'])}")


def main():
    for raw in SYMBOLS:
        sym = q.resolve_symbol(raw)
        if not sym:
            fails.append(f"{raw}: resolve_symbol 返回空")
            continue
        print(f"\n=== {raw} → {sym['market']}")
        for period in q.MARKET_PERIODS[sym["market"]]:
            if period == "分时走势":
                continue                    # 分时不做结构分析
            probe(raw, period)

    print("\n" + "=" * 60)
    for f in fails:
        print("  FAIL", f)
    print(f"失败项: {len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
