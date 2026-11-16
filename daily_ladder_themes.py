"""Enrich the existing ladder with dated THS/KPL evidence, without sending messages."""
from __future__ import annotations

import json
import os
import datetime as dt
from pathlib import Path
import time
import urllib.parse
import urllib.request

from data_freshness import norm_date
from ladder_themes import SOURCES, build_payload, parse_attributions
from update_schedule import CST, beijing_now

ROOT = Path(__file__).resolve().parent
LADDER_PATH = ROOT / "data" / "limit_ladder.json"
OUTPUT = ROOT / "data" / "limit_themes.json"
THS_PUBLIC_URL = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"


def fetch_public_ths(trade_date, codes) -> dict:
    """Read the public dated pool; reject mismatched dates and partial pagination."""
    date = norm_date(trade_date)
    if not date:
        raise ValueError("无效的交易日期")
    rows, total, pages = [], None, 1
    started = time.monotonic()
    for page in range(1, 6):
        params = {
            "page": page, "limit": 200,
            "field": "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004",
            "filter": "HS,GEM2STAR", "order_field": "330324", "order_type": "0", "date": date,
        }
        request = urllib.request.Request(THS_PUBLIC_URL + "?" + urllib.parse.urlencode(params), headers={
            "User-Agent": "Mozilla/5.0", "Referer": SOURCES["ths_public"]["url"],
            "Accept": "application/json",
        })
        try:
            remaining = 40 - (time.monotonic() - started)
            if remaining <= 0:
                return {"status": "unavailable", "records": {}}
            with urllib.request.urlopen(request, timeout=min(10, remaining)) as response:
                payload = json.load(response)
            if payload.get("status_code") != 0:
                return {"status": "unavailable", "records": {}}
            data = payload.get("data") or {}
            if norm_date(data.get("date")) != date:
                return {"status": "date_mismatch", "records": {}}
            info, pagination = data.get("info"), data.get("page") or {}
            count, page_count, current = (pagination.get(key) for key in ("total", "count", "page"))
            if (not isinstance(info, list) or
                any(not isinstance(n, int) or isinstance(n, bool) for n in (count, page_count, current)) or
                count < 0 or not 1 <= page_count <= 5 or current != page or
                (total is not None and (total != count or pages != page_count))):
                return {"status": "incomplete", "records": {}}
            total, pages = count, page_count
            for row in info:
                if not isinstance(row, dict):
                    return {"status": "incomplete", "records": {}}
                # The pool's date plus the actual limit-up timestamp must both match.
                stamp_date = dt.datetime.fromtimestamp(int(row["first_limit_up_time"]), CST).strftime("%Y%m%d")
                if stamp_date != date:
                    return {"status": "date_mismatch", "records": {}}
                rows.append({"ts_code": row.get("code"), "trade_date": date,
                             "theme": row.get("reason_type")})
            if page == pages:
                break
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            return {"status": "unavailable", "records": {}}
    if len(rows) != total:
        return {"status": "incomplete", "records": {}}
    return parse_attributions(rows, "ths_public", date, codes)


def fetch_sources(pro, ladder: dict) -> dict:
    date = norm_date(ladder.get("date"))
    if not date:
        raise ValueError("连板天梯缺少有效交易日期")
    codes = [row.get("code") for row in ladder.get("stocks", []) if isinstance(row, dict)]
    results = {"ths_public": fetch_public_ths(date, codes)}
    for source, api, params, fields in (
        ("ths", "limit_list_ths", {"limit_type": "涨停池"}, "ts_code,trade_date,lu_desc"),
        ("kpl", "kpl_list", {"tag": "涨停"}, "ts_code,trade_date,theme,lu_desc"),
    ):
        if source == "ths" and len(results["ths_public"]["records"]) == len(codes):
            results[source] = {"status": "not_needed", "records": {}}
            continue
        if pro is None:
            results[source] = {"status": "unavailable", "records": {}}
            continue
        try:
            frame = getattr(pro, api)(trade_date=date, fields=fields, **params)
            rows = [] if frame is None else frame.to_dict("records")
            results[source] = parse_attributions(rows, source, date, codes)
        except Exception as exc:
            # An exception can contain request credentials. Publish only a category.
            text = str(exc).lower()
            denied = any(word in text for word in ("权限", "积分", "permission", "privilege"))
            results[source] = {"status": "permission_denied" if denied else "unavailable", "records": {}}
    for source, result in results.items():
        print(f"{SOURCES[source]['name']} {date}: {result['status']}, "
              f"匹配 {len(result['records'])}/{len(codes)}, 日期不符 {result.get('rejected_dates', 0)}")
    return results


def main():
    import tushare as ts

    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    ladder = json.loads(LADDER_PATH.read_text(encoding="utf-8"))
    try:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    results = fetch_sources(ts.pro_api(token, timeout=15) if token else None, ladder)
    payload = build_payload(ladder, results, previous=previous, checked_at=beijing_now().isoformat())
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    coverage = payload["coverage"]
    print(f"题材归因已保存：{payload['date']}，覆盖 {coverage['matched']}/{coverage['total']}")


if __name__ == "__main__":
    main()
