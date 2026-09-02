# -*- coding: utf-8 -*-
"""黄金分割页无头渲染自检。

这一页历史上自己实现了一套「6 位代码 → 市场前缀」的硬规则（`6/5/9` 开头算沪市，
否则算深市）和一套自己的 K 线取数（`fqkline?limit=1000`），三个坑全踩了：

  · `000300` 被拼成 `sz000300` —— 腾讯返回 0 根，图直接画不出来；
  · `000831` 被无条件当成深市个股，沪市指数「500低贝」永远取不到；
  · `920982`（北交所）两个前缀都是空；
  · 更坏的是**腾讯对错前缀不容错**：`sz000905` 返回的是「厦门港务」而不是中证500，
    图能正常画出来、只是画的是另一只标的 —— 静默取错比取不到贵得多；
  · `limit=1000` 触发 qfq 的静默悬崖，实际只拿 641 根（比传 800 还少），
    2021 年至今该有 1374 根，画出来的图少了一大半却毫无异常迹象。

所以本探针的断言全部围绕「静默错误」设计：不判「图画出来了」，而判**根数**与
**标的名**这两个能把静默错误逼出来的量。
"""
import datetime
import os
import pathlib
import re
import sys
import tempfile

from streamlit.testing.v1 import AppTest

# 反向验证脚本会用 FIB_PROBE_ONLY=2,3 只跑相关用例 —— 每个用例都要真起一次
# Streamlit runtime 并真拉数据，全跑一轮几分钟，造错验证跑四轮就不可接受了。
# 留空 = 全跑。tag 写错会导致「一个用例都不跑」，此时 fail=0，反向脚本判定为
# 「断言没抓住」而报警 —— 失败方向是安全的。
_ONLY = {s.strip() for s in os.environ.get("FIB_PROBE_ONLY", "").split(",") if s.strip()}


def want(tag: str) -> bool:
    return not _ONLY or tag in _ONLY

ROOT = pathlib.Path(__file__).resolve().parent
SCRIPT = pathlib.Path(tempfile.gettempdir()) / "_fib_probe_app.py"
SCRIPT.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from page_fibonacci import render_fibonacci_chart\n"
    "render_fibonacci_chart()\n",
    encoding="utf-8",
)

# 两页同时渲染的壳：用来验 widget key 是否真的按页隔离。
# 同一进程里两页都对 000831 弹撞号 radio，key 没隔离会直接抛
# StreamlitDuplicateElementKey —— 而单独跑任一页都不会暴露这个问题。
BOTH = pathlib.Path(tempfile.gettempdir()) / "_fib_both_probe_app.py"
BOTH.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from page_live_quote import render_live_quote_page\n"
    "from page_fibonacci import render_fibonacci_chart\n"
    "render_live_quote_page()\n"
    "render_fibonacci_chart()\n",
    encoding="utf-8",
)

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"[FAIL] {msg}")


def run(symbol=None, start=None, end=None, search=None, script=None, timeout=180,
        lq_symbol=None):
    at = AppTest.from_file(str(script or SCRIPT), default_timeout=timeout)
    if symbol:
        at.session_state["fib_symbol"] = symbol
    if lq_symbol:
        # 双页壳专用：行情页读的是 active_symbol，不注入它就只会渲染默认的
        # 600519（不撞号、不弹 radio），撞 key 用例会失去区分度。
        at.session_state["active_symbol"] = lq_symbol
    if start:
        at.session_state["fib_start"] = datetime.date.fromisoformat(start)
    if end:
        at.session_state["fib_end"] = datetime.date.fromisoformat(end)
    if search:
        at.session_state["fib_search_kw"] = search
    at.run()
    return at


def texts(at):
    out = []
    for coll in (at.markdown, at.caption, at.warning, at.error, at.info):
        out += [e.value for e in coll]
    return "\n".join(out)


def bars_of(at):
    """从页面 caption 里抠出「共 N 根日 K」。

    刻意不去读 plotly figure：图上有几根 K 线是渲染细节，而 caption 是**页面对
    用户的承诺**。数据少一半时图看不出异常，这个数字是唯一能暴露它的地方。
    """
    m = re.search(r"共 (\d+) 根日 K", texts(at))
    return int(m.group(1)) if m else None


TODAY = datetime.date.today().isoformat()


def latest_trade_date(tx_code, ep="fqkline"):
    """**独立参照通道**：直接打腾讯的多个 limit 分片，取最大日期 = 真实最新交易日。

    刻意**绕开 get_daily_kline_range** —— 那正是被测对象。第一版拿「同一函数的
    短区间」当参照，结果造错去掉补尾后长短区间都丢当天、对比恒等，断言永不失败
    （典型假断言）。参照物必须与被测对象无共同故障模式。

    为什么取 max 可靠：实测同一天里不同 limit 分片的新鲜度不同（跨 6 标的 × 3 个
    start 统计，limit=300 命中当天 18/18、100 是 17/18、800 是 13/18、640 只
    10/18，且同参数连打 4 次结果完全一致 —— 是稳定分片而非网络抖动），
    但**任何分片都只会少给最新一根、从不多给**（6 个标的的 max 全部等于当天）。
    返回 None = 参照通道本身不可用（须与「被测对象错了」区分开报）。
    """
    import page_live_quote as _lq                       # noqa: PLC0415
    s = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
    got = []
    for lim in (300, 100, 700):
        try:
            d = _lq._http_json(
                f"https://web.ifzq.gtimg.cn/appstock/app/{ep}/get"
                f"?param={tx_code},day,{s},{TODAY},{lim},qfq", timeout=15)
        except Exception:                               # noqa: BLE001
            continue
        node = (d.get("data") or {}).get(tx_code) or {}
        arr = node.get("qfqday") or node.get("day") or []
        if arr:
            got.append(str(arr[-1][0]))
    return max(got) if len(got) >= 2 else None

print("=" * 72)
print("黄金分割页无头渲染自检")
print("=" * 72)

# ---- 1. 默认标的：根数必须是完整区间，不能是 limit 悬崖后的 640 根 ----
# 实测 2021-01-01~今 应有 1374 根；老实现单段 limit=1000 只得 640 根。
# 阈值 1370 卡在两者之间（留 4 根余量给未来交易日增长/接口抖动），有真实区分度。
_MIN_5Y_BARS = 1370
last_date = None
if want("bars"):
    at = run("601869", start="2021-01-01")
    if at.exception:
        bad(f"默认标的 601869 渲染抛异常: {at.exception[0].message}")
    else:
        n = bars_of(at)
        if n is None:
            bad(f"601869: 页面没给出「共 N 根日 K」，无法判断数据是否残缺"
                f"（文案={texts(at)[:200]}）")
        elif n < _MIN_5Y_BARS:
            bad(f"601869 2021-01-01~今 只有 {n} 根（应 >= {_MIN_5Y_BARS}）—— "
                f"K 线静默残缺，图能画出来但少了一大半")
        else:
            m = re.search(r"实际区间 (\S+) ~ (\S+)", texts(at))
            last_date = m.group(2) if m else None
            print(f"  OK   601869 共 {n} 根，实际区间 "
                  f"{m.group(1) if m else '?'} ~ {last_date}")
            if m and m.group(1)[:4] != "2021":
                bad(f"601869: 起点是 {m.group(1)}，没回补到 2021 —— "
                    f"分段只补了一段就停，长区间静默从中途开始")

# ---- 2. 长区间不能丢掉最新那一根 ----
# `end` 落在**当天**时，腾讯某些 limit 分片当天还没刷新，末根会停在前一个交易日
# （与是否顶满 limit 无关：66 根的区间在 limit=200/800 下照样丢，而 100/300/400/700
# 都给，同参数连打 4 次稳定复现）。所以这条**必须用 end=今天**才有区分度，
# 写固定的过去日期根本触发不到 —— 我第一版就写错成固定日期。
#
# 参照物用 latest_trade_date()（独立打分片取 max），**不能用同一函数的短区间**：
# 去掉补尾后短区间同样丢当天（实测 66 根末根也是 09-01），两边一起错 → 对比恒等
# → 断言永不失败。这是我第二版真实踩到的假断言，反向验证造错12 才把它逼出来。
if want("tail"):
    ref = latest_trade_date("sh600519")
    at_l = run("600519", start="2015-01-01")       # 长区间，需要分段 + 补尾
    if at_l.exception:
        bad(f"600519 长区间渲染抛异常: {at_l.exception[0].message}")
    elif ref is None:
        # 参照通道自己挂了：此时无法判断被测对象对错，必须说清而不是判 PASS。
        bad("600519: 参照通道（腾讯多分片取 max）不可用，本用例这次什么都没验到 —— "
            "通道恢复后必须重跑，别把它当通过")
    else:
        m_l = re.search(r"实际区间 (\S+) ~ (\S+)", texts(at_l))
        n_l = bars_of(at_l)
        if not m_l:
            bad("600519: 页面没给出实际区间，无法判断末根")
        elif n_l is None or n_l < 2800:
            # 元断言：长区间必须真的长（>800 根才会触发 limit 分段 + 补尾），
            # 否则本用例退化成一个普通短区间，补尾逻辑压根没被执行，失去区分度。
            bad(f"600519 长区间只有 {n_l} 根（11 年应有 2800+），"
                f"没触发分段拉取，本用例失去区分度")
        elif m_l.group(2) != ref:
            bad(f"600519: 长区间末根 {m_l.group(2)}，而最新交易日是 {ref} —— "
                f"最新一天被吞了，图上看不出任何异常")
        elif m_l.group(1)[:4] != "2015":
            bad(f"600519: 长区间起点是 {m_l.group(1)}，没回补到 2015 —— "
                f"分段只补一段就停，长区间静默从中途开始")
        else:
            print(f"  OK   600519 长区间 {n_l} 根 {m_l.group(1)} ~ {m_l.group(2)}，"
                  f"末根 == 最新交易日({ref})")

# ---- 3. 撞号：必须弹选择器且默认命中在交易的那个，绝不静默取错 ----
# 老实现的硬前缀（6/5/9 开头算沪市，否则算深市）在这三条上全错，且**腾讯对错前缀
# 不容错**：sz000905 返回的是「厦门港务」而不是中证500，图照样能画，只是画的是
# 另一只标的 —— 所以断言必须落在**标的名**上，不能只判「有没有画出来」。
COLLIDE = [
    ("000905", "中证500", "厦门港务"),    # 老实现 → sz000905 静默取到厦门港务
    ("000831", "中国稀土", "500低贝"),    # 老实现 → sz000831 恰好对，但沪市指数永远取不到
]
if want("collide"):
    for raw, want_first, want_also in COLLIDE:
        at = run(raw, start="2024-01-01")
        if at.exception:
            bad(f"{raw} 渲染抛异常: {at.exception[0].message}")
            continue
        radios = [r for r in at.radio if "哪一个" in (r.label or "")]
        if not radios:
            bad(f"{raw}: 撞号时没有出现候选选择器 —— 页面在静默替用户决定用哪个市场")
            continue
        opts = list(radios[0].options)
        page = texts(at)
        if want_first not in (opts[0] if opts else ""):
            bad(f"{raw}: 默认候选不是 {want_first}，实为 {opts[:1]}")
        elif not any(want_also in o for o in opts):
            bad(f"{raw}: 候选里丢了 {want_also}（实为 {opts}）—— 静默丢弃标的")
        elif want_first not in page:
            bad(f"{raw}: 选中了 {want_first} 但页面标题/说明里没有它，"
                f"可能画的是另一只标的（文案={page[:160]}）")
        elif bars_of(at) is None:
            bad(f"{raw}: 选了候选却没取到 K 线（文案={page[:160]}）")
        else:
            print(f"  OK   {raw} → 默认 {opts[0]}｜另有 {opts[1:]}，共 {bars_of(at)} 根")

# ---- 4. 老实现拼不出前缀的两类标的 ----
# 000300：老规则算成 sz000300，腾讯直接返回 0 根（图画不出来）。
# 920982：老规则 sh/sz 两个前缀都是空，北交所在这页等于完全不可用。
# 北交所还多一层坑：必须走 newfqkline，老 fqkline 对 bj 代码静默只返回 1 根。
for tag, raw, want_name, min_bars in (("idx", "000300", "沪深300", _MIN_5Y_BARS),
                                      ("bj", "920982", "锦波生物", 600)):
    if not want(tag):
        continue
    at = run(raw, start="2021-01-01" if tag == "idx" else "2024-01-01")
    if at.exception:
        bad(f"{raw} 渲染抛异常: {at.exception[0].message}")
        continue
    n, page = bars_of(at), texts(at)
    if want_name not in page:
        bad(f"{raw}: 页面里找不到 {want_name} —— 前缀拼错取到了别的标的或没取到"
            f"（文案={page[:200]}）")
    elif n is None:
        bad(f"{raw}: 没取到日 K（文案={page[:200]}）")
    elif n < min_bars:
        bad(f"{raw} ({want_name}): 只有 {n} 根，应 >= {min_bars}")
    else:
        print(f"  OK   {raw} → {want_name}，共 {n} 根")

# ---- 5. 本页不支持的市场：必须明确指路，不能画一张错图或干脆空白 ----
if want("mkt"):
    at = run("00700")
    if at.exception:
        bad(f"00700 渲染抛异常: {at.exception[0].message}")
    else:
        page = texts(at)
        if "实时行情" not in page:
            bad(f"00700(港股): 没给出「请到实时行情页查看」的指路文案"
                f"（文案={page[:200]}）")
        elif bars_of(at) is not None:
            bad("00700(港股): 本页竟然画出了 K 线 —— 港股前复权通道与本页口径不一致")
        else:
            print("  OK   00700(港股) 明确提示改用实时行情页，未画图")

# ---- 6. 名称/拼音误打进代码框 —— 与行情页同一个坑，本页也必须原地给候选 ----
# 这条依赖外部搜索接口，实测会偶发瞬时空返回（同一用例紧接着复跑就通）。
# 直接 FAIL 会把「通道抖了一下」误报成「回落逻辑没了」—— 归因错误的报错比未知
# 错误更贵。所以：重试 2 次仍无按钮时，再**带外复核**一次搜索通道本身，
# 按复核结果分别给出两种完全不同的结论。
if want("fallback"):
    import page_live_quote as _lq_probe   # noqa: E402  带外复核用

    for wrong in ("宁德时代", "ndsd"):
        hit, tip, btns = [], [], []
        for _attempt in range(2):
            at = run(wrong)
            if at.exception:
                bad(f"代码框输入 `{wrong}` 渲染抛异常: {at.exception[0].message}")
                hit = None
                break
            btns = [b.label for b in at.button]
            hit = [l for l in btns if "宁德时代" in l and "\n`" in l]
            tip = [i.value for i in at.info if "看着像名称" in i.value]
            if hit:
                break
        if hit is None:
            continue
        if not hit:
            try:
                oob = _lq_probe.search_symbols(wrong)
            except Exception as _e:                     # noqa: BLE001
                oob = f"异常 {type(_e).__name__}"
            if not oob or isinstance(oob, str):
                bad(f"代码框输入 `{wrong}`: 页面无回落按钮，带外复核搜索通道同样"
                    f"取不到结果（{oob!r}）—— 本用例这次验的是通道而非回落逻辑，"
                    f"通道恢复后必须重跑确认")
            else:
                bad(f"代码框输入 `{wrong}`: 搜索通道正常（带外能搜到 "
                    f"{[h['query'] for h in oob][:3]}）却没给出回落按钮 —— "
                    f"回落逻辑失效，用户只看到报错（按钮={btns[:5]}）")
        elif not tip:
            bad(f"代码框输入 `{wrong}`: 出了按钮但没说明原因，用户不知道这排按钮干嘛的")
        elif not any("sz300750" in l for l in hit):
            bad(f"代码框输入 `{wrong}`: 回落结果里没有 sz300750，实为 {hit}")
        else:
            print(f"  OK   代码框输入 `{wrong}` → 原地给出 {len(hit)} 个候选")

# ---- 7. 两页同进程渲染不能撞 widget key ----
# 本页复用行情页的组件，两页都对 000831 弹撞号 radio。key 没按页隔离时
# 会直接抛 StreamlitDuplicateElementKey —— 而单独跑任一页都不会暴露。
if want("both"):
    at = run("000831", lq_symbol="000831", start="2024-01-01",
             script=BOTH, timeout=300)
    if at.exception:
        msg = at.exception[0].message or ""
        if "DuplicateElementKey" in msg or "duplicate" in msg.lower():
            bad(f"两页同渲染撞 widget key：{msg[:200]} —— "
                f"复用组件时必须传 key_prefix / key_tag 隔离")
        else:
            bad(f"两页同渲染抛异常: {msg[:200]}")
    else:
        radios = [r for r in at.radio if "哪一个" in (r.label or "")]
        if len(radios) < 2:
            bad(f"两页同渲染只出现 {len(radios)} 个撞号选择器，应有 2 个"
                f"（少一个说明有一页没渲染，本用例失去区分度）")
        else:
            print(f"  OK   两页同进程渲染各自弹出选择器（{len(radios)} 个），未撞 key")

print(f"\n{'=' * 72}\n失败项: {fail}")
sys.exit(1 if fail else 0)
