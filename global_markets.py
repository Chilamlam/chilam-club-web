"""Read-only Polymarket and Hyperliquid observations; no trading/wallet endpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import re

import requests

from market_watch import numeric
from market_watch_data import bundle, calendar, request
from update_schedule import beijing_now, CST

GAMMA = "https://gamma-api.polymarket.com"
HYPERLIQUID = "https://api.hyperliquid.xyz/info"
CHAIN_ASSETS = {
    "BTC": ("比特币", "数字资产"), "ETH": ("以太坊", "数字资产"),
    "xyz:GOLD": ("黄金", "商品"), "xyz:SILVER": ("白银", "商品"),
    "xyz:CL": ("WTI 原油", "商品"), "xyz:BRENTOIL": ("布伦特原油", "商品"), "xyz:COPPER": ("铜", "商品"),
    "xyz:SP500": ("标普500", "股指"), "xyz:XYZ100": ("XYZ100 合约", "股指"),
    "xyz:NVDA": ("英伟达", "海外股票"), "xyz:TSLA": ("特斯拉", "海外股票"),
    "xyz:AAPL": ("苹果", "海外股票"), "xyz:MSFT": ("微软", "海外股票"),
    "xyz:GOOGL": ("谷歌", "海外股票"), "xyz:AMZN": ("亚马逊", "海外股票"),
    "xyz:AMD": ("AMD", "海外股票"), "xyz:MU": ("美光", "海外股票"),
    "xyz:TSM": ("台积电", "海外股票"), "xyz:SKHX": ("海力士", "海外股票"),
    "xyz:BABA": ("阿里巴巴", "中国资产"), "xyz:TENCENT": ("腾讯", "中国资产"),
    "xyz:CAMBRICON": ("寒武纪", "中国资产"), "xyz:GIGADEV": ("兆易创新", "中国资产"),
    "xyz:CXMT": ("长鑫科技", "中国资产"), "xyz:INNOLIGHT": ("中际旭创", "中国资产"),
    "xyz:CATL": ("宁德时代", "中国资产"), "xyz:AMEC": ("中微公司", "中国资产"),
}


def json_list(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, list) else []
    except ValueError:
        return []


def category(title):
    text = title.casefold()
    for label, words in (
        ("央行与利率", r"\bfed\b|fomc|interest rate|treasury|central bank|bank of|reserve bank"),
        ("经济数据", r"inflation|\bcpi\b|\bpce\b|\bgdp\b|unemployment|jobs added|\bpmi\b|recession"),
        ("贸易与政策", r"tariff|trade deal|duty rate|hormuz|diesel|export|import"),
        ("商品与汇率", r"gold|oil|silver|copper|gas|\busd\b|currency"),
        ("股市与公司", r"ipo|company|companies|valuation|market cap|s&p|spx|nasdaq|stock|shares|earnings"),
    ):
        if re.search(words, text):
            return label
    return "其他金融事件"


def event_label(event):
    title = str(event.get("title") or "")
    # Translate only unambiguous templates; original question remains visible.
    end = str(event.get("endDate") or "")[:7]
    if re.match(r"^Fed Decision in \w+\?", title):
        return f"美联储议息 · {end}"
    replacements = {"Inflation US - Annual":"美国 CPI 同比", "Inflation US - Monthly":"美国 CPI 环比",
                    "Unemployment Rate":"失业率", "Another Fed rate hike":"美联储再次加息",
                    "How many Fed rate cuts":"美联储降息次数", "US GDP growth":"美国 GDP 增速",
                    "China GDP growth":"中国 GDP 增速"}
    for original, cn in replacements.items():
        if original.casefold() in title.casefold():
            return f"{cn} · {end}"
    return title


def outcome_label(value):
    label = str(value or "Yes（是）")
    if label.casefold() == "no change":
        return "维持利率"
    match = re.fullmatch(r"(\d+)(\+?) bps (decrease|increase)", label, re.I)
    if match:
        return ("降息" if match[3].lower()=="decrease" else "加息")+match[1]+"基点"+("及以上" if match[2] else "")
    return label


def normalize_events(events):
    result=[]
    for event in events:
        if event.get("closed") or event.get("archived") or event.get("active") is not True:
            continue
        markets=[]
        for m in event.get("markets") or []:
            if m.get("closed") or m.get("active") is not True:
                continue
            outcomes, prices, tokens = (json_list(m.get(k)) for k in ("outcomes", "outcomePrices", "clobTokenIds"))
            if len(outcomes) != 2 or len(outcomes) != len(prices) or {str(o).lower() for o in outcomes} != {"yes", "no"}:
                continue
            yes = next((i for i, name in enumerate(outcomes) if str(name).lower() == "yes"), None)
            if yes is None:  # do not mislabel a non-binary outcome as Yes
                continue
            price = numeric(prices[yes])
            if price is None or not 0 <= price <= 1:
                continue
            bid, ask = (numeric(m.get(k)) for k in ("bestBid", "bestAsk")) if yes == 0 else (None, None)
            spread = (ask-bid)*100 if bid is not None and ask is not None and 0 <= bid <= ask <= 1 else None
            change = numeric(m.get("oneDayPriceChange"))
            markets.append(dict(id=str(m.get("id")), question=m.get("question"), label=outcome_label(m.get("groupItemTitle")),
                probability=price*100, change_pp=change*100 if change is not None and yes == 0 else None,
                volume24=numeric(m.get("volume24hr")), liquidity=numeric(m.get("liquidityNum")),
                spread_pp=spread, token=str(tokens[yes]) if len(tokens)>yes else "",
                rules=m.get("description") or event.get("description") or "",
                end=m.get("endDate"), updated_at=m.get("updatedAt")))
        if markets:
            slug = str(event.get("slug") or "")
            if not re.fullmatch(r"[a-zA-Z0-9-]+", slug):
                continue
            result.append(dict(id=str(event.get("id")), slug=slug, title=event_label(event), original=event.get("title"),
                category=category(str(event.get("title") or "")), end=event.get("endDate"),
                volume24=numeric(event.get("volume24hr")), markets=markets))
    return sorted(result, key=lambda e: -(e["volume24"] or 0))


def collect_polymarket():
    def one(slug):
        try:
            tag = request(f"{GAMMA}/tags/slug/{slug}").json()
            rows = request(f"{GAMMA}/events", params={"tag_id":tag["id"], "active":"true", "closed":"false",
                "order":"volume24hr", "ascending":"false", "limit":100}).json()
            if not isinstance(rows,list):
                raise ValueError("事件列表异常")
            return rows, None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return [], f"{slug} 类别本次获取失败（{type(exc).__name__}）"
    events, errors = {}, []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for rows, error in pool.map(one, ("economy", "finance")):
            for event in rows:
                events[str(event.get("id"))] = event
            if error:
                errors.append(error)
    return bundle(normalize_events(events.values()), "Polymarket Gamma API · economy / finance 各取24小时成交额前100个未关闭事件", errors)


def probability_history(token, days=30):
    if not re.fullmatch(r"\d{1,100}", token):
        raise ValueError("缺少有效合约标识")
    now = int(beijing_now().timestamp())
    data = request("https://clob.polymarket.com/prices-history", params={"market":token, "startTs":now-days*86400,
        "endTs":now, "fidelity":60 if days > 1 else 5}).json()
    return [dict(time=int(r["t"]), probability=float(r["p"])*100) for r in data.get("history", [])
            if numeric(r.get("p")) is not None and 0 <= float(r["p"]) <= 1 and now-days*86400 <= int(r["t"]) <= now]


def ashare_close(now=None):
    now = now or beijing_now()
    days = [day for day in calendar().get("open_dates", []) if day < now.strftime("%Y%m%d")
            or (day == now.strftime("%Y%m%d") and now.time().replace(tzinfo=None) >= dt.time(15))]
    if not days:
        return None
    return dt.datetime.strptime(max(days)+"1500", "%Y%m%d%H%M").replace(tzinfo=CST)


def normalize_chain(payload, checked_at):
    if not isinstance(payload, list) or len(payload) != 2:
        raise ValueError("合约列表异常")
    universe = payload[0].get("universe") or []
    if len(universe) != len(payload[1]):
        raise ValueError("合约与行情数量不一致")
    rows=[]
    for meta, ctx in zip(universe, payload[1]):
        symbol = meta.get("name")
        if symbol not in CHAIN_ASSETS or meta.get("isDelisted"):
            continue
        price, previous = numeric(ctx.get("markPx")), numeric(ctx.get("prevDayPx"))
        if price is None or price <= 0:
            continue
        oi = numeric(ctx.get("openInterest"))
        oracle = numeric(ctx.get("oraclePx"))
        rows.append(dict(symbol=symbol, name=CHAIN_ASSETS[symbol][0], category=CHAIN_ASSETS[symbol][1],
            price=price, change24=(price/previous-1)*100 if previous and previous>0 else None,
            volume24=numeric(ctx.get("dayNtlVlm")), oi_usd=oi*price if oi is not None else None,
            oracle=oracle, oracle_gap=(price/oracle-1)*100 if oracle and oracle>0 else None,
            checked_at=checked_at, venue="Hyperliquid · xyz" if ":" in symbol else "Hyperliquid"))
    return rows


def candle_observation(candles, close_ms, now_ms):
    valid = []
    for c in candles:
        t, end, price, volume = (numeric(c.get(k)) for k in ("t", "T", "c", "v"))
        if None in (t,end,price,volume) or end > now_ms or end < t or price <= 0 or volume <= 0:
            continue
        valid.append(dict(time=int(t), end=int(end), close=price, volume=volume))
    valid.sort(key=lambda c:c["time"])
    before = [c for c in valid if close_ms-30*60000 <= c["time"] < close_ms and c["end"] < close_ms]
    after = [c for c in valid if c["time"] >= close_ms]
    return dict(baseline=before[-1] if before else None, candles=after,
                volume=sum(c["volume"] for c in after) if after else None,
                through=after[-1]["end"] if after else None)


def chain_history(symbol, *, now=None):
    if symbol not in CHAIN_ASSETS:
        raise ValueError("未收录该合约")
    now = now or beijing_now()
    close = ashare_close(now)
    if close is None:
        raise ValueError("交易日历未覆盖")
    close_ms, now_ms = int(close.timestamp()*1000), int(now.timestamp()*1000)
    data = request(HYPERLIQUID, payload={"type":"candleSnapshot", "req":{
        "coin":symbol, "interval":"15m", "startTime":close_ms-30*60000, "endTime":now_ms}}).json()
    if not isinstance(data,list):
        raise ValueError("合约成交历史为空")
    return dict(candle_observation(data, close_ms, now_ms), close_at=close.isoformat(), checked_at=now.isoformat())


def after_close_change(quote, history):
    base = history.get("baseline")
    price = numeric(quote.get("price"))
    if not base or not price:
        return None
    try:
        quoted = dt.datetime.fromisoformat(quote["checked_at"])
        close = dt.datetime.fromisoformat(history["close_at"])
        if quoted < close:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return (price/base["close"]-1)*100


def collect_onchain():
    checked_at = beijing_now().isoformat()
    def one(dex):
        try:
            data = request(HYPERLIQUID, payload={"type":"metaAndAssetCtxs", "dex":dex}).json()
            return normalize_chain(data, checked_at), None
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            return [], f"{dex or '主市场'} 行情本次获取失败（{type(exc).__name__}）"
    rows, errors = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for group, error in pool.map(one, ("", "xyz")):
            rows.extend(group)
            if error:
                errors.append(error)
    return bundle(rows, "Hyperliquid 官方 Info API · 主市场和 xyz 永续合约", errors, checked_at=checked_at)
