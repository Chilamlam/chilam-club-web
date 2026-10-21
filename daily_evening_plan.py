# -*- coding: utf-8 -*-
"""
晚间复盘与次日交易计划跑批脚本

依赖前置跑批产物：
  - data/limit_ladder.json          （daily_market_monitor.py）
  - data/sentiment/derived.json     （daily_sentiment.py）
  - data/sentiment/ladder_history.csv
  - data/sentiment/breadth_history.csv
  - data/market_snapshot.csv        （daily_snapshot.py）
  - data/strong_stocks.csv          （daily_rps_pro.py）
  - data/breakout_stocks.csv        （daily_breakout.py）
  - data/scorecard/prices.csv       （daily_scorecard.py）

产出：
  - data/evening_plan/latest.json
  - data/evening_plan/history/YYYYMMDD.json

失败语义：
  - 缺少当日涨停池 → 退出码 3（不写空壳覆盖旧产物）
  - 全部核心池生成完毕 → 退出码 0（若情绪派生缺失则 status=incomplete，退出码 2）
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

import pandas as pd

import evening_plan as ep
import symbol_resolve as sr

_BJ_PREFIX = ("92", "43", "83", "87")


def _bare(code) -> str:
    return str(code or "").strip().upper().split(".")[0]


def _tx_code(bare: str, ts_code_hint: str = "") -> str:
    h = str(ts_code_hint or "").strip().upper()
    if "." in h:
        num, suf = h.split(".", 1)
        if suf in ("SH", "SZ", "BJ") and num.isdigit():
            return f"{suf.lower()}{num}"
    c = _bare(bare)
    if not c.isdigit() or len(c) != 6:
        return ""
    if c[:2] in _BJ_PREFIX:
        return f"bj{c}"
    if c.startswith(("6", "5", "9")):
        return f"sh{c}"
    return f"sz{c}"


def _load_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_csv(path: str, dtype=None) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=dtype)
    except Exception:
        return pd.DataFrame()


def _fetch_kline_one(tx_code: str, max_date_fmt: str, limit: int = 30) -> pd.DataFrame:
    """拉取单只标的前复权日K（腾讯 stdlib 通道），严格截断至 max_date_fmt 当日及以前。"""
    if not tx_code:
        return pd.DataFrame()
    urls = [
        sr.kline_url(tx_code, limit=limit),
        f"https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={tx_code},day,,,{min(int(limit), 800)},qfq",
        f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={tx_code},day,,,{min(int(limit), 800)},qfq",
    ]
    d = None
    for url in dict.fromkeys(urls):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                cand = json.loads(resp.read().decode("utf-8", "ignore"))
            if (cand.get("data") or {}).get(tx_code):
                d = cand
                break
        except Exception:
            continue
    if not d:
        return pd.DataFrame()
    node = (d.get("data") or {}).get(tx_code) or {}
    arr = node.get("qfqday") or node.get("day") or []
    if not arr:
        for v in node.values():
            if isinstance(v, list) and v:
                arr = v
                break
    rows = []
    for it in arr:
        if len(it) < 6:
            continue
        dt_str = str(it[0])[:10]
        if max_date_fmt and dt_str > max_date_fmt:
            continue
        try:
            rows.append({
                "date": dt_str.replace("-", ""),
                "open": float(it[1]),
                "close": float(it[2]),
                "high": float(it[3]),
                "low": float(it[4]),
                "vol": float(it[5]),
            })
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows)


def gather_candidate_klines(
    date_key: str,
    ladder_hist: pd.DataFrame,
    strong_df: pd.DataFrame,
    breakout_df: pd.DataFrame,
    snap_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """为近期人气连板股、阶段新高股与高 RPS 股补充最近 30 根 OHLC 日K，用于精确计算 MA6/MA12 与 #169 揉搓线。"""
    max_date_fmt = f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:]}"
    ts_hint_map: dict[str, str] = {}
    if not snap_df.empty and "ts_code" in snap_df.columns:
        for tc in snap_df["ts_code"].dropna().astype(str):
            ts_hint_map[_bare(tc)] = tc

    cands: list[str] = []
    seen = set()

    def _add(c: str) -> None:
        b = _bare(c)
        if b and len(b) == 6 and b.isdigit() and b not in seen:
            seen.add(b)
            cands.append(b)

    # 1. 近 10 个交易日曾达 ≥2 连板的人气股（#128 首阴/回踩核心池）
    if not ladder_hist.empty:
        h = ladder_hist.copy()
        h["date"] = h["date"].astype(str).str.replace("-", "")
        h = h[h["date"] <= date_key]
        recent_d = sorted(h["date"].unique())[-10:]
        sub = h[(h["date"].isin(recent_d)) & (h["limit_times"] >= 2)].sort_values(
            ["limit_times", "date"], ascending=[False, False]
        )
        for c in sub["code"].astype(str):
            _add(c)
            if len(cands) >= 35:
                break

    # 2. 阶段新高池前 25 只（#169 揉搓线 & #172 突破回踩）
    if not breakout_df.empty and "ts_code" in breakout_df.columns:
        for c in breakout_df["ts_code"].head(25).astype(str):
            ts_hint_map[_bare(c)] = c
            _add(c)

    # 3. RPS 强势榜前 20 只
    if not strong_df.empty and "ts_code" in strong_df.columns:
        for c in strong_df["ts_code"].head(20).astype(str):
            ts_hint_map[_bare(c)] = c
            _add(c)

    kline_map: dict[str, pd.DataFrame] = {}
    for b in cands[:65]:
        tx = _tx_code(b, ts_hint_map.get(b, ""))
        df_k = _fetch_kline_one(tx, max_date_fmt=max_date_fmt, limit=30)
        if not df_k.empty and len(df_k) >= 6:
            kline_map[b] = df_k

    print(f"📈 已获取 {len(kline_map)}/{len(cands[:65])} 只核心候选股近 30 日 OHLC K线（用于 MA6/MA12 与揉搓线扫描）")
    return kline_map


def gather_pct_map(
    date_key: str,
    ladder_hist: pd.DataFrame,
    prev_date: str,
    prices_df: pd.DataFrame | None = None,
) -> dict[str, float]:
    """获取当日涨幅映射（用于 #125 首板分时溢价对比与 #065 中位股大面率）。

    1. 优先走 Tushare 全市场日线；
    2. 其次从本地 prices.csv 复权收盘序列计算涨跌幅（零网络开销）；
    3. 剩余昨日涨停股走腾讯行情批量接口一次消歧取数（校验日期一致）。
    """
    token = (os.getenv("TUSHARE_TOKEN") or "").strip()
    if token:
        try:
            import tushare as ts
            ts.set_token(token)
            pro = ts.pro_api()
            df = pro.daily(trade_date=date_key, fields="ts_code,pct_chg")
            if df is not None and not df.empty:
                return {
                    _bare(c): float(v)
                    for c, v in zip(df["ts_code"], df["pct_chg"])
                    if pd.notna(v)
                }
        except Exception as exc:
            print(f"⚠️ Tushare 当日涨幅获取异常（{exc}），尝试本地/腾讯通道补齐")

    out: dict[str, float] = {}
    if prices_df is not None and not prices_df.empty and prev_date:
        p_df = prices_df[prices_df["date"].isin([prev_date, date_key])].copy()
        if not p_df.empty:
            p_df["bare"] = p_df["ts_code"].astype(str).map(_bare)
            p_df["adj_close"] = pd.to_numeric(p_df["adj_close"], errors="coerce")
            piv = p_df.pivot_table(index="bare", columns="date", values="adj_close", aggfunc="last")
            if prev_date in piv.columns and date_key in piv.columns:
                valid = piv.dropna(subset=[prev_date, date_key])
                valid = valid[valid[prev_date] > 0]
                for b_code, row in valid.iterrows():
                    out[str(b_code)] = float((row[date_key] - row[prev_date]) / row[prev_date] * 100.0)

    if ladder_hist.empty or not prev_date:
        return out
    h = ladder_hist.copy()
    h["date"] = h["date"].astype(str).str.replace("-", "")
    prev_codes = sorted({
        _bare(c) for c in h[h["date"] == prev_date]["code"].astype(str)
        if _bare(c) and _bare(c) not in out
    })
    if not prev_codes:
        return out

    for i in range(0, len(prev_codes), 50):
        chunk = prev_codes[i:i + 50]
        probe = {c: [f"{p}{c}" for p in sr.prefixes_for(c)] for c in chunk if re.fullmatch(r"\d{6}", c)}
        flat = [t for lst in probe.values() for t in lst]
        got = sr.fetch_quotes(flat, timeout=12)
        if not got:
            continue
        for b, tx_list in probe.items():
            cands = [got[t] for t in tx_list if t in got]
            if not cands:
                continue
            cands.sort(key=sr.rank_key)
            q = cands[0]
            day = str(q.get("update_time") or "")[:8]
            if day == date_key and q.get("pct_chg") is not None:
                out[b] = float(q["pct_chg"])
    return out


def main() -> None:
    print("🚀 正在生成【晚间复盘与次日交易计划】客观战法池...")
    ladder = _load_json(ep.LADDER_PATH)
    if not ladder or not ladder.get("stocks"):
        print(f"❌ [failed] {ep.LADDER_PATH} 缺失或涨停池为空，终止生成")
        sys.exit(3)

    derived = _load_json(ep.DERIVED_PATH) or {}
    date_raw = str(ladder.get("date") or derived.get("date") or "")
    date_key = re.sub(r"\D", "", date_raw)[:8]
    if len(date_key) != 8:
        print(f"❌ [failed] 无法从涨停池或情绪产物解析有效交易日：{date_raw!r}")
        sys.exit(3)

    prev_date = re.sub(r"\D", "", str(derived.get("prev_date") or ""))[:8]
    ladder_hist = _load_csv(ep.LADDER_HIST_PATH, dtype={"code": str, "date": str})
    breadth_hist = _load_csv(ep.BREADTH_HIST_PATH, dtype={"date": str})
    snap_df = _load_csv(ep.SNAPSHOT_CSV)
    strong_df = _load_csv(ep.STRONG_CSV)
    breakout_df = _load_csv(ep.BREAKOUT_CSV)
    prices_df = _load_csv(ep.PRICES_CSV, dtype={"date": str, "ts_code": str})

    if not prev_date and not ladder_hist.empty:
        all_d = sorted(ladder_hist["date"].astype(str).str.replace("-", "").unique())
        earlier = [d for d in all_d if d < date_key]
        if earlier:
            prev_date = earlier[-1]

    pct_map = gather_pct_map(date_key, ladder_hist, prev_date, prices_df=prices_df)
    kline_map = gather_candidate_klines(date_key, ladder_hist, strong_df, breakout_df, snap_df)

    payload = ep.build_evening_plan_payload(
        trade_date=date_key,
        ladder=ladder,
        derived=derived,
        ladder_hist=ladder_hist,
        breadth_hist=breadth_hist,
        snapshot_df=snap_df,
        strong_df=strong_df,
        breakout_df=breakout_df,
        prices_df=prices_df,
        kline_map=kline_map,
        pct_map=pct_map,
    )

    if payload.get("status") == "failed":
        print(f"❌ [failed] {payload.get('reason')}")
        sys.exit(3)

    out_path = ep.save_evening_plan(payload)
    pools = payload.get("pools") or {}
    laws = (payload.get("cycle_and_laws") or {}).get("active_laws") or []
    print(f"✅ 晚间战法池已落盘 → {out_path}（交易日 {date_key}，状态={payload['status']}）")
    print(f"   触发周期/日历特殊法则：{len(laws)} 条")
    for k, p in pools.items():
        print(f"   · {p.get('title', k)[:26]}：{p.get('count', 0)} 只")

    if payload.get("status") == "incomplete":
        sys.exit(2)


if __name__ == "__main__":
    main()
