# -*- coding: utf-8 -*-
"""板块轮动跑批「Tushare 兜底通道」自检。

为什么要单独一个探针：兜底改的是**失败语义**——原来「clist 取不到就拒收/退出」，
现在多了「换一条管子再试」。这里最容易出的错不是崩溃，而是：
  · 兜底失败却当成成功（把半份/空数据写进归档）
  · 兜底把周末/停滞日也写进去（数据语义错了，通道好坏无关）
  · 兜底成功但 data_channel 仍写 clist（事后排查失去唯一线索）
  · 兜底该触发时没触发（等于没修）

每个用例都在 .probe_tmp/_fb_probe 下用独立目录跑，绝不碰真实归档。
"""
from __future__ import annotations

import csv
import datetime
import json
import pathlib
import shutil
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

import pandas as pd  # noqa: E402

import daily_sector_rotation as dsr  # noqa: E402
import sector_dc_source as dc  # noqa: E402
import sector_rotation as sr  # noqa: E402

# run_case 会把 dsr 上的 fetch_snapshot / fetch_tushare_snapshot / 时间函数打桩，
# 打桩是**永久**改模块属性。单元测试必须用真身，所以在模块加载时先留一份原件。
_ORIG = {
    "fetch_snapshot": dsr.fetch_snapshot,
    "fetch_tushare_snapshot": dsr.fetch_tushare_snapshot,
    "probe_fallback_channel": dsr.probe_fallback_channel,
    "_snapshot_trade_date": dsr._snapshot_trade_date,
    "_cst_now": dsr._cst_now,
    "OUT_DIR": dsr.OUT_DIR,
    "HISTORY_PATH": dsr.HISTORY_PATH,
    "ANALYSIS_PATH": dsr.ANALYSIS_PATH,
}

BASE = ROOT / ".probe_tmp" / "_fb_probe"
TODAY = "2026-10-20"
PREV = "2026-10-19"

_fails: list[str] = []
_n = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _n
    _n += 1
    if cond:
        print(f"✅ {name}" + (f" — {detail}" if detail else ""))
    else:
        print(f"❌ {name}" + (f" — {detail}" if detail else ""))
        _fails.append(name)


def make_rows(n: int, pct: float = 2.0) -> list[dict]:
    """clist 原始行（字段名是东财的 f12/f14/...）。"""
    return [{"f12": f"BK{i:04d}", "f14": f"板块{i:02d}", "f2": 1000.0 + i,
             "f3": pct, "f109": 1.0, "f160": 2.0, "f110": 3.0, "f24": 4.0,
             "f6": 1e9, "f8": 1.0, "f104": 10, "f105": 10} for i in range(n)]


def make_df(day: str, n: int, pct: float = 3.0) -> pd.DataFrame:
    """兜底通道产出的归档同形 DataFrame（ARCHIVE_COLS）。"""
    rows = [{"date": day, "code": f"BK{i:04d}", "name": f"板块{i:02d}",
             "close": 1000.0 + i, "pct_chg": pct, "pct_5d": 1.0, "pct_10d": 2.0,
             "pct_20d": 3.0, "pct_60d": 4.0, "turnover": 1.0, "up_count": 10.0,
             "down_count": 10.0, "amount_yi": 50.0} for i in range(n)]
    return pd.DataFrame(rows)[list(dc.ARCHIVE_COLS)]


def seed_history(path: pathlib.Path, days: list[str], n: int = 12) -> None:
    rows = []
    for d in days:
        for i in range(n):
            rows.append({"date": d, "code": f"BK{i:04d}", "name": f"板块{i:02d}",
                         "close": 1000.0 + i, "pct_chg": 1.0, "pct_5d": 1.0,
                         "pct_10d": 2.0, "pct_20d": 3.0, "pct_60d": 4.0,
                         "turnover": 1.0, "up_count": 10.0, "down_count": 10.0,
                         "amount_yi": 50.0})
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8")


CTX: dict = {}


def run_case(name: str, *, clist: list[dict] | None, total: int | None,
             failed: list[int] | None, fb: tuple, now_weekday: int = 1,
             seed_days: list[str] | None = None, seed_today_rows: int = 0,
             date_label: str = TODAY,
             probe: dict | None = None) -> tuple[int, dict, str]:
    """跑一次 main()，返回 (退出码, 运行后归档 dict, stdout 文本)。"""
    d = BASE / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    hist_path = d / "history.csv"
    ana_path = d / "analysis.json"
    seed_history(hist_path, seed_days if seed_days is not None else [PREV])
    if seed_today_rows:
        # 制造「上一次跑批留下的半份残留」，验证残缺拒收时会清掉
        seed_history(d / "_tmp.csv", [date_label], n=seed_today_rows)
        with open(hist_path, "a", encoding="utf-8", newline="") as f:
            with open(d / "_tmp.csv", encoding="utf-8") as g:
                next(g)
                for line in g:
                    f.write(line)

    dsr.OUT_DIR = str(d)
    dsr.HISTORY_PATH = str(hist_path)
    dsr.ANALYSIS_PATH = str(ana_path)
    dsr._snapshot_trade_date = lambda: date_label
    dsr._cst_now = lambda: datetime.datetime(2026, 10, 20 + (now_weekday - 1), 19, 40) \
        if now_weekday < 5 else datetime.datetime(2026, 10, 24, 19, 40)
    dsr.fetch_snapshot = lambda *a, **k: (clist or [], total, failed or [])

    CTX["fb_calls"] = 0

    def _fb(day: str):
        CTX["fb_calls"] += 1
        fb_df, fb_info = fb
        return (None if fb_df is None else fb_df.copy()), dict(fb_info)

    dsr.fetch_tushare_snapshot = _fb

    # 探活：默认成功，用例可指定失败，用来验证「探活失败不影响主流程」
    CTX["probe_calls"] = 0
    probe_result = probe or {"ok": True, "checked_date": date_label, "is_trade_day": True}

    def _probe(day: str):
        CTX["probe_calls"] += 1
        return dict(probe_result)

    dsr.probe_fallback_channel = _probe

    code = 0
    try:
        code = dsr.main()
    except SystemExit as e:
        code = int(e.code or 0)

    rows = []
    if hist_path.exists():
        with open(hist_path, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    ana = json.loads(ana_path.read_text(encoding="utf-8")) if ana_path.exists() else {}
    return code, {"rows": rows}, ana


ok_fb = (make_df(TODAY, 506), {"window_missing": {"pct_60d": 3}, "anchor": PREV})


# ============================ 1. clist 完整：不许走兜底 ============================
def test_clist_complete() -> None:
    code, arch, ana = run_case("clist_complete", clist=make_rows(506), total=506,
                               failed=[], fb=ok_fb)
    days = sorted({r["date"] for r in arch["rows"]})
    check("clist 完整：退出码 0", code == 0, f"code={code}")
    check("clist 完整：**未**触发兜底通道", CTX["fb_calls"] == 0, f"calls={CTX['fb_calls']}")
    check("clist 完整：当日已归档", TODAY in days, f"days={days[-3:]}")
    check("clist 完整：data_channel=clist", ana.get("data_channel") == "clist",
          str(ana.get("data_channel")))
    check("clist 完整：不写 channel_note", "channel_note" not in ana)
    check("clist 完整：analysis.date 是当日", ana.get("date") == TODAY, str(ana.get("date")))
    check("clist 完整：探活记录落了盘", ana.get("fallback_probe", {}).get("ok") is True,
          str(ana.get("fallback_probe")))
    check("clist 完整：通道探活被调用过一次", CTX["probe_calls"] == 1,
          f"calls={CTX['probe_calls']}")


# ============================ 1b. 探活失败不影响主流程 ============================
def test_probe_failure_is_harmless() -> None:
    code, arch, ana = run_case(
        "probe_fail", clist=make_rows(506), total=506, failed=[], fb=ok_fb,
        probe={"ok": False, "error": "DataSourceError: 上游返回 code=40203 权限不足"})
    days = sorted({r["date"] for r in arch["rows"]})
    check("探活失败：退出码仍为 0（探活不许拖垮主流程）", code == 0, f"code={code}")
    check("探活失败：当日照常归档", TODAY in days, f"days={days[-2:]}")
    check("探活失败：analysis 里如实记下失败原因",
          ana.get("fallback_probe", {}).get("ok") is False
          and "40203" in str(ana["fallback_probe"].get("error")),
          str(ana.get("fallback_probe"))[:70])
    check("探活失败：data_channel 不受影响", ana.get("data_channel") == "clist",
          str(ana.get("data_channel")))


# ============================ 2. clist 完全失败：兜底接住 ============================
def test_clist_empty_fallback_ok() -> None:
    code, arch, ana = run_case("clist_empty_fb_ok", clist=[], total=None,
                               failed=[1], fb=ok_fb)
    days = sorted({r["date"] for r in arch["rows"]})
    n_today = sum(1 for r in arch["rows"] if r["date"] == TODAY)
    check("clist 全败：退出码 0（兜底接住）", code == 0, f"code={code}")
    check("clist 全败：兜底被调用 1 次", CTX["fb_calls"] == 1, f"calls={CTX['fb_calls']}")
    check("clist 全败：当日按兜底数据归档", n_today == 506, f"today_rows={n_today}")
    check("clist 全败：data_channel=tushare_dc", ana.get("data_channel") == "tushare_dc",
          str(ana.get("data_channel")))
    check("clist 全败：channel_note 写明换了通道",
          isinstance(ana.get("channel_note"), str) and "Tushare" in ana["channel_note"],
          str(ana.get("channel_note"))[:60])
    check("clist 全败：前一交易日数据未被破坏", PREV in days)


# ============================ 3. clist 残缺：兜底顶上去（不是拒收）============================
def test_clist_partial_fallback_ok() -> None:
    code, arch, ana = run_case("clist_partial_fb_ok", clist=make_rows(300), total=506,
                               failed=[4, 5], fb=ok_fb)
    n_today = sum(1 for r in arch["rows"] if r["date"] == TODAY)
    check("clist 残缺：退出码 0（旧逻辑这里是 2）", code == 0, f"code={code}")
    check("clist 残缺：兜底被调用", CTX["fb_calls"] == 1, f"calls={CTX['fb_calls']}")
    check("clist 残缺：当日归档的是兜底全量 506 行，不是 300 行残片", n_today == 506,
          f"today_rows={n_today}")
    check("clist 残缺：data_channel=tushare_dc", ana.get("data_channel") == "tushare_dc")


# ============================ 4. 两条通道都不可用：仍拒收 + 清残留 ============================
def test_partial_and_fallback_fail() -> None:
    code, arch, ana = run_case("clist_partial_fb_fail", clist=make_rows(300), total=506,
                               failed=[4, 5], fb=(None, {"error": "上游 400"}),
                               seed_today_rows=5)
    n_today = sum(1 for r in arch["rows"] if r["date"] == TODAY)
    days = sorted({r["date"] for r in arch["rows"]})
    check("两通道皆败：退出码 2（incomplete）", code == 2, f"code={code}")
    check("两通道皆败：当日**不**留在归档里", n_today == 0, f"today_rows={n_today}")
    check("两通道皆败：该日残留被清掉（元断言，证明第 3 条不是空转）", TODAY not in days,
          f"days={days}")
    check("两通道皆败：data_channel 仍是 clist（兜底没成功就不许冒充）",
          ana.get("data_channel") == "clist", str(ana.get("data_channel")))
    check("两通道皆败：不留 channel_note", "channel_note" not in ana)
    check("两通道皆败：分析仍照常落盘（否则前端会瞎）", ana.get("status") == "ok",
          str(ana.get("status")))


# ============================ 5. clist 全败且兜底也败：退出码 3 ============================
def test_both_fail_exit3() -> None:
    code, arch, ana = run_case("both_fail", clist=[], total=None, failed=[1],
                               fb=(None, {"error": "无 TUSHARE_TOKEN"}))
    days = sorted({r["date"] for r in arch["rows"]})
    check("clist 全败且兜底败：退出码 3（failed），不是 0", code == 3, f"code={code}")
    check("clist 全败且兜底败：归档未被动过", TODAY not in days, f"days={days}")
    check("clist 全败且兜底败：**没有**写出 analysis（fail() 在分析之前）",
          ana == {}, f"keys={list(ana)[:5]}")


# ============================ 6. 周末：不许兜底落盘 ============================
def test_weekend_no_fallback() -> None:
    code, arch, ana = run_case("weekend_partial", clist=make_rows(300), total=506,
                               failed=[], fb=ok_fb, now_weekday=6, seed_days=[PREV])
    days = sorted({r["date"] for r in arch["rows"]})
    check("周末(clist 残缺)：**未**触发兜底（数据语义决定不落盘，与通道无关）",
          CTX["fb_calls"] == 0, f"calls={CTX['fb_calls']}")
    check("周末(clist 残缺)：当日没有新增归档", TODAY not in days, f"days={days}")
    # 周末不落盘时仍报 incomplete（退出码 2）是**既有**语义：判定只看当日快照
    # 覆盖度，不看是否落盘。这里钉住它是为了「兜底改造没有偷偷改掉旧行为」，
    # 不是为了把它当成期望行为。
    check("周末(clist 残缺)：退出码沿用既有的 incomplete=2（不是 3，也没变成 0）",
          code == 2, f"code={code}")
    check("周末：analysis.note 说明未落盘",
          isinstance(ana.get("note"), str) and "非交易日" in ana["note"],
          str(ana.get("note"))[:40])

    code2, arch2, ana2 = run_case("weekend_full", clist=make_rows(506), total=506,
                                  failed=[], fb=ok_fb, now_weekday=6, seed_days=[PREV])
    days2 = sorted({r["date"] for r in arch2["rows"]})
    check("周末(clist 完整)：退出码 0", code2 == 0, f"code={code2}")
    check("周末(clist 完整)：**未**触发兜底", CTX["fb_calls"] == 0,
          f"calls={CTX['fb_calls']}")
    check("周末(clist 完整)：当日没有新增归档", TODAY not in days2, f"days={days2}")
    check("周末(clist 完整)：data_channel 仍是 clist（兜底没参与）",
          ana2.get("data_channel") == "clist", str(ana2.get("data_channel")))


# ============================ 7. 兜底函数本身的边界 ============================
def test_fallback_fn_units() -> None:
    # 前面的 run_case 把 fetch_tushare_snapshot 换成了打桩版本，且是永久改属性。
    # 这里要测真身，必须先换回来——否则整个用例组都在测 mock，等于没测。
    dsr.fetch_tushare_snapshot = _ORIG["fetch_tushare_snapshot"]

    d = BASE / "_units"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    hist = d / "history.csv"
    dsr.HISTORY_PATH = str(hist)

    # 7.1 归档不存在 → 锚点为 None，不崩
    anchor_date, anchor_close = dsr._archive_anchor()
    check("锚点：归档不存在时返回 (None, {})", anchor_date is None and anchor_close == {},
          f"{anchor_date}")

    seed_history(hist, ["2026-10-16", PREV], n=3)
    anchor_date, anchor_close = dsr._archive_anchor()
    check("锚点：取归档最后一个交易日", anchor_date == PREV, str(anchor_date))
    check("锚点：给出该日每个板块的 close", len(anchor_close) == 3, str(len(anchor_close)))

    # 7.2 模块导入失败 → 返回 None（不是抛异常穿透到 main）
    real_mod = sys.modules.get("sector_dc_source")
    sys.modules["sector_dc_source"] = None       # 触发 ImportError
    try:
        df, info = dsr.fetch_tushare_snapshot(TODAY)
        check("兜底：模块不可用时返回 (None, error) 不抛", df is None and "error" in info,
              str(info.get("error"))[:50])
    finally:
        sys.modules["sector_dc_source"] = real_mod

    # 7.3 取数抛异常 → 返回 None
    fake = types.SimpleNamespace()
    fake.fetch_snapshot = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模拟上游超时"))
    sys.modules["sector_dc_source"] = fake
    try:
        df, info = dsr.fetch_tushare_snapshot(TODAY)
        check("兜底：取数抛异常时返回 (None, error) 不抛",
              df is None and "模拟上游超时" in info.get("error", ""),
              str(info.get("error"))[:60])
    finally:
        sys.modules["sector_dc_source"] = real_mod

    # 7.4 行数不足 MIN_COMPLETE → 视为不可用（绝不拿半份冒充）
    fake2 = types.SimpleNamespace()
    fake2.ARCHIVE_COLS = dc.ARCHIVE_COLS
    fake2.fetch_snapshot = lambda *a, **k: (make_df(TODAY, dsr.MIN_COMPLETE - 1),
                                            {"window_missing": {}})
    sys.modules["sector_dc_source"] = fake2
    try:
        df, info = dsr.fetch_tushare_snapshot(TODAY)
        check(f"兜底：行数 < MIN_COMPLETE({dsr.MIN_COMPLETE}) 视为不可用",
              df is None and "行数不足" in info.get("error", ""),
              str(info.get("error"))[:40])
    finally:
        sys.modules["sector_dc_source"] = real_mod

    # 7.5 行数够 → 放行
    fake3 = types.SimpleNamespace()
    fake3.ARCHIVE_COLS = dc.ARCHIVE_COLS
    fake3.fetch_snapshot = lambda *a, **k: (make_df(TODAY, dsr.MIN_COMPLETE + 50),
                                            {"window_missing": {}})
    sys.modules["sector_dc_source"] = fake3
    try:
        df, info = dsr.fetch_tushare_snapshot(TODAY)
        check("兜底：行数达标时放行并带出锚点",
              df is not None and len(df) == dsr.MIN_COMPLETE + 50 and "anchor" in info,
              f"rows={0 if df is None else len(df)}")
    finally:
        sys.modules["sector_dc_source"] = real_mod


# ============================ 7b. 探活函数本身的边界 ============================
def test_probe_fn_units() -> None:
    import os
    dsr.probe_fallback_channel = _ORIG["probe_fallback_channel"]

    old_tok = os.environ.pop("TUSHARE_TOKEN", None)
    try:
        r = dsr.probe_fallback_channel(TODAY)
        check("探活：没有 TUSHARE_TOKEN 时 ok=False 且说清缺什么",
              r["ok"] is False and "TUSHARE_TOKEN" in r["error"], str(r)[:70])
    finally:
        if old_tok is not None:
            os.environ["TUSHARE_TOKEN"] = old_tok

    os.environ["TUSHARE_TOKEN"] = "dummy-for-probe"
    real = sys.modules.get("sector_dc_source")
    try:
        fake = types.SimpleNamespace()
        fake.trade_cal = lambda a, b, **k: [TODAY]
        sys.modules["sector_dc_source"] = fake
        r = dsr.probe_fallback_channel(TODAY)
        check("探活：接口可用时 ok=True 并标出是交易日",
              r["ok"] is True and r["is_trade_day"] is True, str(r))

        fake.trade_cal = lambda a, b, **k: []
        r = dsr.probe_fallback_channel(TODAY)
        check("探活：非交易日 is_trade_day=False，但不是失败",
              r["ok"] is True and r["is_trade_day"] is False, str(r))

        def _boom(*a, **k):
            raise RuntimeError("上游返回 code=40203 权限不足")
        fake.trade_cal = _boom
        r = dsr.probe_fallback_channel(TODAY)
        check("探活：调用失败时 ok=False 并带出上游原话",
              r["ok"] is False and "40203" in r.get("error", ""), str(r)[:80])
    finally:
        if real is not None:
            sys.modules["sector_dc_source"] = real
        os.environ.pop("TUSHARE_TOKEN", None)


def test_wiring() -> None:
    src = (ROOT / "daily_sector_rotation.py").read_text(encoding="utf-8")
    import re
    check("接线：兜底只在「该落盘且 clist 不足」时触发",
          re.search(r"if\s+not\s+skipped\s+and\s+incomplete\s*:", src) is not None)
    check("接线：兜底成功才切换 df_today（不是无条件覆盖）",
          re.search(r"if\s+fb_df\s+is\s+not\s+None\s*:\s*\n\s*df_today\s*=\s*fb_df", src)
          is not None)
    check("接线：data_channel 写进 analysis",
          re.search(r'analysis\["data_channel"\]\s*=\s*channel', src) is not None)
    check("接线：兜底失败时 error 进 fail() 信息（不留白）",
          re.search(r"兜底=\{fb_info\.get\('error'\)", src) is not None)
    check("接线：两条通道都拿不到才 fail()",
          re.search(r"if\s+df_today\.empty\s+or\s+len\(df_today\)\s*<\s*MIN_VALID", src)
          is not None)


def main() -> int:
    if BASE.exists():
        shutil.rmtree(BASE)
    BASE.mkdir(parents=True)

    for fn in (test_clist_complete, test_probe_failure_is_harmless,
               test_clist_empty_fallback_ok,
               test_clist_partial_fallback_ok, test_partial_and_fallback_fail,
               test_both_fail_exit3, test_weekend_no_fallback,
               test_fallback_fn_units, test_probe_fn_units, test_wiring):
        print(f"\n=== {fn.__name__} ===")
        fn()

    print("\n" + "=" * 60)
    print(f"共 {_n} 项，失败 {len(_fails)} 项")
    for f in _fails:
        print(f"  ❌ {f}")
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
