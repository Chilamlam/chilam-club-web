"""板块轮动计算层自检（纯离线，不依赖网络）。

校验口径（涨跌幅一律百分数口径，与东财 clist / 生产归档一致：
12.34 表示 12.34%，不是 0.1234——单位错位会让 bug 隐形）：
1. Top10 必须排除属性型/复盘型板块（即使它涨幅全场最高）
2. 榜单按窗口涨幅严格降序；缺失 pct_10d 的板块不得参榜（新板块≠0）
3. 重合度阈值判读：≥7 主升 / ≤3 轮动 / 4~6 过渡 / 单日样本不足
4. 连续在榜天数逐日累计，断榜重置
5. 判读理由必须带可对账的数字（重合 X/10、中位数），观察条件必须带阈值
6. 计算层不得 import streamlit / 网络库
7. 接线完整性：run_daily STEPS、app.py 路由、analysis.json 产物存在且日期新鲜
8. 排名矩阵：日期列最新在最左、同 rank 跨日可比、板块不足的格子=None（缺失≠0）、
   属性板块不占名次、页面第 1/2/3 名加粗且走 ui_compat 渲染
9. 重合度只在「上一份归档确实是相邻交易日」时才作数：归档有空档 → 不给重合度、
   标签改「样本不连续」并如实给出相隔天数；跨空档也不累加连续在榜天数。
   日期无法解析时必须按「不可比」处理（gap 绝不能退化成 0 = 当成相邻）
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import re
import sys

import pandas as pd

import sector_rotation as sr

FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def _rows(date: str, tops: list[tuple[str, float]], fillers: int = 5,
          extra_rows: list[dict] | None = None) -> list[dict]:
    """构造某日板块行情行。tops 是 (code, pct_10d)（百分数口径）。

    fillers 是垫底板块（涨幅递减的负值），保证 Top10 由 tops 决定。
    """
    rows = []
    for i, (code, pct) in enumerate(tops):
        rows.append({
            "date": date, "code": code, "name": f"题材_{code}",
            "close": 1000.0 + i, "pct_chg": round(pct / 5, 2),
            "pct_5d": round(pct * 0.6, 2), "pct_10d": pct,
            "pct_20d": round(pct * 1.2, 2), "pct_60d": round(pct * 1.5, 2),
            "amount_yi": 50.0, "turnover": 3.0, "up_count": 10, "down_count": 5,
        })
    for i in range(fillers):
        rows.append({
            "date": date, "code": f"F{date[-2:]}{i:02d}", "name": f"垫底_{i}",
            "close": 500.0, "pct_chg": -0.5, "pct_5d": -1.0, "pct_10d": -2.0 - i,
            "pct_20d": -3.0, "pct_60d": -4.0,
            "amount_yi": 10.0, "turnover": 1.0, "up_count": 3, "down_count": 20,
        })
    for r in (extra_rows or []):
        rows.append({**r, "date": date})
    return rows


def _style_row(date: str, pct: float) -> dict:
    return {"date": date, "code": "SB01", "name": "昨日连板_含一字",
            "close": 2000.0, "pct_chg": 9.9, "pct_5d": pct * 0.8, "pct_10d": pct,
            "pct_20d": pct, "pct_60d": pct, "amount_yi": 99.0,
            "turnover": 9.0, "up_count": 50, "down_count": 0}


def test_style_exclusion_and_order() -> None:
    rows = _rows("20260825", [(f"T{i:02d}", 20.0 - i) for i in range(12)],
                 extra_rows=[_style_row("20260825", 30.0)])
    hist = pd.DataFrame(rows)
    a = sr.compute_analysis(hist, as_of="20260825")

    check("单日历史 status=ok（榜单可算）", a.get("status") == "ok", str(a.get("reason", "")))
    names = [r["name"] for r in a["top"]]
    check("属性型板块即使涨幅最高也不进 Top10",
          all("昨日" not in n for n in names), str(names[:3]))
    check("Top10 恰好 10 个", len(a["top"]) == 10, f"{len(a['top'])}")
    pcts = [r["pct_10d"] for r in a["top"]]
    check("榜单严格降序", all(pcts[i] >= pcts[i + 1] for i in range(len(pcts) - 1)), str(pcts))
    check("第一名是 20.0 的题材（非属性板块）",
          a["top"][0]["pct_10d"] == 20.0 and a["top"][0]["code"] == "T00", str(a["top"][0]))


def test_missing_pct_not_ranked() -> None:
    rows = _rows("20260825", [(f"T{i:02d}", 20.0 - i) for i in range(12)],
                 extra_rows=[{"code": "NEW1", "name": "新板块_无窗口", "close": 800.0,
                              "pct_chg": 5.0, "pct_5d": None, "pct_10d": None,
                              "pct_20d": None, "pct_60d": None,
                              "amount_yi": 30.0, "turnover": 2.0,
                              "up_count": 5, "down_count": 5}])
    a = sr.compute_analysis(pd.DataFrame(rows), as_of="20260825")
    names = [r["name"] for r in a["top"]]
    check("缺失 10日涨幅的板块不参榜（不是按 0）",
          all("新板块" not in n for n in names), str(names))


def _two_day_history(overlap: int) -> pd.DataFrame:
    """D1 固定 Top12；D2 保留 overlap 个（涨幅抬到 30 段与新股隔离）。

    留榜板块涨幅整体抬到 30~（30.0-i），新进板块压到 15~：
    两段涨幅区间完全隔开，才能保证 Top10 恰好 = overlap 个旧板块 + 其余新板块。
    否则新旧涨幅交叉，重合数受并列排序影响，夹具本身就是错的。
    """
    d1_tops = [(f"T{i:02d}", 20.0 - i) for i in range(12)]
    keep = [(c, 30.0 - i) for i, (c, _) in enumerate(d1_tops[:overlap])]
    new = [(f"N{i:02d}", 15.0 - i) for i in range(12 - overlap)]
    rows = _rows("20260825", d1_tops) + _rows("20260826", keep + new)
    return pd.DataFrame(rows)


def test_overlap_verdicts() -> None:
    a7 = sr.compute_analysis(_two_day_history(7), as_of="20260826")
    check("重合 7/10 → 主线主升", a7["verdict"]["label"] == "主线主升",
          a7["verdict"]["label"])
    check("重合值如实上报 7", a7["overlap_count"] == 7, str(a7["overlap_count"]))

    a3 = sr.compute_analysis(_two_day_history(3), as_of="20260826")
    check("重合 3/10 → 快速轮动", a3["verdict"]["label"] == "快速轮动",
          a3["verdict"]["label"])

    a4 = sr.compute_analysis(_two_day_history(4), as_of="20260826")
    check("重合 4/10 → 过渡/混合", a4["verdict"]["label"] == "过渡/混合",
          a4["verdict"]["label"])

    a6 = sr.compute_analysis(_two_day_history(6), as_of="20260826")
    check("重合 6/10 → 过渡/混合", a6["verdict"]["label"] == "过渡/混合",
          a6["verdict"]["label"])

    for a in (a7, a3, a4):
        reason_txt = " ".join(a["verdict"]["reasons"])
        check(f"判读理由带重合数字（{a['verdict']['label']}）",
              f"{a['overlap_count']}/10" in reason_txt, reason_txt[:60])
        # 必须先断言 watch 是字符串：直接 `"7" in watch` 在 watch 为 None 时抛
        # TypeError 让**整个探针崩掉**，后面的断言一条都跑不到——造错反验就会
        # 误报成「断言没抓住」（2026-10-12 实测踩到）。
        watch = a["verdict"].get("watch")
        check(f"观察条件带阈值（{a['verdict']['label']}）",
              isinstance(watch, str) and "7" in watch and "3" in watch,
              str(watch))
        check(f"中位数带数值（{a['verdict']['label']}）",
              a["top_median_pct"] is not None and a["universe_median_pct"] is not None)


def test_streaks() -> None:
    d1 = [(f"T{i:02d}", 20.0 - i) for i in range(12)]
    # D2: T00~T05 留榜（streak=2），N 系新进（streak=1）
    d2 = [(f"T{i:02d}", 21.0 - i) for i in range(6)] + \
         [(f"N{i:02d}", 15.0 - i) for i in range(6)]
    rows = _rows("20260825", d1) + _rows("20260826", d2)
    a = sr.compute_analysis(pd.DataFrame(rows), as_of="20260826")
    streak = {r["code"]: r["streak"] for r in a["top"]}
    check("连榜两天的板块 streak=2", streak.get("T00") == 2, str(streak.get("T00")))
    check("新进板块 streak=1", streak.get("N00") == 1, str(streak.get("N00")))

    # D3: T00 继续在榜 → streak=3；N00 掉榜 → 不出现在 top
    d3 = [(f"T{i:02d}", 22.0 - i) for i in range(6)] + \
         [(f"M{i:02d}", 14.0 - i) for i in range(4)]
    rows += _rows("20260827", d3)
    a3 = sr.compute_analysis(pd.DataFrame(rows), as_of="20260827")
    streak3 = {r["code"]: r["streak"] for r in a3["top"]}
    check("三连榜 streak=3", streak3.get("T00") == 3, str(streak3.get("T00")))


def _dated(tops_by_date: dict) -> pd.DataFrame:
    """按 {日期: [(code, pct), ...]} 造归档（日期用生产格式 YYYY-MM-DD）。"""
    rows = []
    for d, tops in tops_by_date.items():
        rows += _rows(d, tops)
    return pd.DataFrame(rows)


def _reasons(a: dict) -> str:
    return " ".join(a["verdict"]["reasons"])


def test_prev_adjacency() -> None:
    """重合度只在「上一份归档确实是上一交易日」时才作数（2026-10-12 加）。

    背景：归档可能缺日期（取数被拒收、历史清洗剔除残缺日、连续几天失败）。
    此时 dates[-2] 只是「上一份数据」而非「上一交易日」。照旧计算会得到跨空档的
    累计变化，却顶着「与上一交易日重合 x/10」的说法——数字正常，含义全变。
    """
    base = [(f"T{i:02d}", 20.0 - i) for i in range(12)]
    # 当日榜单：保留 base 前 6 个（抬到 30 段）+ 6 个新进（压到 15 段），
    # 两段区间完全隔开，Top10 才恰好是 6 旧 + 4 新 → 重合度确定性为 6。
    today = ([(c, 30.0 - i) for i, (c, _) in enumerate(base[:6])]
             + [(f"N{i:02d}", 15.0 - i) for i in range(6)])

    # ---- A 相邻 ----
    a = sr.compute_analysis(_dated({"2026-08-24": base, "2026-08-25": today}))
    check("相邻：prev_is_adjacent=True", a.get("prev_is_adjacent") is True,
          str(a.get("prev_is_adjacent")))
    check("相邻：gap 如实为 1", a.get("prev_gap_days") == 1, str(a.get("prev_gap_days")))
    check("相邻：重合度如实为 6", a.get("overlap_count") == 6, str(a.get("overlap_count")))
    check("相邻：文案写「上一交易日（2026-08-24）」",
          "上一交易日（2026-08-24）" in _reasons(a), _reasons(a)[:80])
    check("相邻：prev_label 出现在文案里（措辞唯一真源，不是两处各写一遍）",
          bool(a.get("prev_label")) and a["prev_label"] in _reasons(a),
          f"prev_label={a.get('prev_label')}")
    check("相邻：文案不含「上一份数据」", "上一份数据" not in _reasons(a))

    # ---- B 中等空档：仍可比，但称谓必须改口 ----
    b = sr.compute_analysis(_dated({"2026-08-14": base, "2026-08-25": today}))
    check("空档（元断言）：用例 gap 落在 (NORMAL, MAX] 区间，否则没测到目标分支",
          sr.NORMAL_GAP_DAYS < (b.get("prev_gap_days") or 0) <= sr.MAX_PREV_GAP_DAYS,
          str(b.get("prev_gap_days")))
    check("空档：仍判为可比", b.get("prev_is_adjacent") is True,
          str(b.get("prev_is_adjacent")))
    check("空档：仍给出重合度", b.get("overlap_count") is not None,
          str(b.get("overlap_count")))
    check("空档：文案**不**说「上一交易日」", "上一交易日（" not in _reasons(b),
          _reasons(b)[:80])
    check("空档：文案说「上一份数据（2026-08-14，相隔 11 天）」",
          "上一份数据（2026-08-14，相隔 11 天）" in _reasons(b), _reasons(b)[:95])

    # ---- C 长空档：不给重合度 ----
    c = sr.compute_analysis(_dated({"2026-08-05": base, "2026-08-25": today}))
    check("长空档（元断言）：用例 gap 确实 > MAX_PREV_GAP_DAYS",
          (c.get("prev_gap_days") or 0) > sr.MAX_PREV_GAP_DAYS,
          str(c.get("prev_gap_days")))
    check("长空档：prev_is_adjacent=False", c.get("prev_is_adjacent") is False)
    check("长空档：重合度为 None（不是 0）", c.get("overlap_count") is None,
          str(c.get("overlap_count")))
    check("长空档：overlap_denominator 为 None（不是 10）",
          c.get("overlap_denominator") is None, str(c.get("overlap_denominator")))
    check("长空档：标签「样本不连续」", c["verdict"]["label"] == "样本不连续",
          c["verdict"]["label"])
    check("长空档：不给「明日观察」条件（缺口期无从给）",
          c["verdict"].get("watch") is None, str(c["verdict"].get("watch")))
    check("长空档：文案点名相隔天数与上一份数据日期",
          "相隔 20 天" in _reasons(c) and "2026-08-05" in _reasons(c), _reasons(c)[:95])
    check("长空档：文案不出现 None", "None" not in _reasons(c))
    check("长空档：prev_label 也用「上一份数据」措辞（下游摘要据此渲染）",
          "上一份数据" in (c.get("prev_label") or ""), str(c.get("prev_label")))

    # ---- D 日期无法解析：按不可比处理，绝不能当成相邻（gap 当 0 是最危险的方向）----
    # 直接单测判据本身：通过 compute_analysis 造这个场景时，非法日期会因字符串
    # 排序跑到 as_of 位置上，测到的是另一条路径（先前的用例就是这么写歪的）。
    check("坏日期：_gap_days 对无法解析的输入返回 None（不是 0）",
          sr._gap_days("2026-08-25", "2026/08/14") is None)
    check("坏日期：_gap_days 对合法输入如实返回天数",
          sr._gap_days("2026-08-25", "2026-08-24") == 1)
    check("坏日期：_gap_days 对空串返回 None",
          sr._gap_days("2026-08-25", "") is None)
    check("坏日期：_prev_label 在 gap=None 时不说「上一交易日」",
          "上一交易日" not in sr._prev_label("2026-08-25", None),
          sr._prev_label("2026-08-25", None))
    # 归档里混入非法日期时，整条产物路径也必须判「不可比」而不是「相邻」
    d = sr.compute_analysis(_dated({"2026-08-24": base, "2026/08/25": today}))
    check("坏日期：产物 prev_is_adjacent 不为 True（没把非法日期当相邻）",
          d.get("prev_is_adjacent") is not True, str(d.get("prev_is_adjacent")))
    check("坏日期：产物 gap 为 None（不是 0）", d.get("prev_gap_days") is None,
          str(d.get("prev_gap_days")))
    check("坏日期：文案不出现 None（不能打出「相隔 None 天」）",
          "None" not in _reasons(d))

    # ---- E 单日归档仍走「样本不足」----
    e = sr.compute_analysis(_dated({"2026-08-25": today}))
    check("单日：标签「样本不足」（没被新分支抢走）",
          e["verdict"]["label"] == "样本不足", e["verdict"]["label"])
    check("单日：prev_is_adjacent 为 None（无从判断，不是 False）",
          e.get("prev_is_adjacent") is None, str(e.get("prev_is_adjacent")))

    # ---- F streak 不跨空档累加 ----
    cont = sr.compute_analysis(_dated({"2026-08-10": base, "2026-08-11": base,
                                       "2026-08-12": base, "2026-08-13": today}))
    gapd = sr.compute_analysis(_dated({"2026-08-10": base, "2026-08-11": base,
                                       "2026-08-12": base, "2026-09-25": today}))

    def _streak_of(res: dict, code: str):
        return next((r["streak"] for r in res["top"] if r["code"] == code), None)

    s_cont, s_gap = _streak_of(cont, "T00"), _streak_of(gapd, "T00")
    check("streak（元断言）：连续归档下 T00 的 streak > 1，否则比较无区分度",
          (s_cont or 0) > 1, str(s_cont))
    check("streak：跨空档不累加（空档版 < 连续版）",
          s_gap is not None and s_cont is not None and s_gap < s_cont,
          f"{s_gap} < {s_cont}")

    # ---- G 常量关系 ----
    check("常量：NORMAL_GAP_DAYS < MAX_PREV_GAP_DAYS",
          sr.NORMAL_GAP_DAYS < sr.MAX_PREV_GAP_DAYS,
          f"{sr.NORMAL_GAP_DAYS} < {sr.MAX_PREV_GAP_DAYS}")
    check("常量：MAX_PREV_GAP_DAYS >= 11（须覆盖春节/国庆最长休市，"
          "否则长假后首个交易日会被误报「样本不连续」）",
          sr.MAX_PREV_GAP_DAYS >= 11, str(sr.MAX_PREV_GAP_DAYS))

    # ---- H 接线：页面标签分支 + 阈值取常量（不许硬编码天数）----
    # 判「分支存在」不能只搜字符串：把 elif 条件改成永假的比较，字符串照样在文件里，
    # 分支却已经死了（项目里「名字出现过 ≠ 生效」已经栽过一次）。
    src = open("page_sector_rotation.py", encoding="utf-8").read()
    check("页面：有「样本不连续」的 elif 判读分支（是分支，不是仅出现在文案里）",
          re.search(r'elif\s+label\s*==\s*"样本不连续"\s*:', src) is not None)
    check("页面：缺口说明用 sr.MAX_PREV_GAP_DAYS（不是硬编码天数）",
          "sr.MAX_PREV_GAP_DAYS" in src)
    check("页面：重合度 KPI 的 help 会区分「未计算重合度」",
          "未计算重合度" in src)
    ev = open("review_evidence.py", encoding="utf-8").read()
    n_prev = ev.count('sector.get("prev_label")')
    check("复盘素材：两处重合度文案都用产物的 prev_label（措辞唯一真源，"
          "两处各写一遍必然漂移）",
          n_prev >= 2, f"出现 {n_prev} 次")
    check("复盘素材：算出重合度的分支先判 None，不会拼出「重合 None/None」",
          "if ov is None" in ev)


def test_insufficient_and_no_data() -> None:
    single = pd.DataFrame(_rows("20260825", [(f"T{i:02d}", 20.0 - i) for i in range(12)]))
    a = sr.compute_analysis(single, as_of="20260825")
    check("单日 → 判读样本不足", a["verdict"]["label"] == "样本不足", a["verdict"]["label"])
    check("单日 → 重合度为 None（不是 0）", a["overlap_count"] is None)
    check("样本不足理由说明原因",
          "不足 2 个交易日" in a["verdict"]["reasons"][0], a["verdict"]["reasons"][0])

    check("空历史 → no_data", sr.compute_analysis(None).get("status") == "no_data")
    check("空 DataFrame → no_data",
          sr.compute_analysis(pd.DataFrame()).get("status") == "no_data")


def test_rank_matrix() -> None:
    """排名矩阵：行=名次，列=交易日（最新在最左），格=板块+当日涨跌幅。"""
    d1 = [(f"T{i:02d}", 20.0 - i) for i in range(12)]
    d2 = [(f"T{i:02d}", 21.0 - i) for i in range(6)] + \
         [(f"N{i:02d}", 15.0 - i) for i in range(6)]
    d3 = [(f"M{i:02d}", 40.0 - i) for i in range(12)]  # 涨幅区间与 D1/D2 隔开
    rows = (_rows("20260825", d1) + _rows("20260826", d2)
            + _rows("20260827", d3, extra_rows=[_style_row("20260827", 50.0)]))
    m = sr.build_rank_matrix(pd.DataFrame(rows))

    check("矩阵 status=ok", m.get("status") == "ok", str(m.get("reason", "")))
    check("矩阵行数=10", len(m.get("rows") or []) == 10, str(len(m.get("rows") or [])))
    check("日期列最新在最左（倒序）",
          m.get("dates") == ["20260827", "20260826", "20260825"], str(m.get("dates")))
    r1 = m["rows"][0]
    check("rank1 最新日第一名=M00", r1["cells"][0]["code"] == "M00", str(r1["cells"][0]))
    check("rank1 格带当日涨跌幅（fixture 口径 pct/5 → 8.0）",
          r1["cells"][0]["pct_chg"] == 8.0, str(r1["cells"][0]))
    check("同 rank 跨日可比（前两日第一名都是 T00）",
          r1["cells"][1]["code"] == "T00" and r1["cells"][2]["code"] == "T00",
          f'{r1["cells"][1]} | {r1["cells"][2]}')
    all_cells = [c for r in m["rows"] for c in r["cells"] if c]
    check("属性板块不占矩阵任何名次",
          all(c.get("code") != "SB01" for c in all_cells), f"{len(all_cells)} cells")

    # 某日有效板块只有 2 个 → rank>=3 该日格=None（缺失≠0）
    sparse = pd.DataFrame([
        {"date": "20260830", "code": "S1", "name": "题材_S1", "close": 100.0,
         "pct_chg": 1.0, "pct_5d": 2.0, "pct_10d": 3.0, "pct_20d": 4.0,
         "pct_60d": 5.0, "amount_yi": 10.0, "turnover": 1.0, "up_count": 5, "down_count": 1},
        {"date": "20260830", "code": "S2", "name": "题材_S2", "close": 90.0,
         "pct_chg": 0.5, "pct_5d": 1.0, "pct_10d": 2.0, "pct_20d": 3.0,
         "pct_60d": 4.0, "amount_yi": 10.0, "turnover": 1.0, "up_count": 4, "down_count": 2},
    ])
    m2 = sr.build_rank_matrix(sparse)
    check("板块不足 10 个时低名次格=None（不补 0）",
          m2["rows"][2]["cells"][0] is None, str(m2["rows"][2]["cells"][0]))
    check("板块不足时高名次格照常",
          m2["rows"][0]["cells"][0]["code"] == "S1", str(m2["rows"][0]["cells"][0]))

    check("矩阵：空历史 → no_data", sr.build_rank_matrix(None).get("status") == "no_data")
    check("矩阵：空 DataFrame → no_data",
          sr.build_rank_matrix(pd.DataFrame()).get("status") == "no_data")


def test_layer_purity() -> None:
    path = os.path.join(os.path.dirname(__file__), "sector_rotation.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    check("计算层未 import streamlit", "streamlit" not in mods, f"{sorted(mods)}")
    check("计算层未 import 网络库",
          not ({"urllib", "requests", "http", "socket"} & mods), f"{sorted(mods)}")


def test_wiring() -> None:
    root = os.path.dirname(os.path.abspath(__file__))

    rd = open(os.path.join(root, "run_daily.py"), encoding="utf-8").read()
    check("run_daily STEPS 已挂板块轮动步骤",
          '{"key": "sector_rotation", "script": "daily_sector_rotation.py"' in rd)
    check("run_daily FRESHNESS 已挂板块轮动断言",
          "data/sector_rotation/analysis.json" in rd)

    app = open(os.path.join(root, "app.py"), encoding="utf-8").read()
    check("app.py 已导入渲染函数",
          "from page_sector_rotation import render_sector_rotation_page" in app)
    check("app.py 路由已接线（调用形式）", 'render_sector_rotation_page()' in app)
    check("app.py 菜单已接线", '"🔄 板块轮动"' in app)

    daily = open(os.path.join(root, "daily_sector_rotation.py"), encoding="utf-8").read()
    check("跑批脚本字段映射含 f160=10日（勿与 f110=20日 对调）",
          '"f160": "pct_10d"' in daily and '"f110": "pct_20d"' in daily)
    check("快照标签用交易日锚定（过零点跑批不得拿日历今天当标签——09-04 00:51 错标事故）",
          "today = _snapshot_trade_date()" in daily)
    check("东财取数多域名轮换（Actions 机房 IP 被单域名 502 拒绝）",
          "EM_HOSTS" in daily and "push2delay" in daily)

    page = open(os.path.join(root, "page_sector_rotation.py"), encoding="utf-8").read()
    check("页面已接排名矩阵（调用形式）", "render_rank_matrix(load_rank_matrix())" in page)
    check("矩阵第 1/2/3 名加粗（rank<=3 → hot 类）",
          "rank <= 3" in page and '"hot"' in page, "须为赋值/条件形式而非裸子串")
    check("矩阵走 ui_compat.html_embed（页面不得裸用 components.v1）",
          "html_embed(" in page and "components.v1" not in page)
    check("矩阵缺失格显示 —（nm/pct 缺省渲染存在）",
          '<div class="nm">—</div>' in page and '<div class="pct">—</div>' in page)

    srsrc = open(os.path.join(root, "sector_rotation.py"), encoding="utf-8").read()
    check("矩阵日期取最近 N 日并倒序（最新在左）", "[-days:][::-1]" in srsrc)

    uisrc = open(os.path.join(root, "ui_compat.py"), encoding="utf-8").read()
    check("html_embed data URI 显式声明 charset=utf-8（缺声明→浏览器按本地码页解码中文乱码）",
          "data:text/html;charset=utf-8;base64" in uisrc)
    check("html_embed 文档头部注入 <meta charset>（双保险）",
          '<meta charset="utf-8">' in uisrc)


def test_artifacts_fresh() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    ana_path = os.path.join(root, "data", "sector_rotation", "analysis.json")
    hist_path = os.path.join(root, "data", "sector_rotation", "history.csv")
    if not os.path.exists(ana_path):
        check("analysis.json 产物存在", False, "请先跑 daily_sector_rotation.py")
        return
    with open(ana_path, encoding="utf-8") as f:
        d = json.load(f)
    check("analysis.json 可解析且含 status/date",
          d.get("status") in ("ok", "insufficient", "no_data") and bool(d.get("date")),
          f"status={d.get('status')} date={d.get('date')}")

    # 时间戳新鲜度断言：数据日期必须落在最近 10 个自然日内（拦住硬编码旧数据）
    today = datetime.date.today()
    try:
        d_date = datetime.datetime.strptime(str(d["date"]), "%Y-%m-%d").date()
    except ValueError:
        check("analysis.json 日期格式 YYYY-MM-DD", False, str(d.get("date")))
        return
    check("analysis.json 数据日期在最近 10 个自然日内",
          today - datetime.timedelta(days=10) <= d_date <= today,
          f"{d_date} vs today {today}")

    if os.path.exists(hist_path):
        h = pd.read_csv(hist_path, dtype={"code": str})
        last_date = str(sorted(h["date"].astype(str).unique())[-1])
        check("analysis.json 的 date 与归档最后一个交易日一致",
              last_date == str(d["date"]), f"history={last_date} analysis={d['date']}")
        if d.get("status") == "ok":
            check("Top10 的 10日涨幅字段齐全（None 只允许出现在次要窗口）",
                  all(r.get("pct_10d") is not None for r in d.get("top", [])))


if __name__ == "__main__":
    print("=" * 60)
    print("板块轮动计算层自检")
    print("=" * 60)
    test_style_exclusion_and_order()
    test_missing_pct_not_ranked()
    test_overlap_verdicts()
    test_streaks()
    test_prev_adjacency()
    test_insufficient_and_no_data()
    test_rank_matrix()
    test_layer_purity()
    test_wiring()
    test_artifacts_fresh()
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
