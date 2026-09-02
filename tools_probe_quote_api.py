"""行情通道健康自检：逐一验证 page_live_quote 对各市场各周期是否真的取到数据。

用法: /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_quote_api.py
无需 Streamlit runtime —— 通过 stub 掉 st.cache_data 直接调用底层函数。

三类断言，缺一不可：
  A. 撞号消歧：同一个 6 位代码在沪深北同时存在时，必须命中「当日有成交」的那个，
     且候选列表要把另一个也列出来（不能静默丢弃）。
     —— 这是历史 bug 的回归防线：000831 曾被固定解析为沪市指数「500低贝」，
        而用户想看的是深市个股「中国稀土」。
  B. K 线根数下限：北交所日 K 走错端点（老 fqkline）时只返回 1 根，
     「df 非空」这种断言抓不住，必须断言根数 >= _MIN_BARS。
  C. 名称搜索：中文名 / 拼音必须能搜到，且返回的 query 能被 resolve_candidates 查通。
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

# 一根 K 线也算「非空」，所以必须显式设根数下限。北交所走错端点正好返回 1 根。
_MIN_BARS = 20

CASES = [
    ("600519", "A股-茅台", "贵州茅台"),
    ("sh000001", "A股-上证指数", "上证指数"),
    ("513100", "ETF-纳指", None),
    ("bj920002", "北交所-万达轴承", "万达轴承"),
    ("00700", "港股-腾讯", "腾讯控股"),
    ("HSI", "港股-恒生指数", None),
    ("NVDA", "美股-英伟达", None),
    ("IXIC", "美股-纳斯达克指数", None),
    ("GC", "商品-纽约黄金", None),
    ("CL", "商品-纽约原油", None),
]

# 撞号用例: (输入, 期望默认命中的名称, 期望同时出现在候选里的另一个名称)
#
# 口径：裸 6 位数字里
#   · 沪市是「主流宽基指数」(000001/000016/000300/000688/000905…) → 默认给指数
#   · 沪市是无人主动查的衍生指数（500低贝 / 上证周期 / Ａ股指数…）→ 默认给深市个股
# 两种情况下另一个标的都必须出现在候选里，绝不静默丢弃。
COLLISION_CASES = [
    # 沪市为冷门衍生指数 → 应默认给深市个股
    ("000831", "中国稀土", "500低贝"),      # 历史 bug 现场：曾固定给 500低贝
    ("000063", "中兴通讯", "上证周期"),
    ("000002", "万  科Ａ", "Ａ股指数"),
    ("000100", "TCL科技", "上证F500"),
    ("000858", "五 粮 液", "500信息"),
    # 沪市为主流宽基 → 应默认给指数
    ("000001", "上证指数", "平安银行"),
    ("000688", "科创50", "国城矿业"),
    ("000905", "中证500", "厦门港务"),
    ("000016", "上证50", "*ST康佳A"),
]

# 单一市场存在，不应触发消歧
UNIQUE_CASES = [
    ("000651", "格力电器"),
    ("000300", "沪深300"),
    ("600519", "贵州茅台"),
    ("920002", "万达轴承"),
]

# 显式前缀必须绕过消歧，精确命中
EXPLICIT_CASES = [
    ("sh000831", "500低贝"),
    ("sz000831", "中国稀土"),
    ("sh000001", "上证指数"),
    ("sz000001", "平安银行"),
    ("bj920002", "万达轴承"),
]

SEARCH_CASES = [
    ("中国稀土", "sz000831"),
    ("maotai", "sh600519"),
    ("宁德时代", "sz300750"),
    ("英伟达", "NVDA"),
]

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"[FAIL] {msg}")


# ---------- A. 撞号消歧 ----------
print("=" * 68)
print("A. 撞号消歧（沪深北同号）")
print("=" * 68)
for raw, want_first, want_also in COLLISION_CASES:
    cands = q.resolve_candidates(raw)
    if not cands:
        bad(f"{raw}: 候选列表为空")
        continue
    names = [c["quote"]["name"] for c in cands]
    first = names[0]
    ok_first = first == want_first
    ok_also = want_also in names
    if not ok_first:
        bad(f"{raw}: 默认命中 {first}，期望 {want_first}（候选={names}）")
    if not ok_also:
        bad(f"{raw}: 候选里丢了 {want_also}（候选={names}，静默丢弃标的是严重问题）")
    if ok_first and ok_also:
        detail = " | ".join(q.candidate_label(c) for c in cands)
        print(f"  OK   {raw} → {first}   [{detail}]")

print()
for raw, want in EXPLICIT_CASES:
    cands = q.resolve_candidates(raw)
    if len(cands) != 1:
        bad(f"{raw}: 显式前缀应只有 1 个候选，实得 {len(cands)}")
        continue
    got = cands[0]["quote"]["name"]
    if got != want:
        bad(f"{raw}: 显式前缀命中 {got}，期望 {want}")
    else:
        print(f"  OK   {raw} → {got}（显式前缀，未走消歧）")

print()
for raw, want in UNIQUE_CASES:
    cands = q.resolve_candidates(raw)
    if len(cands) != 1:
        names = [c["quote"]["name"] for c in cands]
        bad(f"{raw}: 只有一个市场有此代码，不该出现消歧选项（实得 {names}）")
        continue
    got = cands[0]["quote"]["name"]
    if got != want:
        bad(f"{raw}: 命中 {got}，期望 {want}")
    else:
        print(f"  OK   {raw} → {got}（唯一标的，无需选择）")

# ---------- C. 名称搜索 ----------
print()
print("=" * 68)
print("C. 名称 / 拼音搜索")
print("=" * 68)
for kw, want_query in SEARCH_CASES:
    hits = q.search_symbols(kw)
    if not hits:
        bad(f"搜索 `{kw}`: 无结果（接口可能限流；页面已做降级，但自检仍视为失败）")
        continue
    queries = [h["query"] for h in hits]
    if want_query not in queries:
        bad(f"搜索 `{kw}`: 结果里没有 {want_query}（实得 {queries[:5]}）")
        continue
    # 搜出来的 query 必须真能查通，否则等于给了个死链接
    probe_cands = q.resolve_candidates(want_query)
    if not probe_cands:
        bad(f"搜索 `{kw}` → {want_query}: 该 query 无法取到行情")
        continue
    print(f"  OK   `{kw}` → {want_query} ({probe_cands[0]['quote']['name']})  "
          f"共 {len(hits)} 条: {queries[:4]}")

# ---------- B. 各市场周期通道 ----------
print()
print("=" * 68)
print("B. 各市场报价 + 全周期 K 线（K 线根数须 >= %d）" % _MIN_BARS)
print("=" * 68)
for raw, label, want_name in CASES:
    cands = q.resolve_candidates(raw)
    if not cands:
        bad(f"{label:18s} 代码无法解析或报价为空")
        continue
    sym, quote = cands[0]["sym"], cands[0]["quote"]
    if quote.get("price", 0) <= 0:
        bad(f"{label:18s} 报价为 0")
        continue
    if want_name and quote["name"] != want_name:
        bad(f"{label:18s} 名称对不上: 实得 {quote['name']}，期望 {want_name}")
    print(f"\n=== {label} ({raw}) → {sym['market']} | {quote['name']} "
          f"[{quote.get('kind', '?')}] {quote['price']} {quote['pct_chg']:+.2f}% "
          f"@ {quote['update_time']} alive={quote.get('alive')}")

    for period in q.MARKET_PERIODS[sym["market"]]:
        if period == "分时走势":
            df = q.get_minute_line(sym)
            desc = f"n={len(df)}"
            if not df.empty:
                desc += f" {df['time'].iloc[0]}~{df['time'].iloc[-1]} px={df['price'].iloc[-1]}"
            ok = not df.empty and len(df) >= 2
        else:
            df = q.get_kline(sym, period)
            desc = f"n={len(df)}"
            if not df.empty:
                desc += f" {df['datetime'].iloc[0]}~{df['datetime'].iloc[-1]} close={df['close'].iloc[-1]}"
            # 关键: 不能只判非空 —— 端点选错时会返回恰好 1 根
            ok = len(df) >= _MIN_BARS
            if not df.empty and not ok:
                desc += f"  <<< 只有 {len(df)} 根，疑似端点选错"
        # 失败必须走唯一出口 bad()：一份规则两处实现必漂移 —— 这里曾经自己打
        # "FAIL" 而不带 [FAIL] 前缀，导致反向验证脚本按 [FAIL] 过滤时漏抓
        # 「北交所日K 只有 1 根」，等于 B 段断言在外部工具眼里是隐形的。
        if ok:
            print(f"   OK   {period:8s} {desc}")
        else:
            bad(f"{label:18s} {period:8s} {desc}")

print(f"\n{'=' * 68}\n失败项: {fail}")
sys.exit(1 if fail else 0)
