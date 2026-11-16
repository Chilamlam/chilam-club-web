"""Dated limit-up attribution; business industries are never used as themes."""
from __future__ import annotations

import html
import re

from data_freshness import norm_date


SOURCES = {
    "ths_public": {"name": "同花顺涨停揭秘", "url": "https://data.10jqka.com.cn/limit_up/"},
    "ths": {"name": "同花顺", "url": "https://tushare.pro/document/2?doc_id=355"},
    "kpl": {"name": "开盘啦", "url": "https://tushare.pro/document/2?doc_id=347"},
}


def stock_code(value) -> str:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})(?:\.(?:SH|SZ|BJ))?", text)
    return match.group(1) if match else ""


def clean_text(value) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"<[^>]*>", "", html.unescape(value))
    text = " ".join(text.split())
    return "" if text.lower() in {"", "-", "--", "暂无", "无", "nan", "none", "null"} else text


def _unique_text(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = clean_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def parse_attributions(rows, source: str, trade_date: str, codes) -> dict:
    """Accept only explicitly dated, code-matched source rows with useful text."""
    if source not in SOURCES or not norm_date(trade_date):
        raise ValueError("无效的题材来源或交易日期")
    date = norm_date(trade_date)
    wanted = {stock_code(code) for code in codes} - {""}
    records = {}
    rejected = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = stock_code(row.get("ts_code"))
        if norm_date(row.get("trade_date")) != date:
            rejected += 1
            continue
        if code not in wanted:
            continue
        reason = clean_text(row.get("lu_desc"))
        # THS tag is 首板/4天4板 etc., not a concept classification.
        theme = clean_text(row.get("theme")) if source in {"kpl", "ths_public"} else ""
        if not (reason or theme):
            continue
        record = {"code": code, "date": date, "reason": reason, "theme": theme}
        # Conflicting duplicate rows must not arbitrarily pick an attribution.
        if code in records and records[code] != record:
            records[code] = None
        else:
            records[code] = record
    return {"status": "ok" if any(records.values()) else "empty",
            "records": {code: row for code, row in records.items() if row},
            "rejected_dates": rejected,
            "conflicts": sorted(code for code, row in records.items() if row is None)}


def source_status_text(payload) -> str:
    """Describe the actual provider responses without implying unavailable coverage."""
    if not isinstance(payload, dict):
        return "尚未生成"
    labels = []
    for source, meta in SOURCES.items():
        data = (payload.get("sources") or {}).get(source) or {}
        status = clean_text(data.get("status")) or "unknown"
        count = len(data.get("records") or {}) if isinstance(data.get("records"), dict) else 0
        retained = data.get("retained_count", 0)
        retained = retained if isinstance(retained, int) and not isinstance(retained, bool) else 0
        retained = min(count, max(0, retained))
        if status == "ok":
            label = f"{meta['name']} {count}条"
        elif status == "empty":
            label = f"{meta['name']}本次暂无匹配"
        elif status == "permission_denied":
            label = f"{meta['name']}权限不足"
        elif status == "not_needed":
            continue
        elif status == "date_mismatch":
            label = f"{meta['name']}日期不符"
        elif status == "incomplete":
            label = f"{meta['name']}数据不完整"
        else:
            label = f"{meta['name']}不可用"
        if retained:
            label += f"（保留{retained}条同日记录）"
        labels.append(label)
    return "；".join(labels) or "暂无来源状态"


def attribution_for(code, trade_date, payload) -> dict | None:
    """Merge same-date evidence while keeping each provider's credit visible."""
    date = norm_date(trade_date)
    code = stock_code(code)
    if not date or not code or not isinstance(payload, dict) or norm_date(payload.get("date")) != date:
        return None
    matches = []
    for source, meta in SOURCES.items():
        data = (payload.get("sources") or {}).get(source) or {}
        row = (data.get("records") or {}).get(code)
        if not isinstance(row, dict) or stock_code(row.get("code")) != code or norm_date(row.get("date")) != date:
            continue
        if clean_text(row.get("reason")) or clean_text(row.get("theme")):
            matches.append((source, meta, row))
    if not matches:
        return None
    themes = _unique_text(row.get("theme") for source, _, row in matches if source in {"kpl", "ths_public"})
    reasons = _unique_text(row.get("reason") for _, _, row in matches)
    providers = [meta["name"] for _, meta, _ in matches]
    urls = [meta["url"] for _, meta, _ in matches]
    return {"source": " + ".join(providers), "sources": providers, "urls": urls,
            "date": date, "reason": "；".join(reasons), "theme": "、".join(themes)}


def build_payload(ladder: dict, responses: dict, *, previous=None, checked_at="") -> dict:
    """Retain valid same-date evidence on retries, never carry it to another day."""
    date = norm_date(ladder.get("date"))
    if not date:
        raise ValueError("连板天梯缺少有效交易日期")
    codes = {stock_code(row.get("code")) for row in ladder.get("stocks", [])
             if isinstance(row, dict)} - {""}
    if not codes:
        raise ValueError("连板天梯没有有效股票代码")
    old = previous if isinstance(previous, dict) and norm_date(previous.get("date")) == date else {}
    payload = {"date": date, "checked_at": checked_at, "sources": {}}
    for source in SOURCES:
        response = responses.get(source) or {"status": "unavailable", "records": {}}
        retained = {}
        conflicts = {stock_code(code) for code in response.get("conflicts", [])}
        # A successful response supersedes that provider's old snapshot. On a failed
        # or empty retry retain only same-day records, excluding explicit conflicts.
        retain_codes = codes - conflicts if response.get("status") != "ok" else set()
        for code in sorted(retain_codes):
            single_source = {"date": date, "sources": {source: (old.get("sources") or {}).get(source, {})}}
            item = attribution_for(code, date, single_source)
            if item:
                retained[code] = {key: item[key] for key in ("date", "reason", "theme")}
                retained[code]["code"] = code
        clean_records = {}
        for code, record in (response.get("records") or {}).items():
            if not isinstance(record, dict):
                continue
            record_code = stock_code(record.get("code") or code)
            record_date = norm_date(record.get("date"))
            reason = clean_text(record.get("reason"))
            theme = clean_text(record.get("theme")) if source in {"kpl", "ths_public"} else ""
            if record_code in codes and record_date == date and (reason or theme):
                clean_records[record_code] = {"code": record_code, "date": date,
                                              "reason": reason, "theme": theme}
        retained_count = len(set(retained) - set(clean_records))
        retained.update(clean_records)
        payload["sources"][source] = {**response, "records": retained,
                                      "retained_count": retained_count}
    payload["coverage"] = {"matched": sum(attribution_for(code, date, payload) is not None for code in codes),
                           "total": len(codes)}
    return payload
