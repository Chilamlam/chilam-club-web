"""Dated leader constraints, explicit theme hypotheses and reviewable relay plans."""
from __future__ import annotations

import copy
import re
from urllib.parse import urlparse

import abnormal_windows as windows
from decision_workbench import symbol_id
from theme_activity import labels


PAGE = "🧭 题材接力预案"
THS_URL = "https://data.10jqka.com.cn/limit_up/"


def stage(text):
    """THS N天M板 is not M consecutive boards, and a first board is not a low price."""
    if text == "首板":
        return "首板"
    match = re.fullmatch(r"(\d+)天(\d+)板", str(text))
    if match:
        days, boards = map(int, match.groups())
        return "连板" if days == boards and boards > 1 else "反复涨停"
    return "待核对"


def valid_url(value):
    parsed = urlparse(str(value))
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def visible_links(topic, asof):
    # An editorial hypothesis has its own publication date, even if its source is older.
    return [copy.deepcopy(r) for r in topic.get("links", [])
            if r.get("known_on", "9999") <= asof and r.get("source_date", "9999") <= asof
            and valid_url(r.get("url", "")) and r.get("basis")]


def candidates(pool, leader, tags=(), topic=None, include_hypotheses=True):
    """Never infer membership from names or industries. All extensions are explicit."""
    day = pool["date"]
    links = {r["symbol"]: r for r in visible_links(topic or {}, day)}
    result = []
    for raw in pool.get("stocks", []):
        if raw["symbol"] == leader:
            continue
        link = links.get(raw["symbol"])
        matched = sorted(set(labels(raw.get("theme", ""))) & set(tags))
        if link and (include_hypotheses or link.get("kind") == "业务关联"):
            relation = dict(link)
        elif matched:
            relation = dict(kind="同标签观察", basis="同花顺当日共同标签：" + "、".join(matched),
                            url=THS_URL, source_date=day, known_on=day,
                            caveat="共同标签不证明资金承接，也不自动确认补涨关系。")
        else:
            continue
        result.append(dict(raw, relation=relation, stage=stage(raw.get("height", ""))))
    result.sort(key=lambda r: (r["stage"] != "首板", r.get("first_time") or "99", r["symbol"]))
    return result


def price_context(series, symbol, asof, calendar, name=""):
    rows = sorted([r for r in series.get(symbol, []) if r["date"] <= asof], key=lambda r:r["date"])
    expected = [d for d in calendar if d <= asof][-21:]
    if (len(expected) < 21 or len(rows) < 21 or [r["date"] for r in rows[-21:]] != expected):
        return dict(available=False)
    recent = rows[-21:]
    if any(r.get("adj_close", 0) <= 0 for r in recent):
        return dict(available=False)
    result = dict(available=True, date=asof, close=recent[-1]["close"],
                  five=(recent[-1]["adj_close"]/recent[-6]["adj_close"]-1)*100,
                  ten=(recent[-1]["adj_close"]/recent[-11]["adj_close"]-1)*100,
                  twenty=(recent[-1]["adj_close"]/recent[0]["adj_close"]-1)*100)
    try:
        rules = windows.market_rules(symbol, name)
        # Corporate actions change the raw previous-close basis; don't label them from raw bars.
        action = any(abs(a['adj_close']/a['close']-b['adj_close']/b['close']) > 1e-6
                     for a,b in zip(recent,recent[1:]))
        paths = windows.path_rows(recent, recent[1]["date"], rules)
        hits = [r["date"] for r in paths if r["event"] == "收盘涨停"] if not action else None
        result.update(limit_dates=hits, prior_limits=sum(d < asof for d in hits) if hits is not None else None)
    except ValueError:
        result.update(limit_dates=None, prior_limits=None)
    return result


def outlook(rows, events, calendar, rules, first_change=10.0, index_change=0.0):
    """Tomorrow and conditional day-after boundaries share one explicit price path."""
    dates = windows.future_dates(calendar, rows[-1]["date"], 2)
    known = windows.known_events(events, rows[-1]["date"])
    state = windows.analyse(rows, known, rules)[-1]
    if state["serious_reasons"]:
        return dict(state=state, dates=dates, tomorrow=None, after=None, path=[], blocked=True)
    tomorrow = windows.next_boundaries(rows, known, dates[0], index_change, rules)
    path = windows.project(rows, known, dates[:1], first_change, index_change, rules=rules)
    after = None
    if path and not path[0]["serious_reasons"]:
        last = rows[-1]
        p = path[0]["close"]
        assumed = dict(last, date=dates[0], close=p, adj_close=last["adj_close"]*p/last["close"],
                       index=last["index"]*(1+index_change/100),open=p,high=p,low=p)
        after = windows.next_boundaries(rows + [assumed], known, dates[1], index_change, rules)
    return dict(state=state, dates=dates, tomorrow=tomorrow, after=after, path=path, blocked=False,
                first_change=first_change, index_change=index_change)


def nearest(boundaries):
    eligible = [b for b in boundaries or [] if b["serious"]]
    return min(eligible,key=lambda b:b["trigger_price"]) if eligible else None


def leader_watch(pool, series, events, calendar):
    """Scan actual multi-board paths for a reachable constraint over two sessions."""
    found, missing = [], []
    dates=windows.future_dates(calendar,pool['date'],2)
    for stock in pool['stocks']:
        if stage(stock.get('height','')) not in ('连板','反复涨停'):
            continue
        try:
            rules=windows.market_rules(stock['symbol'],stock['name'])
            rows=windows.aligned(series.get(stock['symbol'],[]),series.get(rules['benchmark'],[]),calendar,pool['date'])
            known=events.get(stock['symbol'],[])
            state=windows.analyse(rows,known,rules)[-1]
            if state['serious_reasons']:
                found.append(dict(stock,when='本日已触及',boundary=None,note='先核对正式公告与监管状态'))
                continue
            tomorrow=nearest(windows.next_boundaries(rows,known,dates[0],rules=rules))
            if tomorrow and tomorrow['within_limit']:
                found.append(dict(stock,when=dates[0],boundary=tomorrow['trigger_price'],
                    note=f"指数持平，收盘较本日{tomorrow['trigger_pct']:+.2f}%触及"))
                continue
            projection=outlook(rows,known,calendar,rules,first_change=rules['limit'])
            after=nearest(projection['after'])
            if after and after['within_limit']:
                found.append(dict(stock,when=dates[1],boundary=after['trigger_price'],
                    note=f"假设明日涨停、指数两日持平；后日再涨{after['trigger_pct']:+.2f}%触及"))
        except (ValueError,KeyError,IndexError,TypeError) as exc:
            missing.append(dict(name=stock['name'],reason=str(exc)))
    return dict(rows=found,missing=missing)


def plan_fields(candidate, leader, topic, asof, result, user_fields, evidence_date):
    """Freeze the thesis, selected candidate and conditional path, never future results."""
    relation = candidate["relation"]
    evidence = dict(topic=topic, leader=leader["symbol"], leader_name=leader["name"],
                    candidate=copy.deepcopy(candidate), date=asof, quotes_date=evidence_date,
                    outlook=copy.deepcopy(result))
    return dict(symbol=candidate["symbol"], name=candidate["name"], style="relay", mode="模拟观察",
                asof=asof.replace("-", ""), data_current=False,
                market_evidence={"theme_relay": evidence},
                manual_evidence=f"{relation['kind']}：{relation['basis']}\n来源 {relation['url']}\n"
                                f"证据日 {relation['source_date']}；研究假设录入日 {relation['known_on']}",
                **user_fields)


def follow_up(evidence, series, calendar):
    """Return dated facts for the next two sessions, not a mechanical thesis verdict."""
    asof = evidence["date"]
    result = []
    dates = windows.future_dates(calendar, asof, 2)
    for symbol, role in [(evidence["leader"], "原观察龙头"),
                         (evidence["candidate"]["symbol"], "原观察候选")]:
        lookup = {r["date"]:r for r in series.get(symbol, [])}
        base = lookup.get(asof)
        for date in dates:
            row = lookup.get(date)
            result.append(dict(symbol=symbol, role=role, date=date,
                change=(row["adj_close"]/base["adj_close"]-1)*100 if row and base else None,
                close=row["close"] if row and base else None))
    return result
