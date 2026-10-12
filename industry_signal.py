# -*- coding: utf-8 -*-
"""行业「低市值占比 + 高资金热度」信号计算层（不 import streamlit、不联网）。

这个信号在找什么
----------------
同一天里同时满足两件事的行业：

  1. **市值占比处于自己的历史低位** —— 该行业流通市值占全体行业的比重，
     在近 MV_WINDOW 个交易日里排在很靠前（小）的位置：相对整个市场「缩水」了。
  2. **成交额占比处于自己的历史高位** —— 该行业成交额占全市场的比重，
     在近 AMT_WINDOW 个交易日里排在很靠后（大）的位置：资金关注度骤升。

组合含义是「跌够了 + 刚开始有人来」，即低位启动候选。这是**描述性筛选**，
不是买卖建议；本模块只输出数字与命中结果，不做任何操作方向判断。

口径与单位（错一个就会静默给出错误结论）
----------------------------------------
- 行业口径：申万 2021 版**二级**行业（134 个），来自 tushare `index_classify`。
  一级行业名称随行携带（`l1_name`）供展示，不参与计算。
- 归档列一律用**亿元**：`amount_yi` / `float_mv_yi` / `total_mv_yi`。
  tushare 原始单位是千元（amount）与万元（float_mv/total_mv），转换在跑批层完成。
- 占比是**百分数**（8.12 表示 8.12%），分母是当日同口径全体行业之和。
  用「全体二级行业之和」而非外部「全A流通市值」，是为了让分子分母同源、
  可逐日复算：申万二级行业并集即全市场，两者理论上相等，但引入第二个来源
  只会带来口径漂移风险。

两种「分位」的区别（本项目已有的坑，务必别混）
----------------------------------------------
- `percentile_rank(窗口, 当日值)`：当日值**在窗口内的位置**，包含当日自身。
  回答的是「今天处在什么位置」——本模块用的就是这一个。
- `sentiment.phase_thresholds`：用**严格早于当日**的历史算阈值，不包含当日。
  回答的是「用什么刻度去判断今天」。
两者语义不同，不能互相套用。这里包含当日是刻意的：占比分位是状态量而非判据阈值。

数据质量守卫（比结论本身更重要）
--------------------------------
`MIN_SECTION_RATIO`：某交易日的行业数明显少于常态（接口只返回了半份、
行业分类调整中间态）时，**整日剔除**。绝不参与计算的理由：
占比的分母是当日全体行业之和，少一半行业会让每个行业的占比凭空放大近一倍，
而结果看起来仍然是一组正常的百分数——这类错误没有任何下游迹象。
剔除而不是补零/取邻日，是本项目一贯规则。

唯一真源（改判定规则时只改这里）
--------------------------------
- `hit_mask()`  命中判定（严格不等式）。`compute_signal` 与展示页的高亮
  都调它——两处各写一遍必然漂移，届时会出现「表里标红了、结论里没命中」。
- `sort_by_priority()` 排序与并列消解。同上。
- `_rolling_pcts()` 逐点分位与窗口长度。`industry_series`（页面走势）与
  `history_hits`（历史回算）都调它——这两处若各写一遍循环，"某天分位是
  多少"就会有两个答案，而图上的曲线与表里的数字看着都很正常。

历史回算（`history_hits`）
--------------------------
对每个交易日回放一遍当日结论：「那天会选出谁、之后涨跌如何」。
三条口径上的硬要求，缺一条结论就是假的：
  1. **逐点分位只用截至该日的窗口**（含当日）——与 `compute_signal` 同口径。
     绝不能先在全期序列上算完分位再按日取用：那会让"历史命中"用上未来数据，
     选出来的行业事后看当然漂亮，而当时根本选不出来。
  2. **只统计分位窗口满额的交易日**。窗口不满时（归档开头那段）mid-rank 分位
     是在更短的尺度上算的，"30 分位"在两个尺度下不是同一件事；混在一起统计，
     读者无法分辨哪些样本可信。被跳过的天数如实上报，不隐藏。
  3. **之后涨跌只认归档里真有收盘价的交易日**：对齐全市场交易日序列取
     「之后第 N 个交易日」的收盘价比值，缺数据记 None，**绝不补 0**
     （补 0 会把"还没到那一天"变成"涨跌为零"，直接拉低统计结论）。

展示层只装配不计算
------------------
`universe_frame()` / `industry_series()` 是给页面用的**纯装配**函数：
不 import streamlit、不画图、不做取整，缺值一律留 NA 交给页面显示 —。
`industry_series()` 每个点的分位只用「截至该点」的窗口算，与
`compute_signal` 逐点同口径（否则就是前视偏差，见该函数 docstring）。
"""
from __future__ import annotations

import os
import statistics

import pandas as pd

MV_WINDOW = 250          # 市值占比分位窗口（交易日）
AMT_WINDOW = 20          # 成交额占比分位窗口（交易日）
MV_PCT_MAX = 30.0        # 命中条件：市值占比分位 < 30
AMT_PCT_MIN = 80.0       # 命中条件：成交额占比分位 > 80
MAX_PICKS = 3            # 每日最多报几个

MIN_TRADE_DAYS = 20      # 归档少于这么多交易日 → 不出结论（样本不足）
MIN_SECTION_RATIO = 0.90  # 当日行业数 < 常态 × 此值 → 整日视为残缺，剔除

SERIES_DAYS = 120        # 单行业走势图默认展示的交易日数（页面用）

# 历史回顾的观察窗口：命中后第 N 个交易日的累计涨跌（用收盘价比值）
FWD_DAYS = (1, 3, 5, 10)

NUMERIC_COLS = ("close", "pct_chg", "amount_yi", "float_mv_yi", "total_mv_yi", "vol")

# 页面明细表/散点图需要的行业字段（缺列补 NA，绝不猜）
UNIVERSE_COLS = ("code", "name", "l1_name", "mv_share", "mv_pct",
                 "amt_share", "amt_pct", "pct_chg", "amount_yi", "float_mv_yi")
UNIVERSE_NUMERIC = ("mv_share", "mv_pct", "amt_share", "amt_pct", "pct_chg",
                    "amount_yi", "float_mv_yi")


def load_history(path: str) -> pd.DataFrame | None:
    """读历史归档 CSV。文件不存在或为空返回 None（取数失败 ≠ 空数据）。"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, dtype={"code": str})
    except Exception:
        return None
    if df is None or df.empty or "date" not in df.columns or "code" not in df.columns:
        return None
    df["date"] = df["date"].astype(str)
    for c in NUMERIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.NA
    if "l1_name" not in df.columns:
        df["l1_name"] = pd.NA
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def percentile_rank(values, value) -> float | None:
    """value 在 values 中的百分位（0~100，含 value 自身）。

    用中位秩（mid-rank）：并列值各分一半，避免 `(v <= x).mean()` 把一串相同值
    全判成高分位。样本为空或 value 缺失返回 None——绝不用 0/50 之类的默认值
    冒充「算出来了」。
    """
    if value is None or pd.isna(value):
        return None
    v = pd.to_numeric(pd.Series(list(values)), errors="coerce").dropna()
    n = len(v)
    if n == 0:
        return None
    less = int((v < value).sum())
    equal = int((v == value).sum())
    return (less + 0.5 * equal) / n * 100.0


def _rolling_pcts(series: pd.Series, window: int) -> tuple[list, list]:
    """逐点分位与逐点窗口长度（**唯一真源**）。返回 (分位列表, 窗口长度列表)。

    第 i 个点只用 `series[max(0, i-window+1) : i+1]` 这一段算分位，
    即「截至该点、含该点自身」。这个切片与 `compute_signal` 算当日分位时用的
    `tail(window)` 是同一个集合（序列长度不足窗口时 `tail` 返回全部，
    与切片从 0 开始等价）——切片形状必须保持一致，否则本函数与当日结论
    会给出不同数字，而两边的输出看起来都完全正常。

    窗口长度一并返回，是为了让展示层能如实告诉读者「这个点只用了多少个
    交易日」，而不是假装窗口一直是满的。
    """
    s = pd.to_numeric(pd.Series(series), errors="coerce").reset_index(drop=True)
    pcts: list[float | None] = []
    lens: list[int] = []
    for i in range(len(s)):
        w = s.iloc[max(0, i - window + 1): i + 1].dropna()
        pcts.append(percentile_rank(w, w.iloc[-1] if len(w) else None))
        lens.append(int(len(w)))
    return pcts, lens


def section_valid_dates(hist: pd.DataFrame) -> list[str]:
    """返回「截面完整」的交易日列表（升序）。

    完整性基准取各交易日行业数的**中位数**而非最大值：最大值可能来自
    某天重复写入（同日重跑出 bug）而被抬高，中位数对此免疫。
    """
    counts = hist.groupby("date")["code"].nunique()
    if counts.empty:
        return []
    base = float(counts.median())
    if base <= 0:
        return []
    ok = counts[counts >= base * MIN_SECTION_RATIO]
    return sorted(ok.index.astype(str).tolist())


def _with_shares(hist: pd.DataFrame) -> pd.DataFrame:
    """在完整截面上加 mv_share / amt_share（百分数）。缺市值或成交额的行业剔除。"""
    d = hist.copy()
    d = d[pd.to_numeric(d["float_mv_yi"], errors="coerce").fillna(0) > 0]
    d = d[pd.to_numeric(d["amount_yi"], errors="coerce").fillna(0) > 0]
    if d.empty:
        return d
    d["mv_share"] = d["float_mv_yi"] / d.groupby("date")["float_mv_yi"].transform("sum") * 100.0
    d["amt_share"] = d["amount_yi"] / d.groupby("date")["amount_yi"].transform("sum") * 100.0
    return d


def hit_mask(frame: pd.DataFrame, thresholds: dict | None = None) -> pd.Series:
    """命中判定（**唯一真源**）：市值分位 < 阈值 且 成交额分位 > 阈值。

    严格不等式——恰好等于阈值不算命中。用 `<`/`>` 还有一个附带好处：
    NaN 比较恒为 False，缺失值天然不会被动判成命中（不需要额外过滤）。

    阈值优先取传入的那份（即 signal.json 里落盘的 `thresholds`），
    使展示页的高亮与生成结论时的规则必定同源。调用方不传才退回模块常量。
    """
    th = thresholds or {}
    mv_max = th.get("mv_pct_max", MV_PCT_MAX)
    amt_min = th.get("amt_pct_min", AMT_PCT_MIN)
    if "mv_pct" not in frame.columns or "amt_pct" not in frame.columns:
        # 缺列 → 全部不命中。绝不假设缺失即满足条件。
        return pd.Series(False, index=frame.index, dtype=bool)
    mv = pd.to_numeric(frame["mv_pct"], errors="coerce")
    amt = pd.to_numeric(frame["amt_pct"], errors="coerce")
    return (mv < float(mv_max)) & (amt > float(amt_min))


def sort_by_priority(frame: pd.DataFrame) -> pd.DataFrame:
    """优先级排序（**唯一真源**）：热度分位降序 → 市值分位升序 → code 升序。

    为什么必须带上第三个键 code：前两个键都可能并列（多个行业的成交额末值同为
    窗口最大值，或都降到窗口最小值），而 pandas 的多列排序对并列键是**稳定**的
    —— 稳定意味着「沿用输入行序」。于是这批数据的输出顺序会随调用方的行序而变。

    `compute_signal` 内部恰好先做了 `d.sort_values(["code", "date"])`，行序已被
    规范成 code 序，所以**它自己看不出这个问题**（连打乱输入行序都测不出来，
    2026-10-12 的造错反验第 4 项实证了这一点：去掉 code 键后主探针全绿）。
    但 `universe_frame` 是直接吃 signal.json 里 industries 的顺序的，将来任何
    按别的顺序装配数据的调用方都会踩到。带上 code 之后，输出与输入行序无关。

    这条保证由探针**直接对 sort_by_priority 断言**（构造前两键全并列的帧、
    打乱行序后比顺序），绕开上游的 code 排序，否则断言会被掩盖。
    """
    cols = [c for c in ("amt_pct", "mv_pct", "code") if c in frame.columns]
    if not cols:
        return frame
    ascending = [c != "amt_pct" for c in cols]
    return frame.sort_values(cols, ascending=ascending, na_position="last")


def universe_frame(sig: dict | None) -> pd.DataFrame | None:
    """把 signal dict 的全量行业装配成表（页面散点图 + 明细表用）。

    纯装配：不取整、不补默认值、不重算分位。缺列补 NA —— 页面显示 `—`。
    `status != ok` 返回 None：让展示层走明确的降级路径，而不是渲染一张空表
    （空表看起来和"今天真的没有行业"一模一样）。
    """
    if not sig or sig.get("status") != "ok":
        return None
    rows = sig.get("industries") or []
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for c in UNIVERSE_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    for c in UNIVERSE_NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 命中标记走唯一真源，不在这里重写一遍不等式
    df["is_hit"] = hit_mask(df, sig.get("thresholds"))
    return sort_by_priority(df).reset_index(drop=True)


def industry_series(history: pd.DataFrame | None, code: str,
                    days: int = SERIES_DAYS) -> pd.DataFrame | None:
    """某行业的占比与分位历史（页面走势图用）。

    每个点的分位只用**截至该点**的窗口算（含该点自身），口径与
    `compute_signal` 逐点一致。绝不能先在全期序列上算完再截取——那会让
    历史点用上未来数据（前视偏差），曲线画出来很漂亮，但当时根本得不到
    这样的数，而图上没有任何迹象能看出来。

    探针里有一条断言专门守它：给历史追加未来数据后，
    已有各交易日的分位必须逐字节不变。

    返回列：date / close? / pct_chg / mv_share / mv_pct / amt_share /
    amt_pct / amount_yi / float_mv_yi / mv_window_len / amt_window_len
    （原表没的列就不产出，不凭空补）。两个 window_len 让展示层能如实告诉
    读者「这个点只用了多少个交易日算分位」，而不是假装窗口一直是满的。
    """
    if history is None or history.empty or not code:
        return None
    valid = section_valid_dates(history)
    if not valid:
        return None
    d = _with_shares(history[history["date"].astype(str).isin(valid)])
    if d.empty:
        return None
    g = d[d["code"].astype(str) == str(code)].sort_values("date")
    if g.empty:
        return None

    mv_s = pd.to_numeric(g["mv_share"], errors="coerce").reset_index(drop=True)
    amt_s = pd.to_numeric(g["amt_share"], errors="coerce").reset_index(drop=True)
    # 逐点分位走唯一真源：history_hits 用的是同一个函数，
    # 所以「走势图上这个点」与「历史回顾表里那天」必然是同一个数。
    mv_pcts, mv_lens = _rolling_pcts(mv_s, MV_WINDOW)
    amt_pcts, amt_lens = _rolling_pcts(amt_s, AMT_WINDOW)

    out = g.assign(mv_pct=mv_pcts, amt_pct=amt_pcts,
                   mv_window_len=mv_lens, amt_window_len=amt_lens)
    keep = [c for c in ("date", "close", "pct_chg", "mv_share", "mv_pct",
                        "amt_share", "amt_pct", "amount_yi", "float_mv_yi",
                        "mv_window_len", "amt_window_len")
            if c in out.columns]
    out = out[keep]
    n = int(days)
    out = out.tail(n) if n > 0 else out.iloc[0:0]
    return out.reset_index(drop=True)


def compute_signal(history: pd.DataFrame | None, as_of: str | None = None) -> dict:
    """主入口：返回可直接落盘 signal.json / 进推送的 dict。

    status:
      ok            有结论（picks 为空 = 今日无符合行业，这是有效结论）
      insufficient  归档交易日太少，不出结论
      no_data       无归档或指定日期不在归档里
    """
    if history is None or history.empty:
        return {"status": "no_data", "reason": "无历史归档（数据尚未生成）"}

    valid_dates = section_valid_dates(history)
    if not valid_dates:
        return {"status": "no_data", "reason": "归档中没有截面完整（行业数达标）的交易日"}

    if as_of is None:
        as_of = valid_dates[-1]
    if as_of not in valid_dates:
        return {"status": "no_data",
                "reason": f"指定日期 {as_of} 不在归档的完整截面里"
                          f"（最近完整截面 {valid_dates[-1]}）"}

    days_available = valid_dates.index(as_of) + 1
    if days_available < MIN_TRADE_DAYS:
        return {"status": "insufficient",
                "reason": f"归档仅 {days_available} 个完整截面交易日，"
                          f"少于出结论所需 {MIN_TRADE_DAYS} 日",
                "date": as_of, "trade_days_available": days_available}

    d = _with_shares(history[history["date"].astype(str).isin(valid_dates)])
    if d.empty:
        return {"status": "no_data", "reason": "完整截面内没有同时具备市值与成交额的行业"}

    d = d.sort_values(["code", "date"])
    rows: list[dict] = []
    for code, g in d.groupby("code", sort=False):
        last = g.iloc[-1]
        if str(last["date"]) != as_of:
            continue  # 该行业当日无数据（分类调整/停更），不拿旧值冒充今日
        mv_win = g["mv_share"].tail(MV_WINDOW).dropna()
        amt_win = g["amt_share"].tail(AMT_WINDOW).dropna()
        mv_pct = percentile_rank(mv_win, mv_win.iloc[-1] if not mv_win.empty else None)
        amt_pct = percentile_rank(amt_win, amt_win.iloc[-1] if not amt_win.empty else None)
        if mv_pct is None or amt_pct is None:
            continue
        l1 = last.get("l1_name")
        rows.append({
            "code": str(code),
            "name": str(last.get("name") or code),
            "l1_name": None if l1 is None or pd.isna(l1) else str(l1),
            "mv_share": round(float(last["mv_share"]), 4),
            "mv_pct": round(float(mv_pct), 1),
            "amt_share": round(float(last["amt_share"]), 4),
            "amt_pct": round(float(amt_pct), 1),
            "pct_chg": None if pd.isna(last.get("pct_chg")) else round(float(last["pct_chg"]), 2),
            "amount_yi": None if pd.isna(last.get("amount_yi")) else round(float(last["amount_yi"]), 2),
            "float_mv_yi": None if pd.isna(last.get("float_mv_yi")) else round(float(last["float_mv_yi"]), 2),
            "mv_window_len": int(len(mv_win)),
            "amt_window_len": int(len(amt_win)),
        })

    if not rows:
        return {"status": "no_data", "reason": f"{as_of} 没有任何可用行业"}

    universe = pd.DataFrame(rows)
    # 命中规则与排序都走唯一真源：展示页调的是同两个函数，
    # 因此「表里标红的」与「结论里命中的」不可能不一致。
    thresholds = {"mv_pct_max": MV_PCT_MAX, "amt_pct_min": AMT_PCT_MIN}
    hits = sort_by_priority(universe[hit_mask(universe, thresholds)])

    picks = hits.head(MAX_PICKS).to_dict("records")
    as_of_day = d[d["date"].astype(str) == as_of]
    # 全量行业也按 code 排序：落盘 JSON 的内容必须与其键序无关地可复现。
    universe = universe.sort_values("code").reset_index(drop=True)

    return {
        "status": "ok",
        "date": as_of,
        "source": "tushare_sw2021_l2",
        "industry_level": "L2",
        "industry_count": int(len(universe)),
        "trade_days_available": days_available,
        "first_date": valid_dates[0],
        "mv_window": MV_WINDOW,
        "amt_window": AMT_WINDOW,
        "mv_window_actual": int(universe["mv_window_len"].max()),
        "amt_window_actual": int(universe["amt_window_len"].max()),
        "thresholds": {**thresholds, "max_picks": MAX_PICKS},
        "universe_mv_total_yi": round(float(as_of_day["float_mv_yi"].sum()), 2),
        "universe_amt_total_yi": round(float(as_of_day["amount_yi"].sum()), 2),
        "hits_count": int(len(hits)),
        "picks": picks,
        "picks_count": int(len(picks)),
        # 全量行业两个分位：供探针核对与将来做页面，不参与推送文案。
        "industries": universe.drop(columns=["mv_window_len", "amt_window_len"])
                              .to_dict("records"),
    }


def _rnd(v, nd: int):
    """四舍五入；NaN/None → None。落 JSON 前必须过这一道。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return round(f, nd)


def _mean(xs: list) -> float | None:
    return None if not xs else round(sum(xs) / len(xs), 2)


def _median(xs: list) -> float | None:
    return None if not xs else round(float(statistics.median(xs)), 2)


def _win_rate(xs: list) -> float | None:
    """上涨占比（百分数）。样本为空返回 None——不返回 0，那会被读成「全都没涨」。"""
    return None if not xs else round(sum(1 for x in xs if x > 0) / len(xs) * 100.0, 1)


def history_hits(history: pd.DataFrame | None,
                 forward_days: tuple[int, ...] = FWD_DAYS,
                 max_picks: int = MAX_PICKS) -> dict:
    """历史命中回算：每个交易日会选出谁、之后 N 个交易日的涨跌如何。

    这是**描述性统计**，不是策略回测：没有交易成本、没有仓位、没有择时，
    只回答「这套筛选规则在过去那些天分别指向了什么，其后发生了什么」。

    只统计分位窗口满额的交易日（见模块 docstring 第 2 条）。归档开头那段
    窗口不满的日子被跳过并如实计入 `skipped_short_window`，不混进统计。
    `empty_days`（当日 0 命中）是有效样本，计入但不产生记录——把它当成
    "没有数据"删掉，会让命中率的样本基数凭空变大。

    返回：
      status / reason / forward_days / mv_window / amt_window / thresholds
      first_date / last_date / trade_days_total / trade_days_scanned
      skipped_short_window / calendar_gaps / hit_days / empty_days / total_hits
      stats: {N: {n, mean, median, win_rate, n_paired, mean_bench,
                  mean_excess, excess_win_rate}}
      bench_by_date: {date: {N: 全行业等权平均涨跌}}（同期基准，按日存一份）
      records: [{date, code, name, l1_name, mv_share, mv_pct, amt_share,
                 amt_pct, pct_chg, close, fwd: {N: 涨跌}, excess: {N: 超额}}]
    """
    fwd_list = sorted({int(n) for n in forward_days if int(n) > 0})
    thresholds = {"mv_pct_max": MV_PCT_MAX, "amt_pct_min": AMT_PCT_MIN}
    tpl = {
        "forward_days": fwd_list,
        "mv_window": MV_WINDOW,
        "amt_window": AMT_WINDOW,
        "thresholds": {**thresholds, "max_picks": int(max_picks)},
    }

    if history is None or history.empty:
        return {**tpl, "status": "no_data", "reason": "无历史归档（数据尚未生成）"}
    valid_dates = section_valid_dates(history)
    if not valid_dates:
        return {**tpl, "status": "no_data",
                "reason": "归档中没有截面完整（行业数达标）的交易日"}
    d = _with_shares(history[history["date"].astype(str).isin(valid_dates)])
    if d.empty:
        return {**tpl, "status": "no_data",
                "reason": "完整截面内没有同时具备市值与成交额的行业"}
    if "close" not in d.columns or pd.to_numeric(d["close"], errors="coerce").notna().sum() == 0:
        return {**tpl, "status": "no_data",
                "reason": "归档缺收盘价（close），无法计算命中之后的涨跌",
                "trade_days_total": len(valid_dates)}

    d = d.sort_values(["code", "date"])
    parts = []
    for _code, g in d.groupby("code", sort=False):
        mv_pcts, mv_lens = _rolling_pcts(g["mv_share"], MV_WINDOW)
        amt_pcts, amt_lens = _rolling_pcts(g["amt_share"], AMT_WINDOW)
        parts.append(g.assign(mv_pct=mv_pcts, amt_pct=amt_pcts,
                              mv_window_len=mv_lens, amt_window_len=amt_lens))
    long = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    long = long[long["mv_pct"].notna() & long["amt_pct"].notna()]
    if long.empty:
        return {**tpl, "status": "no_data", "reason": "没有任何行业算出分位"}

    trade_days_total = len(valid_dates)
    full = long[(long["mv_window_len"] >= MV_WINDOW)
                & (long["amt_window_len"] >= AMT_WINDOW)]
    scanned = sorted({str(x) for x in full["date"]})
    skipped = sorted({str(x) for x in long["date"]} - set(scanned))

    if not scanned or len(scanned) < MIN_TRADE_DAYS:
        return {**tpl, "status": "insufficient",
                "trade_days_total": trade_days_total,
                "trade_days_scanned": len(scanned),
                "skipped_short_window": len(skipped),
                "reason": f"分位窗口满额的交易日仅 {len(scanned)} 天"
                          f"（需 {MV_WINDOW} 日市值占比窗口 + {AMT_WINDOW} 日热度窗口），"
                          f"少于出统计所需 {MIN_TRADE_DAYS} 天"}

    # 时间轴：从第一个满窗日到归档末尾的可用交易日序列。
    # 「之后第 N 个交易日」按这条轴取——用的是全市场交易日，而不是各行业
    # 自己的序列（某行业中途停更时，按自己的序列会跳到很晚的日期，失真）。
    axis = [x for x in valid_dates if x >= scanned[0]]
    scanned_set = set(scanned)
    calendar_gaps = [x for x in axis if x not in scanned_set]

    close_panel = long.groupby(["date", "code"])["close"].last().unstack()
    close_panel.columns = [str(c) for c in close_panel.columns]
    close_panel = close_panel.reindex(index=axis)
    if len(close_panel) != len(axis):  # reindex 应恒等；不等说明索引有重复
        return {**tpl, "status": "no_data",
                "reason": f"行情面板重采样异常：交易日轴 {len(axis)} 行 → 面板 {len(close_panel)} 行"}

    fwd_map: dict[int, pd.DataFrame] = {}
    bench_map: dict[int, pd.Series] = {}
    for n in fwd_list:
        ratio = close_panel.shift(-n) / close_panel - 1.0
        fwd_map[n] = ratio * 100.0
        # 同期基准＝当日全体参与行业的等权平均涨跌（跳过缺失行业，不补 0）
        bench_map[n] = ratio.mean(axis=1, skipna=True) * 100.0

    hit_rows = full[hit_mask(full, thresholds)]
    records: list[dict] = []
    for day, g in hit_rows.groupby("date", sort=True):
        day = str(day)
        for _, r in sort_by_priority(g).head(int(max_picks)).iterrows():
            code = str(r["code"])
            rec = {
                "date": day,
                "code": code,
                "name": str(r.get("name") or code),
                "l1_name": None if pd.isna(r.get("l1_name", None)) else str(r["l1_name"]),
                "mv_share": _rnd(r.get("mv_share"), 4),
                "mv_pct": _rnd(r.get("mv_pct"), 1),
                "amt_share": _rnd(r.get("amt_share"), 4),
                "amt_pct": _rnd(r.get("amt_pct"), 1),
                "pct_chg": _rnd(r.get("pct_chg"), 2),
                "close": _rnd(r.get("close"), 2),
                "fwd": {}, "excess": {},
            }
            for n in fwd_list:
                fv = (None if day not in fwd_map[n].index or code not in fwd_map[n].columns
                      else _rnd(fwd_map[n].at[day, code], 2))
                bv = _rnd(bench_map[n].get(day, None), 2)
                rec["fwd"][str(n)] = fv
                rec["excess"][str(n)] = None if fv is None or bv is None else round(fv - bv, 2)
            records.append(rec)

    # 同期基准按日存一份即可：同一天同一 N 的所有命中行业共用同一个基准值，
    # 逐条重复存既浪费体积，也会给人「每条记录各有各的基准」的错觉。
    bench_by_date: dict[str, dict] = {}
    for day in scanned:
        row = {str(n): _rnd(bench_map[n].get(day, None), 2) for n in fwd_list}
        row = {k: v for k, v in row.items() if v is not None}
        if row:
            bench_by_date[day] = row

    stats: dict[str, dict] = {}
    for n in fwd_list:
        key = str(n)
        have_fwd = [rec["fwd"][key] for rec in records if rec["fwd"][key] is not None]
        paired = [(rec["fwd"][key], bench_by_date.get(rec["date"], {}).get(key))
                  for rec in records]
        paired = [(f, b) for f, b in paired if f is not None and b is not None]
        exc = [f - b for f, b in paired]
        stats[key] = {
            "n": len(have_fwd),
            "mean": _mean(have_fwd),
            "median": _median(have_fwd),
            "win_rate": _win_rate(have_fwd),
            "n_paired": len(paired),
            "mean_bench": _mean([b for _, b in paired]),
            "mean_excess": _mean(exc),
            "excess_win_rate": _win_rate(exc),
        }

    hit_days = len({rec["date"] for rec in records})
    return {
        **tpl,
        "status": "ok",
        "first_date": scanned[0],
        "last_date": scanned[-1],
        "trade_days_total": trade_days_total,
        "trade_days_scanned": len(scanned),
        "skipped_short_window": len(skipped),
        "calendar_gaps": len(calendar_gaps),
        "industry_count": int(long["code"].nunique()),
        "hit_days": hit_days,
        "empty_days": len(scanned) - hit_days,
        "total_hits": len(records),
        "stats": stats,
        "bench_by_date": bench_by_date,
        "records": records,
    }


def signal_markdown(sig: dict | None) -> tuple[str, str | None]:
    """把 signal 渲染成推送段落。返回 (markdown, 缺失原因)。

    规矩与 digest 其余段落一致：**没数据就整段不出现**，并说明原因；
    命中 0 个是有效结论，必须如实播报（不能因为"不好看"就不说话，
    那会让读者分不清「今天没有」和「这一段坏了」）。
    """
    if not sig:
        return "", "行业信号未生成"
    st = sig.get("status")
    if st == "no_data":
        return "", f"行业信号不可用（{sig.get('reason', '未知')}）"
    if st == "insufficient":
        return "", f"行业信号样本不足（{sig.get('reason', '未知')}）"

    date = sig.get("date", "")
    th = sig.get("thresholds") or {}
    mv_w = sig.get("mv_window_actual") or sig.get("mv_window")
    amt_w = sig.get("amt_window_actual") or sig.get("amt_window")
    lines = [
        f"### 🔥 低市值占比 + 高资金热度行业（{date}）",
        "",
        f"口径：申万二级行业 {sig.get('industry_count', '—')} 个。"
        f"市值占比取近 {mv_w} 日分位（低于 {th.get('mv_pct_max')} 视为低位），"
        f"成交额占比取近 {amt_w} 日分位（高于 {th.get('amt_pct_min')} 视为高热度）。",
        "",
    ]
    picks = sig.get("picks") or []
    if not picks:
        lines.append(f"- 今日无同时满足两条件的行业（全 {sig.get('industry_count', '—')} 个行业已逐一核对）。")
    else:
        for p in picks:
            l1 = f"（{p['l1_name']}）" if p.get("l1_name") else ""
            chg = "—" if p.get("pct_chg") is None else f"{p['pct_chg']:+.2f}%"
            lines.append(
                f"- **{p['name']}**{l1}：市值占比 {p['mv_share']:.2f}%"
                f"（{mv_w} 日分位 **{p['mv_pct']:.1f}**）"
                f"，成交额占比 {p['amt_share']:.2f}%（{amt_w} 日分位 **{p['amt_pct']:.1f}**）"
                f"，当日 {chg}"
            )
        if sig.get("hits_count", 0) > len(picks):
            lines.append(f"- （另有 {sig['hits_count'] - len(picks)} 个行业满足条件，"
                         f"按热度分位排序未列入）")
    lines += ["",
              "_该筛选只描述「相对自身历史缩水 + 成交占比重回高位」这一状态，"
              "不含方向判断，不构成投资建议。_"]
    return "\n".join(lines), None
