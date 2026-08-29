"""行情通道健康自检：逐一验证 page_live_quote 对各市场各周期是否真的取到数据。

用法: python tools_probe_quote_api.py
无需 Streamlit runtime —— 通过 stub 掉 st.cache_data 直接调用底层函数。
"""
import sys
import types

# 用轻量 stub 顶掉 streamlit，避免起 runtime
_st = types.ModuleType("streamlit")


def _cache_data(*a, **kw):
    if a and callable(a[0]):
        return a[0]

    def deco(fn):
        return fn
    return deco


_st.cache_data = _cache_data
sys.modules.setdefault("streamlit", _st)

import page_live_quote as q  # noqa: E402

CASES = [
    ("600519", "A股-茅台"),
    ("sh000001", "A股-上证指数"),
    ("513100", "ETF-纳指"),
    ("00700", "港股-腾讯"),
    ("HSI", "港股-恒生指数"),
    ("NVDA", "美股-英伟达"),
    ("IXIC", "美股-纳斯达克指数"),
    ("GC", "商品-纽约黄金"),
    ("CL", "商品-纽约原油"),
]

fail = 0
for raw, label in CASES:
    sym = q.resolve_symbol(raw)
    if not sym:
        print(f"[FAIL] {label:18s} 代码无法解析")
        fail += 1
        continue
    quote = q.get_quote(sym)
    if not quote or quote.get("price", 0) <= 0:
        print(f"[FAIL] {label:18s} 报价为空")
        fail += 1
        continue
    print(f"\n=== {label} ({raw}) → {sym['market']} | {quote['name']} "
          f"{quote['price']} {quote['pct_chg']:+.2f}% @ {quote['update_time']}")

    for period in q.MARKET_PERIODS[sym["market"]]:
        if period == "分时走势":
            df = q.get_minute_line(sym)
            desc = f"n={len(df)}"
            if not df.empty:
                desc += f" {df['time'].iloc[0]}~{df['time'].iloc[-1]} px={df['price'].iloc[-1]}"
        else:
            df = q.get_kline(sym, period)
            desc = f"n={len(df)}"
            if not df.empty:
                desc += f" {df['datetime'].iloc[0]}~{df['datetime'].iloc[-1]} close={df['close'].iloc[-1]}"
        ok = not df.empty and len(df) >= 2
        if not ok:
            fail += 1
        print(f"   {'OK  ' if ok else 'FAIL'} {period:8s} {desc}")

print(f"\n{'=' * 60}\n失败项: {fail}")
sys.exit(1 if fail else 0)
