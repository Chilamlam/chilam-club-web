"""
短线情绪派生指标跑批

依赖 daily_market_monitor.py 已产出的 data/limit_ladder.json（涨停池），
本脚本负责：
  1. 把当日涨停池归档进 data/sentiment/ladder_history.csv（累积样本）
  2. 取当日全市场涨幅，算 breadth（均值/中位数/涨跌家数）并归档
  3. 调 sentiment.py 计算晋级率、连板溢价、梯队断层、周期定位、明日验证条件
  4. 写出 data/sentiment/derived.json

失败语义：
  归档缺前一交易日 → 晋级率标 failed，其余指标照算，整体 incomplete（退出码 2）
  当日涨停池或行情取不到 → failed（退出码 3），不用旧值顶替

回溯模式：
  python daily_sentiment.py --backfill 15
  东财涨停池接口支持按 date 参数取历史（实测可回溯约 15 个交易日，更早返回空），
  故首次上线可一次性补齐归档，让 1进2 晋级率立刻有样本。
  注意：回溯只补「涨停池 + 全市场涨幅」这两项客观数据，两者都是当日收盘后
  即固化的事实，重建不存在失真。若某日接口返回空则整日跳过，绝不用邻日顶替。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.request

import pandas as pd
import tushare as ts

import sentiment as sm

MY_TOKEN = os.getenv("TUSHARE_TOKEN", "")
pro = None
if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

LADDER_PATH = "data/limit_ladder.json"


def fail(msg: str) -> None:
    print(f"❌ [failed] {msg}")
    sys.exit(3)


def _bare(code) -> str:
    return str(code).strip().upper().split(".")[0]


def load_ladder() -> dict:
    if not os.path.exists(LADDER_PATH):
        fail(f"{LADDER_PATH} 不存在，请先跑 daily_market_monitor.py")
    with open(LADDER_PATH, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not d.get("stocks"):
        fail("涨停池为空，无法计算情绪派生指标")
    return d


def archive_ladder(d: dict) -> str:
    """把当日涨停池写入累积归档。返回 YYYYMMDD 形态的日期。"""
    date_disp = str(d.get("date") or "")
    date_key = date_disp.replace("-", "")
    if len(date_key) != 8 or not date_key.isdigit():
        fail(f"涨停池日期格式异常：{date_disp!r}")

    rows = []
    for s in d["stocks"]:
        rows.append({
            "date": date_key,
            "code": _bare(s.get("code")),
            "name": s.get("name", ""),
            "industry": s.get("industry", "-"),
            "limit_times": int(s.get("limit_times") or 1),
            "first_time": s.get("first_time", ""),
        })
    total = sm.append_ladder(pd.DataFrame(rows))
    print(f"✅ 涨停归档 +{len(rows)} 条（{date_key}），累计 {total} 条")
    return date_key


def fetch_breadth(date_key: str) -> tuple[dict, dict]:
    """
    取当日全市场涨幅，返回 (breadth_row, {纯代码: 涨幅%})。
    全市场行情是关键数据，取不到直接 fail。
    """
    if pro is None:
        fail("未配置 TUSHARE_TOKEN，无法取全市场涨幅")
    try:
        df = pro.daily(trade_date=date_key, fields="ts_code,pct_chg")
    except Exception as e:
        fail(f"{date_key} 全市场行情取数异常：{e}")
    if df is None or df.empty:
        fail(f"{date_key} 全市场行情为空")

    pct = pd.to_numeric(df["pct_chg"], errors="coerce").astype("float64").dropna()
    is_star = df["ts_code"].astype(str).str.startswith(("30", "68"))
    limit_up = int(((~is_star & (pct >= 9.8)) | (is_star & (pct >= 19.8))).sum())
    limit_down = int(((~is_star & (pct <= -9.8)) | (is_star & (pct <= -19.8))).sum())

    row = {
        "date": date_key,
        "mean_pct": float(pct.mean()),
        "median_pct": float(pct.median()),
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "limit_up": limit_up,
        "limit_down": limit_down,
    }
    pct_map = {_bare(c): float(v) for c, v in zip(df["ts_code"], df["pct_chg"]) if pd.notna(v)}
    n = sm.append_breadth(row)
    print(f"✅ 市场宽度归档（{date_key}）：均值 {row['mean_pct']:+.2f}% "
          f"中位 {row['median_pct']:+.2f}% 涨/跌 {row['up']}/{row['down']} "
          f"涨停 {limit_up} 跌停 {limit_down}，累计 {n} 天")
    return row, pct_map


ZT_URL = ("https://push2ex.eastmoney.com/getTopicZTPool"
          "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt"
          "&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date={date}&_=1")


def fetch_ladder_remote(date_key: str) -> list[dict]:
    """
    直接向东财涨停池接口按日期取历史。返回归档行列表；取不到返回空列表。

    这里刻意不复用 daily_market_monitor 的三通道兜底：回溯场景下 AkShare 与
    Tushare 通道都不支持任意历史日，硬凑会得到「当日数据冒充历史」的假数据。
    宁可这一天没有，也不要错的一天。
    """
    req = urllib.request.Request(
        ZT_URL.format(date=date_key),
        headers={"User-Agent": "Mozilla/5.0",
                 "Referer": "https://quote.eastmoney.com/"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            j = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        print(f"   ⚠️ {date_key} 涨停池取数异常：{e}")
        return []
    pool = ((j or {}).get("data") or {}).get("pool") or []
    rows = []
    for it in pool:
        fbt = str(it.get("fbt") or "").zfill(6)
        rows.append({
            "date": date_key,
            "code": _bare(it.get("c")),
            "name": str(it.get("n") or ""),
            "industry": str(it.get("hybk") or "-"),
            "limit_times": int(it.get("lbc") or 1),
            "first_time": (f"{fbt[:2]}:{fbt[2:4]}:{fbt[4:6]}"
                           if fbt.strip("0") else ""),
        })
    return rows


def backfill(days: int) -> None:
    """
    回溯补齐涨停池与市场宽度归档。

    交易日历取自 tushare（不自己推算，避免把节假日当交易日算进"断层"）。
    每天两件事：涨停池（东财，按 date 取历史）+ 全市场涨幅（tushare daily）。
    某日任一项缺失就整日跳过——半天数据会让晋级率分母失真，比没有更糟。
    """
    if pro is None:
        fail("未配置 TUSHARE_TOKEN，无法取交易日历")
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y%m%d")
    start = (datetime.datetime.strptime(today, "%Y%m%d")
             - datetime.timedelta(days=days * 2 + 20)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=today,
                            is_open="1", fields="cal_date")
    except Exception as e:
        fail(f"交易日历取数异常：{e}")
    all_days = sorted(str(x) for x in cal["cal_date"])[-days:]
    print(f"🔁 回溯 {len(all_days)} 个交易日：{all_days[0]} → {all_days[-1]}")

    got, skipped = 0, []
    for dk in all_days:
        rows = fetch_ladder_remote(dk)
        if not rows:
            skipped.append(dk)
            print(f"   ⏭️ {dk} 涨停池为空（接口不提供该日历史），跳过")
            continue
        try:
            df = pro.daily(trade_date=dk, fields="ts_code,pct_chg")
        except Exception as e:
            skipped.append(dk)
            print(f"   ⏭️ {dk} 全市场行情异常（{e}），跳过")
            continue
        if df is None or df.empty:
            skipped.append(dk)
            print(f"   ⏭️ {dk} 全市场行情为空，跳过")
            continue

        sm.append_ladder(pd.DataFrame(rows))
        pct = pd.to_numeric(df["pct_chg"], errors="coerce").astype("float64").dropna()
        is_star = df["ts_code"].astype(str).str.startswith(("30", "68"))
        sm.append_breadth({
            "date": dk,
            "mean_pct": float(pct.mean()),
            "median_pct": float(pct.median()),
            "up": int((pct > 0).sum()),
            "down": int((pct < 0).sum()),
            "limit_up": int(((~is_star & (pct >= 9.8)) | (is_star & (pct >= 19.8))).sum()),
            "limit_down": int(((~is_star & (pct <= -9.8)) | (is_star & (pct <= -19.8))).sum()),
        })
        got += 1
        print(f"   ✅ {dk} 涨停 {len(rows)} 只，全市场 {len(pct)} 只")

    print(f"🔁 回溯完成：成功 {got} 天，跳过 {len(skipped)} 天"
          + (f"（{', '.join(skipped)}）" if skipped else ""))


def main() -> None:
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 15
        backfill(n)

    d = load_ladder()
    date_key = archive_ladder(d)
    breadth_row, pct_map = fetch_breadth(date_key)


    hist = sm.load_ladder_history()
    dates = sorted(hist["date"].astype(str).unique())
    prev_date = None
    for x in reversed(dates):
        if x < date_key:
            prev_date = x
            break

    if prev_date is None:
        promo = {"status": "failed",
                 "reason": "归档中尚无更早的交易日，晋级率需要至少两天数据才能计算"}
        premium = {"status": "failed", "reason": "缺少前一交易日涨停归档"}
        print("⚠️ 归档仅有一天，晋级率与连板溢价本次无法计算（不做猜测填空）")
    else:
        promo = sm.promotion_rates(hist, date_key, prev_date)
        premium = sm.continuation_premium(hist, prev_date, pct_map)
        print(f"📅 对比基准日：{prev_date}")

    gap = sm.ladder_gap(hist, date_key)
    breadth = sm.profit_effect(breadth_row["mean_pct"], breadth_row["median_pct"],
                               breadth_row["up"], breadth_row["down"])
    phase = sm.cycle_phase(promo, gap, premium)
    plan = sm.verification_plan(promo, gap, premium, breadth)

    ok_flags = [promo.get("status") == "ok", gap.get("status") == "ok",
                breadth.get("status") == "ok"]
    status = "complete" if all(ok_flags) else ("incomplete" if any(ok_flags) else "failed")

    payload = {
        "status": status,
        "date": date_key,
        "prev_date": prev_date,
        "generated_at": (datetime.datetime.utcnow()
                         + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
        "archive_days": len(dates),
        "promotion": promo,
        "premium": premium,
        "ladder_gap": gap,
        "breadth": breadth,
        "phase": phase,
        "verification_plan": plan,
    }
    path = sm.save_derived(payload)
    print(f"📊 情绪派生指标 status={status} → {path}")

    rates = (promo or {}).get("rates") or {}
    if "1进2" in rates:
        r = rates["1进2"]
        print(f"   1进2 晋级率 {r['rate'] * 100:.1f}% ({r['promoted']}/{r['base']})")
    if phase.get("status") == "ok":
        print(f"   周期定位：{phase['phase']} — {phase['basis']}")
    print(f"   明日验证条件 {len(plan)} 条")

    if status == "failed":
        sys.exit(3)
    if status == "incomplete":
        sys.exit(2)


if __name__ == "__main__":
    main()
