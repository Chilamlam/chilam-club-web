"""Archive SSE+SZSE financing balances; keep disclosure dates and missing days.

Official units: rzye in yuan; https://tushare.pro/document/2?doc_id=58
The last trading day can legitimately be unpublished at the evening batch.
"""
import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd

from data_freshness import norm_date
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "funding" / "margin_history.csv"


def normalize_margin(frame, cutoff):
    required = {"trade_date", "exchange_id", "rzye"}
    if frame is None or frame.empty or not required <= set(frame):
        raise ValueError("融资汇总为空或缺列，保留历史文件")
    frame = frame[list(required)].copy()
    frame["trade_date"] = frame["trade_date"].map(norm_date)
    frame["rzye"] = pd.to_numeric(frame["rzye"], errors="coerce")
    frame = frame[(frame["trade_date"] != "") & (frame["trade_date"] <= cutoff)
                  & frame["exchange_id"].isin(["SSE", "SZSE"])].drop_duplicates()
    rows = []
    for day, group in frame.groupby("trade_date"):
        if (len(group) != 2 or set(group["exchange_id"]) != {"SSE", "SZSE"}
                or group["rzye"].isna().any() or not group["rzye"].between(0, 1e16, inclusive="neither").all()):
            continue
        rows.append(dict(date=day, balance_yi=float(group["rzye"].sum()) / 1e8,
                         source="tushare.margin:SSE+SZSE"))
    if not rows:
        raise ValueError("没有沪深两市齐全且有效的融资余额，保留历史文件")
    return pd.DataFrame(rows).sort_values("date")


def main():
    import tushare as ts
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        print("融资余额未更新：缺少 TUSHARE_TOKEN，保留历史文件。")
        return 2
    now = beijing_now()
    cutoff = now.strftime("%Y%m%d")
    try:
        pro = ts.pro_api(token, timeout=15)
        fresh = normalize_margin(pro.margin(start_date=(now.date()-dt.timedelta(days=60)).strftime("%Y%m%d"),
                                           end_date=cutoff, fields="trade_date,exchange_id,rzye"), cutoff)
        old = pd.read_csv(OUTPUT, dtype={"date": str}) if OUTPUT.exists() else pd.DataFrame()
        combined = pd.concat([old, fresh], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date").tail(260)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUTPUT.with_suffix(".tmp")
        combined.to_csv(tmp, index=False, encoding="utf-8")
        tmp.replace(OUTPUT)
        metadata = {"checked_at": now.isoformat(), "date": str(fresh.iloc[-1]["date"]),
                    "source": "https://tushare.pro/document/2?doc_id=58", "scope": "SSE+SZSE", "unit": "亿元"}
        status = OUTPUT.parent / "status.json"
        tmp = status.with_suffix(".tmp")
        tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(status)
        print(f"融资余额已更新至 {metadata['date']}（披露通常滞后一交易日）。")
        return 0
    except Exception as exc:
        # Provider exceptions may contain credentials: expose only the category.
        print(f"融资余额未更新：{type(exc).__name__}，保留历史文件；请核对接口权限和网络。")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
