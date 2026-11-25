"""Actual daily limit-up attribution, with one named provider per comparison."""
from __future__ import annotations

import re

from data_freshness import norm_date
from ladder_themes import clean_text, stock_code
from decision_workbench import number, symbol_id

PROVIDERS = {"ths_public":"同花顺涨停揭秘", "kpl":"开盘啦"}


def labels(text):
    return sorted({s.strip() for s in re.split(r"[+＋、;；,，|]",clean_text(text)) if s.strip()})


def trading_days(calendar, asof, count=10):
    asof = norm_date(asof)
    start,end=norm_date(calendar.get("start_date")),norm_date(calendar.get("end_date"))
    if not asof or not start or not end or not start <= asof <= end:
        return []
    return sorted({norm_date(d) for d in calendar.get("open_dates",[]) if norm_date(d) and norm_date(d)<=asof})[-count:]


def day_snapshot(archive, source="ths_public"):
    date=norm_date(archive.get("date"))
    ladder, attribution=archive.get("ladder") or {},archive.get("attribution") or {}
    empty=dict(date=date,available=False,complete=False,groups={},matched=0,total=0,
               source=source,stocks={},projection={},checked_at=attribution.get("checked_at",""))
    if not date or norm_date(ladder.get("date"))!=date or norm_date(attribution.get("date"))!=date:
        return empty
    stocks,conflicts={},set()
    for r in ladder.get("stocks") or []:
        code=stock_code(r.get("code"))
        if not code:
            continue
        if code in stocks and stocks[code]!=r:
            conflicts.add(code)
        stocks[code]=r
    total=len(stocks)
    stocks={k:v for k,v in stocks.items() if k not in conflicts}
    provider=(attribution.get("sources") or {}).get(source) or {}
    records=provider.get("records") or {}
    groups, matched={},0
    accepted={}
    for code,stock in stocks.items():
        reason=records.get(code) or {}
        tags=labels(reason.get("theme"))
        if norm_date(reason.get("date"))!=date or stock_code(reason.get("code"))!=code or not tags:
            continue
        matched+=1
        accepted[code]=reason
        boards=number(stock.get("limit_times"))
        boards=int(boards) if boards is not None and boards>=1 and boards.is_integer() else None
        member=dict(code=code,symbol=symbol_id(code,equity=True),name=clean_text(stock.get("name")),
                    boards=boards,first_time=clean_text(stock.get("first_time")),theme=clean_text(reason.get("theme")),
                    reason=clean_text(reason.get("reason")),source=PROVIDERS.get(source,source))
        for tag in tags:
            groups.setdefault(tag,[]).append(member)
    for members in groups.values():
        members.sort(key=lambda r:(-(r['boards'] or 0),r['first_time'] or '99',r['code']))
    projection=dict(date=date,ladder=ladder,attribution=dict(date=date,sources={source:dict(provider,records=accepted)}))
    return dict(empty,available=matched>0,complete=total>0 and matched==total,
                matched=matched,total=total,groups=groups,stocks=stocks,projection=projection,
                status=provider.get("status","unknown"))


def observed_count(day, theme):
    if not day or not day.get("available"):
        return None
    if theme in day["groups"]:
        return len(day["groups"][theme])
    return 0 if day["complete"] else None


def activity(archives, calendar, asof, source="ths_public"):
    asof=norm_date(asof)
    days=trading_days(calendar,asof)
    snapshots={d:day_snapshot((archives.get(d) or {}) if norm_date((archives.get(d) or {}).get('date'))==d else {},source) for d in days}
    current=snapshots.get(asof) or day_snapshot({},source)
    previous=snapshots.get(days[-2]) if len(days)>1 else None
    rows=[]
    themes=set().union(*(s['groups'] for s in snapshots.values())) if current['available'] else set()
    for theme in themes:
        members=current['groups'].get(theme,[])
        counts=[observed_count(snapshots[d],theme) for d in days]
        today=observed_count(current,theme)
        old=observed_count(previous,theme)
        heights=[r['boards'] for r in members if r['boards'] is not None]
        streak=0
        for count in reversed(counts):
            if count is None or count==0:
                break
            streak+=1
        rows.append(dict(theme=theme,count=today,previous=old,
            change=today-old if today is not None and old is not None and current['complete'] and previous['complete'] else None,
            height=max(heights) if heights else None,leaders='、'.join(r['name'] for r in members[:3]),
            appearances=sum(c is not None and c>0 for c in counts),observed=sum(c is not None for c in counts),
            peak=max((c for c in counts if c is not None),default=0),
            streak=streak,members=members,counts=counts))
    rows.sort(key=lambda r:(-(r['count'] or 0),-(r['height'] or 0),r['theme']))
    return dict(date=asof,days=days,snapshots=snapshots,current=current,previous=previous,rows=rows,
        source=source,source_name=PROVIDERS[source],missing=[d for d,s in snapshots.items() if not s['available']])


def member_changes(result, theme):
    current, previous=result['current'],result['previous']
    now={m['code']:m for m in current['groups'].get(theme,[])}
    if previous is None or not previous['available']:
        return dict(previous_date=None,old=[],retained=[],new=list(now.values()),exited=[],known=False)
    old={m['code']:m for m in previous['groups'].get(theme,[])}
    return dict(previous_date=previous['date'],old=list(old.values()),retained=sorted(set(now)&set(old)),
        new=[m for k,m in now.items() if k not in old],exited=[dict(m,today_limit=k in current['stocks']) for k,m in old.items() if k not in now],
        known=previous['complete'] and current['complete'])
