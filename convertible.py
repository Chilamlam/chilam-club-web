"""Convertible-bond classification and post-close pattern statistics.

The module is deliberately pure: it takes already archived daily bars
(open/high/low/close/pct_chg/amount) plus the bond master table and returns
everything the page renders.  No network access happens here, so the rules can
be unit-tested against hand-built fixtures and the archive can be rebuilt
offline.

Classification follows the three complete categories used by the operator:

    A 债强于股   stock does not participate, the bond's move is bond-driven
    B 股强于债   stock is strong but the bond has not been activated
    C 股债联动   bond follows the stock with a comparable move
    N 中性       neither side diverges enough to call

Every bond-day gets exactly one of the four labels, so the partition is
complete.  Thresholds are explicit constants because the page prints them.
"""
from __future__ import annotations

import math

import pandas as pd

# --- Classification thresholds (percentage points) -------------------------
# C is decided first: both legs must move together with a comparable size.
C_STOCK_MIN = 1.5      # |stock move| at least this much
C_BOND_MIN = 0.8       # |bond move| at least this much
C_BOND_RATIO = 0.30    # and at least this share of the stock move
# A: the bond is at least this much stronger than the stock.
A_GAP = 1.5
# B: the stock is at least this much stronger and is up on the day.
B_GAP = 2.0
B_STOCK_MIN = 2.0

CLASS_LABELS = {
    "A": "A 债强于股（不联动）",
    "B": "B 股强于债（不联动）",
    "C": "C 股债联动",
    "N": "N 中性/无明显分化",
}

# --- Rhythm stages for the C (linked) family -------------------------------
# Order matters: the first matching rule wins.
STAGE_RULES = {
    "退潮": "今日跌幅≥5%，且前5日累计涨幅≥8%",
    "分歧": "振幅≥8%，收盘位于当日振幅下40%，且前5日累计涨幅≥5%",
    "高潮": "今日涨幅≥15%",
    "加速": "今日涨幅≥5%，前5日累计≥8%，且收盘距20日高点≤1%",
    "回流": "今日涨幅≥3%，昨日跌幅≥3%，且前5日累计≥5%",
    "启动": "今日涨幅≥5%，前5日累计≤3%，且成交额≥5日均量1.5倍",
}
STAGE_ORDER = ["退潮", "分歧", "高潮", "加速", "回流", "启动"]

LIQUID_AMOUNT = 30_000.0     # 千元 -> 3000 万元
AMOUNT_YI = 100_000.0        # 千元 -> 亿元
BALANCE_YUAN_PER_YI = 100_000_000.0  # cb_basic.remain_size 的源单位是元
LIQUID_YI = LIQUID_AMOUNT / AMOUNT_YI   # 亿元口径的同一阈值
DIP_PCT = 3.0                # 盘中回踩幅度：当日开盘价下方 3% 挂单


def num(value, default=None):
    """Finite float or default.  bool is not a number here."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def pct(a, b):
    """(a / b - 1) * 100 with every degenerate case returning None."""
    a, b = num(a), num(b)
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1.0) * 100.0


def balance_yi(value) -> float | None:
    """Convert the documented cb_basic balance unit (yuan) to 亿元."""
    value = num(value)
    return value / BALANCE_YUAN_PER_YI if value is not None and value >= 0 else None


def classify(bond_pct, stock_pct) -> str:
    """Return A/B/C/N for one bond-day.  Same direction and size -> C."""
    b, s = num(bond_pct), num(stock_pct)
    if b is None or s is None:
        return "N"
    if s != 0 and b * s > 0 and abs(s) >= C_STOCK_MIN:
        need = max(C_BOND_MIN, C_BOND_RATIO * abs(s))
        if abs(b) >= need:
            return "C"
    if b - s >= A_GAP:
        return "A"
    if s - b >= B_GAP and s >= B_STOCK_MIN:
        return "B"
    return "N"


def sub_tag(*, list_date, trade_date, premium, amount_ratio) -> str:
    """Observable sub-label inside A.  Narrative only, never a class change."""
    tags = []
    days_listed = _days_between(list_date, trade_date)
    if days_listed is not None and 0 <= days_listed <= 60:
        tags.append("次新")
    if premium is not None and premium >= 30:
        tags.append("高溢价")
    if amount_ratio is not None and amount_ratio >= 1.5:
        tags.append("放量")
    return "+".join(tags)


def _days_between(start, end) -> int | None:
    try:
        a = pd.Timestamp(str(start))
        b = pd.Timestamp(str(end))
    except (TypeError, ValueError):
        return None
    if pd.isna(a) or pd.isna(b):
        return None
    return int((b - a).days)


def _stage(hist: pd.DataFrame, index: int) -> str:
    """Rule-based rhythm tag for one row of a single bond's history."""
    row = hist.iloc[index]
    pct_today = num(row.get("pct_chg"))
    if pct_today is None:
        return ""
    window = hist.iloc[max(0, index - 20): index + 1]
    close = num(row.get("close"))
    close_5 = num(hist.iloc[index - 5].get("close")) if index >= 5 else None
    cum5 = pct(close, close_5)
    prev_pct = num(hist.iloc[index - 1].get("pct_chg")) if index >= 1 else None
    high, low, pre = num(row.get("high")), num(row.get("low")), num(row.get("pre_close"))
    amplitude = pct(high, low) if high and low else None
    close_pos = None
    if high is not None and low is not None and close is not None and high > low:
        close_pos = (close - low) / (high - low)
    high20 = max((v for v in (num(x) for x in window["close"]) if v is not None), default=None)
    near_high = close is not None and high20 and high20 > 0 and close >= high20 * 0.99
    amounts = [num(x) for x in hist.iloc[max(0, index - 5): index]["amount"]]
    amounts = [v for v in amounts if v is not None and v > 0]
    amount, amount_ratio = num(row.get("amount")), None
    if amount is not None and amounts:
        amount_ratio = amount / (sum(amounts) / len(amounts))

    if pct_today <= -5 and cum5 is not None and cum5 >= 8:
        return "退潮"
    if (amplitude is not None and amplitude >= 8 and close_pos is not None and close_pos <= 0.4
            and cum5 is not None and cum5 >= 5):
        return "分歧"
    if pct_today >= 15:
        return "高潮"
    if pct_today >= 5 and cum5 is not None and cum5 >= 8 and near_high:
        return "加速"
    if (pct_today >= 3 and prev_pct is not None and prev_pct <= -3
            and cum5 is not None and cum5 >= 5):
        return "回流"
    if (pct_today >= 5 and cum5 is not None and cum5 <= 3
            and amount_ratio is not None and amount_ratio >= 1.5):
        return "启动"
    return ""


def _agg(values) -> dict:
    clean = [v for v in (num(x) for x in values) if v is not None]
    if not clean:
        return {"n": 0, "mean": None, "median": None, "win": None}
    wins = sum(1 for v in clean if v > 0)
    ordered = sorted(clean)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"n": len(clean), "mean": sum(clean) / len(clean),
            "median": median, "win": wins / len(clean) * 100.0}


def _outcomes(today: pd.Series | None, nxt: pd.Series | None) -> dict:
    if today is None or nxt is None:
        return {}
    close = num(today.get("close"))
    open_ = num(nxt.get("open"))
    low, close_next = num(nxt.get("low")), num(nxt.get("close"))
    # 盘中回踩：以“当日开盘价”为已知参照下调 DIP_PCT%，只有最低价真的触及时才算
    # 成交样本，成交价就是挂单价，收盘卖出。 这样不会把“事后才知道的最低价”
    # 当成入场价，样本才有意义。
    trigger = open_ * (1.0 - DIP_PCT / 100.0) if open_ is not None else None
    touched = (trigger is not None and low is not None and low <= trigger)
    return {
        "overnight": pct(nxt.get("open"), close),
        "open_to_close": pct(nxt.get("close"), nxt.get("open")),
        "low_to_close": pct(nxt.get("close"), nxt.get("low")),
        "dip_to_close": pct(close_next, trigger) if touched else None,
        "dip_touch": touched if trigger is not None and low is not None else None,
        "close_to_close": pct(nxt.get("close"), close),
        "next_high": pct(nxt.get("high"), close),
        "next_pct": num(nxt.get("pct_chg")),
    }


def _corr(hist: pd.DataFrame, stock_hist: pd.DataFrame) -> float | None:
    """20-day return correlation; None unless enough overlapping observations."""
    if hist is None or stock_hist is None:
        return None
    merged = hist[["date", "pct_chg"]].merge(
        stock_hist[["date", "pct_chg"]], on="date", suffixes=("_b", "_s"))
    merged = merged.dropna().tail(20)
    if len(merged) < 10:
        return None
    if merged["pct_chg_b"].std() == 0 or merged["pct_chg_s"].std() == 0:
        return None
    value = merged["pct_chg_b"].corr(merged["pct_chg_s"])
    return num(value)


def build_snapshot(bond: pd.DataFrame, stock: pd.DataFrame, basic: dict,
                   trade_date: str, *, now=None, archive_days: int = 0,
                   stat_days: int = 10) -> dict:
    """Assemble the payload rendered by the page."""
    dates = sorted(set(bond["date"].astype(str)))
    if trade_date not in dates:
        raise ValueError(f"归档中没有 {trade_date} 的转债行情")
    by_date_bond = {d: g.set_index("ts_code") for d, g in bond.groupby("date")}
    by_date_stock = {d: g.set_index("ts_code") for d, g in stock.groupby("date")}
    histories = {code: g.sort_values("date").reset_index(drop=True)
                 for code, g in bond.groupby("ts_code")}
    stock_histories = {code: g.sort_values("date").reset_index(drop=True)
                       for code, g in stock.groupby("ts_code")}

    today = by_date_bond[trade_date]
    today_stock = by_date_stock.get(trade_date)
    rows = []
    for code, row in today.iterrows():
        info = basic.get(code) or {}
        stock_code = str(info.get("stk_code") or "")
        stock_row = today_stock.loc[stock_code] if (
            today_stock is not None and stock_code in today_stock.index) else None
        stock_pct = num(stock_row.get("pct_chg")) if stock_row is not None else None
        bond_pct = num(row.get("pct_chg"))
        hist = histories.get(code)
        index = len(hist) - 1 if hist is not None else -1
        stage = _stage(hist, index) if hist is not None and index >= 0 else ""
        amounts = [num(x) for x in hist.iloc[-6:-1]["amount"]] if hist is not None and len(hist) >= 6 else []
        amounts = [v for v in amounts if v is not None and v > 0]
        amount = num(row.get("amount"))
        amount_ratio = amount / (sum(amounts) / len(amounts)) if amount and amounts else None
        conv_price = num(info.get("conv_price"))
        stock_close = num(stock_row.get("close")) if stock_row is not None else None
        conv_value = 100.0 / conv_price * stock_close if conv_price and conv_price > 0 and stock_close else None
        premium = pct(row.get("close"), conv_value) if conv_value else None
        close = num(row.get("close"))
        corr = _corr(hist, stock_histories.get(stock_code)) if stock_code else None
        label = classify(bond_pct, stock_pct)
        rows.append({
            "code": code,
            "name": str(info.get("bond_short_name") or code),
            "stock_code": stock_code,
            "stock_name": str(info.get("stk_short_name") or stock_code),
            "stock_pct": stock_pct,
            "close": close,
            "pct_chg": bond_pct,
            "amount_yi": round(amount / AMOUNT_YI, 2) if amount else None,
            "premium": premium,
            "double_low": (close + premium) if close is not None and premium is not None else None,
            "remain_size": num(info.get("remain_size")),
            "list_date": info.get("list_date"),
            "corr20": corr,
            "class": label,
            "stage": stage if label == "C" else "",
            "sub_tag": sub_tag(list_date=info.get("list_date"), trade_date=trade_date,
                               premium=premium, amount_ratio=amount_ratio) if label == "A" else "",
            "amount_ratio": amount_ratio,
        })

    frame = pd.DataFrame(rows)
    classes = {}
    for key in ("A", "B", "C", "N"):
        part = frame[frame["class"] == key].copy()
        part = part.sort_values(["pct_chg", "amount_yi"], ascending=False)
        classes[key] = {"label": CLASS_LABELS[key], "count": int(len(part)),
                        "rows": _records(part)}

    market = _market(frame)
    stock_map = {code: str(info.get("stk_code") or "")
                 for code, info in basic.items() if isinstance(info, dict)}
    patterns = _patterns(bond, stock, stock_map=stock_map, stat_days=stat_days)
    return {
        "date": trade_date,
        "generated_at": now.isoformat() if now is not None else "",
        "status": "ok",
        "source": "Tushare cb_daily / daily / cb_basic",
        "archive_days": archive_days or len(dates),
        "market": market,
        "classes": classes,
        "patterns": patterns,
        "watch": _watch(frame, patterns, by_date_bond, dates),
        "notes": [
            "分类只使用当日涨跌幅，阈值全部写在页面上；不预测涨跌。",
            "溢价率按最新转股价与当日正股收盘计算；转股价下修后如未同步，会短暂失真。",
            "收益统计只用「昨收→今开」「今开→今收」「回踩挂单→今收」三种可执行口径；"
            "「最低价→收盘」只是理论天花板，不作胜率统计。均未扣费用与滑点。",
        ],
    }


def _records(frame: pd.DataFrame) -> list[dict]:
    out = []
    for row in frame.to_dict("records"):
        out.append({k: (None if (isinstance(v, float) and not math.isfinite(v)) else v)
                    for k, v in row.items()})
    return out


def _market(frame: pd.DataFrame) -> dict:
    pcts = [v for v in (num(x) for x in frame["pct_chg"]) if v is not None]
    amounts = [v for v in (num(x) for x in frame["amount_yi"]) if v is not None]
    dls = [v for v in (num(x) for x in frame["double_low"]) if v is not None]
    premiums = [v for v in (num(x) for x in frame["premium"]) if v is not None]
    return {
        "total": int(len(frame)),
        "up": sum(1 for v in pcts if v > 0),
        "down": sum(1 for v in pcts if v < 0),
        "flat": sum(1 for v in pcts if v == 0),
        "median_pct": _agg(pcts)["median"],
        "avg_double_low": sum(dls) / len(dls) if dls else None,
        "avg_premium": sum(premiums) / len(premiums) if premiums else None,
        "amount_yi": sum(amounts) if amounts else None,
        "liquid": sum(1 for v in amounts if v >= LIQUID_YI),
        "liquid_threshold_yi": LIQUID_YI,
    }


def _patterns(bond: pd.DataFrame, stock: pd.DataFrame, *, stock_map: dict,
              stat_days: int = 10) -> dict:
    dates = sorted(set(bond["date"].astype(str)))
    by_date_bond = {d: g.set_index("ts_code") for d, g in bond.groupby("date")}
    by_date_stock = {d: g.set_index("ts_code") for d, g in stock.groupby("date")}
    histories = {code: g.sort_values("date").reset_index(drop=True)
                 for code, g in bond.groupby("ts_code")}
    pos_by_code = {code: {str(d): i for i, d in enumerate(hist["date"])}
                   for code, hist in histories.items()}
    rows, swap_rows, b_rows, stage_rows = [], [], [], []

    for i in range(len(dates) - 1):
        date, nxt = dates[i], dates[i + 1]
        today, tomorrow = by_date_bond[date], by_date_bond[nxt]
        stock_today, stock_tomorrow = by_date_stock.get(date), by_date_stock.get(nxt)
        liquid = today[(today["amount"].fillna(0) >= LIQUID_AMOUNT) & today["pct_chg"].notna()]
        leaders = liquid.sort_values("pct_chg", ascending=False).head(2)
        for rank, (code, row) in enumerate(leaders.iterrows(), start=1):
            outcome = _outcomes(row, tomorrow.loc[code] if code in tomorrow.index else None)
            rows.append({"date": date, "next_date": nxt, "rank": rank, "code": code,
                         "pct_chg": num(row.get("pct_chg")),
                         "amount_yi": round(num(row.get("amount"), 0) / AMOUNT_YI, 2),
                         **outcome})
        if len(leaders) == 2 and stock_tomorrow is not None:
            codes = list(leaders.index)
            nxt1 = num(tomorrow.loc[codes[0], "pct_chg"]) if codes[0] in tomorrow.index else None
            nxt2 = num(tomorrow.loc[codes[1], "pct_chg"]) if codes[1] in tomorrow.index else None
            if nxt1 is not None and nxt2 is not None:
                swap_rows.append({"date": date, "next_date": nxt,
                                  "swapped": bool(nxt2 > nxt1),
                                  "top1_next": nxt1, "top2_next": nxt2})

        # B family: stock strong and still strong at the close, bond lagging.
        if stock_today is not None and stock_tomorrow is not None:
            for code, row in today.iterrows():
                stock_code = stock_map.get(code, "")
                if not stock_code or stock_code not in stock_today.index:
                    continue
                s_now = stock_today.loc[stock_code]
                s_pct = num(s_now.get("pct_chg"))
                b_pct = num(row.get("pct_chg"))
                if s_pct is None or b_pct is None or s_pct < 3 or s_pct - b_pct < B_GAP:
                    continue
                high, low, close = num(s_now.get("high")), num(s_now.get("low")), num(s_now.get("close"))
                if high is None or low is None or close is None or high <= low:
                    continue
                if (close - low) / (high - low) < 0.75:
                    continue
                outcome = _outcomes(row, tomorrow.loc[code] if code in tomorrow.index else None)
                b_rows.append({"date": date, "next_date": nxt, "code": code,
                               "pct_chg": b_pct, "stock_pct": s_pct, **outcome})

        # C family rhythm stages, computed on each bond's own history.  Only the
        # linked family is scored: the theme-play cycle belongs to C by definition.
        for code, hist in histories.items():
            position = pos_by_code.get(code, {}).get(date)
            if position is None or position < 5:
                continue
            stock_code = stock_map.get(code, "")
            s_today = stock_today.loc[stock_code] if (
                stock_today is not None and stock_code in stock_today.index) else None
            if classify(hist.iloc[position].get("pct_chg"),
                        s_today.get("pct_chg") if s_today is not None else None) != "C":
                continue
            stage = _stage(hist, position)
            if not stage:
                continue
            row = hist.iloc[position]
            outcome = _outcomes(row, tomorrow.loc[code] if code in tomorrow.index else None)
            stage_rows.append({"date": date, "next_date": nxt, "code": code,
                               "stage": stage, "pct_chg": num(row.get("pct_chg")), **outcome})

    recent_dates = sorted({r["date"] for r in rows})[-stat_days:]
    rows = [r for r in rows if r["date"] in recent_dates]
    b_rows = [r for r in b_rows if r["date"] in recent_dates]
    stage_rows = [r for r in stage_rows if r["date"] in recent_dates]
    swap_rows = [r for r in swap_rows if r["date"] in recent_dates]

    aggregate = {}
    for rank in (1, 2):
        subset = [r for r in rows if r["rank"] == rank]
        aggregate[f"rank{rank}"] = {
            "n": len(subset),
            "overnight": _agg([r.get("overnight") for r in subset]),
            "open_to_close": _agg([r.get("open_to_close") for r in subset]),
            "low_to_close": _agg([r.get("low_to_close") for r in subset]),
            "dip": {**_agg([r.get("dip_to_close") for r in subset]), "total": len(subset)},
            "close_to_close": _agg([r.get("close_to_close") for r in subset]),
            "next_high": _agg([r.get("next_high") for r in subset]),
        }
    aggregate["b_overnight"] = {
        "n": len(b_rows),
        "overnight": _agg([r.get("overnight") for r in b_rows]),
        "open_to_close": _agg([r.get("open_to_close") for r in b_rows]),
        "close_to_close": _agg([r.get("close_to_close") for r in b_rows]),
    }
    stages = {}
    for stage in STAGE_ORDER:
        subset = [r for r in stage_rows if r["stage"] == stage]
        stages[stage] = {
            "n": len(subset),
            "next_pct": _agg([r.get("next_pct") for r in subset]),
            "overnight": _agg([r.get("overnight") for r in subset]),
            "open_to_close": _agg([r.get("open_to_close") for r in subset]),
            "low_to_close": _agg([r.get("low_to_close") for r in subset]),
        }
    swaps = sum(1 for r in swap_rows if r["swapped"])
    return {
        "window": {"days": len(recent_dates), "from": recent_dates[0] if recent_dates else None,
                   "to": recent_dates[-1] if recent_dates else None},
        "aggregate": aggregate,
        "rows": sorted(rows, key=lambda r: (r["date"], r["rank"]), reverse=True),
        "swap": {"n": len(swap_rows), "rate": swaps / len(swap_rows) * 100.0 if swap_rows else None},
        "b_rows": sorted(b_rows, key=lambda r: r["date"], reverse=True),
        "stages": stages,
        "stage_rows": sorted(stage_rows, key=lambda r: (r["date"], r["code"]), reverse=True),
        "definitions": {
            "overnight": "昨收 → 今日开盘",
            "open_to_close": "今日开盘 → 今日收盘（开盘低吸）",
            "low_to_close": "今日最低 → 今日收盘：理论天花板。收盘必然高于最低价，所以恒为正，只表示当天振幅上限，不作收益统计",
            "dip": (f"盘中回踩：以今日开盘价为参照，下方 {DIP_PCT:g}% 挂单；当日最低价触及该价才算成交，"
                    f"成交后按今日收盘计；未触及的日子不计入样本"),
            "close_to_close": "昨收 → 今日收盘",
            "next_high": "昨收 → 今日最高（隔夜最高浮盈）",
            "swap": "昨日第二名今日涨幅超过昨日第一名，记为一次互换",
            "stage_rules": STAGE_RULES,
            "stage_order": STAGE_ORDER,
            "liquid": f"日成交额≥{LIQUID_AMOUNT / 10:g}万元（{LIQUID_YI:g}亿）",
        },
    }


def _watch(frame: pd.DataFrame, patterns: dict, by_date_bond: dict, dates: list[str]) -> dict:
    """Observation lists, never framed as buy advice."""
    liquid = frame[(frame["amount_yi"].fillna(0) >= LIQUID_YI) & frame["pct_chg"].notna()]
    leaders = liquid.sort_values("pct_chg", ascending=False).head(2)
    indexed = frame.set_index("code")
    out = {"today_leaders": _records(leaders), "yesterday": [], "b_candidates": []}
    if len(dates) >= 2:
        prev, last = dates[-2], dates[-1]
        prev_frame = by_date_bond[prev]
        today_bars = by_date_bond[last]
        prev_liquid = prev_frame[(prev_frame["amount"].fillna(0) >= LIQUID_AMOUNT)
                                 & prev_frame["pct_chg"].notna()]
        for code, row in prev_liquid.sort_values("pct_chg", ascending=False).head(2).iterrows():
            if code not in indexed.index or code not in today_bars.index:
                continue
            today_row = indexed.loc[code]
            # 今日的真实分时口径（开/高/低/收）必须取自行情表，不能取分类快照。
            outcome = _outcomes(row, today_bars.loc[code])
            out["yesterday"].append({
                "code": code, "name": today_row.get("name"),
                "prev_pct": num(row.get("pct_chg")),
                "today_pct": num(today_row.get("pct_chg")),
                "overnight": outcome.get("overnight"),
                "open_to_close": outcome.get("open_to_close"),
                "dip_to_close": outcome.get("dip_to_close"),
                "dip_touch": outcome.get("dip_touch"),
            })
    b_part = frame[(frame["class"] == "B") & (frame["amount_yi"].fillna(0) >= LIQUID_YI)]
    b_sorted = b_part.sort_values("stock_pct", ascending=False)
    out["b_total"] = int(len(b_sorted))
    out["b_candidates"] = _records(b_sorted.head(8))
    return out
