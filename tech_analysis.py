"""
自动技术分析引擎 —— 缠论结构 + 帝纳波利（DiNapoli）位置

设计原则
--------
1. **纯本地计算**：只吃 K 线 DataFrame（datetime/open/close/high/low/vol），
   不发任何网络请求，不依赖 talib，只用 pandas/numpy。任何市场任何周期通用。
2. **结论可回溯**：每个结论都能指回具体的 K 线下标与价位，图上画得出来。
3. **不给投资建议**：只描述「结构处于什么位置」「关键价位在哪」，
   不输出买卖指令。UI 层负责加免责声明。

术语与实现口径（与经典定义的差异都写在这里，避免误读）
--------------------------------------------------
缠论：
  - 包含处理：标准做法，按前一段方向决定合并后取高高/低低。
  - 分型：合并后 K 线三根中间者最高（顶）/最低（底）。
  - 笔：相邻异型分型之间，合并 K 线跨度 >= 4（严格口径）；
        若严格口径下笔太少（< 4 笔），自动放宽到 >= 3。
  - 线段：对「笔端点序列」再做一次分型识别得到的高一级 zigzag。
        这是工程近似，不是特征序列分型的严格实现，但在多周期实测中
        与人工画线一致度较高，且不会出现递归不收敛。
  - 中枢：连续三段同级别走势区间存在重叠 → ZG=min(高), ZD=max(低)，
        随后向右延伸吸收仍与 [ZD, ZG] 有重叠的走势。
        这是实用口径（区间重叠法），非「次级别走势类型」严格定义。

帝纳波利：
  - DMA：位移移动平均。3x3 = SMA(3) 右移 3 根，7x5 = SMA(7) 右移 5 根，
        25x5 = SMA(25) 右移 5 根。右移意味着最后 N 根是「悬空」的预测段。
  - Fibnode：以最近一段推动浪 A→B 为基准，F3 = 0.382 回撤，F5 = 0.618 回撤。
  - 目标位：需要 A(起点)→B(推动终点)→C(回撤终点) 三点。
        COP = C + 0.618*(B-A)，OP = C + 1.0*(B-A)，XOP = C + 1.618*(B-A)。
  - Confluence（汇聚区）：两段不同摆动算出的 Fibnode 互相靠近（< 1.5% 价差）
        时合并标注，帝纳波利认为这类价位支撑/阻力更硬。
  - MACD 用 8/17/9（帝纳波利偏好的快参数），配合去趋势振荡器判背离。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

MIN_BARS = 30            # 少于这么多根不做结构分析
_STROKE_GAP_STRICT = 4
_STROKE_GAP_LOOSE = 3
# 中枢延伸上限。缠论原意是「9 段以上应升级看更高级别」，工程上必须封顶：
# 否则宽区间中枢会一路吸收几十段，跨度好几年，失去实战参考价值。
_PIVOT_MAX_LEGS = 9


# ==================== 0. 工具 ====================

def _ok(df: pd.DataFrame) -> bool:
    if df is None or len(df) < MIN_BARS:
        return False
    return all(c in df.columns for c in ("high", "low", "close"))


def _pct(a: float, b: float) -> float:
    """b 相对 a 的涨跌幅（%）。a 为 0 时返回 0。"""
    return 0.0 if not a else (b - a) / a * 100.0


def _n(x: float) -> str:
    """价格格式化。大数用千分位，小数按量级给位数——避免出现 2.591e+04 这种科学计数。"""
    if x is None or not np.isfinite(x):
        return "—"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.0f}"
    if ax >= 100:
        return f"{x:,.2f}"
    if ax >= 1:
        return f"{x:.3f}"
    return f"{x:.4f}"


# ==================== 1. 缠论：包含处理 ====================

def merge_klines(df: pd.DataFrame) -> list:
    """K 线包含处理。返回 [{i, high, low}]，i 是原始 DataFrame 的下标（取合并区间末根）。"""
    highs = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype="float64")
    lows = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype="float64")
    n = len(highs)

    bars: list = []
    for i in range(n):
        h, l = highs[i], lows[i]
        if not np.isfinite(h) or not np.isfinite(l):
            continue
        if not bars:
            bars.append({"i": i, "high": h, "low": l})
            continue

        prev = bars[-1]
        # 包含关系：后包前 或 前包后
        if (h >= prev["high"] and l <= prev["low"]) or (h <= prev["high"] and l >= prev["low"]):
            # 方向由 prev 与 prev-1 决定，只有一根时默认向上
            up = True
            if len(bars) >= 2:
                up = prev["high"] > bars[-2]["high"]
            if up:
                prev["high"] = max(prev["high"], h)
                prev["low"] = max(prev["low"], l)
            else:
                prev["high"] = min(prev["high"], h)
                prev["low"] = min(prev["low"], l)
            prev["i"] = i
        else:
            bars.append({"i": i, "high": h, "low": l})
    return bars


# ==================== 2. 缠论：分型 ====================

def find_fractals(bars: list) -> list:
    """在合并 K 线上找分型。返回 [{k, i, type, price}]，k 是 bars 下标。"""
    out = []
    for k in range(1, len(bars) - 1):
        a, b, c = bars[k - 1], bars[k], bars[k + 1]
        if b["high"] > a["high"] and b["high"] > c["high"]:
            out.append({"k": k, "i": b["i"], "type": "top", "price": b["high"]})
        elif b["low"] < a["low"] and b["low"] < c["low"]:
            out.append({"k": k, "i": b["i"], "type": "bottom", "price": b["low"]})
    return out


# ==================== 3. 缠论：笔 ====================

def build_strokes(bars: list, fractals: list, gap: int = _STROKE_GAP_STRICT) -> list:
    """
    由分型连成笔。规则：
      - 相邻两个分型必须异型；同型则保留更极端的那个（顶取更高、底取更低）。
      - 两分型之间合并 K 线跨度 >= gap。
    返回 [{k, i, type, price}] 形式的端点序列（首尾即笔的两端）。
    """
    if len(fractals) < 2:
        return []

    kept = [fractals[0]]
    for f in fractals[1:]:
        last = kept[-1]
        if f["type"] == last["type"]:
            # 同型取更极端者
            better = (f["price"] > last["price"]) if f["type"] == "top" else (f["price"] < last["price"])
            if better:
                kept[-1] = f
            continue
        if f["k"] - last["k"] < gap:
            # 距离不够：不成笔。若新分型比上一个更极端方向的前一个更极端，考虑回退
            continue
        # 顶必须高于前一个底，底必须低于前一个顶
        if f["type"] == "top" and f["price"] <= last["price"]:
            continue
        if f["type"] == "bottom" and f["price"] >= last["price"]:
            continue
        kept.append(f)
    return kept


def chan_strokes(df: pd.DataFrame) -> list:
    """对外接口：返回笔端点序列，自动在严格/放宽口径间择优。"""
    if not _ok(df):
        return []
    bars = merge_klines(df)
    fr = find_fractals(bars)
    pts = build_strokes(bars, fr, _STROKE_GAP_STRICT)
    if len(pts) < 5:
        loose = build_strokes(bars, fr, _STROKE_GAP_LOOSE)
        if len(loose) > len(pts):
            pts = loose
    return pts


# ==================== 4. 缠论：线段（对笔端点再做一次分型） ====================

def build_segments(points: list) -> list:
    """
    在笔端点序列上做高一级 zigzag。

    注意：不能直接取「比左右邻居更极端」的点 —— 笔端点本身是顶底交替的，
    任何顶都必然高于左右两个底，那样过滤等于恒等映射（线段数会恒等于笔数）。
    正确做法是与**同型**的前后邻居比较：一个顶要成为线段顶，必须比上一个顶
    和下一个顶都高（下一个顶不存在时按未确认处理，仍保留）。
    """
    if len(points) < 5:
        return list(points)

    tops = [k for k, p in enumerate(points) if p["type"] == "top"]
    bots = [k for k, p in enumerate(points) if p["type"] == "bottom"]

    keep = set()
    for group in (tops, bots):
        for j, k in enumerate(group):
            cur = points[k]["price"]
            prev = points[group[j - 1]]["price"] if j > 0 else None
            nxt = points[group[j + 1]]["price"] if j + 1 < len(group) else None
            if points[k]["type"] == "top":
                if (prev is None or cur > prev) and (nxt is None or cur > nxt):
                    keep.add(k)
            else:
                if (prev is None or cur < prev) and (nxt is None or cur < nxt):
                    keep.add(k)

    seg = []
    for k in sorted(keep):
        cur = points[k]
        if seg and seg[-1]["type"] == cur["type"]:
            better = (cur["price"] > seg[-1]["price"]) if cur["type"] == "top" \
                else (cur["price"] < seg[-1]["price"])
            if better:
                seg[-1] = cur
            continue
        seg.append(cur)

    # 末端未确认的最新笔端点仍应挂上，否则线段永远滞后一笔
    if seg and points[-1]["type"] != seg[-1]["type"] and points[-1] is not seg[-1]:
        seg.append(points[-1])
    return seg if len(seg) >= 3 else list(points)


# ==================== 5. 缠论：中枢 ====================

def find_pivots(points: list, max_pivots: int = 4) -> list:
    """
    中枢识别（区间重叠实用口径）。

    关键：**ZG/ZD 由前三段一次确定后就固定不变**。
    早期实现在延伸时不断 min/max 收窄 ZG/ZD，结果一个中枢能跨 8 个月 16 段、
    区间被压成 0.03% 宽 —— 那算出来的是「所有段的公共交集」，不是中枢。
    正确做法是：ZG=前三段高点的最小值，ZD=前三段低点的最大值，此后每来一段
    只判断它与固定区间 [ZD, ZG] 是否仍有重叠；有则视为中枢延伸（legs+1），
    第一次完全脱离就结束这个中枢。

    返回 [{start_i, end_i, start_k, end_k, zg, zd, legs, direction, alive}]，
    按时间倒序取最近 max_pivots 个。alive=True 表示中枢右端就是最新走势，
    尚未确认离开。
    """
    if len(points) < 4:
        return []

    def leg_range(a, b):
        return (max(a["price"], b["price"]), min(a["price"], b["price"]))

    n_legs = len(points) - 1
    pivots = []
    k = 0
    while k + 3 < len(points):
        r = [leg_range(points[k + j], points[k + j + 1]) for j in range(3)]
        zg = min(x[0] for x in r)
        zd = max(x[1] for x in r)
        # 三段无共同重叠，或区间宽度不足 0.3%（退化成一条线，无实战意义）
        if zg <= zd or _pct(zd, zg) < 0.3:
            k += 1
            continue

        end = k + 3
        legs = 3
        while end + 1 < len(points) and legs < _PIVOT_MAX_LEGS:
            h, l = leg_range(points[end], points[end + 1])
            if min(h, zg) > max(l, zd):          # 与固定区间仍有重叠 → 延伸
                end += 1
                legs += 1
            else:
                break

        pivots.append({
            "start_i": points[k]["i"], "end_i": points[end]["i"],
            "start_k": k, "end_k": end,
            "zg": zg, "zd": zd, "legs": legs,
            "direction": "up" if points[k]["type"] == "bottom" else "down",
            "alive": end >= n_legs - 1,
            "legs_after": max(n_legs - end, 0),
        })
        k = end
    return pivots[-max_pivots:]


# ==================== 6. 帝纳波利：DMA 位移移动平均 ====================

DMA_SPEC = (("3x3", 3, 3, "#f39c12"), ("7x5", 7, 5, "#9b59b6"), ("25x5", 25, 5, "#16a085"))


def dma_lines(df: pd.DataFrame) -> dict:
    """
    位移移动平均。SMA(period) 向右位移 shift 根 —— 位移段落在未来，
    所以序列长度 = len(df) + shift，前 len(df) 段与 K 线对齐，尾部 shift 根是悬空预测段。
    返回 {name: {"y": list, "future": int, "color": str, "last": float}}
    """
    close = pd.to_numeric(df["close"], errors="coerce")
    out = {}
    for name, period, shift, color in DMA_SPEC:
        if len(close) < period + shift:
            continue
        ma = close.rolling(period).mean()
        y = [None] * shift + [None if pd.isna(v) else float(v) for v in ma]
        last = next((v for v in reversed(y) if v is not None), None)
        out[name] = {"y": y, "future": shift, "color": color, "last": last}
    return out


# ==================== 7. 帝纳波利：摆动点与 Fibnode ====================

def swings(df: pd.DataFrame, points: list, limit: int = 6) -> list:
    """把缠论笔端点复用为帝纳波利的摆动点（reaction point）。倒序取最近 limit 个。"""
    return points[-limit:] if points else []


def fibnodes(points: list, top_n: int = 2) -> list:
    """
    对最近 top_n 段推动浪计算 F3(0.382) / F5(0.618) 回撤位。
    每段用相邻两个异型端点 A→B，A 是起点，B 是推动终点。
    返回 [{"from": A价, "to": B价, "dir": "up"/"down", "f3": x, "f5": x,
           "start_i": i, "end_i": i}]
    """
    if len(points) < 2:
        return []
    out = []
    for a, b in zip(points[-(top_n + 1):-1], points[-top_n:]):
        rng = b["price"] - a["price"]
        if abs(rng) < 1e-12:
            continue
        out.append({
            "from": a["price"], "to": b["price"],
            "dir": "up" if rng > 0 else "down",
            "f3": b["price"] - rng * 0.382,
            "f5": b["price"] - rng * 0.618,
            "start_i": a["i"], "end_i": b["i"],
        })
    return out


def fib_targets(points: list) -> dict:
    """
    帝纳波利目标位，需要 A→B→C 三点：
      A = 推动浪起点，B = 推动浪终点，C = 对 A→B 的回撤终点。
    有效性要求 C 必须落在 A 与 B 之间（真回撤），否则 A→B→C 是同向延伸，
    不是帝纳波利定义的 ABC，算出来的目标位没有意义 —— 此时回退一个端点重试。
      COP = C + 0.618*(B-A)，OP = C + 1.0*(B-A)，XOP = C + 1.618*(B-A)
    返回 {} 表示条件不足。
    """
    for off in (0, 1):
        if len(points) < 3 + off:
            break
        sl = points[:len(points) - off] if off else points
        a, b, c = sl[-3], sl[-2], sl[-1]
        rng = b["price"] - a["price"]
        if abs(rng) < 1e-12:
            continue
        lo, hi = min(a["price"], b["price"]), max(a["price"], b["price"])
        if not (lo < c["price"] < hi):
            continue                      # C 不在 A~B 之间，不是有效回撤
        retr = abs((b["price"] - c["price"]) / rng)
        return {
            "a": a["price"], "b": b["price"], "c": c["price"],
            "dir": "up" if rng > 0 else "down",
            "retrace": retr,
            "confirmed": off == 0,        # off=1 表示用的是更早的 ABC，C 已被后续走势突破
            "cop": c["price"] + rng * 0.618,
            "op": c["price"] + rng * 1.0,
            "xop": c["price"] + rng * 1.618,
            "a_i": a["i"], "b_i": b["i"], "c_i": c["i"],
        }
    return {}


def confluence(nodes: list, tol_pct: float = 1.5) -> list:
    """
    汇聚区：不同摆动算出的 Fibnode 价位互相靠近（价差 < tol_pct%）时合并。
    帝纳波利认为汇聚位的支撑/阻力强度显著高于单一节点。
    返回 [{"price": 中心价, "members": ["段1 F3", "段2 F5"], "lo": x, "hi": x}]
    """
    flat = []
    for idx, nd in enumerate(nodes, 1):
        flat.append((nd["f3"], f"段{idx} F3"))
        flat.append((nd["f5"], f"段{idx} F5"))
    flat = [(p, tag) for p, tag in flat if np.isfinite(p)]
    flat.sort()

    out, cur = [], []
    for p, tag in flat:
        if cur and abs(_pct(cur[0][0], p)) <= tol_pct:
            cur.append((p, tag))
        else:
            if len(cur) >= 2:
                ps = [x[0] for x in cur]
                out.append({"price": float(np.mean(ps)), "lo": min(ps), "hi": max(ps),
                            "members": [x[1] for x in cur]})
            cur = [(p, tag)]
    if len(cur) >= 2:
        ps = [x[0] for x in cur]
        out.append({"price": float(np.mean(ps)), "lo": min(ps), "hi": max(ps),
                    "members": [x[1] for x in cur]})
    return out


# ==================== 8. 帝纳波利 MACD(8/17/9) 与背离 ====================

def macd_dinapoli(df: pd.DataFrame, fast: int = 8, slow: int = 17, signal: int = 9) -> pd.DataFrame:
    """帝纳波利偏好的快 MACD 参数。返回含 dif/dea/hist 三列的 DataFrame。"""
    close = pd.to_numeric(df["close"], errors="coerce")
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()
    dif = ef - es
    dea = dif.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"dif": dif, "dea": dea, "hist": (dif - dea) * 2})


def find_divergence(df: pd.DataFrame, points: list, macd: pd.DataFrame) -> list:
    """
    背离：相邻同型端点之间，价格与 MACD 的 dif 走向相反。
    顶背离 = 价格新高但 dif 不新高；底背离 = 价格新低但 dif 不新低。
    只看最近两组同型端点，返回 [{"type", "i0", "i1", "p0", "p1", "d0", "d1"}]
    """
    if len(points) < 3 or macd.empty:
        return []
    dif = macd["dif"].to_numpy(dtype="float64")
    n = len(dif)
    out = []
    for t, want in (("top", "顶背离"), ("bottom", "底背离")):
        same = [p for p in points if p["type"] == t and 0 <= p["i"] < n]
        if len(same) < 2:
            continue
        a, b = same[-2], same[-1]
        d0, d1 = dif[a["i"]], dif[b["i"]]
        if not (np.isfinite(d0) and np.isfinite(d1)):
            continue
        hit = (b["price"] > a["price"] and d1 < d0) if t == "top" \
            else (b["price"] < a["price"] and d1 > d0)
        if hit:
            out.append({"type": want, "i0": a["i"], "i1": b["i"],
                        "p0": a["price"], "p1": b["price"], "d0": float(d0), "d1": float(d1)})
    return out


# ==================== 9. 顶层：一次算完所有结构 ====================

def analyze(df: pd.DataFrame) -> dict:
    """
    对一份 K 线做完整技术分析。返回结构字典，UI 层只负责画和展示，不做计算。
    失败/数据不足时返回 {"ok": False, "reason": str}。
    """
    if not _ok(df):
        return {"ok": False, "reason": f"K 线不足 {MIN_BARS} 根，无法做结构分析。"}

    try:
        bars = merge_klines(df)
        fr = find_fractals(bars)
        pts = chan_strokes(df)
        segs = build_segments(pts)
        pivots = find_pivots(pts)
        nodes = fibnodes(pts, top_n=2)
        targets = fib_targets(pts)
        conf = confluence(nodes)
        macd = macd_dinapoli(df)
        divs = find_divergence(df, pts, macd)
        dmas = dma_lines(df)
    except Exception as e:                                    # 单个标的数据异常不该崩页
        return {"ok": False, "reason": f"结构计算异常：{type(e).__name__}"}

    close = float(pd.to_numeric(df["close"], errors="coerce").iloc[-1])
    return {
        "ok": True, "close": close, "n_bars": len(df),
        "merged": len(bars), "fractals": fr,
        "strokes": pts, "segments": segs, "pivots": pivots,
        "fibnodes": nodes, "targets": targets, "confluence": conf,
        "macd": macd, "divergence": divs, "dma": dmas,
        "state": _describe_state(close, pts, pivots, dmas, divs, targets),
        "levels": _key_levels(close, pivots, nodes, targets, conf),
    }


def _describe_state(close: float, pts: list, pivots: list, dmas: dict,
                    divs: list, targets: dict) -> dict:
    """把结构翻译成人话。返回 {"chan": str, "dinapoli": str, "target": str, "warn": [str]}"""
    warn = []

    # --- 缠论位置
    if not pts:
        chan = "笔数量不足，尚未形成可辨识的结构。"
    elif pivots:
        pv = pivots[-1]
        zg, zd = pv["zg"], pv["zd"]
        dir_cn = "上涨" if pv["direction"] == "up" else "下跌"
        band = f"{_n(zd)} ~ {_n(zg)}"
        if close > zg:
            chan = (f"已向上突破最近一个{dir_cn}中枢（{band}，{pv['legs']} 段），"
                    f"当前价高出中枢上沿 {_pct(zg, close):+.2f}%。")
        elif close < zd:
            chan = (f"已向下跌破最近一个{dir_cn}中枢（{band}，{pv['legs']} 段），"
                    f"当前价低于中枢下沿 {_pct(zd, close):+.2f}%。")
        else:
            span = zg - zd
            pos = (close - zd) / span * 100 if span > 0 else 50.0
            chan = (f"仍在最近一个{dir_cn}中枢内部震荡（{band}，已走 {pv['legs']} 段），"
                    f"当前处于中枢区间约 {pos:.0f}% 的位置，方向未明。")
        if pv["legs"] >= 7:
            warn.append(f"该中枢已延伸 {pv['legs']} 段，属于长时间盘整，突破方向确认前假信号会偏多。")
    else:
        last = pts[-1]
        d = "上涨笔" if last["type"] == "top" else "下跌笔"
        chan = f"尚未形成中枢，当前处于单边{d}中，最近一个端点价位 {_n(last['price'])}。"

    if len(pts) >= 2:
        a, b = pts[-2], pts[-1]
        way = "向上" if b["price"] > a["price"] else "向下"
        chan += f" 最近一笔{way}，从 {_n(a['price'])} 到 {_n(b['price'])}，幅度 {abs(_pct(a['price'], b['price'])):.2f}%。"

    # --- 帝纳波利趋势判定：价 vs 3x3
    d33 = dmas.get("3x3", {}).get("last")
    d75 = dmas.get("7x5", {}).get("last")
    d255 = dmas.get("25x5", {}).get("last")
    parts = []
    if d33:
        if close >= d33:
            parts.append(f"价格站在 3x3 DMA（{_n(d33)}）上方，短线趋势健康")
        else:
            parts.append(f"价格已跌破 3x3 DMA（{_n(d33)}），短线趋势转弱")
    if d33 and d75:
        parts.append("3x3 位于 7x5 上方，中短期均线多头排列" if d33 > d75
                     else "3x3 位于 7x5 下方，中短期均线空头排列")
    if d255:
        parts.append(f"25x5 DMA 在 {_n(d255)}，{'构成下方支撑' if close >= d255 else '构成上方阻力'}")
    dinapoli = "；".join(parts) + "。" if parts else "均线数据不足。"

    for dv in divs:
        warn.append(f"MACD(8/17/9) 出现{dv['type']}：价格 {_n(dv['p0'])} → {_n(dv['p1'])}，"
                    f"但 DIF {dv['d0']:.3f} → {dv['d1']:.3f} 未同步。")

    # --- 目标位描述
    if targets:
        way = "上涨" if targets["dir"] == "up" else "下跌"
        tail = "" if targets.get("confirmed") else "（该 ABC 的 C 点已被后续走势突破，目标位仅作参考）"
        target = (f"以 A={_n(targets['a'])} → B={_n(targets['b'])} → C={_n(targets['c'])} 构成的"
                  f"{way} ABC 结构测算，回撤深度 {targets['retrace'] * 100:.1f}%："
                  f"COP {_n(targets['cop'])} ／ OP {_n(targets['op'])} ／ XOP {_n(targets['xop'])}。"
                  f"帝纳波利的口径是 COP 最常达到、OP 次之、XOP 属于强势延伸。{tail}")
    else:
        target = "尚未形成有效的 ABC 回撤结构（要求 C 点落在 A、B 之间），无法测算 COP/OP/XOP。"

    return {"chan": chan, "dinapoli": dinapoli, "target": target, "warn": warn}


def _key_levels(close: float, pivots: list, nodes: list, targets: dict, conf: list) -> list:
    """
    汇总所有关键价位并按「距现价远近」排序，标注类型与方向。
    返回 [{"price", "label", "side", "gap_pct"}]，side ∈ {"支撑","阻力"}
    """
    out = []

    def add(p, label):
        if p is None or not np.isfinite(p) or p <= 0:
            return
        out.append({"price": float(p), "label": label,
                    "side": "支撑" if p < close else "阻力",
                    "gap_pct": _pct(close, p)})

    if pivots:
        pv = pivots[-1]
        add(pv["zg"], "中枢上沿 ZG")
        add(pv["zd"], "中枢下沿 ZD")
    for idx, nd in enumerate(nodes, 1):
        add(nd["f3"], f"F3 回撤位（段{idx} 0.382）")
        add(nd["f5"], f"F5 回撤位（段{idx} 0.618）")
    if targets:
        add(targets["cop"], "COP 目标位（0.618）")
        add(targets["op"], "OP 目标位（1.000）")
        add(targets["xop"], "XOP 目标位（1.618）")
    for c in conf:
        add(c["price"], f"汇聚区（{' + '.join(c['members'])}）")

    out.sort(key=lambda x: abs(x["gap_pct"]))
    return out[:10]
