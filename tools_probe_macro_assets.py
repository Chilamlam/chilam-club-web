"""全球核心资产实时报价自检（page_macro_erp.py 的 get_global_assets 通道）。

校验口径：
  1. 每个资产都能取到 price 且为正有限数
  2. pct 必须在 [-30%, 30%] 之内（超出说明字段取错或昨收错位）
  3. 时间戳必须是今天或最近 5 天内（防止把过期数据当实时）
  4. 格式化后的字符串不能出现科学计数法
  5. 页面里不允许残留硬编码价格字面量

用法（须用装了 pandas 的 venv）：
  /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_macro_assets.py
"""
import re
import sys
import types
from datetime import datetime, timedelta


def _stub_streamlit():
    st = types.ModuleType("streamlit")

    def cache_data(*a, **k):
        if a and callable(a[0]):
            fn = a[0]
            fn.clear = lambda: None
            return fn

        def deco(fn):
            fn.clear = lambda: None
            return fn
        return deco

    st.cache_data = cache_data
    st.session_state = {}
    for name in ("markdown", "caption", "subheader", "warning", "info", "error",
                 "divider", "write", "title", "rerun", "plotly_chart", "dataframe"):
        setattr(st, name, lambda *a, **k: None)
    st.button = lambda *a, **k: False
    st.columns = lambda n, **k: [_Ctx() for _ in (range(n) if isinstance(n, int) else n)]
    st.container = lambda **k: _Ctx()
    st.tabs = lambda labels: [_Ctx() for _ in labels]
    st.radio = lambda label, options, **k: options[0]
    st.selectbox = lambda label, options, **k: options[0]
    st.metric = lambda *a, **k: None
    sys.modules["streamlit"] = st


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return lambda *a, **k: None


_stub_streamlit()
import page_macro_erp as mac  # noqa: E402

fails = []
print("=" * 72)
print("全球核心资产实时报价自检")
print("=" * 72)

data = mac.get_global_assets()
today = datetime.now().date()

for key, src, code, disp, kind, tag, desc in mac.GLOBAL_ASSETS:
    d = data.get(key) or {}
    price, pct, ts = d.get("price"), d.get("pct"), d.get("ts", "")
    txt = mac._fmt_asset_price(price, kind)
    bad = []

    if price is None or not (price > 0):
        bad.append("price 缺失或非正")
    if pct is None:
        bad.append("pct 缺失")
    elif abs(pct) > 30:
        bad.append(f"pct 异常 {pct:.2f}%")
    if "e+" in txt.lower():
        bad.append("科学计数法")

    # 时间戳格式不统一：腾讯美股 "2026-08-28 17:15:59"，腾讯港股 "2026/08/28 18:31:31"，
    # 新浪 "2026-08-29 04:59:58"。两种分隔符都要认。
    m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", ts or "")
    if m:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        if (today - dt) > timedelta(days=5):
            bad.append(f"行情过期 {dt}")
    else:
        bad.append("无有效时间戳")

    status = "OK  " if not bad else "FAIL"
    if bad:
        fails.append(f"{disp}: {'; '.join(bad)}")
    pct_s = "—" if pct is None else f"{pct:+.2f}%"
    print(f" {status} {disp:26s} {txt:>16s} {pct_s:>9s}  src={src}:{code:12s} ts={ts}")

print()
print("-" * 72)
print("硬编码价格残留扫描")
src_txt = open("page_macro_erp.py", encoding="utf-8").read()
banned = ["19,845", "5,620.8", "2,512.4", "12,180", "$75.8"]
hit = [b for b in banned if b in src_txt and "曾经写死过" not in src_txt.split(b)[0][-80:]]
for b in banned:
    for i, line in enumerate(src_txt.splitlines(), 1):
        if b in line and not line.strip().startswith("#"):
            fails.append(f"page_macro_erp.py:{i} 残留硬编码 {b}")
            print(f" FAIL line {i}: {line.strip()[:90]}")
if not any("残留硬编码" in f for f in fails):
    print(" OK   无硬编码价格残留（注释里的历史说明不计）")

print()
print("-" * 72)
print("铜金比可算性")
cu = (data.get("cu") or {}).get("price")
au = (data.get("xau") or {}).get("price")
if cu and au:
    print(f" OK   铜金比 = {cu / au:.3f}  (cu={cu} au={au})")
else:
    fails.append("铜金比无法计算")
    print(" FAIL 铜金比所需报价缺失")

print()
print("=" * 72)
print(f"失败项: {len(fails)}")
for f in fails:
    print("  -", f)
sys.exit(1 if fails else 0)
