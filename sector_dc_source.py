# -*- coding: utf-8 -*-
"""东财概念板块的 Tushare 取数通道（板块轮动跑批的第二数据源）。

为什么需要它
------------
板块轮动原本只有 push2 clist 一个数据源，而东财会对机房 IP 与高频请求
按边缘拒绝：2026-09-21 起 Actions 侧每天只取到 100~300 个板块（应 504），
2026-10-12 更是把本机也限流到近乎全拒。Tushare 的 dc_index / dc_daily
转发的**就是同一份东财概念板块数据**，只是换了一条不被掐断的管子——
用归档已有的 2026-09-18（504 个板块全交集）逐字段反验：

    名称 / 日涨跌幅 / 换手率 / 上涨家数 / 下跌家数 / 成交额
        → 全部 **100% 完全一致，最大绝对差 0.0000**

两条通道的分工
--------------
- **直取列**（name / pct_chg / turnover / up_count / down_count / amount_yi）
  由 Tushare 原样给出，与 clist 同口径。
- **窗口涨跌幅**（5/10/20/60 日）Tushare **不给**（dc_daily 没有这些字段），
  必须自算：用交易日历定位「n 个交易日前」，取两端收盘价之比。
  自算与东财自算的差距已用归档反验：5 日 99.4% 落在 0.01pp 内、
  中位差 0.0024pp、相关系数 0.999999；10 日 99.2% 落在 0.01pp 内。

三个必须防的坑（都是实测踩出来的）
----------------------------------
1. **非交易日会被伪造**：`dc_index` 的区间查询会给非交易日返回「上一交易日的
   副本」——实测 2026-09-25/26/27 三天数据与 09-24 逐字段完全相同，而且单日
   查询 09-25 也照样返回 504 行。→ 一切结果都必须与**交易日历求交**后再用，
   绝不能凭 `trade_date` 存在就信。
2. **单次返回有行数上限**：dc_daily 实测 2000 行（≈4 个交易日）、dc_index
   约 5000 行。撞到上限就是**静默截断**。→ 分块取 + 检测到撞线就二分重取。
   注意分块要按「连续交易日」而不能只按「要几天」：缺口期的日子是不连续的，
   3 个缺口日的区间可能横跨 21 个自然日（实测把 dc_index 顶到 5000 行上限）。
3. **板块指数会被重新定基/换尺度**：实测 BK0815/0816/0817/1050/1051 五个
   「昨日连板/触板/涨停」类板块出现过单日 ÷1000 或 ×1000 的尺度跳变。
   跨这个跳变算窗口会得到 +86737% 这种垃圾。→ 用「单日比值接近 10 的整数
   次幂」判定并**把序列拼平**（尺度变化是纯单位变化，拼平后窗口才正确）。
   这也解释了归档为何存东财自算的百分比而不是存收盘价：百分比对定基免疫。

设计约束
--------
- 不 import streamlit、不在 import 时联网（可脱 runtime 单测）。
- 窗口有哪些、各是几天，**从计算层的 WINDOW_COLS 派生**，不另立一份定义。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request

import numpy as np
import pandas as pd

import sector_rotation as sr

TUSHARE_URL = "https://api.tushare.pro"
IDX_TYPE = "概念板块"
API_INDEX = "dc_index"
API_DAILY = "dc_daily"


class DataSourceError(RuntimeError):
    """取数失败（网络 / 权限 / 返回结构异常）。

    调用方据此决定「降级到另一条通道」还是「本次拒收」——两条通道都失败才
    叫取不到，单通道失败不等于数据源不可用。
    """


# 单次返回行数上限（实测值）。撞线即视为可能被截断，二分重取。
DC_DAILY_CAP = 2000
DC_INDEX_CAP = 5000

# 每块的交易日个数：506 板块 × 3 天 = 1518 < 2000，留出板块数增长的余量。
CHUNK_DAYS = 3

# 归档列的顺序（Tushare 通道产出的 DataFrame 必须与 clist 通道同形，
# 否则 append_history 里 concat 出来的列会错位）。
ARCHIVE_COLS = ("date", "code", "name", "close", "pct_chg", *sr.WINDOW_COLS,
                "turnover", "up_count", "down_count", "amount_yi")


def _window_specs() -> tuple[tuple[str, int], ...]:
    """(列名, 需要往前数几个交易日)，从计算层的 WINDOW_COLS 派生。

    单一真源：窗口是哪些、各是几天，只在 sector_rotation.WINDOW_COLS 定义一次。
    这里再写一份「5/10/20/60」就等着某天两处不一致。
    """
    out = []
    for col in sr.WINDOW_COLS:
        num = col.split("_")[1].rstrip("d")
        out.append((col, int(num)))
    return tuple(out)


WINDOWS = _window_specs()
MAX_WINDOW = max(n for _, n in WINDOWS)
# 要算 N 日窗口，至少需要 N+1 个收盘价；多留 1 天余量。
LOOKBACK_DAYS = MAX_WINDOW + 2

# 尺度跳变判定：概念板块指数单日不可能动 50%，超过 1.5 倍还要接近 10 的
# 整数次幂（±15%）才算「换了尺度」。两个条件同时要求，避免把真实行情误判。
SCALE_RATIO_HI = 1.5
SCALE_RATIO_LO = 1.0 / SCALE_RATIO_HI
SCALE_POW_TOL = 0.15


def _compact(iso: str) -> str:
    """'2026-10-12' → '20261012'。Tushare 的参数一律要紧凑格式。"""
    return str(iso).replace("-", "")


def _iso(compact: str) -> str:
    """'20261012' → '2026-10-12'。

    ★ 模块内部**一律用 ISO**（与归档 history.csv 的 date 列同格式）。
    若这里保留 Tushare 的紧凑格式，追加归档时会因为「20261012 != 2026-10-12」
    而无法命中同日替换——归档会出现同一交易日的两份行、两种日期格式。
    """
    s = str(compact)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """把原始返回归一化：板块代码去掉 .DC 后缀、交易日统一成 ISO。

    幂等——已经归一化过的表再走一遍不变。这样 `build_snapshots` 既能吃
    `fetch_dc` 的产物，也能吃原始接口返回（探针要拿原始返回直接验重建方法，
    走的不该是两条不同的预处理）。
    """
    if df is None or len(df) == 0:
        return df
    df = df.copy()
    if "code" not in df.columns and "ts_code" in df.columns:
        df["code"] = df["ts_code"].astype(str).str.replace(".DC", "", regex=False)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str).map(_iso)
    return df


def _token(token: str | None = None) -> str:
    tok = token or os.getenv("TUSHARE_TOKEN", "")
    if not tok:
        raise DataSourceError(
            "缺少 TUSHARE_TOKEN：Actions 上由 secrets 注入，本机需显式提供")
    return tok


def _post(api_name: str, params: dict, token: str, timeout: int = 30) -> list[dict]:
    """调一次 Tushare 接口，返回 [{字段: 值}, ...]。

    直接走 HTTPS 而不是用 `tushare` 包：包的方法签名随版本漂移，而这里需要
    精确控制参数（start_date/end_date/idx_type）与错误信息——取数失败必须
    带着**上游的原话**进入日志，否则线上只看到一句「取数失败」没法定位。
    """
    body = {"api_name": api_name, "token": token, "params": params, "fields": ""}
    req = urllib.request.Request(
        TUSHARE_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise DataSourceError(f"{api_name} 请求失败：{type(exc).__name__}: {exc}") from exc
    code = payload.get("code")
    if code != 0:
        raise DataSourceError(
            f"{api_name} 上游返回 code={code}：{payload.get('msg')}"
            f"（若为权限类错误，说明该 token 不具备「打板专题数据」接口权限）")
    data = payload.get("data") or {}
    fields = data.get("fields") or []
    items = data.get("items") or []
    return [dict(zip(fields, row)) for row in items]


def trade_cal(start: str, end: str, token: str | None = None) -> list[str]:
    """区间内的交易日（ISO，升序）。"""
    rows = _post("trade_cal",
                 {"exchange": "SSE", "start_date": _compact(start),
                  "end_date": _compact(end), "is_open": "1"},
                 _token(token))
    return sorted(_iso(r["cal_date"]) for r in rows)


def _fetch_group(api_name: str, days: list[str], token: str, cap: int,
                 out: list[dict], depth: int = 0) -> None:
    """取一组（连续）交易日；撞到行数上限就二分重取。days 为 ISO 升序。"""
    if not days:
        return
    if depth > 8:
        raise DataSourceError(f"{api_name} 分块递归过深，放弃（{days[0]}~{days[-1]}）")
    rows = _post(api_name, {"start_date": _compact(days[0]), "end_date": _compact(days[-1]),
                            "idx_type": IDX_TYPE}, token)
    if len(rows) >= cap and len(days) > 1:
        mid = len(days) // 2
        _fetch_group(api_name, days[:mid], token, cap, out, depth + 1)
        _fetch_group(api_name, days[mid:], token, cap, out, depth + 1)
        return
    if len(rows) >= cap:
        raise DataSourceError(
            f"{api_name} 单日 {days[0]} 就返回 {len(rows)} 行（上限 {cap}），疑似被截断")
    for r in rows:
        if "trade_date" in r:
            r["trade_date"] = _iso(r["trade_date"])
    want = set(days)
    got = {str(r.get("trade_date")) for r in rows}
    extra = got - want
    if extra:
        # dc_index 会为非交易日伪造行（上一交易日的副本），必须剔除——
        # 让它进归档就等于把一个交易日的数据写两遍。
        print(f"⚠️ {api_name} {days[0]}~{days[-1]} 含非交易日行 {sorted(extra)}，已剔除")
    out.extend(r for r in rows if str(r.get("trade_date")) in want)
    time.sleep(0.15)


def fetch_dc(api_name: str, days: list[str], token: str | None = None) -> pd.DataFrame:
    """按连续交易日分块取数，返回长表 DataFrame。

    days 必须是**升序交易日列表**；有空洞时（缺口日正是这种）会被切成多块，
    每块内部保持连续——否则一个块可能横跨几十个自然日而撞上返回上限。
    """
    tok = _token(token)
    cap = DC_DAILY_CAP if api_name == API_DAILY else DC_INDEX_CAP
    days = sorted(str(d) for d in days)
    rows: list[dict] = []
    for i in range(0, len(days), CHUNK_DAYS):
        _fetch_group(api_name, days[i:i + CHUNK_DAYS], tok, cap, rows)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["trade_date"] = df["trade_date"].astype(str)
    df["code"] = df["ts_code"].astype(str).str.replace(".DC", "", regex=False)
    return df.drop_duplicates(subset=["code", "trade_date"]).reset_index(drop=True)


def splice_scales(wide: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """把「指数被重新定基/换尺度」造成的跳变拼平，返回 (拼平后的宽表, 事件列表)。

    概念板块指数单日动 50% 是不可能的；实测那 5 处跳变全部是 ÷1000 或 ×1000
    的纯单位变化（如 BK0816 的 42709.18 → 42.36）。单位变化对「窗口比值」是
    可逆的——把两段拼到同一尺度，跨断点的窗口才算得对。

    判定刻意保守：既要比值越界（>1.5 或 <1/1.5），又要它接近 10 的整数次幂
    （±15%）。只要一条不满足就**不动**，并记进事件列表由调用方决定是否告警。
    """
    w = wide.copy()
    events: list[dict] = []
    for code in w.columns:
        a = w[code].to_numpy(dtype=float).copy()
        for i in range(1, len(a)):
            prev, cur = a[i - 1], a[i]
            if not (math.isfinite(prev) and math.isfinite(cur)) or prev == 0:
                continue
            r = cur / prev
            if SCALE_RATIO_LO <= r <= SCALE_RATIO_HI:
                continue
            k = round(math.log10(r))
            if k == 0 or abs(r / (10 ** k) - 1.0) > SCALE_POW_TOL:
                # 越界但不像单位变化：只记事件，不改数据（宁可不拼，也不误拼）
                events.append({"code": code, "date": w.index[i], "ratio": r,
                               "kind": "unexplained"})
                continue
            a[i:] = a[i:] / (10 ** k)
            events.append({"code": code, "date": w.index[i], "ratio": r,
                           "kind": f"rescale_1e{-k}"})
        w[code] = a
    return w, events


def close_matrix(dly: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """dc_daily 长表 → 宽表（index=交易日升序、columns=板块代码、值=已拼平尺度的收盘价）。"""
    dly = _normalize(dly)
    if dly is None or dly.empty:
        return pd.DataFrame(), []
    w = dly.pivot_table(index="trade_date", columns="code", values="close",
                        aggfunc="last").sort_index()
    return splice_scales(w)


def compute_windows(wide: pd.DataFrame, opens: list[str],
                    target_days: list[str]) -> pd.DataFrame:
    """按「n 个交易日之前」算窗口涨跌幅（百分数）。返回长表 date/code/pct_*。

    用**交易日历的位置**定位基准日，而不是用「该板块自己往前数 n 行」——后者在
    板块中途缺行时会把基准日算错，而错得很隐蔽（数值仍然像个正常的百分比）。

    基准日缺收盘价（板块新上市、或该日停牌）时该窗口一律 None，绝不补 0、
    也绝不用更早的日期顶替。
    """
    if wide.empty or not target_days:
        return pd.DataFrame(columns=["date", "code", *sr.WINDOW_COLS])
    w = wide.reindex(opens)
    arr = w.to_numpy(dtype=float)
    codes = list(w.columns)
    n_row = arr.shape[0]

    ratio = {}
    for name, n in WINDOWS:
        base = np.full_like(arr, np.nan)
        if 0 < n < n_row:
            base[n:, :] = arr[:-n, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio[name] = (arr / base - 1.0) * 100.0

    pos = {d: i for i, d in enumerate(opens)}
    frames = []
    for d in target_days:
        i = pos.get(d)
        if i is None:
            continue
        rec = {"date": d, "code": codes}
        for name, _ in WINDOWS:
            v = ratio[name][i]
            rec[name] = np.where(np.isfinite(v), v, np.nan)
        frames.append(pd.DataFrame(rec))
    if not frames:
        return pd.DataFrame(columns=["date", "code", *sr.WINDOW_COLS])
    return pd.concat(frames, ignore_index=True)


def build_snapshots(opens: list[str], dly: pd.DataFrame, idx: pd.DataFrame,
                    target_days: list[str], anchor_date: str | None = None,
                    anchor_close: dict[str, float] | None = None,
                    ) -> tuple[pd.DataFrame, dict]:
    """把原始数据拼成归档同形的快照（含 date 列），并返回诊断信息。

    anchor_close 是「归档里最近一个交易日的收盘价」{板块: 值}，用来把 Tushare
    的收盘价折算到归档的尺度上——**只用它保 close 列连续**，窗口涨跌幅靠比值
    与尺度无关，不依赖它。两者缺一时不折算，并在诊断里如实标记。
    """
    idx = _normalize(idx)
    dly = _normalize(dly)
    if idx is None or len(idx) == 0 or not target_days:
        return pd.DataFrame(columns=list(ARCHIVE_COLS)), {"reason": "no_index_rows"}

    wide, events = close_matrix(dly)
    win = compute_windows(wide, opens, target_days)

    keep_idx = ["trade_date", "code", "name", "pct_change", "turnover_rate",
                "up_num", "down_num"]
    ix = idx[[c for c in keep_idx if c in idx.columns]].rename(
        columns={"trade_date": "date", "pct_change": "pct_chg",
                 "turnover_rate": "turnover", "up_num": "up_count", "down_num": "down_count"})
    keep_dly = ["trade_date", "code", "close", "amount"]
    dl = dly[[c for c in keep_dly if c in dly.columns]].rename(
        columns={"trade_date": "date"})
    dl["amount_yi"] = pd.to_numeric(dl["amount"], errors="coerce") / 1e8
    dl = dl.drop(columns=["amount"])

    # 两条接口的板块集合若不一致，取交集：宁可少一行，也不要一行没有名字的
    # 记录进归档（没有名字的板块在榜上无法辨认，等于坏数据）。
    # 计数必须先各自收敛到 target_days，否则 dly 是整段回看期（数万行），
    # 诊断里的「差多少行」会变成没有意义的巨大数字。
    ix = ix[ix["date"].isin(target_days)]
    dl = dl[dl["date"].isin(target_days)]
    n_idx, n_dly = len(ix), len(dl)
    df = ix.merge(dl, on=["date", "code"], how="inner")

    # 收盘价折算到归档尺度：按板块取「归档末日 / Tushare 同日」的比值。
    scaled = 0
    if anchor_close and anchor_date and anchor_date in wide.index:
        anchor_row = wide.loc[anchor_date]
        factor = {c: (float(anchor_close[c]) / float(anchor_row[c]))
                  for c in wide.columns
                  if c in anchor_close and math.isfinite(float(anchor_row[c]))
                  and float(anchor_row[c]) != 0 and math.isfinite(float(anchor_close[c]))}
        if factor:
            df["close"] = pd.to_numeric(df["close"], errors="coerce") * df["code"].map(factor)
            scaled = int(df["close"].notna().sum())

    # 窗口列由自算结果提供（win 自带 date/code/pct_*）。**不要在这里先放占位列**：
    # 占位列与 win 同名，merge 会生成 pct_5d_x/pct_5d_y 两个残列，
    # 随后按 ARCHIVE_COLS 取列时两个都取不到 —— 窗口列会整批消失。
    df = df.merge(win, on=["date", "code"], how="left")
    for c in sr.WINDOW_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    df = df[[c for c in ARCHIVE_COLS if c in df.columns]]
    for c in sr.NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    info = {
        "rows": len(df),
        "boards": int(df["code"].nunique()),
        "idx_rows": n_idx,
        "dly_rows": n_dly,
        "merge_missing_idx": n_dly - len(df),
        "merge_missing_dly": n_idx - len(df),
        "splice_events": events,
        "close_scaled_rows": scaled,
        "anchor_date": anchor_date if scaled else None,
        "window_missing": {c: int(df[c].isna().sum()) for c in sr.WINDOW_COLS},
        "source_board_count": {d: int((df["date"] == d).sum()) for d in target_days},
    }
    return df, info


def fetch_snapshot(day: str, token: str | None = None,
                   anchor_date: str | None = None,
                   anchor_close: dict[str, float] | None = None,
                   ) -> tuple[pd.DataFrame, dict]:
    """取某一天的完整快照（跑批日常用这一条）。

    只发 3 类请求：交易日历、当天的 dc_index、覆盖窗口回看期的 dc_daily。
    """
    end = day
    # 回看 62 个交易日 ≈ 90 个自然日；乘 2.2 再补 20 天，覆盖春节/国庆这类长假。
    start = (pd.Timestamp(day) - pd.Timedelta(days=int(LOOKBACK_DAYS * 2.2) + 20)
             ).strftime("%Y-%m-%d")
    opens = trade_cal(start, end, token)
    if day not in opens:
        raise DataSourceError(f"{day} 不是交易日（Tushare 日历），拒绝据此落盘")
    use = opens[-LOOKBACK_DAYS:]
    dly = fetch_dc(API_DAILY, use, token)
    idx = fetch_dc(API_INDEX, [day], token)
    df, info = build_snapshots(use, dly, idx, [day], anchor_date, anchor_close)
    info["opens_used"] = use
    return df, info
