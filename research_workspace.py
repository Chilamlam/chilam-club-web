"""Personal research revisions and dated checks. No network or trade execution."""
from __future__ import annotations

import copy
import datetime as dt

from data_freshness import norm_date
from decision_workbench import number, symbol_id

TEXT_FIELDS = ("thesis", "trigger", "invalid", "exit_rule", "cost_risk", "manual_evidence")
PRICE_FIELDS = ("observe_low", "observe_high", "invalid_below", "invalid_above")
NUM_FIELDS = PRICE_FIELDS + ("shares", "cost_price")
DATE_FIELDS = ("review_on", "price_basis_date", "position_date")


def research(record):
    """The original entry stays frozen; subsequent decisions have their own dates."""
    original = record.get("plan", {})
    value = {k: original.get(k, "") for k in TEXT_FIELDS}
    if original.get("quick_follow"):
        # System notes describe the candidate. They are not the user's decisions.
        value.update(trigger="", invalid="", exit_rule="", cost_risk="")
    value.update({k: None for k in NUM_FIELDS})
    value.update({k: "" for k in DATE_FIELDS})
    value.update(record.get("outcome", {}).get("research") or {})
    return value


def validate_research(value):
    if not isinstance(value, dict):
        raise ValueError("研究条件格式错误。")
    clean = {}
    for key in TEXT_FIELDS:
        text = value.get(key, "")
        if not isinstance(text, str) or len(text) > 10000:
            raise ValueError("研究文字最多 10000 字。")
        clean[key] = text.strip()
    for key in NUM_FIELDS:
        raw = value.get(key)
        parsed = number(raw)
        if raw is not None and (parsed is None or parsed < 0 or (key != "shares" and parsed == 0)):
            raise ValueError("价位与成本必须大于零，数量不能为负；不使用的条件请留空。")
        clean[key] = parsed
    lo, hi = clean["observe_low"], clean["observe_high"]
    if lo is not None and hi is not None and lo > hi:
        raise ValueError("观察区间下沿不能高于上沿。")
    below, above = clean["invalid_below"], clean["invalid_above"]
    if below is not None and above is not None and below >= above:
        raise ValueError("下方复核价必须低于上方复核价。")
    for key in DATE_FIELDS:
        raw = value.get(key)
        parsed = norm_date(raw)
        if raw and not parsed:
            raise ValueError("请输入有效日期。")
        clean[key] = parsed
    if any(clean[k] is not None for k in PRICE_FIELDS) and not clean["price_basis_date"]:
        raise ValueError("请注明个人价位适用日期，除权除息后需要重新核对。")
    if clean["shares"] and (not clean["cost_price"] or not clean["position_date"]):
        raise ValueError("记录持仓时，请同时填写数量、每股成本和持仓核对日期。")
    return clean


def revise(record, fields, *, now, reason=""):
    """Append a revision; never replace the historical premise or outcome totals."""
    clean = validate_research(fields)
    stamp = dt.datetime.fromisoformat(now)
    if clean["position_date"] and clean["position_date"] > stamp.strftime("%Y%m%d"):
        raise ValueError("持仓核对日期不能在未来。")
    if clean["shares"] and record["plan"]["mode"] != "实盘记录":
        raise ValueError("请先建立实盘记录，再填写实际持仓。")
    updated = copy.deepcopy(record)
    outcome = updated.setdefault("outcome", {})
    if clean == outcome.get("research"):
        return updated
    history = outcome.setdefault("research_history", [])
    if len(history) >= 200:
        raise ValueError("本条记录已保存 200 次修订，请导出后建立新记录。")
    history.append(dict(at=now, reason=str(reason).strip()[:1000], values=clean))
    outcome.update(research=clean, updated_at=now)
    return updated


def validate_extensions(outcome):
    """Validate optional schema-1 additions on backup import as well as UI save."""
    if "research" in outcome:
        outcome["research"] = validate_research(outcome["research"])
    history = outcome.get("research_history", [])
    if not isinstance(history, list) or len(history) > 200:
        raise ValueError("研究修订记录格式错误。")
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("研究修订记录格式错误。")
        dt.datetime.fromisoformat(entry["at"])
        reason = entry.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("修订原因最多 1000 字。")
        entry["values"] = validate_research(entry["values"])
    if history and history[-1]["values"] != outcome.get("research"):
        raise ValueError("最新研究条件与最后一次修订不一致。")


def active_by_symbol(records):
    """Group all versions under one stock; do not count multiple plans as holdings."""
    result = {}
    for record in records:
        code = symbol_id(record.get("plan", {}).get("symbol"))
        if code:
            result.setdefault(code, []).append(record)
    for group in result.values():
        group.sort(key=lambda r: (r.get("created_at", ""), r["id"]), reverse=True)
    return result


def price_checks(record, quote, *, today):
    """Only unadjusted, dated quotes can be compared to user-entered prices."""
    values = research(record)
    messages = []
    if record.get("outcome", {}).get("status") in ("已结束", "已放弃"):
        return messages
    due = norm_date(values.get("review_on"))
    if due and due <= norm_date(today):
        messages.append(dict(kind="due", text=f"复核日 {due} 已到，请更新判断"))
    if not any(values.get(k) is not None for k in PRICE_FIELDS):
        return messages
    date = norm_date((quote or {}).get("date"))
    basis = norm_date(values.get("price_basis_date"))
    close = number((quote or {}).get("close"))
    if not date or not basis or date < basis or date > norm_date(today) or not close or (quote or {}).get("adjusted") is not False:
        messages.append(dict(kind="missing", text="尚无价位适用日之后的未复权收盘价，个人价位待核对"))
        return messages
    low, high = values.get("observe_low"), values.get("observe_high")
    if (low is not None or high is not None) and (low is None or close >= low) and (high is None or close <= high):
        messages.append(dict(kind="range", text=f"{date} 收盘 {close:g} 在自定观察区间内"))
    for key, hit, label in (("invalid_below", lambda p: close <= p, "不高于"),
                            ("invalid_above", lambda p: close >= p, "不低于")):
        price = number(values.get(key))
        if price is not None and hit(price):
            messages.append(dict(kind="price", text=f"{date} 收盘 {close:g} {label}自定复核价 {price:g}"))
    return messages


def holdings(records, quotes):
    """One latest real position per symbol; coverage and unknown exposure stay visible."""
    rows, duplicates = [], []
    for code, versions in active_by_symbol(records).items():
        real = [r for r in versions if r["plan"]["mode"] == "实盘记录"
                and r.get("outcome", {}).get("status") not in ("已结束", "已放弃")
                and research(r).get("shares") is not None]
        if not real:
            continue
        if len(real) > 1:
            duplicates.append(code)
            continue
        record = real[0]
        values, quote = research(record), quotes.get(code) or {}
        shares, cost = number(values.get("shares")), number(values.get("cost_price"))
        if not shares:
            continue
        close = number(quote.get("close"))
        dated = bool(quote.get("adjusted") is False and norm_date(quote.get("date"))
                     and quote["date"] >= norm_date(values.get("position_date")))
        value = close * shares if dated and close and cost else None
        rows.append(dict(symbol=code, name=record["plan"].get("name") or code,
                         industry=quote.get("industry") or "归属待核对", shares=shares,
                         cost=cost, close=close if dated else None, date=quote.get("date", ""),
                         value=value, pnl=(close-cost)*shares if value is not None else None,
                         position_date=values.get("position_date", "")))
    dates = {r["date"] for r in rows if r["value"] is not None}
    complete = bool(rows and len(dates) == 1 and all(r["value"] is not None for r in rows) and not duplicates)
    total = sum(r["value"] for r in rows) if complete else None
    for row in rows:
        row["weight"] = row["value"] / total if total else None
    return dict(rows=rows, total=total, complete=complete, duplicates=duplicates)
