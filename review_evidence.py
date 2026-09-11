# -*- coding: utf-8 -*-
"""
引导式复盘答卷 - 每题当日标的/数据参考（计算层）

存在理由：纯选择题会退化成「闭眼猜」。每题配上当日真实盘面的
标的名单与关键数字，用户「看着盘面数据填卷」，AI 对比与次日
回验才有意义（用户反馈 2026-09-11：每题下面要有对应标的参考）。

泄题红线（结构级约束，tools_probe_review 硬断言）：
  本模块只输出**事实素材**（名单、数值、时间序列），绝不输出定性
  结论——derived.phase（情绪周期位置）、ladder_gap.verdict（梯队
  完整性判读）、breadth.verdict、sector.verdict（轮动定性）一个都
  不取。周期/模式/风格正是答卷要考的题，把站内判读亮出来等于泄题，
  锚定效应会毁掉「你的判断 vs AI 判断 vs 实际回验」的产品灵魂。

一处实现原则：daily_review_ai 的天梯/板块上下文提取也走本模块
（ladder_snapshot / sector_snapshot），杜绝两处实现漂移。历史上
daily_review_ai 取板块涨幅用 pct_1d 字段——实际字段名是 pct_chg，
恒 None 的静默 bug 就在这次收口时修复（2026-09-11）。

分层约束：不 import streamlit、不联网、只读本地 data/ 产物。
缺数据 = 该题参考缺失，绝不编造名单或数字。
"""
from __future__ import annotations

import csv
import json
import os

DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
LADDER_PATH = os.path.join("data", "limit_ladder.json")
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")
SPEC_PATH = os.path.join("data", "speculation_data.json")
ANALYSIS_PATH = os.path.join("data", "ai_market_analysis.json")
BREAKOUT_PATH = os.path.join("data", "breakout_stocks.csv")
STRONG_PATH = os.path.join("data", "strong_stocks.csv")
SNAPSHOT_PATH = os.path.join("data", "market_snapshot.csv")
INDEX_PATH = os.path.join("data", "index_history.csv")

# 泄题词表：这些定性结论一旦混进 evidence 输出即违规（探针断言用）
PHASE_WORDS = ("启动期", "发酵期", "高潮期", "退潮期", "冰点期")
LADDER_VERDICT_MARKS = ("接力链条", "梯队断裂")


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _d8(v) -> str:
    """任意日期形态 → YYYYMMDD（'2026-09-02' / 20260902 / '…01:57:42' 前10位）。"""
    return "".join(ch for ch in str(v or "")[:10] if ch.isdigit())


def _pct(x, digits: int = 1) -> str:
    """带符号百分比；None/异常 → '—'（不编数）。"""
    try:
        return f"{float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _f(x, digits: int = 1) -> str:
    try:
        return f"{float(x):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


# ---------------------------------------------------------------------------
# 一处实现：AI 答卷上下文与页面参考共用的结构化提取
# ---------------------------------------------------------------------------

def ladder_snapshot(ladder: dict | None) -> dict | None:
    """连板天梯 → 分层结构。返回 None 表示天梯缺失。"""
    if not ladder:
        return None
    stocks = ladder.get("stocks") or []
    by_h: dict[int, list] = {}
    for s in stocks:
        h = int(s.get("limit_times") or 0)
        by_h.setdefault(h, []).append(s)
    tiers = [{"height": h, "count": len(by_h[h]),
              "names": [s.get("name") for s in by_h[h]][:8]}
             for h in sorted(by_h, reverse=True)]
    top5 = [{"name": s.get("name"), "limit_times": s.get("limit_times"),
             "industry": s.get("industry")}
            for s in sorted(stocks, key=lambda x: -(x.get("limit_times") or 0))[:5]]
    return {"total": ladder.get("total_count"),
            "max_height": ladder.get("max_height"),
            "tiers": tiers, "top5": top5}


def sector_snapshot(sector: dict | None) -> list[dict]:
    """板块涨幅榜 Top10。注意字段是 pct_chg（原 pct_1d 是字段名错配，恒 None）。"""
    if not sector:
        return []
    return [{"name": t.get("name"), "pct": t.get("pct_chg"),
             "pct_5d": t.get("pct_5d"), "streak": t.get("streak")}
            for t in (sector.get("top") or [])[:10] if t.get("name")]


# ---------------------------------------------------------------------------
# 各题素材（返回 {"title","lines","source"}；缺数据返回 None，不硬造）
# ---------------------------------------------------------------------------

def _stock_label(s: dict) -> str:
    name = s.get("name") or ""
    code = (s.get("code") or "")[-6:] if s.get("code") else ""
    ind = s.get("industry") or ""
    ft = (s.get("first_time") or "")[:5]
    return f"{name}({code})·{ind}·首封{ft}" if ft else f"{name}({code})·{ind}"


def _evd_breadth(derived: dict) -> dict | None:
    b = derived.get("breadth") or {}
    if b.get("status") != "ok":
        return None
    p = derived.get("premium") or {}
    prem = (f"连板溢价中位 {_pct(p.get('median_pct'))}（{p.get('n')} 只，"
            f"胜率 {_f((p.get('win_rate') or 0) * 100)}%，再板 {p.get('limit_again')} 只）"
            if p.get("status") == "ok" else "连板溢价数据未就位")
    return {
        "title": "赚钱效应体感素材",
        "lines": [
            f"- 上涨 {b.get('up')} 家 / 下跌 {b.get('down')} 家"
            f"（上涨占比 {_f((b.get('up_ratio') or 0) * 100)}%）",
            f"- 全市场涨幅：均值 {_pct(b.get('mean_pct'))} / 中位数 {_pct(b.get('median_pct'))}",
            f"- {prem}",
        ],
        "source": "sentiment/derived.json · breadth+premium",
    }


def _evd_promotion(derived: dict) -> dict | None:
    pr = derived.get("promotion") or {}
    lg = derived.get("ladder_gap") or {}
    lines = []
    if pr.get("status") == "ok":
        for k, v in (pr.get("rates") or {}).items():
            note = "" if v.get("reliable") else "（样本小）"
            lines.append(f"- 晋级率 {k}：{_f((v.get('rate') or 0) * 100)}%"
                         f"（{v.get('promoted')}/{v.get('base')}）{note}")
    if lg.get("status") == "ok":
        tiers = " → ".join(f"{t.get('height')}板{t.get('count')}只"
                           for t in (lg.get("tiers") or []))
        gap = f"断层位：{'、'.join(str(g) for g in lg.get('gaps'))}" if lg.get("gaps") \
            else "无断层（各高度连续）"
        lines.append(f"- 天梯分层：{tiers}（共 {lg.get('total')} 家，首板 {lg.get('first_board')} 家）")
        lines.append(f"- {gap}")
    p = derived.get("premium") or {}
    if p.get("status") == "ok":
        lines.append(f"- 连板溢价：中位 {_pct(p.get('median_pct'))} / 均值 {_pct(p.get('mean_pct'))}"
                     f"（{p.get('n')} 只，再板 {p.get('limit_again')} 只）")
    if not lines:
        return None
    return {"title": "情绪周期判断素材（晋级率/溢价/天梯）",
            "lines": lines, "source": "sentiment/derived.json"}


def _index_tail() -> list[tuple[str, float, float, float]]:
    """index_history 尾部 11 行 → [(date, zz1000, zz500, spread)]。"""
    rows = _load_csv(INDEX_PATH)
    out = []
    for r in rows[-11:]:
        try:
            out.append((r.get("date") or "", float(r["zz1000"]),
                        float(r["zz500"]), float(r["spread"])))
        except (KeyError, ValueError):
            continue
    return out


def _evd_index_cycle() -> dict | None:
    t = _index_tail()
    if len(t) < 4:
        return None
    lines = ["中证1000 近10日（收盘 / 日涨幅）："]
    for i in range(1, len(t)):
        chg = (t[i][1] / t[i - 1][1] - 1) * 100 if t[i - 1][1] else None
        lines.append(f"- {t[i][0][5:]}　{_f(t[i][1], 2)}　{_pct(chg)}")
    cum = (t[-1][1] / t[0][1] - 1) * 100 if t[0][1] else None
    lines.append(f"- 近 {len(t) - 1} 日累计 {_pct(cum)}（周期位置自己判断，不给结论）")
    return {"title": "指数走势素材（中证1000）", "lines": lines,
            "source": "index_history.csv"}


def _evd_style() -> dict | None:
    t = _index_tail()
    if len(t) < 7:
        return None
    def chg(i, j):
        return (t[i][j] / t[i - 1][j] - 1) * 100 if t[i - 1][j] else None
    def cum(j, n):
        i = max(0, len(t) - 1 - n)
        return (t[-1][j] / t[i][j] - 1) * 100 if t[i][j] else None
    lines = [
        f"- 中证1000：今日 {_pct(chg(-1, 1))} / 近5日 {_pct(cum(1, 5))}",
        f"- 中证500：今日 {_pct(chg(-1, 2))} / 近5日 {_pct(cum(2, 5))}",
        f"- 近5日 1000−500 相对强弱 {_pct(cum(1, 5) - cum(2, 5), 1) if cum(1,5) is not None and cum(2,5) is not None else '—'}",
    ]
    lines.append("- 站内日度仅覆盖中证500/1000（中盘/小盘）；其余指数请结合行情软件")
    return {"title": "风格/领先指数素材", "lines": lines,
            "source": "index_history.csv"}


def _evd_popular(ladder: dict) -> dict | None:
    stocks = ladder.get("stocks") or []
    hi = [s for s in stocks if (s.get("limit_times") or 0) >= 2]
    if not hi:
        return None
    hi.sort(key=lambda s: -(s.get("limit_times") or 0))
    lines = [f"- {_stock_label(s)}" for s in hi[:10]]
    lines.append(f"- 2板及以上共 {len(hi)} 只（高板池 = 当日人气核心区）")
    return {"title": "人气股参考（2板及以上高板池）", "lines": lines,
            "source": "limit_ladder.json"}


def _evd_ladder(ladder: dict) -> dict | None:
    snap = ladder_snapshot(ladder)
    if not snap or not snap.get("tiers"):
        return None
    lines = []
    for tier in snap["tiers"]:
        h, c = tier["height"], tier["count"]
        names = "、".join(tier["names"][:8]) + (" 等" if c > 8 else "")
        lines.append(f"- {h}板（{c}只）：{names}")
    first = [s for s in ladder.get("stocks") or []
             if (s.get("limit_times") or 0) == 1]
    first.sort(key=lambda s: s.get("first_time") or "99")
    if first:
        lines.append("- 首板最早（明日 1进2 池参考）：" +
                     "、".join(s.get("name") or "" for s in first[:8]))
    return {"title": "连板天梯完整分层", "lines": lines,
            "source": "limit_ladder.json"}


def _evd_newhigh(trade_date: str) -> dict | None:
    bk = [r for r in _load_csv(BREAKOUT_PATH)
          if _d8(r.get("update_date")) == trade_date]
    st = [r for r in _load_csv(STRONG_PATH)
          if _d8(r.get("更新日期")) == trade_date]
    lines = []
    for mark, key, cap in (("创一年新高", "250日", 10), ("创半年新高", "120日", 6),
                           ("创60日新高", "60日", 5)):
        rows = [r for r in bk if key in (r.get("level") or "")]
        if rows:
            rows.sort(key=lambda r: -(float(r["pct_chg"]) if r.get("pct_chg") else 0))
            names = "、".join(f"{r.get('name')}({_pct(r.get('pct_chg'))})" for r in rows[:cap])
            lines.append(f"- {mark} {len(rows)} 只：{names}")
    if st:
        st.sort(key=lambda r: -(float(r["RPS_50"]) if r.get("RPS_50") else 0))
        lines.append("- RPS50 前三：" + "、".join(
            f"{r.get('name')}(RPS {_f(r.get('RPS_50'))})" for r in st[:3]))
    if not lines:
        return None
    return {"title": "趋势新高与强势股素材", "lines": lines,
            "source": "breakout_stocks.csv + strong_stocks.csv"}


def _spec_matches(td: str, spec: dict | None) -> bool:
    """speculation_data.json 的 date 是**生成时间戳**而非交易日（凌晨跑批
    会标次日 01:57），不能精确等值匹配。窗口规则：与交易日差 0~1 天视为
    覆盖当日；差得远说明是别的日期的产物，不能拿来冒充当日素材。"""
    sd = _d8((spec or {}).get("date"))
    if not sd or not td:
        return False
    try:
        return 0 <= int(sd) - int(td) <= 1
    except ValueError:
        return False


def _evd_convertible(td: str) -> dict | None:
    spec = _load_json(SPEC_PATH)
    if not _spec_matches(td, spec):
        return None
    cbs = spec.get("cb_list") or []
    lines = [f"- {c.get('bond_short_name')} · {c.get('tag')} · {c.get('desc')}"
             f" · 溢价 {_f(c.get('premium_rate'))}% · 双低 {_f(c.get('double_low'), 2)}"
             for c in cbs[:6]]
    funds = spec.get("fund_list") or []
    for fu in funds[:2]:
        lines.append(f"- 套利基金：{fu.get('name')} {_pct(fu.get('change'))}（{fu.get('tag')}）")
    gen = (spec.get("date") or "")[:16]
    return {"title": "转债与套利参考（双低前列）", "lines": lines,
            "source": f"speculation_data.json · 生成于 {gen}"}


def _sector_matches(td: str, sector: dict | None) -> bool:
    """板块产物日期必须等于当日交易日（analysis.json 的 date 是
    'YYYY-MM-DD' 形态，_d8 已归一化）。不等就不能当作当日板块榜。"""
    return bool(sector and td and _d8(sector.get("date")) == td)


def _evd_sector_table(td: str) -> dict | None:
    sector = _load_json(SECTOR_PATH)
    if not _sector_matches(td, sector):
        return None
    top = sector_snapshot(sector)
    if not top:
        return None
    lines = [f"- {i + 1}. {t['name']}　今日 {_pct(t.get('pct'))} / 5日 {_pct(t.get('pct_5d'))}"
             f"（上榜第 {t.get('streak')} 天）" for i, t in enumerate(top)]
    ov = sector.get("overlap_count")
    if ov is not None:
        lines.append(f"- 与上一交易日 Top10 重合 {ov}/{sector.get('overlap_denominator')}（重合少=换脸快）")
    return {"title": "当日板块涨幅榜 Top10", "lines": lines,
            "source": "sector_rotation/analysis.json"}


def _evd_new_theme(td: str) -> dict | None:
    sector = _load_json(SECTOR_PATH)
    if not _sector_matches(td, sector):
        return None
    top = sector_snapshot(sector)
    if not top:
        return None
    fresh = [t for t in top if (t.get("streak") or 0) <= 1]
    ov, od = sector.get("overlap_count"), sector.get("overlap_denominator")
    if fresh:
        lines = ["新上榜（streak≤1，篡位/分流候选）："]
        lines += [f"- {t['name']}　今日 {_pct(t.get('pct'))} / 5日 {_pct(t.get('pct_5d'))}"
                  for t in fresh]
    else:
        lines = [f"- Top10 无新面孔（与上一交易日重合 {ov}/{od}）"]
    return {"title": "新题材观察素材", "lines": lines,
            "source": "sector_rotation/analysis.json"}


def _evd_patterns(trade_date: str, ladder: dict) -> dict | None:
    lines = []
    snap = ladder_snapshot(ladder)
    if snap and snap.get("top5"):
        lines.append("- 连板接力 · 最高板：" + "、".join(
            f"{s['name']}({s['limit_times']}板)" for s in snap["top5"][:3]))
    snap_rows = _load_csv(SNAPSHOT_PATH)
    if snap_rows:
        def amt(r):
            try:
                return float(r["amount"])
            except (KeyError, ValueError):
                return 0.0
        top_amt = sorted(snap_rows, key=amt, reverse=True)[:5]
        lines.append("- 抱团 · 成交额前列：" + "、".join(
            f"{r.get('name')}({_f(amt(r) / 10000)}亿)" for r in top_amt))
    st = [r for r in _load_csv(STRONG_PATH) if _d8(r.get("更新日期")) == trade_date]
    if st:
        st.sort(key=lambda r: -(float(r["RPS_50"]) if r.get("RPS_50") else 0))
        lines.append("- 趋势低吸 · RPS50 前列：" + "、".join(
            f"{r.get('name')}(RPS {_f(r.get('RPS_50'))})" for r in st[:3]))
        tr = sorted(st, key=lambda r: -(float(r["turnover_rate"]) if r.get("turnover_rate") else 0))
        lines.append("- 人气低吸 · 强势股池换手前列（池内口径）：" + "、".join(
            f"{r.get('name')}(换手 {_f(r.get('turnover_rate'))}%)" for r in tr[:5]))
    spec = _load_json(SPEC_PATH)
    cbs = (spec.get("cb_list") or [])[:2] if spec else []
    if cbs:
        lines.append("- 转债投机 · 双低前列：" + "、".join(
            c.get("bond_short_name") or "" for c in cbs))
    hi = [s for s in (ladder.get("stocks") or []) if (s.get("limit_times") or 0) >= 2]
    if hi:
        lines.append(f"- 高板人气池：2板及以上 {len(hi)} 只")
    lines.append("- 注册制次新 / 开板次新 / 后排补涨：站内暂无专门标的池（凭盘感）")
    if len(lines) <= 1:
        return None
    return {"title": "各模式对应标的对照", "lines": lines,
            "source": "天梯+快照+RPS+转债（多源）"}


def _evd_recognition(trade_date: str, ladder: dict) -> dict | None:
    lines = []
    snap_rows = _load_csv(SNAPSHOT_PATH)
    if snap_rows:
        def amt(r):
            try:
                return float(r["amount"])
            except (KeyError, ValueError):
                return 0.0
        top_amt = sorted(snap_rows, key=amt, reverse=True)[:5]
        lines.append("- 最大成交金额：" + "、".join(
            f"{r.get('name')}({_f(amt(r) / 10000)}亿·{r.get('industry')})" for r in top_amt))
    stocks = ladder.get("stocks") or []
    yizi = [s for s in stocks if (s.get("first_time") or "").startswith("09:25:00")]
    if yizi:
        lines.append("- 竞价一字（09:25:00 口径）：" +
                     "、".join(s.get("name") or "" for s in yizi[:8]))
    first = sorted([s for s in stocks if (s.get("limit_times") or 0) == 1],
                   key=lambda s: s.get("first_time") or "99")
    if first:
        lines.append("- 低位首板（今日首板最快）：" +
                     "、".join(s.get("name") or "" for s in first[:8]))
    bk = [r for r in _load_csv(BREAKOUT_PATH)
          if _d8(r.get("update_date")) == trade_date and "250日" in (r.get("level") or "")]
    if bk:
        lines.append("- 创一年新高（全年涨幅维度）：" +
                     "、".join(r.get("name") or "" for r in bk[:10]))
    st = [r for r in _load_csv(STRONG_PATH) if _d8(r.get("更新日期")) == trade_date]
    if st:
        st.sort(key=lambda r: -(float(r["turnover_rate"]) if r.get("turnover_rate") else 0))
        lines.append("- 最强换手（RPS 强势股池内口径）：" + "、".join(
            f"{r.get('name')}({_f(r.get('turnover_rate'))}%)" for r in st[:5]))
    if not lines:
        return None
    return {"title": "辨识度标签对应标的", "lines": lines,
            "source": "快照+天梯+突破池+强势股（多源）"}


def _evd_tomorrow(derived: dict) -> dict | None:
    vp = derived.get("verification_plan") or []
    if not vp:
        return None
    lines = ["明日盯这些指标（判断素材，不是结论）："]
    for i, v in enumerate(vp[:4], 1):
        lines.append(f"- {i}. {v.get('指标')}｜今日基准 {v.get('今日基准')}")
        lines.append(f"　 验证：{v.get('验证条件')}")
    return {"title": "明日验证条件", "lines": lines,
            "source": "sentiment/derived.json · verification_plan"}


# ---------------------------------------------------------------------------
# 主入口：qid → 该题的当日标的/数据参考
# ---------------------------------------------------------------------------

def build_evidence(trade_date: str) -> dict[str, dict]:
    """组装每题参考。缺数据 → 该 qid 不在返回 dict（页面静默跳过，不编造）。
    trade_date 为 YYYYMMDD。"""
    td = _d8(trade_date)
    derived = _load_json(DERIVED_PATH)
    if derived and _d8(derived.get("date")) == td:
        d_ok = derived
    else:
        d_ok = None
    ladder = _load_json(LADDER_PATH)
    l_ok = ladder if ladder and _d8(ladder.get("date")) == td else None

    ev: dict[str, dict] = {}
    if d_ok:
        ev["q01_sentiment"] = _evd_breadth(d_ok)
        ev["q02_mood"] = _evd_breadth(d_ok)
        ev["q03_emotion_cycle"] = _evd_promotion(d_ok)
        ev["q14_tomorrow_expect"] = _evd_tomorrow(d_ok)
    ev["q04_index_cycle"] = _evd_index_cycle()
    ev["q05_lead_index"] = _evd_style()
    if l_ok:
        ev["q06_hot_money"] = _evd_popular(l_ok)
        ev["q07_ladder"] = _evd_ladder(l_ok)
        ev["q12_main_pattern"] = _evd_patterns(td, l_ok)
        ev["q13_recognition"] = _evd_recognition(td, l_ok)
    ev["q08_trend_new_high"] = _evd_newhigh(td)
    ev["q09_convertible"] = _evd_convertible(td)
    ev["q10_strongest_theme"] = _evd_sector_table(td)
    ev["q11_new_theme"] = _evd_new_theme(td)
    return {k: v for k, v in ev.items() if v}
