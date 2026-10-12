# -*- coding: utf-8 -*-
"""板块轮动展示页无头渲染自检（AppTest，纯离线，不联网）。

为什么计算层的 99 项断言全绿时仍然要给「页面」单独写一份
--------------------------------------------------------
2026-10-12 用户报的问题正是这一类：**数据日期停在 2026-09-18**。
计算层的数字全对（那一份 analysis.json 自洽、无缺列），但页面把一份
「上一份数据是 24 天前」的产物渲染成了看起来完全正常的一页——判读标签、
重合度 KPI、Top10 明细全都在，读者只会以为「今天行情就这样」。

所以本探针只验**页面对用户的承诺**，三条主线：

 · **缺口必须自曝**：归档不连续时，页面必须如实说出「相隔 N 天、未计算重合度」，
   而不是默默把 24 天前的榜单当上一交易日。还要**反向**确认：缺口场景里
   不能出现「与上一交易日重合」这类措辞（否则等于把空档伪装成日间轮动）。
 · **watch=None 不能崩**：「样本不连续」分支的 watch 是 None，页面若直接
   `st.caption(f"...{watch}")` 就会渲染出「None」甚至抛错。渲染一遍才算验过。
 · **阈值不许硬编码**：页面上的「相邻交易日判据 12 天」必须取自
   `sr.MAX_PREV_GAP_DAYS`。测法是**改这个常量再渲染**，看页面数字跟不跟着变
   ——写死 12 的实现会照样全绿到这一条才露馅。

fixture 的来源刻意不是手编的 dict：**先用真实计算层 `compute_analysis()`
算出产物**，再喂给页面。这样验的是「跑批写 CSV → 计算层 → 页面」这条真链路上
的渲染，而不是我脑子里以为的计算层输出长什么样。手编 fixture 最危险的地方在于
它会跟着我的误解一起错——页面和测试同时错，全绿但线上照旧。
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import sys
import tempfile

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import sector_rotation as sr  # noqa: E402

TMP = pathlib.Path(tempfile.gettempdir())
FIXTURE = TMP / "_srp_probe_fixture.json"
SCRIPT = TMP / "_srp_probe_app.py"
HIST_CSV = TMP / "_srp_probe_history.csv"

# 被渲染脚本：把产物注入进程，**页面本体一行都不改**。
# 用替换模块属性的方式而不是改页面源码——改源码验的就不是线上那份代码了。
SCRIPT.write_text(
    "import json, os, sys, pathlib\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "import page_sector_rotation as p\n"
    "fx = json.load(open(os.environ['SRP_PROBE_FIXTURE'], encoding='utf-8'))\n"
    "p.load_analysis = lambda: fx['analysis']\n"
    "p.load_rank_matrix = lambda: fx.get('matrix')\n"
    "p.load_history_tail = lambda: None\n"
    "if fx.get('max_gap') is not None:\n"
    "    p.sr.MAX_PREV_GAP_DAYS = fx['max_gap']\n"
    "p.render_sector_rotation_page()\n",
    encoding="utf-8",
)

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


# ============================ 归档 fixture ============================
# 合成归档：先落成 CSV 再用真实 sr.load_history() 读回来，走的是跑批那条读路径
# （列名、dtype 转换、排序都跟线上一致），不是凭空造 DataFrame。

STYLE_BOARD = "昨日涨停"  # 属性型板块，计算层应剔除；页面也不许出现


def _write_history(dates: list[str], n_boards: int = 24,
                   style_pct_10d: float | None = None) -> pd.DataFrame:
    rows = []
    for i, d in enumerate(dates):
        for b in range(n_boards):
            rows.append({
                "date": d, "code": f"BK{b:04d}", "name": f"板块{b:02d}",
                "close": 1000.0 + i, "pct_chg": round(0.5 + (i % 4) * 0.1, 2),
                "pct_5d": 1.0, "pct_10d": float(30 - b) + (i % 3),
                "pct_20d": 2.0, "pct_60d": 3.0, "turnover": 1.0,
                "up_count": 10.0, "down_count": 10.0, "amount_yi": 50.0,
            })
        if style_pct_10d is not None:
            # 属性型板块故意给最高涨幅：它一旦漏过过滤就会顶到 Top10 第一名，
            # 在页面上极显眼 —— 这样「页面不显示它」才有区分度。
            rows.append({
                "date": d, "code": "BK9999", "name": STYLE_BOARD,
                "close": 9999.0, "pct_chg": 9.99, "pct_5d": 99.0,
                "pct_10d": style_pct_10d, "pct_20d": 99.0, "pct_60d": 99.0,
                "turnover": 1.0, "up_count": 50.0, "down_count": 1.0,
                "amount_yi": 999.0,
            })
    pd.DataFrame(rows).to_csv(HIST_CSV, index=False, encoding="utf-8")
    return sr.load_history(str(HIST_CSV))


def _consecutive(end_day: int, n: int, month: str = "09") -> list[str]:
    return [f"2026-{month}-{d:02d}" for d in range(end_day - n + 1, end_day + 1)]


# 相邻场景：9/07~9/18 共 12 个连续自然日（差 1 天 ≤ NORMAL_GAP_DAYS）
DATES_ADJ = _consecutive(18, 12)
# 缺口场景：12 个连续日 + 一个月后的 10/12（相差 24 天 > 12）—— 正是用户看到的那份
DATES_GAP = DATES_ADJ + ["2026-10-12"]
# 长缺口场景：用于「阈值取自常量」用例（相差 50 天，改阈值后仍属缺口）
DATES_GAP_LONG = _consecutive(23, 12, month="08") + ["2026-10-12"]


def _analysis(dates: list[str], **kw) -> dict:
    hist = _write_history(dates, **kw)
    return sr.compute_analysis(hist)


# ============================ 渲染与取值 ============================

_ORIG_MAX_GAP = sr.MAX_PREV_GAP_DAYS


def render(analysis: dict, matrix=None, max_gap=None, timeout=180):
    FIXTURE.write_text(json.dumps(
        {"analysis": analysis, "matrix": matrix, "max_gap": max_gap},
        ensure_ascii=False), encoding="utf-8")
    os.environ["SRP_PROBE_FIXTURE"] = str(FIXTURE)
    at = AppTest.from_file(str(SCRIPT), default_timeout=timeout)
    try:
        at.run()
    finally:
        # AppTest 是在**同一进程**里 exec 页面脚本的，所以页面里那句
        # `p.sr.MAX_PREV_GAP_DAYS = fx['max_gap']` 改的就是**本探针自己的 sr 模块**。
        # 不还回去就污染后面所有用例 —— 实测：阈值被放宽到 37 之后，相隔 24 天的
        # 缺口场景被算成「相邻」，把两条元断言弄成假失败（2026-10-12 踩到）。
        if max_gap is not None:
            sr.MAX_PREV_GAP_DAYS = _ORIG_MAX_GAP
    return at


def texts(at) -> str:
    """页面所有可见文本（含 expander 内部；AppTest 不折叠，这点与浏览器不同）。"""
    out = []
    for coll in (at.markdown, at.caption, at.warning, at.error, at.info,
                 at.success, at.subheader, at.header):
        out += [str(e.value) for e in coll]
    return "\n".join(out)


def metric_map(at) -> dict:
    return {str(m.label): str(m.value) for m in at.metric}


def captions(at) -> str:
    """页面自己的 caption 文本（不含 markdown / warning）。

    为什么要把 caption 单独拎出来：计算层的缺口理由走 st.markdown，页面自己那句
    「归档在此前缺了交易日…相隔 24 天…」走 st.caption。两者都含「相隔 24 天」，
    所以拿整页文本去 `in` 判「页面说清了相隔天数」时，**判读理由行会把断言喂饱**
    ——页面那句说明被整段删掉也照样绿。这是同源假断言，必须按元素类型分开判。
    """
    return "\n".join(str(c.value) for c in at.caption)


def metric_help(at, label: str) -> str:
    for m in at.metric:
        if str(m.label) == label:
            return str(getattr(m, "help", "") or "")
    return "<无此 KPI>"


def table_md(at, i: int = 0):
    if len(at.dataframe) <= i:
        return None
    v = at.dataframe[i].value
    if hasattr(v, "data"):
        v = v.data
    try:
        return v.to_csv(index=False)
    except Exception:                                   # noqa: BLE001
        return str(v)


def iframe_html(at) -> str:
    """解出 st.iframe 内联 HTML（排名矩阵走 ui_compat.html_embed 的 data-URI）。

    矩阵内容**不在**页面文本里：html_embed 把整张表 base64 塞进 iframe 的 src，
    所以 `texts()` 抓不到它（这也正是浏览器里 `body.innerText` 抓不到 canvas 表格
    的同一类坑）。取值办法是把 src 的 base64 解回来 —— 拿到的就是页面真正
    交给浏览器的那段 HTML。
    """
    try:
        els = at.get("iframe")
    except Exception:                                   # noqa: BLE001
        return ""
    for e in els:
        src = getattr(e, "src", None)
        if not src and hasattr(e, "proto"):
            src = getattr(e.proto, "src", None)
        if src and "base64," in str(src):
            try:
                return base64.b64decode(str(src).split("base64,", 1)[1]).decode("utf-8")
            except Exception:                           # noqa: BLE001
                return ""
    return ""


GAP_CAPTION_MARK = "归档在此前缺了交易日"
NO_ADJ_WORDING = "与上一交易日重合"   # 只在真正相邻时才能出现
NO_DETAIL_TEXT = "今日无上榜板块数据"


# ============================ 1. 相邻：正常路径 ============================

def test_adjacent_normal() -> None:
    an = _analysis(DATES_ADJ, style_pct_10d=999.0)
    # 元断言：先确认计算层确实产出了「相邻」场景，否则本例没有区分度
    check("元断言：合成归档被判为相邻（prev_is_adjacent=True）",
          an["prev_is_adjacent"] is True and an["prev_gap_days"] == 1,
          f"is_adjacent={an['prev_is_adjacent']} gap={an['prev_gap_days']}")
    check("元断言：相邻场景确实算出了重合度",
          isinstance(an["overlap_count"], int), str(an["overlap_count"]))

    at = render(an)
    if at.exception:
        check("相邻场景渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("相邻场景渲染不抛异常", True)
    tx = texts(at)

    check("KPI 数据日期 == 归档末日", metric_map(at).get("数据日期") == DATES_ADJ[-1],
          str(metric_map(at).get("数据日期")))
    check("KPI 重合度与计算层一致",
          metric_map(at).get("Top10 榜单重合度")
          == f"{an['overlap_count']}/{an['overlap_denominator']}",
          str(metric_map(at).get("Top10 榜单重合度")))
    check("相邻时重合度 help 讲的是「与上一交易日」",
          "与上一交易日" in metric_help(at, "Top10 榜单重合度"),
          metric_help(at, "Top10 榜单重合度"))
    check("相邻时**不**出现缺口说明", GAP_CAPTION_MARK not in tx)
    check("相邻时判读不是「样本不连续」", "样本不连续" not in tx)
    check("相邻时判读理由是「与上一交易日重合 x/10」",
          "与上一交易日（2026-09-17）重合" in tx, tx[:0])
    check("窗口涨幅中位数 KPI 有值",
          metric_map(at).get("Top10 10日涨幅中位数", "—") != "—",
          str(metric_map(at).get("Top10 10日涨幅中位数")))

    csv = table_md(at)
    check("Top10 明细表渲染出来了", csv is not None)
    if csv:
        check("明细表 10 行", len(csv.strip().splitlines()) - 1 == 10,
              f"{len(csv.strip().splitlines()) - 1} 行")
        check("属性型板块「昨日涨停」没有出现在明细表里",
              STYLE_BOARD not in csv, csv[:120])
        check("明细表板块名与计算层一致",
              all(str(r["name"]) in csv for r in an["top"]),
              "")


# ============================ 2. 缺口：用户报的那个场景 ============================

def test_gap_scenario() -> None:
    """归档缺了交易日 → 页面必须自曝，且不许把空档当「上一交易日」。"""
    an = _analysis(DATES_GAP, style_pct_10d=999.0)
    check("元断言：合成归档确实造成缺口（label=样本不连续）",
          an["verdict"]["label"] == "样本不连续"
          and an["prev_is_adjacent"] is False
          and an["overlap_count"] is None,
          f"label={an['verdict']['label']} adj={an['prev_is_adjacent']} "
          f"overlap={an['overlap_count']}")
    check("元断言：缺口场景 watch 为 None（这正是页面崩溃风险点）",
          an["verdict"].get("watch") is None, str(an["verdict"].get("watch")))

    at = render(an)
    if at.exception:
        check("缺口场景渲染不抛异常（watch=None 不崩）", False,
              str(at.exception[0].message)[:200])
        return
    check("缺口场景渲染不抛异常（watch=None 不崩）", True)
    tx = texts(at)

    # ---- 必须自曝（这几条只认页面自己的 caption，理由行喂不饱它们）----
    cap = captions(at)
    check("缺口说明出现", GAP_CAPTION_MARK in cap, "")
    check("缺口说明带出相隔天数 24", "相隔 24 天" in cap, "")
    check("缺口说明带出上一份数据日期", "2026-09-18" in cap)
    check("缺口说明带出判据阈值",
          f"相邻交易日判据 {sr.MAX_PREV_GAP_DAYS} 天" in cap)
    check("缺口说明点明「不计算榜单重合度」", "不计算榜单重合度" in cap)
    check("缺口说明点明「不给明日观察条件」", "明日观察" in cap)
    check("判读标签渲染为「样本不连续」", "样本不连续" in texts(at))
    check("判读用 warning 呈现（需人处理的异常，不是等着就好的 info）",
          any("样本不连续" in str(w.value) for w in at.warning),
          str([str(w.value) for w in at.warning]))
    check("计算层的缺口理由（带相隔天数）原样上屏",
          an["verdict"]["reasons"][0] in tx, "")

    # ---- 反向观察：不许伪装成日间轮动 ----
    check("缺口时不出现「与上一交易日重合」措辞（否则等于把空档当相邻）",
          NO_ADJ_WORDING not in tx, "")
    check("缺口时不给「明日观察」条件",
          "🔭 明日观察" not in tx and "明日重合" not in tx, "")
    check("重合度 KPI 显示 —（不是 0/10，也不是空白）",
          metric_map(at).get("Top10 榜单重合度") == "—",
          str(metric_map(at).get("Top10 榜单重合度")))
    check("重合度 KPI 的 help 讲明「未计算重合度」",
          "未计算重合度" in metric_help(at, "Top10 榜单重合度"),
          metric_help(at, "Top10 榜单重合度"))

    # ---- 缺口只影响重合度，明细不许跟着消失 ----
    check("数据日期 KPI 仍是最新归档日",
          metric_map(at).get("数据日期") == "2026-10-12",
          str(metric_map(at).get("数据日期")))
    csv = table_md(at)
    check("缺口时 Top10 明细表照常渲染", csv is not None)
    if csv:
        check("缺口时明细表仍是 10 行",
              len(csv.strip().splitlines()) - 1 == 10,
              f"{len(csv.strip().splitlines()) - 1} 行")
        check("缺口时明细表仍剔除属性型板块", STYLE_BOARD not in csv)
    check("缺口时中位数 KPI 仍有值",
          metric_map(at).get("Top10 10日涨幅中位数", "—") != "—",
          str(metric_map(at).get("Top10 10日涨幅中位数")))


# ============================ 3. 缺口 + 日期无法解析 ============================

def test_gap_unparsable_prev_date() -> None:
    """日期坏了也是「不可比」，但不能打出「相隔 None 天」这种无意义的话。"""
    dates = _consecutive(18, 12) + ["2026/10/12"]      # 斜杠日期解析不了
    an = _analysis(dates)
    check("元断言：坏日期确实被判为不可比",
          an["prev_is_adjacent"] is False and an["prev_gap_days"] is None,
          f"adj={an['prev_is_adjacent']} gap={an['prev_gap_days']}")

    at = render(an)
    if at.exception:
        check("坏日期场景渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("坏日期场景渲染不抛异常", True)
    tx = texts(at)
    cap = captions(at)
    check("坏日期说明点明「无法判断相隔多久」",
          "无法判断相隔多久" in cap or "日期无法解析" in cap, cap[:160])
    check("坏日期**不**打出「相隔 None 天」", "None 天" not in tx, "")
    check("坏日期仍显示 — 而非 0/10",
          metric_map(at).get("Top10 榜单重合度") == "—",
          str(metric_map(at).get("Top10 榜单重合度")))
    check("坏日期也走「样本不连续」判读", "样本不连续" in tx)


# ============================ 4. 缺口 + 明细为空 ============================

def test_gap_with_empty_detail() -> None:
    """缺口说明必须在「明细为空」的提前 return 之前 —— 否则两个异常互相吞掉。"""
    an = _analysis(DATES_GAP)
    an = dict(an)
    an["top"] = []                                     # 模拟当日无上榜板块
    at = render(an)
    if at.exception:
        check("缺口+明细为空渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("缺口+明细为空渲染不抛异常", True)
    tx = texts(at)
    check("元断言：确实走到了「无上榜板块」分支", NO_DETAIL_TEXT in tx, tx[:160])
    check("缺口说明没有被「无上榜板块」的提前 return 吞掉",
          GAP_CAPTION_MARK in captions(at), "")


# ============================ 5. 阈值必须取自常量 ============================

def test_gap_threshold_from_constant() -> None:
    """把 sr.MAX_PREV_GAP_DAYS 改成特异值再渲染：写死 12 的实现会在这里露馅。"""
    an = _analysis(DATES_GAP_LONG)
    check("元断言：长缺口场景 gap 确实大于两个阈值",
          an["prev_gap_days"] > 37 and an["prev_is_adjacent"] is False,
          f"gap={an['prev_gap_days']}")

    at = render(an, max_gap=37)
    if at.exception:
        check("自定义阈值下渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("自定义阈值下渲染不抛异常", True)
    tx = texts(at)
    cap = captions(at)
    check("页面判据阈值跟着常量变成 37",
          "相邻交易日判据 37 天" in cap, cap[:160])
    check("页面**没有**写死 12（反向观察）",
          "相邻交易日判据 12 天" not in tx, "")


# ============================ 6. 旧产物向后兼容 ============================

def test_legacy_product_backward_compat() -> None:
    """线上此刻那份 analysis.json 是旧代码产出的：没有 prev_* 三个新键。

    新页面读到旧产物**不能**误报「缺口」——那会把一份完全正常的数据说成坏的。
    """
    an = _analysis(DATES_ADJ)
    legacy = {k: v for k, v in an.items()
              if k not in ("prev_is_adjacent", "prev_gap_days", "prev_label")}
    check("元断言：构造的旧产物确实不含三个新键",
          not {"prev_is_adjacent", "prev_gap_days", "prev_label"} & set(legacy),
          str(sorted(legacy))[:120])

    at = render(legacy)
    if at.exception:
        check("旧产物渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("旧产物渲染不抛异常", True)
    tx = texts(at)
    check("旧产物不误报缺口说明", GAP_CAPTION_MARK not in tx, "")
    check("旧产物不误报「样本不连续」", "样本不连续" not in tx, "")
    check("旧产物重合度照常显示",
          metric_map(at).get("Top10 榜单重合度")
          == f"{legacy['overlap_count']}/{legacy['overlap_denominator']}",
          str(metric_map(at).get("Top10 榜单重合度")))
    check("旧产物重合度 help 不误报「未计算」",
          "未计算重合度" not in metric_help(at, "Top10 榜单重合度"),
          metric_help(at, "Top10 榜单重合度"))


# ============================ 7. 排名矩阵在缺口时照常 ============================

def test_rank_matrix_in_gap() -> None:
    hist = _write_history(DATES_GAP)
    matrix = sr.build_rank_matrix(hist)
    check("元断言：矩阵由真实计算层产出且为 ok",
          matrix.get("status") == "ok" and len(matrix.get("dates") or []) == 10,
          str(matrix.get("status")))
    check("元断言：矩阵最新一列就是归档末日（含缺口那一端）",
          (matrix.get("dates") or [None])[0] == "2026-10-12",
          str((matrix.get("dates") or [None])[0]))

    at = render(sr.compute_analysis(hist), matrix=matrix)
    if at.exception:
        check("缺口+矩阵渲染不抛异常", False, str(at.exception[0].message)[:200])
        return
    check("缺口+矩阵渲染不抛异常", True)
    tx = texts(at)
    check("矩阵小节渲染出来了", "每日 Top10 排名矩阵" in tx)
    check("矩阵可用时不显示「暂不可用」", "排名矩阵暂不可用" not in tx)
    check("归档够 10 天时不再显示积累进度",
          "/10 个交易日" not in tx, "")

    html = iframe_html(at)
    # 元断言：先确认 iframe 里真有内容，否则下面的格值断言会空洞通过
    check("元断言：iframe 里解出了矩阵 HTML", len(html) > 500, f"{len(html)} 字符")
    if len(html) > 500:
        # 页面的列头写法是 d[5:] 后把 - 换成 /：2026-10-12 → 10/12
        check("矩阵列头用的是省去年份的 M/D 记法",
              ">10/12<" in html and ">09/18<" in html, "")
        check("矩阵最新一列在最左（缺口两端都当作独立列，不合并）",
              html.index(">10/12<") < html.index(">09/18<"), "")
        check("矩阵含计算层给的板块名",
              all(str(r["cells"][0]["name"]) in html for r in matrix["rows"][:3]
                  if r["cells"] and r["cells"][0]), "")


# ============================ 8. 页面接线（源码级） ============================

def test_source_wiring() -> None:
    src = (ROOT / "page_sector_rotation.py").read_text(encoding="utf-8")

    # 用正则而不是 in：把条件改成永假时字符串照样在文件里，`in` 抓不到这种退坏
    check("页面：存在「样本不连续」判读分支",
          re.search(r'elif\s+label\s*==\s*"样本不连续"\s*:', src) is not None)
    check("页面：「样本不连续」用 st.warning 呈现",
          re.search(r'elif\s+label\s*==\s*"样本不连续"\s*:[\s\S]{0,200}?st\.warning\(',
                    src) is not None)
    check("页面：缺口阈值引用 sr.MAX_PREV_GAP_DAYS（不是字面量 12）",
          re.search(r'相邻交易日判据\s*\{sr\.MAX_PREV_GAP_DAYS\}', src) is not None)
    check("页面：watch 渲染前有真值判断（None 不进 caption）",
          re.search(r'if\s+verdict\.get\("watch"\)\s*:', src) is not None)
    check("页面：重合度 help 按 prev_is_adjacent 分支",
          re.search(r'prev_is_adjacent"\)\s*is\s+not\s+False', src) is not None)
    check("页面：缺口说明位于「无上榜板块」提前 return 之前",
          src.index(GAP_CAPTION_MARK) < src.index(NO_DETAIL_TEXT)
          if GAP_CAPTION_MARK in src and NO_DETAIL_TEXT in src else False,
          "")


# ============================ 9. 元断言：注入真的生效 ============================

def test_fixture_injection_effective() -> None:
    """两个场景渲染出的页面必须不同 —— 否则整份探针可能是空转（fixture 没进去）。"""
    # 这条放在最前面：上一用例改过 sr.MAX_PREV_GAP_DAYS，泄漏出来会把「缺口」悄悄
    # 放宽成「相邻」，让后面所有缺口断言变成假失败/假通过。必须显式守住。
    check("元断言：上一用例改动的阈值常量已还原（无跨用例污染）",
          sr.MAX_PREV_GAP_DAYS == _ORIG_MAX_GAP,
          f"当前={sr.MAX_PREV_GAP_DAYS} 期望={_ORIG_MAX_GAP}")

    at_adj = render(_analysis(DATES_ADJ))
    at_gap = render(_analysis(DATES_GAP))
    d1 = metric_map(at_adj).get("数据日期")
    d2 = metric_map(at_gap).get("数据日期")
    check("元断言：两次渲染的数据日期确实不同（fixture 注入生效）",
          d1 == DATES_ADJ[-1] and d2 == "2026-10-12",
          f"{d1} vs {d2}")
    check("元断言：两次渲染的判读确实不同",
          "样本不连续" not in texts(at_adj)
          and "样本不连续" in texts(at_gap), "")
    check("元断言：两次渲染的重合度 KPI 确实不同",
          metric_map(at_adj).get("Top10 榜单重合度") != "—"
          and metric_map(at_gap).get("Top10 榜单重合度") == "—", "")


# ============================ 10. 空产物 / no_data ============================

def test_missing_and_no_data() -> None:
    at = render(None)
    check("产物不存在：渲染不抛异常", not at.exception,
          str(at.exception[0].message)[:160] if at.exception else "")
    check("产物不存在：给出「初始化中」提示而非报错数据缺失",
          "初始化中" in texts(at), texts(at)[:120])
    check("产物不存在：没有误报缺口", GAP_CAPTION_MARK not in texts(at))

    at2 = render({"status": "no_data", "reason": "东财接口取数失败"})
    check("no_data：渲染不抛异常", not at2.exception,
          str(at2.exception[0].message)[:160] if at2.exception else "")
    tx2 = texts(at2)
    check("no_data：报出计算层给的原因", "东财接口取数失败" in tx2, tx2[:120])
    check("no_data：不显示判读标签", "样本不连续" not in tx2)


# ============================ 11. 合规文案 ============================

def test_compliance_text() -> None:
    at = render(_analysis(DATES_GAP))
    if at.exception:
        check("缺口场景合规文案检查前置渲染成功", False, "渲染抛异常")
        return
    check("缺口场景合规文案检查前置渲染成功", True)
    tx = texts(at)
    for banned in ("建议买入", "建议卖出", "推荐买入", "可以买入"):
        check(f"页面不含正向荐股用语「{banned}」", banned not in tx)
    # 「目标价 / 仓位指引」只允许以否定形态出现：页面里那句
    # 「不提供买点、目标价或仓位指引」是合规声明，不是荐股。裸禁词会把它误判成违规。
    for word in ("目标价", "仓位指引", "买点"):
        bad = [ln for ln in tx.splitlines() if word in ln and "不" not in ln]
        check(f"「{word}」只以否定形态出现", not bad, str(bad)[:140])
    check("合规声明那句话确实在页面上（元断言，防止上面一条空洞通过）",
          "不提供买点" in tx, "")
    check("页面声明「不构成操作建议」", "不构成任何操作建议" in tx)
    check("口径说明里写明了板块口径", "东财概念板块" in tx)


def main() -> int:
    for fn in (test_adjacent_normal, test_gap_scenario,
               test_gap_unparsable_prev_date, test_gap_with_empty_detail,
               test_gap_threshold_from_constant,
               test_legacy_product_backward_compat, test_rank_matrix_in_gap,
               test_source_wiring, test_fixture_injection_effective,
               test_missing_and_no_data, test_compliance_text):
        print(f"\n=== {fn.__name__} ===")
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            check(f"{fn.__name__} 执行时抛异常", False,
                  f"{type(exc).__name__}: {exc}")

    total = len(FAIL)
    print(f"\n{'=' * 50}\n共 {'通过' if not total else '失败'}：{total} 项失败")
    if total:
        for f in FAIL:
            print(f"  ❌ {f}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
