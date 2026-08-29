"""
战绩回看计算层（纯 pandas/numpy，禁止 import streamlit / tushare / akshare）

设计原则（对标 TradingAgents-astock 的 performance 子命令）：
1. 榜单快照与价格快照分开归档，追加写入，永不覆盖历史。
2. 前瞻收益一律用「复权价」计算，基准为沪深300（000300.SH）。
3. 只统计超额收益 alpha，不统计绝对收益——绝对收益在牛市里人人都对。
4. 样本不足时显式返回 insufficient 状态，由展示层如实标注「基本是噪音」，
   绝不用小样本充当业绩证明。
5. 关键数据缺失就标 failed，不用旧值或猜测填空。

归档文件：
    data/scorecard/picks.csv   date,strategy,ts_code,name,rank,bucket,ref_close
    data/scorecard/prices.csv  date,ts_code,adj_close
    data/scorecard/performance.json  由 daily_scorecard.py 写出的汇总产物
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

# ================= 配置 =================
SC_DIR = os.path.join("data", "scorecard")
PICKS_PATH = os.path.join(SC_DIR, "picks.csv")
PRICES_PATH = os.path.join(SC_DIR, "prices.csv")
PERF_PATH = os.path.join(SC_DIR, "performance.json")

BENCHMARK = "000300.SH"          # 沪深300 作为超额收益基准
HORIZONS = (1, 5, 10)            # 前瞻交易日数
MIN_SAMPLE = 20                  # 样本下限，低于此值一律标 insufficient
MIN_SAMPLE_BUCKET = 15           # 分档区分度检验的每档样本下限

PICKS_COLS = ["date", "strategy", "ts_code", "name", "rank", "bucket", "ref_close"]
PRICES_COLS = ["date", "ts_code", "adj_close"]

STRATEGY_LABELS = {
    "rps": "RPS 强势股榜",
    "breakout": "阶段新高突破池",
    "etf": "强势 ETF 榜",
    "radar": "核心龙头雷达",
}


# ================= 归档读写 =================

def _ensure_dir() -> None:
    os.makedirs(SC_DIR, exist_ok=True)


def load_picks() -> pd.DataFrame:
    """读取历史榜单归档。文件不存在时返回带列名的空表。"""
    if not os.path.exists(PICKS_PATH):
        return pd.DataFrame(columns=PICKS_COLS)
    df = pd.read_csv(PICKS_PATH, dtype={"ts_code": str, "strategy": str, "date": str})
    for c in PICKS_COLS:
        if c not in df.columns:
            df[c] = np.nan
    df["ref_close"] = pd.to_numeric(df["ref_close"], errors="coerce").astype("float64")
    df["rank"] = pd.to_numeric(df["rank"], errors="coerce")
    return df[PICKS_COLS]


def load_prices() -> pd.DataFrame:
    """读取历史价格归档（仅含曾上榜标的 + 基准指数）。"""
    if not os.path.exists(PRICES_PATH):
        return pd.DataFrame(columns=PRICES_COLS)
    df = pd.read_csv(PRICES_PATH, dtype={"ts_code": str, "date": str})
    df["adj_close"] = pd.to_numeric(df["adj_close"], errors="coerce").astype("float64")
    return df[PRICES_COLS].dropna(subset=["adj_close"])


def append_picks(new_rows: pd.DataFrame) -> int:
    """追加榜单快照，按 (date, strategy, ts_code) 去重，同键保留新值。返回归档总行数。"""
    _ensure_dir()
    if new_rows is None or new_rows.empty:
        return len(load_picks())
    new_rows = new_rows.copy()
    for c in PICKS_COLS:
        if c not in new_rows.columns:
            new_rows[c] = np.nan
    new_rows = new_rows[PICKS_COLS]
    merged = pd.concat([load_picks(), new_rows], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date", "strategy", "ts_code"], keep="last")
    merged = merged.sort_values(["date", "strategy", "rank"], na_position="last")
    merged.to_csv(PICKS_PATH, index=False, encoding="utf-8")
    return len(merged)


def append_prices(new_rows: pd.DataFrame) -> int:
    """追加价格快照，按 (date, ts_code) 去重，同键保留新值。返回归档总行数。"""
    _ensure_dir()
    if new_rows is None or new_rows.empty:
        return len(load_prices())
    new_rows = new_rows.copy()[PRICES_COLS]
    merged = pd.concat([load_prices(), new_rows], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date", "ts_code"], keep="last")
    merged = merged.sort_values(["date", "ts_code"])
    merged.to_csv(PRICES_PATH, index=False, encoding="utf-8")
    return len(merged)


def tracked_codes() -> list[str]:
    """所有曾上榜的标的代码 + 基准，用于每日只归档必要的价格。"""
    codes = set(load_picks()["ts_code"].dropna().astype(str))
    codes.add(BENCHMARK)
    return sorted(codes)


# ================= 收益计算 =================

def _price_matrix(prices: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """把长表价格转成 date x ts_code 的宽表，并返回有序交易日列表。"""
    if prices.empty:
        return pd.DataFrame(), []
    wide = prices.pivot_table(index="date", columns="ts_code", values="adj_close", aggfunc="last")
    wide = wide.sort_index()
    return wide, list(wide.index)


def compute_returns(picks: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """
    为每条上榜记录计算各 horizon 的自身收益、基准收益与超额 alpha。

    口径说明（务必与前端文案一致）：
    - 入选日 D 的收盘价为基准点，T+N 收益 = (D+N 收盘 / D 收盘 - 1)。
      即「当晚看到榜单，次日无法以 D 收盘价买入」这一点在文案中必须诚实说明，
      此处口径衡量的是「榜单排序是否具备信息量」，不是可交易收益。
    - alpha = 自身收益 - 同期沪深300 收益。
    - 未来交易日不足时该 horizon 留空（NaN），不做任何外推。
    """
    if picks.empty or prices.empty:
        return pd.DataFrame()

    wide, trade_days = _price_matrix(prices)
    if not trade_days:
        return pd.DataFrame()
    day_idx = {d: i for i, d in enumerate(trade_days)}

    if BENCHMARK not in wide.columns:
        # 基准缺失属于关键数据缺失，直接返回空表由上层标 failed
        return pd.DataFrame()

    rows = []
    for rec in picks.to_dict("records"):
        d0 = rec.get("date")
        code = rec.get("ts_code")
        if d0 not in day_idx or code not in wide.columns:
            continue
        i0 = day_idx[d0]
        p0 = wide.at[d0, code]
        b0 = wide.at[d0, BENCHMARK]
        if not (np.isfinite(p0) and np.isfinite(b0)) or p0 <= 0 or b0 <= 0:
            continue

        out = {
            "date": d0,
            "strategy": rec.get("strategy"),
            "ts_code": code,
            "name": rec.get("name"),
            "rank": rec.get("rank"),
            "bucket": rec.get("bucket"),
        }
        for n in HORIZONS:
            i1 = i0 + n
            if i1 >= len(trade_days):
                out[f"ret_{n}"] = np.nan
                out[f"bench_{n}"] = np.nan
                out[f"alpha_{n}"] = np.nan
                continue
            d1 = trade_days[i1]
            p1 = wide.at[d1, code]
            b1 = wide.at[d1, BENCHMARK]
            if not (np.isfinite(p1) and np.isfinite(b1)):
                out[f"ret_{n}"] = np.nan
                out[f"bench_{n}"] = np.nan
                out[f"alpha_{n}"] = np.nan
                continue
            r = float(p1 / p0 - 1.0)
            b = float(b1 / b0 - 1.0)
            out[f"ret_{n}"] = r
            out[f"bench_{n}"] = b
            out[f"alpha_{n}"] = r - b
        rows.append(out)

    return pd.DataFrame(rows)


# ================= 统计口径 =================

def _stat_block(alpha: pd.Series) -> dict:
    """
    单组 alpha 的统计口径。

    - 用中位数作为主口径：均值会被个别妖股拉飞，中位数才代表「随手买一只的体验」。
    - direction_accuracy：榜单是看多信号，跑赢基准才算方向正确。
    - 样本 < MIN_SAMPLE 时 status='insufficient'，展示层必须标注为噪音。
    """
    a = pd.to_numeric(alpha, errors="coerce").dropna().astype("float64")
    n = int(len(a))
    if n == 0:
        return {"n": 0, "status": "failed", "reason": "无可用样本"}
    block = {
        "n": n,
        "status": "ok" if n >= MIN_SAMPLE else "insufficient",
        "alpha_median": float(a.median()),
        "alpha_mean": float(a.mean()),
        "alpha_p25": float(a.quantile(0.25)),
        "alpha_p75": float(a.quantile(0.75)),
        "direction_accuracy": float((a > 0).mean()),
        "win_count": int((a > 0).sum()),
        "worst": float(a.min()),
        "best": float(a.max()),
    }
    if n < MIN_SAMPLE:
        block["reason"] = f"样本仅 {n} 条（阈值 {MIN_SAMPLE}），基本是噪音，不构成参考"
    return block


def bucket_monotonic(rets: pd.DataFrame, horizon: int) -> dict:
    """
    评级区分度检验：若榜单排序真有信息量，分档平均 alpha 应随档位下降而单调递减。
    不单调则说明排序本身没有区分能力——这个自检比任何漂亮数字都重要。
    """
    col = f"alpha_{horizon}"
    if rets.empty or col not in rets.columns or "bucket" not in rets.columns:
        return {"status": "failed", "reason": "缺少分档或收益数据"}

    df = rets.dropna(subset=[col, "bucket"]).copy()
    if df.empty:
        return {"status": "failed", "reason": "分档样本全为空"}

    grp = df.groupby("bucket")[col]
    stat = pd.DataFrame({"n": grp.size(), "alpha_median": grp.median()})
    # 分档标签形如 "1-10" / "11-50" / "51+"，按起始序号排序
    stat["_order"] = [int(str(b).split("-")[0].replace("+", "")) for b in stat.index]
    stat = stat.sort_values("_order")

    usable = stat[stat["n"] >= MIN_SAMPLE_BUCKET]
    buckets = [
        {"bucket": str(b), "n": int(r["n"]), "alpha_median": float(r["alpha_median"])}
        for b, r in stat.iterrows()
    ]
    if len(usable) < 2:
        return {
            "status": "insufficient",
            "reason": f"可用档位不足 2 个（每档需 ≥{MIN_SAMPLE_BUCKET} 条）",
            "buckets": buckets,
        }

    vals = usable["alpha_median"].tolist()
    monotonic = all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
    return {
        "status": "ok",
        "monotonic": bool(monotonic),
        "buckets": buckets,
        "verdict": ("排序具备区分度：档位越靠前，超额收益越高"
                    if monotonic else
                    "排序未通过单调性检验：靠前档位并未更优，该榜单的排序信息量存疑"),
    }


def summarize(picks: pd.DataFrame | None = None,
              prices: pd.DataFrame | None = None) -> dict:
    """
    汇总全部策略的战绩，返回可直接 json.dump 的字典。
    顶层 status: complete / incomplete / failed（对标参考仓库的三态失败语义）
    """
    picks = load_picks() if picks is None else picks
    prices = load_prices() if prices is None else prices

    result = {
        "generated_at": pd.Timestamp.utcnow().tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S"),
        "benchmark": BENCHMARK,
        "horizons": list(HORIZONS),
        "min_sample": MIN_SAMPLE,
        "strategies": {},
    }

    if picks.empty:
        result["status"] = "failed"
        result["reason"] = "榜单归档为空，尚未开始积累样本"
        return result
    if prices.empty or BENCHMARK not in set(prices["ts_code"]):
        result["status"] = "failed"
        result["reason"] = f"价格归档缺失或缺少基准 {BENCHMARK}，无法计算超额收益"
        return result

    rets = compute_returns(picks, prices)
    if rets.empty:
        result["status"] = "failed"
        result["reason"] = "榜单与价格归档无法对齐，收益计算结果为空"
        return result

    result["archive"] = {
        "pick_rows": int(len(picks)),
        "price_rows": int(len(prices)),
        "date_from": str(picks["date"].min()),
        "date_to": str(picks["date"].max()),
        "trade_days": int(picks["date"].nunique()),
    }

    any_ok = False
    for strat, g in rets.groupby("strategy"):
        entry = {
            "label": STRATEGY_LABELS.get(str(strat), str(strat)),
            "total_picks": int(len(g)),
            "days": int(g["date"].nunique()),
            "horizons": {},
        }
        for n in HORIZONS:
            blk = _stat_block(g.get(f"alpha_{n}", pd.Series(dtype="float64")))
            entry["horizons"][str(n)] = blk
            if blk.get("status") == "ok":
                any_ok = True
        entry["discrimination"] = bucket_monotonic(g, HORIZONS[min(1, len(HORIZONS) - 1)])
        entry["daily_alpha"] = _daily_series(g, HORIZONS[min(1, len(HORIZONS) - 1)])
        result["strategies"][str(strat)] = entry

    result["status"] = "complete" if any_ok else "incomplete"
    if not any_ok:
        result["reason"] = f"所有策略样本均不足 {MIN_SAMPLE} 条，当前统计仅作占位，不构成业绩证明"
    return result


def _daily_series(g: pd.DataFrame, horizon: int) -> list[dict]:
    """逐日 alpha 中位数序列，供前端画累计曲线。"""
    col = f"alpha_{horizon}"
    if col not in g.columns:
        return []
    d = g.dropna(subset=[col]).groupby("date")[col].median().sort_index()
    return [{"date": str(k), "alpha_median": float(v)} for k, v in d.items()]


def save_performance(payload: dict) -> str:
    _ensure_dir()
    with open(PERF_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return PERF_PATH


def load_performance() -> dict | None:
    if not os.path.exists(PERF_PATH):
        return None
    try:
        with open(PERF_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def rank_bucket(rank: int) -> str:
    """把榜内名次折算成档位标签，用于区分度检验。"""
    r = int(rank)
    if r <= 10:
        return "1-10"
    if r <= 30:
        return "11-30"
    if r <= 50:
        return "31-50"
    return "51+"
