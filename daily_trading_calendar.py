"""Publish the exchange calendar for offline freshness checks on the website."""
import datetime as dt
import json
import os
from pathlib import Path

from update_schedule import beijing_now

OUTPUT = Path(__file__).resolve().parent / "data" / "trading_calendar.json"


def build_calendar(pro, now=None):
    now = beijing_now(now)
    start = dt.date(now.year - 1, 1, 1)
    end = dt.date(now.year, 12, 31)
    frame = pro.trade_cal(exchange="SSE", start_date=start.strftime("%Y%m%d"),
                          end_date=end.strftime("%Y%m%d"))
    if frame is None or frame.empty:
        raise ValueError("交易日历为空，保留原文件")
    rows = {str(row.cal_date): str(row.is_open) for row in frame.itertuples()}
    required = {(start + dt.timedelta(days=i)).strftime("%Y%m%d")
                for i in range((end - start).days + 1)}
    if not required.issubset(rows) or any(rows[d] not in ("0", "1") for d in required):
        raise ValueError("交易日历不完整，保留原文件")
    return {"source": "Tushare SSE trade_cal", "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"), "generated_at": now.isoformat(),
            "open_dates": sorted(d for d in required if rows[d] == "1")}


def main():
    import tushare as ts
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        raise ValueError("缺少 TUSHARE_TOKEN，保留原交易日历")
    payload = build_calendar(ts.pro_api(token))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(f"交易日历已更新：{payload['start_date']}~{payload['end_date']}")


if __name__ == "__main__":
    main()
