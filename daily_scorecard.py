"""
战绩回看跑批脚本

两种模式：
  python daily_scorecard.py                 # 每日增量：归档今日榜单 + 今日价格 + 重算绩效
  python daily_scorecard.py --backfill 40   # 一次性回溯：重建过去 N 个交易日的 RPS 榜与价格

设计要点：
- 归档与计算严格分离，本脚本只负责取数落盘，统计口径全在 scorecard.py。
- 关键数据（基准指数、当日全市场行情）取不到就以退出码 3 标 failed，
  绝不用旧值或猜测填空——参考仓库的失败语义在这里同样适用。
- 回溯用的 RPS 排名严格复用 daily_rps_pro.py 的口径（复权价 + 三周期百分位排名），
  不允许为了省接口调用而换成别的排序，否则统计出来的是另一个策略的战绩。
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
import time

import pandas as pd
import tushare as ts

import scorecard as sc

MY_TOKEN = os.getenv("TUSHARE_TOKEN", "")
pro = None
if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

RPS_N = [50, 120, 250]
RPS_THRESHOLD = 87
TOP_KEEP = 60          # 每日榜单只归档前 N 名，与前端展示口径一致
SLEEP = 0.35           # 接口间隔，避免触发 tushare 频率限制


def _bj_now() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def fail(msg: str) -> None:
    print(f"❌ [failed] {msg}")
    sys.exit(3)


def trade_calendar(end_date: str, back_days: int = 700) -> list[str]:
    """返回升序交易日列表。"""
    start = (_bj_now() - datetime.timedelta(days=back_days)).strftime("%Y%m%d")
    df = pro.trade_cal(exchange="", is_open="1", start_date=start, end_date=end_date)
    if df is None or df.empty:
        fail("交易日历为空")
    return sorted(df["cal_date"].astype(str).tolist())


def latest_trade_date(cal: list[str]) -> str | None:
    """从日历末尾往前找第一个真的有行情数据的日子。"""
    today = _bj_now().strftime("%Y%m%d")
    for d in reversed([x for x in cal if x <= today]):
        try:
            df = pro.daily(trade_date=d, fields="ts_code,close")
            if df is not None and not df.empty:
                return d
        except Exception as e:
            print(f"   ⚠️ {d} 行情探测异常: {e}")
        time.sleep(SLEEP)
    return None


def adj_snapshot(date_str: str) -> pd.DataFrame:
    """取某交易日的全市场复权收盘价：ts_code, adj_close, raw_close。"""
    try:
        d = pro.daily(trade_date=date_str, fields="ts_code,close")
        a = pro.adj_factor(trade_date=date_str, fields="ts_code,adj_factor")
    except Exception as e:
        print(f"   ⚠️ {date_str} 取价异常: {e}")
        return pd.DataFrame()
    if d is None or a is None or d.empty or a.empty:
        return pd.DataFrame()
    df = pd.merge(d, a, on="ts_code")
    df["adj_close"] = (pd.to_numeric(df["close"], errors="coerce")
                       * pd.to_numeric(df["adj_factor"], errors="coerce")).astype("float64")
    df = df.rename(columns={"close": "raw_close"})
    return df[["ts_code", "adj_close", "raw_close"]].dropna(subset=["adj_close"])


def benchmark_close(date_str: str) -> float | None:
    """沪深300 当日收盘。指数无需复权。"""
    try:
        df = pro.index_daily(ts_code=sc.BENCHMARK, trade_date=date_str, fields="ts_code,close")
    except Exception as e:
        print(f"   ⚠️ {date_str} 基准取数异常: {e}")
        return None
    if df is None or df.empty:
        return None
    return float(df.iloc[0]["close"])


def stock_names() -> pd.DataFrame:
    try:
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        return df if df is not None else pd.DataFrame(columns=["ts_code", "name"])
    except Exception:
        return pd.DataFrame(columns=["ts_code", "name"])


# ================= RPS 榜单重建（口径与 daily_rps_pro.py 一致）=================

def rps_rank_for(date_str: str, cal: list[str], cache: dict) -> pd.DataFrame:
    """
    重建某交易日的 RPS 强势股榜（复权价 + 三周期百分位排名 + 阈值 87）。
    返回 ts_code, rps_50, rps_120, rps_250, raw_close, rank，已按 RPS_50 降序。
    """
    if date_str not in cal:
        return pd.DataFrame()
    i = cal.index(date_str)

    def snap(d: str) -> pd.DataFrame:
        if d not in cache:
            cache[d] = adj_snapshot(d)
            time.sleep(SLEEP)
        return cache[d]

    now = snap(date_str)
    if now.empty:
        return pd.DataFrame()
    df = now.rename(columns={"adj_close": "base_now"})

    for n in RPS_N:
        j = i - n
        if j < 0:
            continue
        past = snap(cal[j])
        if past.empty:
            continue
        past = past[["ts_code", "adj_close"]].rename(columns={"adj_close": "base_past"})
        df = pd.merge(df, past, on="ts_code", how="left")
        pct = (df["base_now"] - df["base_past"]) / df["base_past"]
        df[f"rps_{n}"] = pct.rank(pct=True) * 100.0
        df = df.drop(columns=["base_past"])

    if "rps_50" not in df.columns:
        return pd.DataFrame()

    cond = df["rps_50"] >= RPS_THRESHOLD
    for n in (120, 250):
        if f"rps_{n}" in df.columns:
            cond &= df[f"rps_{n}"].fillna(0) >= RPS_THRESHOLD
    out = df[cond].sort_values("rps_50", ascending=False).head(TOP_KEEP).copy()
    if out.empty:
        return pd.DataFrame()
    out["rank"] = range(1, len(out) + 1)
    keep = ["ts_code", "raw_close", "rank"] + [c for c in out.columns if c.startswith("rps_")]
    return out[keep]


# ================= 归档：今日增量 =================

def archive_today_picks(date_str: str, names: pd.DataFrame) -> int:
    """
    把当日已跑批产出的榜单（RPS / 突破池 / ETF）归档为 picks 行。
    直接读 data/*.csv，保证归档的就是用户当天真实看到的那份榜单。
    """
    rows = []

    # --- RPS 强势股榜 ---
    p = "data/strong_stocks.csv"
    if os.path.exists(p):
        df = pd.read_csv(p)
        col_date = "更新日期" if "更新日期" in df.columns else None
        if col_date and str(df[col_date].iloc[0]).replace("-", "") != date_str:
            print(f"   ⚠️ strong_stocks.csv 日期为 {df[col_date].iloc[0]}，与 {date_str} 不符，跳过归档")
        else:
            df = df.sort_values("RPS_50", ascending=False).head(TOP_KEEP).reset_index(drop=True)
            for i, r in df.iterrows():
                rows.append({
                    "date": date_str, "strategy": "rps",
                    "ts_code": str(r["ts_code"]), "name": r.get("name", ""),
                    "rank": i + 1, "bucket": sc.rank_bucket(i + 1),
                    "ref_close": pd.to_numeric(r.get("price_now"), errors="coerce"),
                })

    # --- 阶段新高突破池 ---
    p = "data/breakout_stocks.csv"
    if os.path.exists(p):
        df = pd.read_csv(p)
        if "update_date" in df.columns and str(df["update_date"].iloc[0]).replace("-", "") != date_str:
            print(f"   ⚠️ breakout_stocks.csv 日期为 {df['update_date'].iloc[0]}，与 {date_str} 不符，跳过归档")
        else:
            df = df.head(TOP_KEEP).reset_index(drop=True)
            for i, r in df.iterrows():
                rows.append({
                    "date": date_str, "strategy": "breakout",
                    "ts_code": str(r["ts_code"]), "name": r.get("name", ""),
                    "rank": i + 1, "bucket": sc.rank_bucket(i + 1),
                    "ref_close": pd.to_numeric(r.get("close"), errors="coerce"),
                })

    # --- 强势 ETF 榜 ---
    p = "data/strong_etfs.csv"
    if os.path.exists(p):
        df = pd.read_csv(p)
        col_date = "更新日期" if "更新日期" in df.columns else None
        if col_date and str(df[col_date].iloc[0]).replace("-", "") != date_str:
            print(f"   ⚠️ strong_etfs.csv 日期不符，跳过归档")
        else:
            df = df.sort_values("RPS_50", ascending=False).head(TOP_KEEP).reset_index(drop=True)
            for i, r in df.iterrows():
                rows.append({
                    "date": date_str, "strategy": "etf",
                    "ts_code": str(r["ts_code"]), "name": r.get("name", ""),
                    "rank": i + 1, "bucket": sc.rank_bucket(i + 1),
                    "ref_close": pd.to_numeric(r.get("price_now"), errors="coerce"),
                })

    if not rows:
        print("   ⚠️ 今日无可归档榜单")
        return 0
    total = sc.append_picks(pd.DataFrame(rows))
    print(f"   ✅ 归档榜单 {len(rows)} 条，累计 {total} 条")
    return len(rows)


def archive_prices(date_str: str) -> int:
    """归档当日「曾上榜标的 + 基准」的复权价。只存必要标的，避免归档爆炸。"""
    codes = set(sc.tracked_codes())
    codes.discard(sc.BENCHMARK)

    rows = []
    b = benchmark_close(date_str)
    if b is None:
        fail(f"{date_str} 基准 {sc.BENCHMARK} 收盘价缺失，超额收益无法计算")
    rows.append({"date": date_str, "ts_code": sc.BENCHMARK, "adj_close": b})

    if codes:
        snap = adj_snapshot(date_str)
        if snap.empty:
            fail(f"{date_str} 全市场行情缺失")
        snap = snap[snap["ts_code"].isin(codes)]
        for r in snap.itertuples(index=False):
            rows.append({"date": date_str, "ts_code": r.ts_code, "adj_close": float(r.adj_close)})

    total = sc.append_prices(pd.DataFrame(rows))
    print(f"   ✅ 归档价格 {len(rows)} 条（含基准），累计 {total} 条")
    return len(rows)


# ================= 回溯 =================

def backfill(days: int) -> None:
    """
    一次性重建过去 N 个交易日的 RPS 榜与价格归档，让战绩页开局就有样本。
    注意：只回溯 RPS 榜——突破池与 ETF 榜依赖当日盘中派生字段，无法忠实重建，
    强行重建会得到「另一个策略」的战绩，属于变相造假。
    """
    today = _bj_now().strftime("%Y%m%d")
    cal = trade_calendar(today, back_days=900)
    last = latest_trade_date(cal)
    if last is None:
        fail("连续探测未取到任何交易日行情")
    end_i = cal.index(last)

    # 需要 250 个历史交易日算 RPS_250，日历长度必须够
    start_i = max(0, end_i - days + 1)
    targets = cal[start_i:end_i + 1]
    print(f"🔁 回溯 {len(targets)} 个交易日：{targets[0]} → {targets[-1]}")

    cache: dict[str, pd.DataFrame] = {}
    names = stock_names()
    name_map = dict(zip(names["ts_code"], names["name"])) if not names.empty else {}

    pick_rows, price_rows = [], []
    all_codes: set[str] = set()

    for d in targets:
        rk = rps_rank_for(d, cal, cache)
        if rk.empty:
            print(f"   ⚠️ {d} RPS 榜为空，跳过")
            continue
        for r in rk.itertuples(index=False):
            pick_rows.append({
                "date": d, "strategy": "rps", "ts_code": r.ts_code,
                "name": name_map.get(r.ts_code, ""), "rank": int(r.rank),
                "bucket": sc.rank_bucket(int(r.rank)),
                "ref_close": float(r.raw_close),
            })
        all_codes.update(rk["ts_code"].tolist())
        print(f"   {d} 上榜 {len(rk)} 只")

    if not pick_rows:
        fail("回溯期内未重建出任何榜单")

    # 收益窗口需要覆盖到最后一批上榜日之后的 max(HORIZONS) 天
    price_days = cal[start_i:min(len(cal), end_i + 1 + max(sc.HORIZONS))]
    print(f"📈 归档 {len(price_days)} 个交易日的价格（{len(all_codes)} 只标的 + 基准）")
    for d in price_days:
        snap = cache.get(d)
        if snap is None or snap.empty:
            snap = adj_snapshot(d)
            time.sleep(SLEEP)
        if not snap.empty:
            sub = snap[snap["ts_code"].isin(all_codes)]
            for r in sub.itertuples(index=False):
                price_rows.append({"date": d, "ts_code": r.ts_code, "adj_close": float(r.adj_close)})
        b = benchmark_close(d)
        if b is not None:
            price_rows.append({"date": d, "ts_code": sc.BENCHMARK, "adj_close": b})
        time.sleep(SLEEP)

    sc.append_picks(pd.DataFrame(pick_rows))
    sc.append_prices(pd.DataFrame(price_rows))
    print(f"✅ 回溯完成：榜单 {len(pick_rows)} 条，价格 {len(price_rows)} 条")


# ================= 主流程 =================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, help="回溯最近 N 个交易日的 RPS 榜")
    args = ap.parse_args()

    if pro is None:
        fail("未配置 TUSHARE_TOKEN")

    if args.backfill > 0:
        backfill(args.backfill)
    else:
        today = _bj_now().strftime("%Y%m%d")
        cal = trade_calendar(today)
        last = latest_trade_date(cal)
        if last is None:
            fail("未取到最新交易日行情")
        print(f"📅 目标交易日：{last}")
        archive_today_picks(last, stock_names())
        archive_prices(last)

    payload = sc.summarize()
    path = sc.save_performance(payload)
    print(f"📊 绩效汇总 status={payload.get('status')} → {path}")
    if payload.get("reason"):
        print(f"   说明：{payload['reason']}")
    for k, v in payload.get("strategies", {}).items():
        h5 = v.get("horizons", {}).get("5", {})
        print(f"   [{v.get('label')}] 样本 {h5.get('n', 0)} 条 "
              f"status={h5.get('status')} "
              f"5日alpha中位数={h5.get('alpha_median')} "
              f"方向正确率={h5.get('direction_accuracy')}")

    if payload.get("status") == "failed":
        sys.exit(3)
    if payload.get("status") == "incomplete":
        sys.exit(2)


if __name__ == "__main__":
    main()
