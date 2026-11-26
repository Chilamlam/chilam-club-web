"""USD ETF option research: explicit contracts, expiration payoff, frozen premises.

No pricing model, probabilities, trade execution or portfolio cross-hedge claims.
Standard 100-share contracts only. Cashflows are per share in quotes, USD in P/L.
"""
from __future__ import annotations

import copy
import datetime as dt
import math

UNIVERSE = {"SPY": "标普500", "QQQ": "纳斯达克100", "IWM": "罗素2000"}
STRATEGIES = {
    "long_call": "买入看涨 Call", "bull_call": "看涨价差",
    "long_put": "买入看跌 Put", "bear_put": "看跌价差",
    "protective_put": "持仓 + 保护性 Put", "collar": "持仓 + 领口保护",
}
HEDGES = {"protective_put", "collar"}
PAGE = "🇺🇸 美股期权策略"
SOURCES = {
    "ETF合约与交割（OCC）": "https://www.theocc.com/clearance-and-settlement/clearing/etf-options",
    "看涨价差（OIC）": "https://www.optionseducation.org/strategies/all-strategies/bull-call-spread-debit-call-spread",
    "看跌价差（OIC）": "https://www.optionseducation.org/strategies/all-strategies/bear-put-spread",
    "保护性Put（OIC）": "https://www.optionseducation.org/strategies/all-strategies/protective-put-married-put",
    "领口保护（OIC）": "https://www.optionseducation.org/strategies/all-strategies/collar-protective-collar",
}


def numeric(value):
    try:
        number = float(str(value).replace(",", ""))
        return number if math.isfinite(number) and not isinstance(value, bool) else None
    except (ValueError, TypeError):
        return None


def positive(value, label, *, zero=False):
    val = numeric(value)
    if val is None or val < 0 or (not zero and val == 0):
        raise ValueError(f"{label}必须是{'非负' if zero else '正'}数。")
    return val


def contract_id(symbol, expiry, kind, strike):
    if symbol not in UNIVERSE or kind not in ("C", "P"):
        raise ValueError("仅支持 SPY、QQQ、IWM 的标准 Call / Put。")
    date = dt.date.fromisoformat(expiry)
    k = positive(strike, "行权价")
    if abs(k * 1000 - round(k * 1000)) > 1e-6:
        raise ValueError("行权价精度不能超过三位小数。")
    return f"{symbol}{date:%y%m%d}{kind}{round(k * 1000):08d}"


def valid_quote(option):
    bid, ask = numeric(option.get("bid")), numeric(option.get("ask"))
    return bid is not None and ask is not None and 0 < bid <= ask


def make_strategy(snapshot, strategy, expiry, buy_strike, sell_strike=None, *,
                  contracts=1, shares=0, fee=1.30):
    """Conservative entry: buy at ask, sell at bid; fees cover a full round trip.

    `shares` means the same ETF already owned, valued at snapshot spot; its old
    cost basis and distributions are deliberately not included in forward P/L.
    """
    if strategy not in STRATEGIES:
        raise ValueError("不支持的策略。")
    symbol = snapshot.get("symbol")
    spot = positive(snapshot.get("spot"), "标的参考价")
    count = positive(contracts, "组合组数")
    if int(count) != count or count > 10000:
        raise ValueError("组合组数应为 1 至 10000 的整数。")
    count = int(count)
    shares = positive(shares, "已有股数", zero=True)
    if shares != int(shares):
        raise ValueError("已有股数须为整数。")
    fee = positive(fee, "每张期权往返费用", zero=True)
    buy = positive(buy_strike, "买入行权价")
    has_short = strategy in ("bull_call", "bear_put", "collar")
    sell = positive(sell_strike, "卖出行权价") if has_short else None
    if strategy in HEDGES:
        if shares < count * 100:
            raise ValueError("每组保护需要至少 100 股同一 ETF；当前股数不足。")
        if buy > spot:
            raise ValueError("本页持仓保护的 Put 行权价须不高于当前参考价。")
    elif shares:
        raise ValueError("方向交易不应混入已有持仓。")
    if strategy == "bull_call" and sell <= buy:
        raise ValueError("看涨价差须买低行权价 Call、卖高行权价 Call。")
    if strategy == "bear_put" and sell >= buy:
        raise ValueError("看跌价差须买高行权价 Put、卖低行权价 Put。")
    if strategy == "collar" and sell <= spot:
        raise ValueError("领口保护卖出的 Call 行权价须高于当前参考价。")
    kind = "C" if strategy in ("long_call", "bull_call") else "P"
    specs = [(1, kind, buy)]
    if has_short:
        specs.append((-1, "C" if strategy == "collar" else kind, sell))
    legs = []
    for side, leg_kind, strike in specs:
        ident = contract_id(symbol, expiry, leg_kind, strike)
        option = next((x for x in snapshot.get("options", []) if x.get("id") == ident), None)
        if not option or option.get("multiplier") != 100 or not valid_quote(option):
            raise ValueError(f"{ident} 缺少有效双边报价，或不是标准 100 股合约。")
        if option.get("kind") != leg_kind or option.get("strike") != strike or option.get("expiry") != expiry:
            raise ValueError("合约代码与类型、行权价或到期日不一致。")
        leg = copy.deepcopy(option)
        leg.update(side=side, quantity=count, entry=option["ask"] if side == 1 else option["bid"])
        legs.append(leg)
    premium = sum(leg["side"] * leg["entry"] * 100 * count for leg in legs)
    if strategy in ("bull_call", "bear_put") and not 0 < premium < abs(buy - sell) * 100 * count:
        raise ValueError("价差净支出不在 0 与行权价差之间，请复核不同腿的报价。")
    fees = fee * count * len(legs)
    return dict(strategy=strategy, symbol=symbol, expiry=expiry, spot=spot,
                contracts=count, shares=int(shares), legs=legs, premium=premium,
                fees=fees, fee_per_contract=fee, currency="USD",
                source=snapshot.get("source", ""), retrieved_at=snapshot.get("retrieved_at", ""),
                quote_time=snapshot.get("quote_time"), underlying_asof=snapshot.get("underlying_asof", ""),
                quote_mode=snapshot.get("quote_mode", "public_reference"))


def expiry_pnl(plan, price):
    price = positive(price, "到期价格", zero=True)
    payoff = plan["shares"] * (price - plan["spot"])
    for leg in plan["legs"]:
        intrinsic = max(0, price - leg["strike"]) if leg["kind"] == "C" else max(0, leg["strike"] - price)
        payoff += leg["side"] * leg["quantity"] * 100 * intrinsic
    return payoff - plan["premium"] - plan["fees"]


def metrics(plan):
    """Exact piecewise-linear extrema and breakevens on S >= 0, incl residual shares."""
    knots = sorted({0.0, *[leg["strike"] for leg in plan["legs"]]})
    points = [(k, expiry_pnl(plan, k)) for k in knots]
    slope = plan["shares"] + sum(l["side"] * l["quantity"] * 100 for l in plan["legs"] if l["kind"] == "C")
    if slope < 0:
        raise ValueError("检测到未覆盖的上涨风险，不能作为本页的有限风险预案。")
    minimum = min(v for _, v in points)
    maximum = None if slope > 0 else max(v for _, v in points)
    breaks = []
    for (a, fa), (b, fb) in zip(points, points[1:]):
        if abs(fa) < 1e-8:
            breaks.append(a)
        if fa * fb < 0:
            breaks.append(a - fa * (b - a) / (fb - fa))
    k, last = points[-1]
    if abs(last) < 1e-8:
        breaks.append(k)
    elif slope > 0 and last < 0:
        breaks.append(k - last / slope)
    return dict(max_loss=max(0, -minimum), max_profit=maximum,
                breakevens=sorted(set(round(v, 6) for v in breaks)),
                entry_outlay=max(0, plan["premium"]) + plan["fees"],
                residual_shares=max(0, plan["shares"] - 100 * plan["contracts"]) if plan["strategy"] in HEDGES else 0,
                covered_shares=100 * plan["contracts"] if plan["strategy"] in HEDGES else 0)


def liquidation_pnl(plan, snapshot):
    """Reference close-out: long sold at bid; short bought back at ask.

    Never substitutes an expiration payoff for a pre-expiration mark. Returns no
    value when any leg is absent/expired. This is not a verified execution P/L.
    """
    if snapshot.get("symbol") != plan["symbol"]:
        raise ValueError("复核标的与原预案不一致。")
    total = plan["shares"] * (positive(snapshot.get("spot"), "参考价") - plan["spot"])
    for leg in plan["legs"]:
        quote = next((q for q in snapshot.get("options", []) if q.get("id") == leg["id"]), None)
        if not quote or not valid_quote(quote) or quote.get("multiplier") != 100:
            return None
        total += leg["side"] * leg["quantity"] * 100 * quote["bid" if leg["side"] == 1 else "ask"]
    return total - plan["premium"] - plan["fees"]


def validate_evidence(evidence, symbol):
    """Reconstruct stored arithmetic on journal import; reject invalid option legs."""
    if not isinstance(evidence, dict) or symbol != evidence.get("symbol", "") + ".US":
        raise ValueError("期权预案的标的与合约依据不一致。")
    legs = evidence.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 2:
        raise ValueError("期权预案缺少合约明细。")
    rebuilt = make_strategy(dict(evidence, options=legs), evidence.get("strategy"), evidence.get("expiry"),
                            legs[0].get("strike"), legs[1].get("strike") if len(legs) == 2 else None,
                            contracts=evidence.get("contracts"), shares=evidence.get("shares"),
                            fee=evidence.get("fee_per_contract"))
    for key in ("premium", "fees"):
        if numeric(evidence.get(key)) is None or abs(evidence[key] - rebuilt[key]) > 1e-6:
            raise ValueError("预案的权利金或费用与合约明细不一致。")
    for old, new in zip(legs, rebuilt["legs"]):
        if any(old.get(k) != new[k] for k in ("side", "quantity", "entry", "id", "strike", "kind", "expiry")):
            raise ValueError("预案合约明细不一致。")
    if len(rebuilt["legs"]) != len(legs):
        raise ValueError("预案合约腿数不一致。")
    for key in ("target_price", "stress_price", "loss_budget"):
        if key in evidence:
            positive(evidence[key], key, zero=key != "target_price")
    stamp = dt.datetime.fromisoformat(evidence["retrieved_at"])
    if stamp.tzinfo is None:
        raise ValueError("报价读取时间必须包含时区。")
    return evidence
