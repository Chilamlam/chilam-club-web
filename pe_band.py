"""Dated, unadjusted-price PE bands. No forecasts or trading signals."""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


QUANTILES = (.10, .25, .50, .75, .90)
CURRENCIES = {"A_SHARE": "CNY", "BJ_SHARE": "CNY", "HK_SHARE": "HKD", "US_SHARE": "USD"}
_US_FUNDS = {"SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VT", "IVV", "GLD", "SLV", "TLT",
             "TQQQ", "SQQQ", "SOXL", "SOXS", "ARKK", "SMH", "XLF", "XLE", "EEM", "KWEB"}


def equity_identity(sym: dict, name: str = "") -> tuple[str, str]:
    """Require a listing, preserving exchange identity and share classes."""
    market = sym.get("market", "")
    code = str(sym.get("tx_code", ""))
    if sym.get("is_index") or market not in CURRENCIES:
        raise ValueError("PE Band 用于个股；指数、基金与期货不适用这里的公司盈利口径。")
    if re.search(r"ETF|ETN|指数|基金|牛证|熊证|涡轮", name, re.I):
        raise ValueError("该标的属于指数或交易产品，请选择一家上市公司的股票。")
    if market in ("A_SHARE", "BJ_SHARE"):
        if not re.fullmatch(r"(?:sh(?:60|68)\d{4}|sz(?:00|30)\d{4}|bj(?:43|83|87|88|92)\d{4})", code):
            raise ValueError("请选择 A 股公司股票，可用 sh / sz / bj 前缀明确交易所。")
        return market, code[2:]
    if market == "HK_SHARE":
        if not re.fullmatch(r"hk\d{5}", code) or int(code[2:]) >= 10000:
            raise ValueError("当前支持港币交易的港股普通股；权证、人民币柜台等暂不覆盖。")
        return market, code[2:]
    ticker = code.removeprefix("us").upper()
    if not re.fullmatch(r"[A-Z][A-Z.\-]{0,9}", ticker) or ticker in _US_FUNDS:
        raise ValueError("请选择美股公司股票；ETF 的组合估值不等于公司的 PE。")
    return market, ticker


def completed_cutoff(market: str, now: dt.datetime | None = None) -> dt.date:
    """Conservative publication cutoff, not an exchange holiday calendar."""
    tz = ZoneInfo("America/New_York" if market == "US_SHARE" else "Asia/Shanghai")
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("时间必须包含时区")
    local = now.astimezone(tz)
    return local.date() - dt.timedelta(days=int(local.hour < 18))


def window_start(end: dt.date, years: int) -> dt.date:
    if years not in (1, 3, 5):
        raise ValueError("请选择近 1、3 或 5 年")
    return (pd.Timestamp(end) - pd.DateOffset(years=years)).date()


def clean_history(rows, columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["date", *columns])
    if not {"date", *columns}.issubset(frame.columns):
        raise ValueError("数据源缺少日期或估值字段")
    frame = frame[["date", *columns]].copy()
    frame["date"] = pd.to_datetime(frame["date"].astype(str).str[:10], errors="coerce")
    for col in columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    frame = frame.dropna(subset=["date"])
    # Conflicting duplicates are not silently selected as a source of truth.
    duplicates = frame[frame.duplicated("date", keep=False)]
    if not duplicates.empty and (duplicates.groupby("date")[list(columns)].nunique(dropna=False) > 1).any().any():
        raise ValueError("同一日期有冲突记录，暂不计算估值带")
    return frame.drop_duplicates("date").sort_values("date").reset_index(drop=True)


def align_history(prices, valuations, *, start: dt.date, end: dt.date) -> tuple[pd.DataFrame, dict]:
    """Keep price dates as the spine. Never fill missing/negative PE or weekends."""
    p = clean_history(prices, ("close",))
    v = clean_history(valuations, ("pe_ttm",))
    p = p[p.date.between(pd.Timestamp(start), pd.Timestamp(end)) & (p.close > 0)]
    v = v[v.date.between(pd.Timestamp(start), pd.Timestamp(end))]
    v["pe_observed"] = True
    frame = p.merge(v, on="date", how="left", validate="one_to_one")
    frame["pe_observed"] = frame.pe_observed.eq(True)
    frame["usable"] = frame.pe_ttm.gt(0) & frame.pe_ttm.notna()
    frame["implied_eps"] = (frame.close / frame.pe_ttm.where(frame.usable)).replace([np.inf, -np.inf], np.nan)
    frame["usable"] &= frame.implied_eps.notna()
    stats = dict(price_days=len(frame), positive_days=int(frame.usable.sum()),
                 nonpositive_days=int(frame.pe_ttm.le(0).sum()),
                 missing_days=int(frame.pe_ttm.isna().sum()),
                 unmatched_valuation_days=int((~v.date.isin(p.date)).sum()),
                 start=frame.date.min().date().isoformat() if len(frame) else None,
                 end=frame.date.max().date().isoformat() if len(frame) else None)
    return frame.reset_index(drop=True), stats


def band_multiples(frame: pd.DataFrame, custom: str | None = None) -> list[tuple[str, float]]:
    if custom is not None:
        try:
            values = sorted({float(s) for s in re.split(r"[,，;；\s]+", custom.strip()) if s})
        except ValueError as exc:
            raise ValueError("请填写数字倍数，例如 10, 15, 20, 25, 30") from exc
        if not 2 <= len(values) <= 7 or any(not np.isfinite(v) or v <= 0 for v in values):
            raise ValueError("请填入 2～7 个不同的正数 PE 倍数")
        return [(f"{v:g} 倍", v) for v in values]
    valid = frame.loc[frame.usable, "pe_ttm"]
    if len(valid) < 2:
        return []
    return [(f"P{int(q * 100)} · {valid.quantile(q):.2f} 倍", float(valid.quantile(q))) for q in QUANTILES]


def band_prices(frame: pd.DataFrame, multiples: list[tuple[str, float]]) -> pd.DataFrame:
    result = frame.copy()
    for label, multiple in multiples:
        result[label] = result.implied_eps * multiple
    return result


def latest_summary(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {}
    row = frame.iloc[-1]
    sample = frame.loc[frame.usable, "pe_ttm"]
    usable = bool(row.usable)
    percentile = float(((sample < row.pe_ttm).sum() + .5 * (sample == row.pe_ttm).sum()) / len(sample) * 100) if usable else None
    return dict(date=row.date.date().isoformat(), close=float(row.close),
                pe_ttm=float(row.pe_ttm) if pd.notna(row.pe_ttm) else None,
                usable=usable, implied_eps=float(row.implied_eps) if usable else None, percentile=percentile)


def scenario_table(summary: dict, multiples: list[tuple[str, float]], earnings_change: float = 0) -> pd.DataFrame:
    if not summary.get("usable"):
        return pd.DataFrame()
    if not np.isfinite(earnings_change) or earnings_change <= -100:
        raise ValueError("盈利变化假设必须大于 -100%")
    rows = []
    for label, multiple in multiples:
        price = summary["implied_eps"] * (1 + earnings_change / 100) * multiple
        rows.append({"估值参照": label, "PE（倍）": multiple, "对应价格": price,
                     "相对参考收盘价（%）": (price / summary["close"] - 1) * 100})
    return pd.DataFrame(rows)
