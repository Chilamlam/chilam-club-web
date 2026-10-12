"""板块轮动计算层（不 import streamlit、不联网）。

职责
----
读入 data/sector_rotation/history.csv（东财概念板块每日快照的累积归档），
纯 pandas 计算出前端与摘要都直接可用的分析结果 dict：

  1. 近 10 个交易日（默认窗口 pct_10d）涨幅 Top N 板块榜
  2. 榜单与上一份归档的重合度（重合越多越像「主线主升」，重合越少越像「快速轮动」）。
     上一份归档**必须是相邻交易日**才作数：归档可能缺日期（取数被拒收、历史清洗
     剔除残缺日），跨空档算出的重合度顶不住「上一交易日」这个说法，此时改报
     「样本不连续」并如实给出相隔天数（见 MAX_PREV_GAP_DAYS）。
  3. 每个上榜板块的连续在榜天数（自该板块进入 Top N 起连续计天）
  4. 周期判读（主升 / 轮动 / 过渡）+ 带基准值的理由与明日观察条件

设计约束（沿用本项目铁律）
--------------------------
- 缺失就标 None / 样本不足，绝不补 0、绝不用邻日顶替。
- 涨跌幅口径一律是「百分数」（12.34 表示 12.34%），与东财 clist 原始口径一致，
  fixture 与展示层都必须用同一口径，否则单位错位 bug 会隐形。
- 「昨日涨停/重仓/股通」这类属性型、复盘型板块不是题材，须从题材榜排除；
  黑名单刻意保守（只排明显不是题材的），宁可漏排也不错杀真题材。
- 判读是启发式描述，不是操作建议；理由必须带可对账的数字与阈值。
"""
from __future__ import annotations

import datetime
import os

import pandas as pd

DEFAULT_WINDOW = "pct_10d"
TOP_N = 10
WINDOW_COLS = ("pct_5d", "pct_10d", "pct_20d", "pct_60d")
NUMERIC_COLS = ("close", "pct_chg", *WINDOW_COLS, "amount_yi", "turnover", "up_count", "down_count")

# 「上一份归档」不等于「上一交易日」——为什么必须判相邻、以及阈值怎么定
# ---------------------------------------------------------------------------
# 归档按交易日累积，但它**可能缺日期**：跑批取数被拒收（宁缺不写坏）、历史清洗
# 剔除残缺日、或连续几天失败，都会在 dates 里留下空档。此时 dates[-2] 只是
# 「上一份数据」，不是「上一交易日」。
#
# 拿它算榜单重合度会得到一个跨空档的累计变化，却顶着「与上一交易日重合 x/10」
# 的说法 —— 数字看着很正常，含义已经完全变了（2026-10-12 实测：归档空档
# 09-18 → 10-12 相隔 24 天，若照旧计算会输出「与上一交易日（09-18）重合」）。
#
# 计算层不联网、拿不到交易日历，只能用**自然日差**判相邻。A 股最长连续休市是
# 春节（约 9 天）加两端周末，相邻交易日的自然日差上界约 11 天。阈值取 12：
# **宁可漏判轻微缺口，也不把长假误判成缺口** —— 长假后第一个交易日误报
# 「样本不连续」是把正常情况说成异常，同样是有害的错告警。
MAX_PREV_GAP_DAYS = 12

# 「上一交易日」这个说法只在**真正紧邻**时才能用。
# 周五→周一相隔 3 天，加 1 天余量取 4：超过它就说明中间隔着休市或缺口，
# 此时如实写「上一份数据（日期，相隔 N 天）」，让读者自己判断这中间隔了什么。
# 拿自然日差判相邻的**已知局限**（务必如实说，别假装它精确）：它区分不了
# 「跨长假」和「缺了几个交易日」——09-10 → 09-18 相隔 8 天，可能中间是合法休市，
# 也可能缺了 5 个交易日。没有交易日历就只能到这个精度；所以退而求其次：
# 无论如何都**把相隔天数显示出来**，绝不把它藏在一个「上一交易日」的说法后面。
NORMAL_GAP_DAYS = 4

# 属性型 / 复盘型板块黑名单（子串匹配，刻意保守）。
# 这些「板块」只是统计口径（昨日涨停、重仓股、互联互通标的…），不是题材主线，
# 混进涨幅榜会污染「当前主线是什么」的答案。
STYLE_BOARD_PATTERNS = (
    "昨日",        # 昨日涨停 / 昨日连板 / 昨日触板（含 _含一字 变体）
    "含一字",
    "ST板块",
    "次新股",
    "B股",
    "股通",        # 沪股通 / 深股通 / 北证50成分? —— 互联互通属性
    "重仓",        # 基金重仓 / 机构重仓 / 社保重仓 / QFII重仓
    "MSCI",
    "富时罗素",
    "标普",        # 标普道琼斯A股
    "道琼斯",
    "证金持股",
    "汇金",
    "融资融券",
    "转融券",
    "破净",
    "破发",
    "举牌",
    "回购增持",    # 回购增持概念（事件属性）
    "股权激励",
    "员工持股",
    "GDR",
    "AH股",
    "央企改革100", # 指数成分类
    "中字头",      # 属性而非题材（国企属性集合）
)


def is_style_board(name: str) -> bool:
    """判断是否属性型/复盘型板块。"""
    if not isinstance(name, str):
        return True
    return any(p in name for p in STYLE_BOARD_PATTERNS)


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_history(path: str) -> pd.DataFrame | None:
    """读历史归档 CSV。文件不存在返回 None（取数失败 ≠ 空数据）。"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"code": str})
    except Exception:
        return None
    if df is None or df.empty or "date" not in df.columns:
        return None
    df["date"] = df["date"].astype(str)
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = _to_num(df[c])
        else:
            df[c] = pd.NA
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def filter_boards(df_day: pd.DataFrame) -> pd.DataFrame:
    """去掉属性型板块与 10 日涨幅缺失的板块（新板块无窗口数据，不能按 0 参榜）。"""
    d = df_day.copy()
    d = d[~d["name"].map(is_style_board)]
    return d.dropna(subset=[DEFAULT_WINDOW])


def top_for_date(history: pd.DataFrame, date: str,
                 window_col: str = DEFAULT_WINDOW, n: int = TOP_N) -> pd.DataFrame:
    """某交易日按窗口涨幅降序的 Top N 明细（已排除属性板块与缺失值）。"""
    d = history[history["date"] == date]
    d = filter_boards(d)
    return d.sort_values(window_col, ascending=False).head(n)


def _gap_days(newer: str, older: str) -> int | None:
    """两个归档日期之间的自然日差（older → newer）。解析失败返回 None。

    返回 None 表示「无法判断是否相邻」，调用方必须按**不可比**处理，
    不能当作 0（那会把非法日期当成相邻日，正好是最危险的方向）。
    """
    try:
        return (datetime.date.fromisoformat(str(newer))
                - datetime.date.fromisoformat(str(older))).days
    except (ValueError, TypeError):
        return None


def _prev_label(prev_date: str, gap: int | None) -> str:
    """重合度比较对象的**如实称谓**（措辞唯一真源）。

    只有真正紧邻（相隔 ≤ NORMAL_GAP_DAYS）才写「上一交易日」；超过就写
    「上一份数据（日期，相隔 N 天）」——否则读者会把一个跨空档的重合度
    当成日间轮动，而它其实是空档期累计变化的对比。

    gap 为 None 表示日期无法解析，此时**绝不能**说「上一交易日」（那等于
    在没确认相邻的情况下断言相邻，方向正好错在最危险的一侧）。
    """
    if gap is None:
        # 不说「日期无法解析」指向哪个日期——调用方只知道有一端解析不了，
        # 措辞要避免把责任安到 prev_date 头上（实际可能是 as_of 那一端）。
        return f"上一份数据（{prev_date}，无法确认相隔天数）"
    if gap > NORMAL_GAP_DAYS:
        return f"上一份数据（{prev_date}，相隔 {gap} 天）"
    return f"上一交易日（{prev_date}）"


def compute_streak(history: pd.DataFrame, dates: list[str], end_idx: int,
                   code: str, window_col: str = DEFAULT_WINDOW, n: int = TOP_N) -> int:
    """连续在榜天数：从 end_idx 当天往回数，连续多少个交易日该板块都在 Top N。

    遇到归档缺口即停：缺日期时「连续」是假的——中间那几个交易日根本没有数据，
    无从知道该板块当时是否在榜。**宁可少算，不可把「没数据」当成「在榜」**，
    否则空档越长，streak 虚高得越离谱。
    """
    streak = 0
    for i in range(end_idx, -1, -1):
        if i < end_idx:
            gap = _gap_days(dates[i + 1], dates[i])
            if gap is None or gap > MAX_PREV_GAP_DAYS:
                break
        codes = set(top_for_date(history, dates[i], window_col, n)["code"])
        if code in codes:
            streak += 1
        else:
            break
    return streak


def build_rank_matrix(history: pd.DataFrame | None, days: int = 10,
                      window_col: str = DEFAULT_WINDOW, n: int = TOP_N,
                      as_of: str | None = None) -> dict:
    """排名矩阵：行=名次 1..n，列=交易日（最新在最左）。

    每格 = 该日在榜该名次的板块（按 window_col 涨幅降序）+ 其当日涨跌幅。
    归档不足 days 天时只返回已有的列（前端如实显示积累进度，绝不拿别日顶替）。
    返回 JSON 可序列化 dict。
    """
    if history is None or history.empty:
        return {"status": "no_data", "reason": "无历史归档（数据尚未生成）"}

    dates = sorted(history["date"].unique().tolist())
    if as_of is None:
        as_of = dates[-1]
    if as_of not in dates:
        return {"status": "no_data", "reason": f"指定日期 {as_of} 不在归档中"}

    col_dates = [d for d in dates if d <= as_of][-days:][::-1]  # 最新在最左
    if not col_dates:
        return {"status": "no_data", "reason": "无可用交易日"}

    tops = {d: top_for_date(history, d, window_col, n) for d in col_dates}
    rows = []
    for rank in range(1, n + 1):
        cells = []
        for d in col_dates:
            tdf = tops[d]
            if len(tdf) < rank:
                cells.append(None)  # 该日有效板块不足此名次
                continue
            r = tdf.iloc[rank - 1]
            pct = r.get("pct_chg")
            cells.append({
                "code": str(r["code"]),
                "name": str(r["name"]),
                "pct_chg": None if pd.isna(pct) else round(float(pct), 2),
            })
        rows.append({"rank": rank, "cells": cells})

    return {"status": "ok", "dates": col_dates, "window_col": window_col,
            "rank_n": n, "rows": rows}


def compute_analysis(history: pd.DataFrame | None, as_of: str | None = None,
                     window_col: str = DEFAULT_WINDOW, n: int = TOP_N) -> dict:
    """主分析入口：返回 JSON 可序列化的 dict（直接落 analysis.json / 进前端）。"""
    if history is None or history.empty:
        return {"status": "no_data", "reason": "无历史归档（数据尚未生成）"}

    dates = sorted(history["date"].unique().tolist())
    if as_of is None:
        as_of = dates[-1]
    if as_of not in dates:
        return {"status": "no_data", "reason": f"指定日期 {as_of} 不在归档中"}

    as_of_idx = dates.index(as_of)
    top_df = top_for_date(history, as_of, window_col, n)

    if top_df.empty:
        return {"status": "no_data", "reason": f"{as_of} 无有效板块数据"}

    as_of_day = history[history["date"] == as_of]
    universe = filter_boards(as_of_day)
    universe_median = (universe[window_col].median()
                       if not universe.empty and universe[window_col].notna().any() else None)

    # ---- 重合度：与「上一份归档」比，但只有在它确实是上一交易日时才作数 ----
    # prev_date 始终如实上报（它就是归档里的前一条，读者需要知道上一份数据是哪天）；
    # 是否**可比**由 prev_is_adjacent 决定，重合度只在可比时给出。
    overlap = None
    prev_date = None
    prev_gap_days = None
    prev_is_adjacent = None
    if as_of_idx >= 1:
        prev_date = dates[as_of_idx - 1]
        prev_gap_days = _gap_days(as_of, prev_date)
        prev_is_adjacent = (prev_gap_days is not None
                            and prev_gap_days <= MAX_PREV_GAP_DAYS)
        if prev_is_adjacent:
            prev_codes = set(top_for_date(history, prev_date, window_col, n)["code"])
            overlap = len(prev_codes & set(top_df["code"]))

    # ---- 明细 + 连续在榜 ----
    rows = []
    for rank, (_, r) in enumerate(top_df.iterrows(), start=1):
        code = str(r["code"])
        rows.append({
            "rank": rank,
            "code": code,
            "name": str(r["name"]),
            "pct_chg": None if pd.isna(r.get("pct_chg")) else round(float(r["pct_chg"]), 2),
            "pct_5d": None if pd.isna(r.get("pct_5d")) else round(float(r["pct_5d"]), 2),
            "pct_10d": None if pd.isna(r.get("pct_10d")) else round(float(r["pct_10d"]), 2),
            "pct_20d": None if pd.isna(r.get("pct_20d")) else round(float(r["pct_20d"]), 2),
            "pct_60d": None if pd.isna(r.get("pct_60d")) else round(float(r["pct_60d"]), 2),
            "streak": compute_streak(history, dates, as_of_idx, code, window_col, n),
        })

    top_median = top_df[window_col].median()

    def _fmt(v) -> str:
        """百分数展示：缺失一律 —，绝不补 0。"""
        try:
            return "—" if v is None or pd.isna(v) else f"{float(v):.1f}"
        except (TypeError, ValueError):
            return "—"

    wname = window_col.replace("pct_", "")

    # ---- 周期判读（启发式，理由必须带数字） ----
    if overlap is None and prev_is_adjacent is False:
        # 归档有空档：上一份数据不是上一交易日。宁可如实说「不连续」，也不硬套
        # 主升/轮动标签——那两个标签的含义都建立在「日间变化」上，跨空档没有意义。
        # gap 为 None 是「日期格式无法解析」，与「确实隔了很多天」是两回事，
        # 文案必须分开说：否则会打出「相隔 None 天」这种无意义的话。
        if prev_gap_days is None:
            gap_txt = f"上一份数据是 {prev_date}，但其日期无法解析、无法判断相隔多久"
        else:
            gap_txt = f"上一份数据是 {prev_date}，相隔 {prev_gap_days} 天"
        verdict = {
            "label": "样本不连续",
            "reasons": [
                f"{as_of} 之前归档缺了交易日（{gap_txt}），无法计算榜单重合度——"
                f"跨空档算出的重合度反映的是这段空档的累计变化，"
                f"不是「上一交易日轮动」；",
                f"Top{n} {wname}涨幅中位数 {_fmt(top_median)}%，"
                f"全市场概念板块中位数 {_fmt(universe_median)}%。",
            ],
            "watch": None,
        }
    elif overlap is None:
        verdict = {
            "label": "样本不足",
            "reasons": [
                f"截至 {as_of} 归档不足 2 个交易日，无法计算榜单重合度；"
                f"明日收盘后再看判读。"
            ],
            "watch": None,
        }
    elif overlap >= 7:
        verdict = {
            "label": "主线主升",
            "reasons": [
                f"Top{n} 与{_prev_label(prev_date, prev_gap_days)}重合 {overlap}/{n}，"
                f"榜单高度稳定；",
                f"Top{n} {wname}涨幅中位数 {_fmt(top_median)}%，"
                f"全市场概念板块中位数 {_fmt(universe_median)}%。",
            ],
            "watch": f"明日重合仍 ≥7 则维持主升判读；若掉到 ≤3 则转向轮动判读。",
        }
    elif overlap <= 3:
        verdict = {
            "label": "快速轮动",
            "reasons": [
                f"Top{n} 与{_prev_label(prev_date, prev_gap_days)}重合仅 {overlap}/{n}，"
                f"领涨板块快速换脸；",
                f"Top{n} {wname}涨幅中位数 {_fmt(top_median)}%，"
                f"全市场概念板块中位数 {_fmt(universe_median)}%。",
            ],
            "watch": f"明日重合回升至 ≥7 则转向主升判读；仍 ≤3 则轮动延续。",
        }
    else:
        verdict = {
            "label": "过渡/混合",
            "reasons": [
                f"Top{n} 与{_prev_label(prev_date, prev_gap_days)}重合 {overlap}/{n}，"
                f"介于主升（≥7）与轮动（≤3）之间，主线与轮动并存；",
                f"Top{n} {wname}涨幅中位数 {_fmt(top_median)}%，"
                f"全市场概念板块中位数 {_fmt(universe_median)}%。",
            ],
            "watch": f"明日重合 ≥7 转主升判读；≤3 转轮动判读。",
        }

    return {
        "status": "ok",
        "date": as_of,
        "prev_date": prev_date,
        # 上一份归档与 as_of 的自然日差、以及它是否真的是「上一交易日」。
        # prev_label 是**措辞就绪**的比较对象称谓（唯一真源）：页面、摘要、复盘素材
        # 一律用它，而不是各自再判一次 gap —— 一份规则两处实现必然漂移，
        # 哪天阈值改了只会改到一处（这条在项目里已经栽过）。
        "prev_gap_days": prev_gap_days,
        "prev_is_adjacent": prev_is_adjacent,
        "prev_label": (_prev_label(prev_date, prev_gap_days)
                       if prev_date else None),
        "trade_days_available": len(dates),
        "first_date": dates[0],
        "window_col": window_col,
        "top_n": n,
        "top": rows,
        "overlap_count": overlap,
        "overlap_denominator": n if overlap is not None else None,
        "top_median_pct": None if pd.isna(top_median) else round(float(top_median), 2),
        "universe_median_pct": None if universe_median is None or pd.isna(universe_median) else round(float(universe_median), 2),
        "universe_count": int(len(universe)),
        "verdict": verdict,
    }
