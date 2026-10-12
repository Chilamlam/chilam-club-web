# -*- coding: utf-8 -*-
"""行业信号展示页无头渲染自检（AppTest，纯离线，不联网、不需要 tushare）。

为什么要单独给「页面」写一份探针
--------------------------------
计算层的 103 项断言全部通过时，页面仍可能错，而且错得最隐蔽：

 · **阈值硬编码**：页面上写死「30 / 80」，而 signal.json 里是别的值。
   图和表照样画得出来、看着完全正常，只是高亮的那批行业不是命中那批。
   光看计算结果断言永远抓不到它——必须**换一组阈值让页面渲染**，
   再看命中数是不是跟着变。
 · **两种「没有」被合并**：「今天真的没有行业命中」与「这一步的数据坏了」
   如果都渲染成一句「暂无数据」，读者会把前者读成后者（或反过来）。
   断言必须同时检查「该文案出现」+「另一种文案**不**出现」，
   否则一个「遇事就显示所有提示」的实现也能全绿。
 · **缺值补 0**：占比缺失显示成 0.00%，看起来像「这个行业占比确实是零」。
 · **widget key 跨页相撞**：带 key 的组件在两页同进程渲染时抛
   StreamlitDuplicateElementKey，而单跑任一页都不暴露。

所以本探针只验**页面对用户的承诺**（文案、数字、表格格值），不验图怎么画。
每个用例都配一条「反向观察」：确认没走到另一种分支上，否则用例没有区分度。
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import re
import sys
import tempfile

import pandas as pd
from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.gettempdir())
FIXTURE = TMP / "_isig_probe_fixture.json"
SCRIPT = TMP / "_isig_probe_app.py"
BOTH = TMP / "_isig_probe_both.py"

# 被渲染脚本：把数据与登录态注入进去，页面本体一行都不改。
# 用替换模块属性的方式而不是改页面源码——改源码验的就不是线上那份代码了。
SCRIPT.write_text(
    "import json, os, sys, pathlib\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "import page_industry_signal as p\n"
    "fx = json.load(open(os.environ['ISIG_PROBE_FIXTURE'], encoding='utf-8'))\n"
    "p.load_signal = lambda: fx['sig']\n"
    "p.load_history = lambda: None\n"
    "p.load_history_hits = lambda: fx.get('hits')\n"
    "class _A:\n"
    "    @staticmethod\n"
    "    def is_logged_in(): return bool(fx.get('logged_in'))\n"
    "    @staticmethod\n"
    "    def is_vip(): return bool(fx.get('vip'))\n"
    "    @staticmethod\n"
    "    def get_vip_remaining_days(): return 30\n"
    "p.auth = _A\n"
    "p.render_industry_signal_page(fx.get('reference_date', ''))\n",
    encoding="utf-8",
)

# 与板块轮动页、收盘摘要页同进程渲染：三页都读本地产物，
# 用来验 widget key 没跨页相撞（摘要页也有一段结构相似的「订阅引导」）。
BOTH.write_text(
    "import json, os, sys, pathlib\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "import page_industry_signal as p\n"
    "fx = json.load(open(os.environ['ISIG_PROBE_FIXTURE'], encoding='utf-8'))\n"
    "p.load_signal = lambda: fx['sig']\n"
    "p.load_history = lambda: None\n"
    "p.load_history_hits = lambda: fx.get('hits')\n"
    "class _A:\n"
    "    @staticmethod\n"
    "    def is_logged_in(): return False\n"
    "    @staticmethod\n"
    "    def is_vip(): return False\n"
    "    @staticmethod\n"
    "    def get_vip_remaining_days(): return None\n"
    "p.auth = _A\n"
    "p.render_industry_signal_page('')\n"
    "from page_sector_rotation import render_sector_rotation_page\n"
    "render_sector_rotation_page()\n"
    "from page_digest import render_digest_page\n"
    "render_digest_page()\n",
    encoding="utf-8",
)

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


# ============================ fixture ============================

def _ind(code, name, l1, mv_pct, amt_pct, mv_share=None, amt_share=None,
         pct_chg=0.0):
    return {"code": code, "name": name, "l1_name": l1,
            "mv_share": mv_share if mv_share is not None else round(mv_pct / 10, 4),
            "mv_pct": mv_pct,
            "amt_share": amt_share if amt_share is not None else round(amt_pct / 10, 4),
            "amt_pct": amt_pct, "pct_chg": pct_chg,
            "amount_yi": 10.0, "float_mv_yi": 1000.0}


# 12 个行业，三条不同的阈值下命中数各不相同 —— 这正是「阈值是否硬编码」的探针：
#   阈值 (30, 80) → A/B/C 命中 = 3
#   阈值 (5, 99)  → 0（A 的 mv_pct 恰好 5，卡严格不等式）
#   阈值 (99, 1)  → 全部 12
INDUSTRIES = [
    _ind("SW01.SI", "甲行业", "一级甲", 5.0, 97.5, pct_chg=1.26),
    _ind("SW02.SI", "乙行业", "一级乙", 10.0, 95.0, pct_chg=-2.10),
    _ind("SW03.SI", "丙行业", "一级丙", 25.0, 90.0, pct_chg=0.43),
    _ind("SW04.SI", "丁行业", "一级丁", 35.0, 99.0),
    _ind("SW05.SI", "戊行业", "一级戊", 2.0, 70.0),
    _ind("SW06.SI", "己行业", "一级己", 50.0, 50.0),
    _ind("SW07.SI", "庚行业", "一级庚", 60.0, 60.0),
    _ind("SW08.SI", "辛行业", "一级辛", 70.0, 40.0),
    _ind("SW09.SI", "壬行业", "一级壬", 80.0, 30.0),
    _ind("SW10.SI", "癸行业", "一级癸", 90.0, 20.0),
    _ind("SW11.SI", "子行业", "一级子", 40.0, 85.0),
    # 严格边界：mv_pct 29.9 < 30 成立，但 amt_pct 恰好 80.0 不满足 > 80 → 不命中
    _ind("SW12.SI", "丑行业", "一级丑", 29.9, 80.0),
]


def _mk_sig(inds=None, mv_max=30.0, amt_min=80.0, max_picks=3,
            date="20261009", **extra):
    inds = INDUSTRIES if inds is None else inds
    hits = sorted([i for i in inds if i["mv_pct"] < mv_max and i["amt_pct"] > amt_min],
                  key=lambda r: (-r["amt_pct"], r["mv_pct"], r["code"]))
    sig = {
        "status": "ok", "date": date, "source": "tushare_sw2021_l2",
        "industry_level": "L2", "industry_count": len(inds),
        "trade_days_available": 250, "first_date": "20250922",
        "mv_window": 250, "amt_window": 20,
        "mv_window_actual": 250, "amt_window_actual": 20,
        "thresholds": {"mv_pct_max": mv_max, "amt_pct_min": amt_min,
                       "max_picks": max_picks},
        "universe_mv_total_yi": 457447.95, "universe_amt_total_yi": 1887.91,
        "hits_count": len(hits), "picks": hits[:max_picks],
        "picks_count": len(hits[:max_picks]),
        "industries": inds,
    }
    sig.update(extra)
    return sig


def _mk_hit_record(date, code, name, l1="一级甲", mv=5.0, amt=97.5,
                   pct=1.0, fwd=None, excess=None):
    return {"date": date, "code": code, "name": name, "l1_name": l1,
            "mv_share": 0.5, "mv_pct": mv, "amt_share": 0.6, "amt_pct": amt,
            "pct_chg": pct, "close": 100.0,
            "fwd": {"1": 1.0, "3": 3.03, "5": 5.1, "10": None} if fwd is None else fwd,
            "excess": {"1": 0.8, "3": 2.53, "5": 4.3, "10": None} if excess is None else excess}


def _mk_hits(records=None, empty_days=2, stats=None, **extra):
    """历史回顾 fixture。

    特意让 T+10 全为 None（n=0）：页面必须把它显示成「—」而不是 0 或 NaN——
    把「还没到期」显示成「涨跌为零」会让整张统计表悄悄失真。
    """
    records = [_mk_hit_record("20261008", "SW01.SI", "甲行业"),
               _mk_hit_record("20261009", "SW02.SI", "乙行业",
                              fwd={"1": 2.0, "3": 4.0, "5": 6.0, "10": None},
                              excess={"1": 1.8, "3": 3.5, "5": 5.2, "10": None})] \
        if records is None else records
    dates = sorted({r["date"] for r in records})
    hits = {
        "status": "ok", "forward_days": [1, 3, 5, 10],
        "mv_window": 250, "amt_window": 20,
        "thresholds": {"mv_pct_max": 30.0, "amt_pct_min": 80.0, "max_picks": 3},
        "first_date": "20250922", "last_date": "20261009",
        "trade_days_total": 600,
        "trade_days_scanned": len(dates) + empty_days if dates else empty_days,
        "skipped_short_window": 249, "calendar_gaps": 0,
        "industry_count": 124, "hit_days": len(dates),
        "empty_days": empty_days, "total_hits": len(records),
        "stats": stats if stats is not None else {
            "1": {"n": 2, "mean": 1.0, "median": 1.0, "win_rate": 100.0, "n_paired": 2,
                  "mean_bench": 0.2, "mean_excess": 0.8, "excess_win_rate": 100.0},
            "3": {"n": 2, "mean": 3.03, "median": 3.03, "win_rate": 100.0, "n_paired": 2,
                  "mean_bench": 0.5, "mean_excess": 2.53, "excess_win_rate": 100.0},
            "5": {"n": 2, "mean": 5.1, "median": 5.1, "win_rate": 100.0, "n_paired": 2,
                  "mean_bench": 0.8, "mean_excess": 4.3, "excess_win_rate": 100.0},
            "10": {"n": 0, "mean": None, "median": None, "win_rate": None, "n_paired": 0,
                   "mean_bench": None, "mean_excess": None, "excess_win_rate": None},
        },
        "bench_by_date": {d: {"1": 0.2, "3": 0.5, "5": 0.8} for d in dates},
        "records": records,
    }
    hits.update(extra)
    return hits


def render(sig, reference_date="", logged_in=False, vip=False,
           session_state=None, script=None, timeout=180, hits="__none__"):
    FIXTURE.write_text(json.dumps(
        {"sig": sig, "reference_date": reference_date,
         "logged_in": logged_in, "vip": vip,
         # "__none__" 表示「产物文件不存在」，与显式的 None/空表区分开
         "hits": None if hits == "__none__" else hits}, ensure_ascii=False),
        encoding="utf-8")
    os.environ["ISIG_PROBE_FIXTURE"] = str(FIXTURE)
    at = AppTest.from_file(str(script or SCRIPT), default_timeout=timeout)
    for k, v in (session_state or {}).items():
        at.session_state[k] = v
    at.run()
    return at


def texts(at) -> str:
    out = []
    for coll in (at.markdown, at.caption, at.warning, at.error, at.info,
                 at.success, at.subheader, at.header):
        out += [str(e.value) for e in coll]
    return "\n".join(out)


def metric_labels(at) -> list[str]:
    return [str(m.label) for m in at.metric]


def metric_map(at) -> dict:
    return {str(m.label): str(m.value) for m in at.metric}


def table_md(at):
    """第一个 dataframe 的文本化内容（用于查格值，如「—」有没有出现）。"""
    if not at.dataframe:
        return None
    v = at.dataframe[0].value
    if hasattr(v, "data"):
        v = v.data
    try:
        return v.to_csv(index=False)
    except Exception:                                   # noqa: BLE001
        return str(v)


HAS_DATA_OK = "今日命中"
NO_HIT_TEXT = "今日无同时满足两条件的行业"
NO_DATA_TEXT = "行业信号数据缺失"
INSUFFICIENT_TEXT = "归档还在积累"
STALE_TEXT = "数据可能未更新"
NO_DETAIL_TEXT = "结论里没有行业明细"


# ============================ 1. 正常路径 ============================

def test_normal_render() -> None:
    at = render(_mk_sig())
    if at.exception:
        check("正常数据渲染不抛异常", False, str(at.exception[0].message))
        return
    check("正常数据渲染不抛异常", True)
    tx = texts(at)
    check("命中行业名出现在页面上", "甲行业" in tx and "乙行业" in tx and "丙行业" in tx)
    check("KPI「今日命中」为 3", metric_map(at).get("今日命中") == "3 个",
          str(metric_map(at).get("今日命中")))
    check("KPI「参与行业」为 12", metric_map(at).get("参与行业") == "12 个",
          str(metric_map(at).get("参与行业")))
    check("没有误报「今日无命中」", NO_HIT_TEXT not in tx)
    check("散点图 caption 报出 12 行业 3 命中",
          "共 12 个行业，命中 3 个" in tx, tx[:0])
    csv = table_md(at)
    check("明细表渲染出来了", csv is not None)
    if csv:
        check("明细表含全部 12 个行业",
              all(n in csv for n in ("甲行业", "丑行业", "癸行业")))
        check("明细表同时含不命中的行业（表格是完整名单，不是只有命中）",
              "癸行业" in csv and "戊行业" in csv)
    check("只列前 3 个命中并提示另有几个",
          "另有 0 个" not in tx and "列出前 3 个" not in tx,
          "仅有 3 个命中时不该出现「另有 N 个」的提示")
    check("有走势图选择器", len(at.selectbox) == 1)
    if at.selectbox:
        check("走势图默认选中第一个命中行业",
              at.selectbox[0].value == "SW01.SI", str(at.selectbox[0].value))


def test_more_hits_than_picks() -> None:
    """命中多于 max_picks 时必须提示「另有 N 个」，且表格仍是完整名单。"""
    at = render(_mk_sig(mv_max=99.0, amt_min=1.0))       # 12 个全命中
    tx = texts(at)
    mm = metric_map(at)
    check("全部命中时 KPI 报 12", mm.get("今日命中") == "12 个", str(mm.get("今日命中")))
    check("全部命中时提示「另有 9 个」", "另有 9 个" in tx, tx[:0])
    check("全部命中时抽查了前 3 个（不是全部列出）",
          metric_labels(at).count([l for l in metric_labels(at)
                                   if "市值占比" in l][0]) >= 1
          and len([l for l in metric_labels(at) if "市值占比" in l]) == 3,
          str([l for l in metric_labels(at) if "市值占比" in l]))
    check("散点图报 12 个全命中", "共 12 个行业，命中 12 个" in tx)


# ============================ 2. 阈值必须来自 json ============================

def test_thresholds_from_json() -> None:
    """换一组阈值，页面的命中数必须跟着变 —— 这一条专门抓「阈值硬编码」。

    页面源码里写死 30/80 时，本用例会看到 3（而不是 0 / 12），全绿就会漏掉
    「图上高亮的不是命中那批」这种静默错误。
    """
    at_strict = render(_mk_sig(mv_max=5.0, amt_min=99.0))
    tx_strict = texts(at_strict)
    mm_strict = metric_map(at_strict)
    check("阈值收紧到 (5,99) 后命中数为 0（不是硬编码的 3）",
          mm_strict.get("今日命中") == "0 个", str(mm_strict.get("今日命中")))
    check("阈值收紧后出现「今日无命中」文案", NO_HIT_TEXT in tx_strict)
    check("阈值收紧后仍不报「数据缺失」（0 命中是有效结论）",
          NO_DATA_TEXT not in tx_strict)
    check("0 命中时散点图仍渲染（不能整段消失）",
          "共 12 个行业，命中 0 个" in tx_strict, tx_strict[:0])
    check("页面显示的阈值数字跟着 json 变（出现 5 与 99）",
          "低于 5" in tx_strict and "高于 99" in tx_strict)

    at_loose = render(_mk_sig(mv_max=99.0, amt_min=1.0))
    check("阈值放宽到 (99,1) 后命中数变 12（同上，反向确认）",
          metric_map(at_loose).get("今日命中") == "12 个",
          str(metric_map(at_loose).get("今日命中")))
    check("两组阈值下命中数不同 → 页面确实读的是 json 而不是常量",
          mm_strict.get("今日命中") != metric_map(at_loose).get("今日命中"))


def test_no_hardcoded_thresholds_in_source() -> None:
    """源码级：页面文件里不许出现阈值字面量，且必须引用 thresholds 字段。"""
    src = (ROOT / "page_industry_signal.py").read_text(encoding="utf-8")
    # 去掉注释与文档字符串后再查字面量，否则解释口径的注释会误报
    code_lines = [ln.split("#", 1)[0] for ln in src.splitlines()]
    code = "\n".join(code_lines)
    check("页面源码里没有裸的 30.0 阈值字面量", "30.0" not in code)
    check("页面源码里没有裸的 80.0 阈值字面量", "80.0" not in code)
    check("页面从 thresholds 里取市值阈值",
          re.search(r'th\.get\(\s*[\'"]mv_pct_max[\'"]', code) is not None)
    check("页面从 thresholds 里取热度阈值",
          re.search(r'th\.get\(\s*[\'"]amt_pct_min[\'"]', code) is not None)


# ============================ 3. 三种「没有」必须分开 ============================

def test_no_data_vs_no_hit() -> None:
    at = render({"status": "no_data", "reason": "无历史归档（数据尚未生成）"})
    tx = texts(at)
    check("no_data 渲染不抛异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("no_data 显示「数据缺失」", NO_DATA_TEXT in tx)
    check("no_data **不**显示「今日无命中」（两种「没有」不能混）",
          NO_HIT_TEXT not in tx)
    check("no_data 把原因原样带出来", "无历史归档" in tx)
    check("no_data 不渲染命中 KPI", HAS_DATA_OK not in metric_map(at))


def test_insufficient() -> None:
    at = render({"status": "insufficient",
                 "reason": "归档仅 8 个完整截面交易日，少于出结论所需 20 日",
                 "date": "20261009", "trade_days_available": 8})
    tx = texts(at)
    check("insufficient 渲染不抛异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("insufficient 显示「归档还在积累」", INSUFFICIENT_TEXT in tx)
    check("insufficient 带出实际交易日数（8）", "8 个完整截面交易日" in tx)
    check("insufficient 不显示「数据缺失」也不显示「今日无命中」",
          NO_DATA_TEXT not in tx and NO_HIT_TEXT not in tx)
    check("insufficient 不出结论（无命中 KPI）", HAS_DATA_OK not in metric_map(at))


def test_missing_product() -> None:
    at = render(None)
    tx = texts(at)
    check("产物不存在时不抛异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("产物不存在时提示等待跑批", "初始化中" in tx)
    check("产物不存在时不显示任何一种「没有命中」结论",
          NO_HIT_TEXT not in tx and NO_DATA_TEXT not in tx)


# ============================ 4. 新鲜度 ============================

def test_freshness() -> None:
    at_ok = render(_mk_sig(date="20261009"), reference_date="20261009")
    check("同批日期一致时不报过期",
          STALE_TEXT not in texts(at_ok) and not at_ok.error,
          str([e.value for e in at_ok.error])[:120])

    at_stale = render(_mk_sig(date="20260105"), reference_date="20261009")
    tx = texts(at_stale)
    check("本产物落后于同批其他产物时报过期", STALE_TEXT in tx)
    check("过期提示里带出实际数据日期（2026-01-05）", "2026-01-05" in tx)

    # 缺日期戳 → unknown，必须与 stale 分开（要修产物 / 要补跑是两件事）
    at_unk = render(_mk_sig(date=None), reference_date="20261009")
    tx_unk = texts(at_unk)
    check("产物缺日期戳时给 unknown 而不是 stale",
          "找不到日期戳" in tx_unk and STALE_TEXT not in tx_unk)


# ============================ 5. 缺值不补零 ============================

def test_missing_values_not_zeroed() -> None:
    inds = [dict(INDUSTRIES[0]), dict(INDUSTRIES[5])]
    inds[1]["mv_share"] = None
    inds[1]["amt_share"] = None
    inds[1]["pct_chg"] = None
    inds[1]["amount_yi"] = None
    at = render(_mk_sig(inds=inds, mv_max=99.0, amt_min=1.0))
    check("含缺失值的行业渲染不抛异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    csv = table_md(at)
    check("明细表渲染出来了（元断言，否则下面的格值检查没有对象）", csv is not None)
    if not csv:
        return
    df = pd.read_csv(io.StringIO(csv))
    row = df[df["行业"] == "己行业"]
    check("缺失值行业出现在表里（元断言）", len(row) == 1, f"{len(row)} 行")
    if len(row) != 1:
        return
    r = row.iloc[0]
    # **逐格**检查，而不是「整张表里出现过 —」：后者会被别的列（比如 _fmt_chg
    # 那条独立路径）留下来的 — 满足，于是「把缺失占比补成 0.00%」这种改动
    # 也照样全绿 —— 2026-10-12 造错反验第 27 项实测确实如此，是一条假断言。
    for col, why in (("市值占比", "占比缺失"), ("成交额占比", "占比缺失"),
                     ("当日涨跌", "涨跌缺失"), ("当日成交额", "成交额缺失")):
        check(f"缺失格显示 — 而不是 0（{why} → {col}）",
              str(r[col]) == "—", f"{col} = {r[col]!r}")
    check("缺失的占比没有被补成 0.00%（全表逐格核对）",
          not any(str(v).strip() in ("0.00%", "0.0000%")
                  for v in list(df["市值占比"]) + list(df["成交额占比"])),
          str(list(df["市值占比"])))
    check("有值的行没有被一起显示成 —（元断言：防止「全表都是 —」也算通过）",
          str(df[df["行业"] == "甲行业"].iloc[0]["市值占比"]) == "0.50%",
          str(df[df["行业"] == "甲行业"].iloc[0]["市值占比"]))


def test_missing_industries_field() -> None:
    sig = _mk_sig()
    del sig["industries"]
    at = render(sig)
    tx = texts(at)
    check("缺明细字段时不抛异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("缺明细字段时明确降级提示", NO_DETAIL_TEXT in tx)
    check("缺明细字段时命中名单仍然可用（部分可用要说清哪部分不可用）",
          "甲行业" in tx)


# ============================ 6. 走势选择器 ============================

def test_trend_selection() -> None:
    at = render(_mk_sig())
    if at.exception or not at.selectbox:
        check("走势选择器可重选", False, "选择器不存在或渲染异常")
        return
    at.selectbox[0].select("SW06.SI")
    at.run()
    check("把走势切到未命中行业后仍在同一页正常渲染", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    check("重选后选中值保持为 SW06.SI", at.selectbox[0].value == "SW06.SI",
          str(at.selectbox[0].value))


def test_stale_trend_selection_fallback() -> None:
    """昨天选的行业今天可能已不在名单里：必须回退，不能渲染空图或抛错。"""
    at = render(_mk_sig(), session_state={"isig_trend_code": "GONE.SI"})
    check("陈旧选择值不导致异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    if at.selectbox:
        check("陈旧选择值被回退到有效行业",
              at.selectbox[0].value in [i["code"] for i in INDUSTRIES],
              str(at.selectbox[0].value))
    else:
        check("陈旧选择值被回退到有效行业", False, "选择器不存在")


# ============================ 7. 门禁与合规文案 ============================

def test_subscribe_gate() -> None:
    tx_anon = texts(render(_mk_sig(), logged_in=False))
    check("未登录时提示登录后可订阅", "登录后可开启推送订阅" in tx_anon)

    tx_novip = texts(render(_mk_sig(), logged_in=True, vip=False))
    check("已登录非会员时给出开通引导", "VIP 权益" in tx_novip)
    check("非会员时出现开通按钮",
          any("查看开通方式" in str(b.label) for b in
              render(_mk_sig(), logged_in=True, vip=False).button))

    at_vip = render(_mk_sig(), logged_in=True, vip=True)
    tx_vip = texts(at_vip)
    check("会员时显示推送权益已生效", "推送权益已生效" in tx_vip)
    check("会员时带出剩余天数（30 天）", "30 天" in tx_vip)


def test_public_content_and_disclaimer() -> None:
    """内容开放（不是付费墙）：任何登录态下都必须渲染出命中结果。"""
    for logged_in in (False, True):
        at = render(_mk_sig(), logged_in=logged_in, vip=False)
        check(f"logged_in={logged_in} 时命中内容照常可见（内容不锁）",
              "甲行业" in texts(at) and metric_map(at).get("今日命中") == "3 个")

    at = render(_mk_sig())
    tx = texts(at)
    check("页面含「不构成投资建议」免责声明", "不构成投资建议" in tx)
    check("页面声明这是描述性筛选而非操作建议", "不是买卖建议" in tx or "描述性筛选" in tx)
    check("页面说明命中高亮与红涨绿跌无关（避免被读成涨跌）",
          "与红涨绿跌无关" in tx)

    # 违规文案分两类判，不能一把梭：
    #  · 「建议买入」这类是**无歧义的**正向荐股用语 → 出现即违规；
    #  · 「目标价 / 仓位指引 / 买卖点」在合规声明里本来就该出现，而且必须是
    #    **否定形态**（「不提供…目标价」）。第一版按裸词判，把页面里正确写的
    #    「不提供方向判断、目标价、买卖点或仓位指引」判成了违规——断言本身错，
    #    不是页面错。改成：出现这些词的行必须同时含「不」，才算否定表述。
    for banned in ("建议买入", "建议卖出", "建议加仓", "建议减仓",
                   "推荐买入", "可以买入", "可以卖出", "满仓", "梭哈"):
        check(f"页面不含正向荐股用语「{banned}」", banned not in tx)

    for word in ("目标价", "仓位指引", "买卖点"):
        bad_lines = [ln for ln in tx.splitlines() if word in ln and "不" not in ln]
        check(f"「{word}」只以否定形态出现（不提供/不含）", not bad_lines,
              str(bad_lines)[:160])



# ============================ 8. 静态约束与接线 ============================

def test_no_network_and_layer() -> None:
    import ast as _ast
    src = (ROOT / "page_industry_signal.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    mods: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    banned = {"requests", "urllib", "http", "httpx", "akshare", "tushare", "socket"}
    check("展示页不联网（无 requests/urllib/akshare/tushare）",
          not (mods & banned), str(sorted(mods & banned)))
    check("展示页不 import app.py（会与路由形成循环 import）", "app" not in mods)
    check("展示页 import 了计算层与新鲜度层",
          "industry_signal" in mods and "data_freshness" in mods)

    isig_src = (ROOT / "industry_signal.py").read_text(encoding="utf-8")
    isig_tree = _ast.parse(isig_src)
    isig_mods: set[str] = set()
    for node in _ast.walk(isig_tree):
        if isinstance(node, _ast.Import):
            isig_mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, _ast.ImportFrom) and node.module:
            isig_mods.add(node.module.split(".")[0])
    check("计算层仍不 import streamlit（页面复用不得把它拖进 UI 层）",
          "streamlit" not in isig_mods)
    check("计算层不联网", not (isig_mods & banned), str(sorted(isig_mods & banned)))


def test_widget_keys_prefixed() -> None:
    src = (ROOT / "page_industry_signal.py").read_text(encoding="utf-8")
    keys = re.findall(r'key\s*=\s*"([^"]+)"', src) + \
        re.findall(r'KEY\s*=\s*"([^"]+)"', src)
    check("页面上所有 widget key 都有本页前缀 isig_", bool(keys) and
          all(k.startswith("isig_") for k in keys), str(keys))
    check("同一页内 widget key 不重复", len(keys) == len(set(keys)), str(keys))


def test_app_wiring() -> None:
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    check("app.py 导入了行业信号页面",
          "from page_industry_signal import render_industry_signal_page" in src)
    check("app.py 导航菜单里有「行业信号」", "🏭 行业信号" in src)
    check("app.py 路由分支调用该页面",
          re.search(r'page\s*==\s*"🏭 行业信号".*?render_industry_signal_page\(',
                    src, re.S) is not None)
    check("app.py 把站内参考日期传进去（不在页面里重复实现新鲜度基准）",
          "render_industry_signal_page(site_reference_date())" in src)
    check("菜单项与路由分支一一对应（菜单里有、路由里也有）",
          src.count("🏭 行业信号") >= 2, str(src.count("🏭 行业信号")))
    # 使用指南必须同步：新功能上了却不写进说明文档，用户只能靠猜。
    check("使用指南里有一节讲行业信号",
          "🏭 行业信号 怎么读" in src)
    check("使用指南里的摘要段落顺序已更新（含行业信号）",
          re.search(r"明日验证条件\s*→\s*\*?\*?行业信号", src.replace("\n", " ")) is not None)


def table_at(at, i: int = 0):
    """第 i 个 dataframe 的 CSV 文本（Styler 与裸 DataFrame 都支持）。"""
    if len(at.dataframe) <= i:
        return None
    v = at.dataframe[i].value
    if hasattr(v, "data"):
        v = v.data
    try:
        return v.to_csv(index=False)
    except Exception:                                   # noqa: BLE001
        return str(v)


HISTFULL_KEY = "isig_hist_full_only"
HIST_TITLE = "历史命中回顾"


def test_history_hits_section() -> None:
    """历史回顾节：四种「没有」分得开、缺值不补 0、与当日结论互不牵连。"""
    # ---- 正常路径 ----
    at = render(_mk_sig(), hits=_mk_hits())
    check("历史回顾：正常路径无渲染异常", not at.exception,
          str(at.exception[0].message) if at.exception else "")
    tx = texts(at)
    check("历史回顾：渲染出小节标题", HIST_TITLE in tx)
    check("历史回顾：点明这是事后回算、不是回测",
          "不是策略回测" in tx, tx[:0])
    check("历史回顾：写明回算区间两端日期",
          "20250922" in tx and "20261009" in tx)
    check("历史回顾：写明满窗交易日数（2 条记录 + 2 天空命中 = 4 天）",
          "共 **4** 个交易日" in tx, [l for l in tx.splitlines() if "个交易日" in l][:1])
    check("历史回顾：如实说明被跳过的窗口不足天数（249 天）",
          "249" in tx and "未纳入回算" in tx)
    check("历史回顾：写明空命中天数也计入统计基数",
          "空命中" in tx and "有效样本" in tx)

    # ---- 统计表 ----
    st_csv = table_at(at, 1)
    check("历史回顾：统计表存在", bool(st_csv))
    if st_csv:
        check("历史回顾：统计表 4 行（T+1/3/5/10）",
              st_csv.count("\n") == 5, str(st_csv.count("\n")))
        check("历史回顾：统计表列齐全",
              all(c in st_csv.splitlines()[0] for c in
                  ("观察窗口", "样本数", "平均涨跌", "中位涨跌", "上涨占比",
                   "同期基准", "平均超额", "跑赢基准占比")), st_csv.splitlines()[0])
        line1 = next((l for l in st_csv.splitlines() if l.startswith("T+1,")), "")
        check("历史回顾：有样本的窗口显示具体数字（元断言，否则下面 — 断言无区分度）",
              "+1.00%" in line1 and "—" not in line1, line1)
        line10 = next((l for l in st_csv.splitlines() if l.startswith("T+10")), "")
        check("历史回顾：0 样本的窗口数值列显示 —（不是 0、不是 nan）",
              line10.count("—") >= 6, line10)
        check("历史回顾：统计表不出现 NaN/nan 字样",
              "nan" not in st_csv.lower(), st_csv.replace("\n", " | ")[:120])
        check("历史回顾：样本数为 0 时仍如实显示 0（不是 —）",
              line10.split(",")[1] == "0", line10)

    # ---- 命中明细表 ----
    h_csv = table_at(at, 2)
    check("历史回顾：命中明细表存在", bool(h_csv))
    if h_csv:
        head = h_csv.splitlines()[0]
        check("历史回顾：明细表列齐全",
              all(c in head for c in ("日期", "行业", "一级行业", "市值分位",
                                      "热度分位", "当日涨跌", "T+1", "T+3", "T+5",
                                      "T+10", "超额(T+5)")), head)
        check("历史回顾：明细表行数 = 记录数（2 条）",
              h_csv.count("\n") == 3, str(h_csv.count("\n")))
        lines = h_csv.strip().splitlines()
        check("历史回顾：明细表按日期倒序（最新的在最上面）",
              lines[1].startswith("20261009") and lines[2].startswith("20261008"),
              lines[1][:44])
        cols10 = lines[1].split(",")
        check("历史回顾：明细表的 T+10 列显示 —（未到期，不是 0）",
              len(cols10) > 9 and cols10[9] == "—", str(cols10[6:11]))
        check("历史回顾：明细表不把缺值写成 0.00%",
              "0.00%" not in h_csv, h_csv.replace("\n", " | ")[:140])

    # ---- 「只看完整样本」勾选 ----
    at2 = render(_mk_sig(), hits=_mk_hits(),
                 session_state={HISTFULL_KEY: True})
    tx2 = texts(at2)
    check("历史回顾：勾选后若无样本，如实说「还没产生」而不是「没命中」",
          "这不是「没命中」" in tx2, [l for l in tx2.splitlines() if "没有" in l][:2])
    recs = [_mk_hit_record("20261001", "SW03.SI", "丙行业",
                           fwd={"1": 1.0, "3": 2.0, "5": 3.0, "10": 4.0},
                           excess={"1": 0.5, "3": 1.0, "5": 1.5, "10": 2.0}),
            _mk_hit_record("20261009", "SW02.SI", "乙行业")]
    at3 = render(_mk_sig(), hits=_mk_hits(records=recs),
                 session_state={HISTFULL_KEY: True})
    h3 = table_at(at3, 2)
    check("历史回顾：勾选完整样本后只剩 T+10 已到期的记录",
          h3 is not None and "20261001" in h3 and "20261009" not in h3,
          (h3 or "").replace("\n", " | ")[:120])
    at3b = render(_mk_sig(), hits=_mk_hits(records=recs))
    check("历史回顾：不勾选时全部记录都在（元断言：勾选确实起了作用）",
          (table_at(at3b, 2) or "").count("20261009") == 1)

    # ---- 0 命中：有效结论，不是数据缺失 ----
    at4 = render(_mk_sig(), hits=_mk_hits(records=[]))
    tx4 = texts(at4)
    check("历史回顾：0 命中时如实播报「没有任何行业同时满足两条件」",
          "没有任何行业同时满足两个条件" in tx4)
    check("历史回顾：0 命中时强调这是有效结论（不是数据缺失）",
          "有效结论" in tx4 and "不是数据缺失" in tx4)
    check("历史回顾：0 命中时不渲染统计表与明细表（没样本可统计）",
          len(at4.dataframe) == 1, str(len(at4.dataframe)))

    # ---- 样本不足 ----
    at5 = render(_mk_sig(), hits={
        "status": "insufficient",
        "reason": "分位窗口满额的交易日仅 11 天（需 250 日市值占比窗口 + 20 日热度窗口），"
                  "少于出统计所需 20 天",
        "trade_days_total": 260, "trade_days_scanned": 11})
    tx5 = texts(at5)
    check("历史回顾：样本不足时明说「还在积累」并给出已积累天数",
          "还在积累" in tx5 and "260" in tx5 and "11" in tx5)
    check("历史回顾：样本不足时不渲染统计表",
          len(at5.dataframe) == 1, str(len(at5.dataframe)))
    check("历史回顾：样本不足 ≠ 数据不可用（不显示「暂不可用」）",
          "历史回顾暂不可用" not in tx5)

    # ---- 数据不可用 ----
    at6 = render(_mk_sig(), hits={
        "status": "no_data", "reason": "归档缺收盘价（close），无法计算命中之后的涨跌"})
    tx6 = texts(at6)
    check("历史回顾：数据不可用时显示「暂不可用」并给出原因",
          "历史回顾暂不可用" in tx6 and "收盘价" in tx6)
    check("历史回顾：数据不可用 ≠ 无命中（不显示 0 命中文案）",
          "没有任何行业同时满足" not in tx6)

    # ---- 产物不存在 ----
    at7 = render(_mk_sig())
    tx7 = texts(at7)
    check("历史回顾：产物不存在时显示「初始化中」，不冒充「无命中」",
          "初始化中" in tx7 and "没有任何行业同时满足" not in tx7)

    # ---- 与当日结论互不牵连 ----
    at8 = render(_mk_sig(inds=[], industries=[]), hits=_mk_hits())
    tx8 = texts(at8)
    check("历史回顾：当日明细缺失时历史回顾照常渲染（两块互不牵连）",
          "结论里没有行业明细" in tx8 and HIST_TITLE in tx8,
          [l for l in tx8.splitlines() if HIST_TITLE in l][:1])

    # ---- 合规文案 ----
    check("历史回顾：含「不构成投资建议」", "不构成投资建议" in tx)
    check("历史回顾：声明历史统计不代表未来收益", "不代表未来收益" in tx)
    for banned in ("建议买入", "建议卖出", "推荐买入", "可以买入"):
        check(f"历史回顾：不含正向荐股用语「{banned}」", banned not in tx)
    for word in ("目标价", "仓位指引"):
        bad = [ln for ln in tx.splitlines() if word in ln and "不" not in ln]
        check(f"历史回顾：「{word}」只以否定形态出现", not bad, str(bad)[:140])


def test_history_hits_page_wiring() -> None:
    """页面接线：产物路径、小节调用点、widget key 前缀。"""
    src = (ROOT / "page_industry_signal.py").read_text(encoding="utf-8")
    check("页面：定义了 history_hits 产物路径",
          re.search(r'HITS_PATH\s*=\s*os\.path\.join\([^)]*"history_hits\.json"', src)
          is not None, "")
    check("页面：读取产物走 load_history_hits 且带缓存",
          "def load_history_hits" in src and "@st.cache_data" in src)
    check("页面：主渲染函数调用了历史回顾节", "render_history_hits()" in src)
    # 关键结构：历史节必须在 frame 判空分支**之外**，否则当日明细缺失会把历史一起吞掉。
    # 判据取「调用行的行首缩进」（主函数体 4 空格 vs 分支内 8 空格）。正则必须带
    # `^[ \t]+`：函数定义行 `def render_history_hits()` 里也有这个名字，
    # 直接搜名字会匹配到它，取到的是 'def ' 这种莫名其妙的「缩进」。
    m = re.search(r"^([ \t]+)render_history_hits\(\)", src, re.M)
    check("页面：历史节渲染调用与 if 同级（缩进 4 空格，不在 frame 判空分支里）",
          m is not None and m.group(1) == "    ",
          repr(m.group(1)) if m else "未找到缩进调用")
    check("页面：历史节的 widget key 有本页前缀",
          "HISTFULL_KEY" in src and 'HISTFULL_KEY = "isig_' in src)


def test_no_duplicate_key_across_pages() -> None:
    """与板块轮动页、收盘摘要页同进程渲染：key 没隔离会抛 StreamlitDuplicateElementKey。"""
    at = render(_mk_sig(), script=BOTH, timeout=300)
    if at.exception:
        msg = str(at.exception[0].message or "")
        if "DuplicateElementKey" in msg or "duplicate" in msg.lower():
            check("三页同进程渲染不撞 widget key", False, msg[:200])
        else:
            check("三页同进程渲染不撞 widget key", False,
                  f"渲染抛了别的异常: {msg[:200]}")
        return
    check("三页同进程渲染不撞 widget key", True)
    tx = texts(at)
    # 元断言：确认另外两页也真的渲染了，否则本用例只跑了半页、没有区分度
    check("三页同渲染用例确实渲染了三页（元断言）",
          "行业信号" in tx and "板块轮动" in tx and ("收盘摘要" in tx or "摘要" in tx),
          tx[:160])


def main() -> int:
    for fn in (test_normal_render, test_more_hits_than_picks,
               test_thresholds_from_json, test_no_hardcoded_thresholds_in_source,
               test_no_data_vs_no_hit, test_insufficient, test_missing_product,
               test_freshness, test_missing_values_not_zeroed,
               test_missing_industries_field, test_trend_selection,
               test_stale_trend_selection_fallback, test_subscribe_gate,
               test_public_content_and_disclaimer, test_no_network_and_layer,
               test_widget_keys_prefixed, test_app_wiring,
               test_history_hits_section, test_history_hits_page_wiring,
               test_no_duplicate_key_across_pages):
        print(f"\n=== {fn.__name__} ===")
        try:
            fn()
        except Exception as exc:                        # noqa: BLE001
            check(f"{fn.__name__} 执行时抛异常", False,
                  f"{type(exc).__name__}: {exc}")

    total = len(FAIL)
    print(f"\n{'=' * 50}\n共 {'通过' if not total else '失败'}：{total} 项失败")
    if total:
        for f in FAIL:
            print(f"  ❌ {f}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
