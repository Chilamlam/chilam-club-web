"""Offline, testable evidence assembly and personal decision journal.

No quotes, orders, network requests, Streamlit, or prediction model here.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import uuid

import pandas as pd

from data_freshness import norm_date
from strategy_playbooks import PLAYBOOKS, VERSION

SCHEMA_VERSION = 1
MODES = ("模拟观察", "实盘记录")
STATUSES = ("观察中", "已执行", "已放弃", "已结束")


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def symbol_id(value, *, equity=False):
    text = str(value or "").strip().upper()
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
        return text
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", text):
        return text[2:] + "." + text[:2]
    if equity and re.fullmatch(r"\d{6}", text):
        market = "BJ" if text.startswith(("43", "83", "87", "88", "92")) else "SH" if text.startswith("6") else "SZ"
        return text + "." + market
    return ""


def snapshot_date(frame):
    """A snapshot must have one valid date on every row, not just the last row."""
    if frame is None or frame.empty:
        return ""
    for col in ("更新日期", "update_date", "date"):
        if col in frame:
            dates = set(frame[col].map(norm_date))
            return next(iter(dates)) if len(dates) == 1 and "" not in dates else ""
    return ""


def market_evidence(sentiment, market, evening, *, asof):
    """Facts stay separate from heuristics; conflicting signals remain visible."""
    facts, warnings = [], []
    valid_sentiment = norm_date(sentiment.get("date")) == asof
    if valid_sentiment:
        for key, label, field, unit in (
            ("breadth", "全市场上涨占比", "up_ratio", "%"),
            ("breadth", "个股中位涨跌幅", "median_pct", "%"),
            ("premium", "昨日连板股今日中位涨跌幅", "median_pct", "%"),
        ):
            block = sentiment.get(key) or {}
            val = number(block.get(field)) if block.get("status") == "ok" else None
            if field == "up_ratio" and val is not None:
                val *= 100
            facts.append(dict(label=label, value=val, unit=unit, date=asof,
                              source="data/sentiment/derived.json",
                              sample=block.get("n"), missing=block.get("missing")))
        promotion = sentiment.get("promotion") or {}
        promo = promotion.get("rates", {}).get("1进2", {}) if promotion.get("status") == "ok" else {}
        rate = number(promo.get("rate"))
        facts.append(dict(label="首板次日晋级率", value=rate * 100 if rate is not None else None,
                          unit="%", date=asof, source="data/sentiment/derived.json",
                          sample=promo.get("base"), missing=None))
        premium = sentiment.get("premium") or {}
        val = number(premium.get("median_pct"))
        if premium.get("status") == "ok" and val is not None and val < 0:
            warnings.append("昨日连板样本的中位涨跌幅为负：晋级比例或个别高标不能代表整体接力收益。")
    else:
        warnings.append("短线情绪缺少同日证据，暂不判读情绪环境。")
    # Market history is a time series, unlike a dated candidate snapshot.
    if market is not None and not market.empty and "date" in market:
        hist = market.copy()
        hist["_date"] = hist["date"].map(norm_date)
        hist = hist[(hist["_date"] != "") & (hist["_date"] <= asof)].sort_values("_date").drop_duplicates("_date")
        if not hist.empty and hist.iloc[-1]["_date"] == asof:
            val = number(hist.iloc[-1].get("total_amount_yi"))
            facts.append(dict(label="全市场成交额", value=val, unit="亿元", date=asof,
                              source="data/market_sentiment.csv", sample=None, missing=None))
            if "total_amount_yi" in hist:
                previous = pd.to_numeric(hist["total_amount_yi"].iloc[:-1].tail(20), errors="coerce")
                if len(previous) == 20 and previous.notna().all() and previous.gt(0).all() and val is not None:
                    facts.append(dict(label="成交额 / 前20个观测日均额", value=val / float(previous.mean()), unit="倍",
                                      date=asof, source="data/market_sentiment.csv", sample=20, missing=0))
    if norm_date(evening.get("date")) == asof:
        for law in (evening.get("cycle_and_laws") or {}).get("active_laws", []):
            if law.get("level") == "warning":
                warnings.append("既有规则提示：" + str(law.get("summary") or law.get("title") or ""))
    return {"date": asof, "facts": facts, "warnings": warnings,
            "unavailable": "融资续接、信用与信任、资金承接主体、账户敞口尚未接入；不能仅凭成交额推断负债区间。"}


def candidate_rows(style, frames, evening, *, asof):
    """Deterministic union of dated evidence, never a recommendation score."""
    book = PLAYBOOKS[style]
    found, excluded = {}, []

    def add(raw, name, source, reason, risks=(), industry="", rps=None, equity=False):
        code = symbol_id(raw, equity=equity)
        if not code:
            return
        row = found.setdefault(code, dict(symbol=code, name=str(name or code), industry=str(industry or ""),
                                         date=asof, reasons=[], risks=[], sources=[], rps=None))
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        if source not in row["sources"]:
            row["sources"].append(source)
        row["risks"] = list(dict.fromkeys(row["risks"] + [str(r) for r in risks]))
        if number(rps) is not None:
            row["rps"] = number(rps)

    keys = ("strong", "breakout") if style == "trend" else ("etf",) if style == "etf" else ()
    labels = {"strong": "RPS强势榜", "breakout": "阶段新高池", "etf": "ETF强势榜"}
    paths = {"strong": "data/strong_stocks.csv", "breakout": "data/breakout_stocks.csv", "etf": "data/strong_etfs.csv"}
    for key in keys:
        frame = frames.get(key)
        if snapshot_date(frame) != asof:
            excluded.append(f"{labels[key]}：缺失、混合日期或与 {asof} 不同日，未合并。")
            continue
        for row in frame.where(pd.notna(frame), None).to_dict("records"):
            rps = number(row.get("RPS_50"))
            reason = str(row.get("level") or labels[key]) if key == "breakout" else f"RPS50 {rps:.1f}" if rps is not None else "入选强势榜（RPS读数缺失）"
            add(row.get("ts_code"), row.get("name"), paths[key], reason,
                industry=row.get("细分行业") or row.get("industry"), rps=rps)
    if book["pools"]:
        if norm_date(evening.get("date")) != asof or evening.get("status") not in ("complete", "partial", "ok"):
            excluded.append("晚间战法池：缺失、失败或不同日，未合并。")
        else:
            for pool_key in book["pools"]:
                pool = (evening.get("pools") or {}).get(pool_key)
                if not isinstance(pool, dict):
                    excluded.append(f"{pool_key}：产物缺少此池，无法判断是否有候选。")
                    continue
                for row in pool.get("items") or []:
                    add(row.get("code"), row.get("name"), f"data/evening_plan/latest.json:{pool_key}",
                        pool.get("title") or pool_key, risks=row.get("risks") or (),
                        industry=row.get("industry"), equity=True)
    # No invented composite score: stable code order, explicit source membership.
    return sorted(found.values(), key=lambda r: r["symbol"]), excluded


def plan_checks(plan):
    issues = []
    if not plan.get("data_current"):
        issues.append("数据未确认最新：本计划仅作历史研究。")
    for field, label in (("thesis", "观察依据"), ("trigger", "触发条件"), ("invalid", "失效条件"),
                         ("exit_rule", "退出与期限"), ("cost_risk", "成本与风险预算")):
        if not str(plan.get(field) or "").strip():
            issues.append(f"尚未写明{label}。")
    if plan.get("funding_check") != "已核对":
        issues.append("资金与承接前提尚未核对或已出现反证。")
    if plan.get("strategy_check") != "有独立记录":
        issues.append("尚无独立策略验证记录，保留为研究假设。")
    if plan.get("capacity") != "能按计划检查":
        issues.append("执行时间或精力尚不匹配，需要调整观察方式。")
    if not str(plan.get("manual_evidence") or "").strip():
        issues.append("缺少人工核对的来源、日期与反证。")
    return issues


def tracking_evidence(record, frames, evening, *, asof):
    """Compare pool membership; absence isn't proof that a price rule failed."""
    original = record["plan"]
    rows, excluded = candidate_rows(original["style"], frames, evening, asof=asof)
    current = next((row for row in rows if row["symbol"] == original["symbol"]), None)
    if asof < original["asof"]:
        message = "现有产物早于原计划，不能用于跟踪。"
        current = None
    elif asof == original["asof"]:
        message = "仍是原计划证据日，尚无更新交易日的结果。"
    elif excluded:
        message = "新日期的部分来源缺失，不能据此断言标的已退出全部候选池。"
    elif current:
        message = "新日期仍命中该风格的观察条件；需继续核对原计划的触发和失效条件。"
    elif original["style"] == "fundamental":
        message = "基本面变化仍需按公告与财报的实际日期人工补充。"
    else:
        message = "新日期未在该风格候选中出现；这不等于止损或原计划失效，需核对价格、逻辑和时间条件。"
    return dict(message=message, current=current, excluded=excluded, date=asof)


def new_plan(fields, *, now):
    value = copy.deepcopy(fields)
    if value.get("style") not in PLAYBOOKS or value.get("mode") not in MODES:
        raise ValueError("请选择有效的研究风格与记录类型。")
    value["symbol"] = symbol_id(value.get("symbol"))
    if not value["symbol"]:
        raise ValueError("请输入带交易所的代码，例如 600519.SH 或 159915.SZ。")
    if not norm_date(value.get("asof")):
        raise ValueError("需要有效的证据日期。")
    for field in ("name", "thesis", "trigger", "invalid", "exit_rule", "cost_risk", "manual_evidence",
                  "funding_check", "strategy_check", "capacity"):
        if not isinstance(value.get(field, ""), str):
            raise ValueError("计划文本字段必须为文字。")
        if len(value.get(field, "")) > 10000:
            raise ValueError("单个文本字段不能超过 10000 字。")
    if not isinstance(value.get("data_current", False), bool):
        raise ValueError("数据核对状态格式错误。")
    if not isinstance(value.get("market_evidence", {}), dict):
        raise ValueError("市场证据格式错误。")
    for field in ("thesis", "trigger", "invalid", "exit_rule", "cost_risk"):
        if not str(value.get(field) or "").strip():
            raise ValueError("请完整填写依据、触发、失效、退出期限及成本风险。")
    value["asof"] = norm_date(value["asof"])
    value["checks"] = plan_checks(value)
    return dict(id=str(uuid.uuid4()), schema=SCHEMA_VERSION, version=VERSION,
                created_at=now, plan=value, outcome={"status": "观察中"}, revision=0)


def with_outcome(record, outcome):
    if outcome.get("status") not in STATUSES:
        raise ValueError("无效的跟踪状态。")
    updated = copy.deepcopy(record)
    result = copy.deepcopy(outcome)
    if result.get("discipline", "待复核") not in ("待复核", "按计划", "偏离计划"):
        raise ValueError("无效的执行质量。")
    if not isinstance(result.get("notes", ""), str) or len(result.get("notes", "")) > 10000:
        raise ValueError("结果说明必须为不超过 10000 字的文字。")
    if result["status"] == "已结束":
        date = norm_date(result.get("closed_on"))
        if not date or date < record["plan"]["asof"]:
            raise ValueError("结束日期不能早于原计划证据日期。")
        gross, costs = number(result.get("gross_pnl")), number(result.get("costs"))
        if gross is None or costs is None or costs < 0:
            raise ValueError("结束记录需填写有效的毛盈亏与非负总成本（元）。")
        result.update(closed_on=date, gross_pnl=gross, costs=costs, net_pnl=gross - costs)
    else:
        for key in ("gross_pnl", "costs", "net_pnl", "closed_on"):
            result.pop(key, None)
    updated["outcome"] = result
    return updated


def journal_stats(records, *, mode, style=None):
    selected = [r for r in records if r["plan"]["mode"] == mode and (not style or r["plan"]["style"] == style)]
    closed = [r for r in selected if r.get("outcome", {}).get("status") == "已结束"
              and number(r["outcome"].get("net_pnl")) is not None]
    pnl = [float(r["outcome"]["net_pnl"]) for r in closed]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p < 0]
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = -sum(losses) / len(losses) if losses else None
    return dict(total=len(selected), n=len(pnl), pending=len(selected) - len(closed),
                win_rate=len(wins) / len(pnl) if pnl else None,
                payoff=avg_win / avg_loss if avg_win is not None and avg_loss else None,
                expectancy=sum(pnl) / len(pnl) if pnl else None, net_total=sum(pnl) if pnl else None,
                breaks=sum(r["outcome"].get("discipline") == "偏离计划" for r in closed))


def export_journal(records):
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
    return json.dumps({"schema": SCHEMA_VERSION, "records": clean}, ensure_ascii=False, indent=2, allow_nan=False)


def import_journal(raw):
    if len(raw) > 2_000_000:
        raise ValueError("文件超过 2 MB，请分批导入。")
    try:
        def reject_constant(value):
            raise ValueError(f"不支持的数值：{value}")
        payload = json.loads(raw, parse_constant=reject_constant)
        records = payload["records"]
        if payload["schema"] != SCHEMA_VERSION or not isinstance(records, list) or len(records) > 1000:
            raise ValueError("不支持的版本或记录数量。")
        clean, ids = [], set()
        for record in records:
            ident = str(uuid.UUID(record["id"]))
            if ident in ids:
                raise ValueError("文件内存在重复计划 ID。")
            ids.add(ident)
            # Validate structure without replacing the original identity/date/style.
            date = dt.datetime.fromisoformat(record["created_at"])
            candidate = new_plan(record["plan"], now=date.isoformat())
            candidate.update(id=ident, version=str(record.get("version") or "unknown"), revision=0,
                             _imported=True)
            candidate = with_outcome(candidate, record["outcome"])
            clean.append(candidate)
        return clean
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise ValueError(f"无法导入复盘记录：{exc}") from exc


def plan_markdown(record):
    p, o = record["plan"], record["outcome"]
    lines = [f"### {p.get('name') or p['symbol']} · {p['symbol']}",
             f"风格：{PLAYBOOKS[p['style']]['name']}｜类型：{p['mode']}｜证据日期：{p['asof']}",
             f"计划 ID：{record['id']}｜建立于：{record['created_at']}"]
    for field, label in (("thesis", "依据"), ("trigger", "触发"), ("invalid", "失效"),
                         ("exit_rule", "退出与期限"), ("cost_risk", "成本与风险"), ("manual_evidence", "核对来源与反证")):
        lines.append(f"{label}：{p.get(field) or '待补充'}")
    lines.extend([f"跟踪状态：{o['status']}", f"结果与偏差：{o.get('notes') or '待记录'}"])
    if o.get("net_pnl") is not None:
        lines.append(f"扣费后盈亏：{o['net_pnl']:.2f} 元（用户记录）")
    return "\n\n".join(lines)
