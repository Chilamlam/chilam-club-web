"""Fetch only official CFTC futures-only reports; preserve archives on failure."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
from html.parser import HTMLParser
import json
from pathlib import Path
import re

import requests

from cta_positions import START, MARKETS, FAMILIES, SCHEDULE_URL
from market_watch import numeric
from update_schedule import beijing_now

PATH = Path(__file__).resolve().parent / "data/observatory/cta_positions.json"
API = "https://publicreporting.cftc.gov/resource/"


def request(url, params=None):
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, timeout=(5, 18), headers={"User-Agent": "ChilamClub/1.0 public market research"})
            r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError):
            if attempt:
                raise


def normalize(rows, family, start, end):
    result = {}
    for r in rows:
        code = str(r.get("cftc_contract_market_code", ""))
        date = str(r.get("report_date_as_yyyy_mm_dd", ""))[:10]
        dt.date.fromisoformat(date)
        if code not in MARKETS or MARKETS[code]["family"] != family or not start <= date <= end:
            raise ValueError("合约或报告日期不符")
        if r.get("futonly_or_combined") != "FutOnly":
            raise ValueError("拒绝混入期权合并报告")
        values = [numeric(r.get(f)) for f in (*FAMILIES[family]["fields"], "open_interest_all")]
        if any(v is None or v < 0 or not v.is_integer() for v in values):
            raise ValueError("持仓数量缺失或无效")
        long, short, spreading, oi = map(int, values)
        if oi <= 0 or long + spreading > oi or short + spreading > oi:
            raise ValueError("持仓与总未平仓量不符")
        row = dict(code=code, date=date, long=long, short=short, spreading=spreading, oi=oi,
                   market=str(r.get("market_and_exchange_names", "")), units=str(r.get("contract_units", "")))
        key = (date, code)
        if key in result:
            raise ValueError("报告日期与合约重复，拒绝分页重叠")
        result[key] = row
    return [v for _, v in sorted(result.items())]


def fetch_family(family, start, end):
    codes = [code for code, meta in MARKETS.items() if meta["family"] == family]
    fields = ["report_date_as_yyyy_mm_dd", "cftc_contract_market_code", "market_and_exchange_names",
              "contract_units", "futonly_or_combined", "open_interest_all", *FAMILIES[family]["fields"]]
    params = {"$select": ",".join(fields),
              "$where": "cftc_contract_market_code in (" + ",".join("'"+c+"'" for c in codes) + ")"
                        + f" AND report_date_as_yyyy_mm_dd >= '{start}T00:00:00' AND report_date_as_yyyy_mm_dd <= '{end}T23:59:59'",
              "$order": "report_date_as_yyyy_mm_dd ASC,cftc_contract_market_code ASC", "$limit": 2000}
    raw = []
    # Move the offset by the actual response size, not the requested page size.
    # An explicit empty page is the termination condition.
    for _ in range(20):
        batch = request(API + FAMILIES[family]["id"] + ".json", dict(params, **{"$offset":len(raw)})).json()
        if not isinstance(batch, list):
            raise ValueError("CFTC未返回记录列表")
        if not batch:
            break
        raw.extend(batch)
    else:
        raise ValueError("CFTC分页未结束")
    rows = normalize(raw, family, start, end)
    if {r["code"] for r in rows} != set(codes):
        raise ValueError("本次查询未取得全部观察合约")
    return rows


class ScheduleTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None
    def handle_starttag(self, tag, attrs):
        if tag == "tr": self.row = []
        if tag in ("td", "th"): self.cell = []
    def handle_data(self, text):
        if self.cell is not None: self.cell.append(text)
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            if self.row is not None: self.row.append("".join(self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row); self.row = None


def parse_schedule(html):
    year = re.search(r"(20\d{2})\s+Release Schedule", html)
    if not year:
        raise ValueError("未取得发布时间表年份")
    year = int(year[1])
    parser = ScheduleTable(); parser.feed(html)
    months = "January February March April May June July August September October November December".split()
    dates, found = [], set()
    for row in parser.rows:
        if not row or row[0].strip() not in months: continue
        month = months.index(row[0].strip()) + 1
        if month in found: raise ValueError("时间表月份重复")
        found.add(month)
        for cell in row[1:]:
            value = cell.strip().rstrip("*").strip()
            if not value: continue
            dates.append(dt.date(year, month, int(value)).isoformat())
    if len(found) != 12 or not 48 <= len(dates) <= 54 or len(set(dates)) != len(dates):
        raise ValueError("发布时间表不完整")
    return dict(year=year, dates=sorted(dates), source=SCHEDULE_URL)


def fetch(full=False):
    now = beijing_now()
    end = now.date().isoformat()
    start = START if full else (now.date() - dt.timedelta(weeks=18)).isoformat()
    payload = dict(version=1, checked_at=now.isoformat(), start=start, end=end, families={}, schedule={}, errors=[])
    def one(family):
        try:
            return family, dict(status="ok", checked_at=now.isoformat(), rows=fetch_family(family, start, end)), None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return family, dict(status="unavailable", checked_at=now.isoformat(), rows=[]), f"{FAMILIES[family]['name']}：{type(exc).__name__}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        for family, result, error in pool.map(one, FAMILIES):
            payload["families"][family] = result
            if error: payload["errors"].append(error)
    try:
        payload["schedule"] = dict(parse_schedule(request(SCHEDULE_URL).text), checked_at=now.isoformat())
    except (requests.RequestException, ValueError, TypeError) as exc:
        payload["errors"].append(f"官方发布时间表：{type(exc).__name__}")
    return payload


def merge(archive, fresh):
    """Replace only successfully fetched report families within their queried date range."""
    if (fresh.get('checked_at') or '') < (archive.get('checked_at') or ''):
        return archive  # An older hourly cache cannot roll back a newly deployed archive.
    rows = {(r['date'], r['code']): r for r in archive.get('rows', [])}
    sources = dict(archive.get('sources', {}))
    errors = list(fresh.get('errors', []))
    for family, entry in fresh.get('families', {}).items():
        if entry.get('status') != 'ok': continue
        codes = [c for c,m in MARKETS.items() if m['family'] == family]
        regressed = any(max((r['date'] for r in entry['rows'] if r['code']==c),default='')
                        < max((r['date'] for r in rows.values() if r['code']==c),default='') for c in codes)
        if regressed:
            errors.append(f"{FAMILIES[family]['name']}：来源返回的最新持仓日期倒退，保留已有数据")
            continue
        rows = {k:r for k,r in rows.items() if MARKETS[r['code']]['family'] != family
                or not fresh['start'] <= r['date'] <= fresh['end']}
        rows.update({(r['date'],r['code']):r for r in entry['rows']})
        sources[family] = dict(checked_at=entry['checked_at'], latest=max(r['date'] for r in entry['rows']))
    return dict(version=1, checked_at=fresh.get('checked_at'), rows=[v for _,v in sorted(rows.items())],
                sources=sources, schedule=fresh.get('schedule') or archive.get('schedule', {}),
                errors=errors, status='partial' if errors else 'ok',
                history_kind='历史回填及后续修订；首次取数时间不等于原始公布时间')


def read():
    try: return json.loads(PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError): return {}


def publish(payload):
    if not payload.get('rows'): raise ValueError('没有持仓记录，保留已有归档')
    PATH.parent.mkdir(parents=True,exist_ok=True)
    tmp = PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    tmp.replace(PATH)
