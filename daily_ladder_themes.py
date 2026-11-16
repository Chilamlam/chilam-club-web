"""Enrich the existing ladder with dated THS/KPL evidence, without sending messages."""
from __future__ import annotations

import json
import os
from pathlib import Path

from data_freshness import norm_date
from ladder_themes import SOURCES, build_payload, parse_attributions
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
LADDER_PATH = ROOT / "data" / "limit_ladder.json"
OUTPUT = ROOT / "data" / "limit_themes.json"


def fetch_sources(pro, ladder: dict) -> dict:
    date = norm_date(ladder.get("date"))
    if not date:
        raise ValueError("连板天梯缺少有效交易日期")
    codes = [row.get("code") for row in ladder.get("stocks", [])]
    results = {}
    for source, api, params, fields in (
        ("ths", "limit_list_ths", {"limit_type": "涨停池"}, "ts_code,trade_date,lu_desc"),
        ("kpl", "kpl_list", {"tag": "涨停"}, "ts_code,trade_date,theme,lu_desc"),
    ):
        try:
            frame = getattr(pro, api)(trade_date=date, fields=fields, **params)
            rows = [] if frame is None else frame.to_dict("records")
            results[source] = parse_attributions(rows, source, date, codes)
        except Exception as exc:
            # An exception can contain request credentials. Publish only a category.
            text = str(exc).lower()
            denied = any(word in text for word in ("权限", "积分", "permission", "privilege"))
            results[source] = {"status": "permission_denied" if denied else "unavailable", "records": {}}
        result = results[source]
        print(f"{SOURCES[source]['name']} {date}: {result['status']}, "
              f"匹配 {len(result['records'])}/{len(codes)}, 日期不符 {result.get('rejected_dates', 0)}")
    return results


def main():
    import tushare as ts

    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        raise ValueError("缺少 TUSHARE_TOKEN，保留原题材文件")
    ladder = json.loads(LADDER_PATH.read_text(encoding="utf-8"))
    try:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        previous = None
    results = fetch_sources(ts.pro_api(token, timeout=15), ladder)
    payload = build_payload(ladder, results, previous=previous, checked_at=beijing_now().isoformat())
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    coverage = payload["coverage"]
    print(f"题材归因已保存：{payload['date']}，覆盖 {coverage['matched']}/{coverage['total']}")


if __name__ == "__main__":
    main()
