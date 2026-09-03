# -*- coding: utf-8 -*-
"""RPS 跑批链路自检（离线：stub 掉 tushare/akshare，不联网、不需要 token）。

本探针为 2026-09-01 的真实事故而写。那天 RPS 榜单一次都没更新成功，
四班跑批死于两个根因，并被三层机制同时掩盖：

  · 根因 1  目标交易日直接取交易日历首位。跑批被平台延迟到过零点时，日历给出的
            「最新交易日」就是当天，而当天距开盘还有几小时，pro.daily 必然为空。
            run 339 / 340 / 342 三班栽在这里。
  · 根因 2  细分题材（装饰性字段）排在榜单写盘之前。run 341 榜单已算完、只差题材，
            题材接口挂住 → 整步被 1800s 超时杀掉 → 磁盘上一个字都没留下。
  · 掩盖 1  main_job 无论成败都 return None → 退出码 0 → 跑批汇总显示 [OK]。
  · 掩盖 2  看门狗只探连板天梯与收盘摘要，两者当天都是新数据 → 判「已就位」。
  · 掩盖 3  前端零日期提示，过期一天的榜单与正常榜单外观完全一致。

六组断言分别对应上述五条，每组都配「造错反向验证」——
一条永不失败的断言等于没有断言。

第 7、8 组是横向排查的收获：`daily_etf_pro.py` 有**完全相同**的日期锚定坑位
（只是运气好还没暴露），外加一个独立老 bug —— 更新日期写系统日期而非交易日，
08-30 00:25 那班已把「2026-08-29（周六）」这个不存在的交易日写进 strong_etfs.csv。
第 8 组盯编排器：原有的新鲜度自检当天确实把 RPS 的 08-31 打印出来了，
但要人逐行用眼睛比日期，汇总里没有任何标记——判据必须由机器给结论。

用法:
  /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_rps_pipeline.py

退出码 0=全绿；1=有断言失败；3=存在假断言（造错后断言仍全绿）。
"""
from __future__ import annotations

import ast
import contextlib
import datetime as dt
import io
import os
import shutil
import sys
import tempfile
import time
import types

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

FAIL: list[str] = []
FAKE: list[str] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def rev(name: str, caught: bool, detail: str = "") -> None:
    """造错反向验证：caught=True 表示造错后对应断言确实炸了。"""
    tag = "✅ [反向]" if caught else "⚠️ [反向] 假断言"
    print(f"{tag} {name}" + (f" — {detail}" if detail else ""))
    if not caught:
        FAKE.append(name)


@contextlib.contextmanager
def capture():
    """把 ck 的失败收集重定向到临时列表，并吞掉输出。造错验证专用。"""
    global FAIL
    saved, FAIL = FAIL, []
    box = FAIL
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield box
    finally:
        FAIL = saved


# ================= 假 tushare / akshare =================

def _weekdays(end: str, count: int) -> list[str]:
    """生成降序的「交易日」列表（只排除周末，够本探针用）。"""
    d = dt.datetime.strptime(end, "%Y%m%d").date()
    out = []
    while len(out) < count:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= dt.timedelta(days=1)
    return out


N_STOCK = 300


class FakePro:
    """只实现 daily_rps_pro 真正用到的四个接口。

    published 决定「某个交易日的行情是否已发布」——这正是 2026-09-01 事故的
    核心变量：日历说今天开市，接口却还没有今天的数据。
    """

    def __init__(self, cal: list[str], published: set[str]):
        self.cal = cal
        self.published = published
        self.daily_calls: list[str] = []

    def trade_cal(self, exchange="", is_open="1", end_date=None, start_date=None):
        return pd.DataFrame({"cal_date": self.cal, "is_open": 1})

    def daily(self, trade_date=None, fields=None):
        self.daily_calls.append(trade_date)
        if trade_date not in self.published:
            return pd.DataFrame(columns=["ts_code", "close"])
        # 锚定日（已发布日期里最新的那个）给每只股票不同的价格，过去日期一律 10.0，
        # 这样 RPS 排名严格随序号递增，筛选结果可预测。
        if trade_date == max(self.published):
            base = [10.0 + i * 0.01 for i in range(N_STOCK)]
        else:
            base = [10.0] * N_STOCK
        return pd.DataFrame({"ts_code": _codes(), "close": base})

    def adj_factor(self, trade_date=None, fields=None):
        if trade_date not in self.published:
            return pd.DataFrame(columns=["ts_code", "adj_factor"])
        return pd.DataFrame({"ts_code": _codes(), "adj_factor": [1.0] * N_STOCK})

    def daily_basic(self, trade_date=None, fields=None):
        if trade_date not in self.published:
            return pd.DataFrame(columns=["ts_code"])
        return pd.DataFrame({
            "ts_code": _codes(),
            "turnover_rate": [3.0] * N_STOCK,
            "pe_ttm": [25.0] * N_STOCK,
            "pb": [3.0] * N_STOCK,
            "circ_mv": [100000.0] * N_STOCK,
        })

    def stock_basic(self, exchange="", list_status="L", fields=None):
        return pd.DataFrame({
            "ts_code": _codes(),
            "name": [f"股票{i:03d}" for i in range(N_STOCK)],
            "industry": [f"粗行业{i % 5}" for i in range(N_STOCK)],
        })

    # ---- ETF 侧（daily_etf_pro 用到的两个接口）----
    def fund_daily(self, trade_date=None, fields=None):
        self.daily_calls.append(trade_date)
        if trade_date not in self.published:
            return pd.DataFrame(columns=["ts_code", "close"])
        if trade_date == max(self.published):
            close = [1.0 + i * 0.001 for i in range(N_STOCK)]
        else:
            close = [1.0] * N_STOCK
        return pd.DataFrame({"ts_code": _etf_codes(), "close": close})

    def fund_basic(self, market="E"):
        return pd.DataFrame({
            "ts_code": _etf_codes(),
            "name": [f"某行业ETF{i:03d}" for i in range(N_STOCK)],
        })


def _codes() -> list[str]:
    return [f"{600000 + i}.SH" for i in range(N_STOCK)]


def _etf_codes() -> list[str]:
    return [f"{510000 + i}.SH" for i in range(N_STOCK)]


def load_rps(cal: list[str], published: set[str], industry_fn=None):
    """全新加载 daily_rps_pro，并把 tushare / akshare 换成可控替身。

    必须每次重新 import：模块级的 _SNAP_CACHE 会跨用例串味，
    上一个用例缓存的行情会让下一个用例「本该取不到数据」变成取得到。
    """
    fake_ts = types.ModuleType("tushare")
    fake_pro = FakePro(cal, published)
    fake_ts.set_token = lambda *a, **k: None
    fake_ts.pro_api = lambda *a, **k: fake_pro

    fake_ak = types.ModuleType("akshare")
    fake_ak.stock_individual_info_em = industry_fn or (
        lambda symbol=None: pd.DataFrame({"item": ["行业"], "value": ["细分题材X"]})
    )

    sys.modules["tushare"] = fake_ts
    sys.modules["akshare"] = fake_ak
    sys.modules.pop("daily_rps_pro", None)
    import daily_rps_pro as rps  # noqa: PLC0415
    rps.pro = fake_pro
    rps._SNAP_CACHE.clear()
    return rps, fake_pro


def load_etf(cal: list[str], published: set[str]):
    """同上，加载 daily_etf_pro。ETF 与个股是两套独立的日期锚定实现，
    修一处不修另一处，下次事故就换个脚本重演一遍。"""
    fake_ts = types.ModuleType("tushare")
    fake_pro = FakePro(cal, published)
    fake_ts.set_token = lambda *a, **k: None
    fake_ts.pro_api = lambda *a, **k: fake_pro
    sys.modules["tushare"] = fake_ts
    sys.modules.pop("daily_etf_pro", None)
    import daily_etf_pro as etf  # noqa: PLC0415
    etf.pro = fake_pro
    etf._ETF_SNAP_CACHE.clear()
    return etf, fake_pro


@contextlib.contextmanager
def sandbox():
    """把 STOCK_PATH 指到临时目录，避免探针写坏生产 data/strong_stocks.csv。"""
    tmp = tempfile.mkdtemp(prefix="rps_probe_")
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        os.makedirs("data", exist_ok=True)
        yield tmp
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


# ================= 1) 目标交易日必须锚定「行情已发布」 =================

def test_anchor_backoff() -> None:
    print("\n--- 1) 目标交易日锚定 ---")
    cal = _weekdays("20260902", 300)      # 首位 20260902（周三）
    # 造 09-01 事故现场：日历首位是当天，但当天行情还没发布
    rps, pro = load_rps(cal, published=set(cal[1:]))
    dates = rps.get_trading_dates("20260902")

    ck("行情未发布时回退到上一交易日（不再空转）",
       dates is not None and dates["now"] == cal[1],
       f"now={dates and dates['now']} 期望 {cal[1]}")
    ck("prev 跟着锚定日往后挪一格",
       dates is not None and dates["prev"] == cal[2], f"prev={dates and dates['prev']}")
    # 窗口必须从锚定日数：回退一天却按日历首位取 50 格 → 实际窗口只有 49 天
    for n in rps.RPS_N:
        ck(f"RPS_{n} 窗口从锚定日往前数（防口径悄悄漂移）",
           dates.get(n) == cal[1 + n], f"{dates.get(n)} 期望 {cal[1 + n]}")

    # 正常情形：当天行情已发布 → 就用当天，不该无谓回退
    rps2, _ = load_rps(cal, published=set(cal))
    d2 = rps2.get_trading_dates("20260902")
    ck("行情已发布时锚定当天（不无谓回退）", d2["now"] == cal[0], str(d2["now"]))

    # 连续多日无行情 → 必须报错，不能靠回退掩盖 token 失效
    rps3, _ = load_rps(cal, published=set(cal[10:]))
    ck("连续超过 MAX_ANCHOR_BACK 天无行情 → 返回 None（报错而非静默用旧数据）",
       rps3.get_trading_dates("20260902") is None)
    ck("MAX_ANCHOR_BACK 是有限值（不允许无限回退）",
       0 < rps3.MAX_ANCHOR_BACK <= 5, str(rps3.MAX_ANCHOR_BACK))

    # 行情快照缓存：锚定探测与正式计算不该把同一天拉两遍
    rps4, pro4 = load_rps(cal, published=set(cal))
    rps4.get_trading_dates("20260902")
    before = list(pro4.daily_calls)
    rps4.get_snapshot(cal[0])
    ck("锚定探测的行情进了缓存（同一天不重复拉取）",
       len(pro4.daily_calls) == len(before), f"{before} -> {pro4.daily_calls}")

    # 缓存必须交副本：调用方 rename(inplace=True) 不得污染缓存
    s1 = rps4.get_snapshot(cal[0])
    s1.rename(columns={"close_val": "base_now"}, inplace=True)
    s2 = rps4.get_snapshot(cal[0])
    ck("缓存交出的是副本（调用方改列名不污染缓存）",
       "close_val" in s2.columns, f"第二次拿到列 {list(s2.columns)}")


# ================= 2) 装饰性字段不得挡在主产物前面 =================

def _hanging_industry(sleep_sec=30):
    def fn(symbol=None):
        time.sleep(sleep_sec)
        return pd.DataFrame({"item": ["行业"], "value": ["永远不会返回"]})
    return fn


def _raising_industry(symbol=None):
    raise RuntimeError("题材接口炸了")


def test_two_phase_save() -> None:
    print("\n--- 2) 两阶段落盘（题材挂住也要有榜单） ---")
    cal = _weekdays("20260902", 300)
    pub = set(cal)

    # (a) 题材接口整体挂住 → 预算到点放弃，榜单必须已在磁盘上
    with sandbox():
        rps, _ = load_rps(cal, pub, industry_fn=_hanging_industry(30))
        rps.INDUSTRY_BUDGET_SEC = 3          # 缩短预算，让用例几秒跑完
        rps.INDUSTRY_SOCKET_TIMEOUT = 2
        t0 = time.time()
        code = rps.main_job()
        cost = time.time() - t0
        ck("题材全部挂住时 main_job 仍返回成功码 0", code == 0, f"code={code}")
        ck("题材挂住不会把整步拖到超时被杀（耗时受预算约束）",
           cost < rps.INDUSTRY_BUDGET_SEC + 25, f"{cost:.1f}s")
        exists = os.path.exists(rps.STOCK_PATH)
        ck("题材挂住时榜单本体已落盘（主产物不被装饰字段绑架）", exists)
        if exists:
            df = pd.read_csv(rps.STOCK_PATH)
            ck("落盘榜单非空", len(df) > 0, f"{len(df)} 行")
            for col in ("ts_code", "name", "RPS_50", "RPS_120", "RPS_250",
                        "连续天数", "更新日期", "细分行业"):
                ck(f"落盘榜单含列 {col}", col in df.columns, str(list(df.columns))[:90])
            ck("更新日期锚定真实交易日（非系统日期）",
               str(df["更新日期"].iloc[-1]) == f"{cal[0][:4]}-{cal[0][4:6]}-{cal[0][6:]}",
               str(df["更新日期"].iloc[-1]))
            ck("题材抓取失败时细分行业退回粗行业（不是一片 —）",
               df["细分行业"].astype(str).str.startswith("粗行业").all(),
               str(df["细分行业"].unique()[:3]))

    # (b) 题材接口抛异常 → 同样不影响榜单
    with sandbox():
        rps, _ = load_rps(cal, pub, industry_fn=_raising_industry)
        rps.INDUSTRY_BUDGET_SEC = 5
        code = rps.main_job()
        ck("题材接口抛异常时 main_job 仍返回 0", code == 0, f"code={code}")
        ck("题材接口抛异常时榜单仍落盘", os.path.exists(rps.STOCK_PATH))

    # (c) 题材正常 → 必须覆盖写入细分题材
    with sandbox():
        rps, _ = load_rps(cal, pub)
        rps.INDUSTRY_BUDGET_SEC = 30
        rps.main_job()
        df = pd.read_csv(rps.STOCK_PATH)
        ck("题材正常时细分行业被覆盖为细分值",
           (df["细分行业"] == "细分题材X").all(), str(df["细分行业"].unique()[:3]))

    # (d) 线程池不得用 with：__exit__ 是 shutdown(wait=True)，会把「已放弃」变回死等
    src = open(os.path.join(ROOT, "daily_rps_pro.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "fetch_detailed_industries")
    with_pool = any(
        isinstance(w, (ast.With, ast.AsyncWith)) and "ThreadPoolExecutor" in ast.dump(w)
        for w in ast.walk(fn))
    ck("题材抓取未用 with ThreadPoolExecutor（否则 shutdown(wait=True) 死等挂住线程）",
       not with_pool)
    body = "\n".join(src.splitlines()[fn.lineno - 1: fn.end_lineno])
    ck("显式 shutdown(wait=False, cancel_futures=True)",
       "wait=False" in body and "cancel_futures=True" in body)
    ck("as_completed 带 timeout（否则主线程也会死等）",
       "as_completed(futures, timeout=" in body)


# ================= 3) 退出码必须如实 =================

def test_exit_code_honesty() -> None:
    print("\n--- 3) 退出码如实反映结果 ---")
    cal = _weekdays("20260902", 300)

    with sandbox():
        # 连续多日无行情 → 必须非 0，否则跑批汇总会把失败显示成 [OK]
        rps, _ = load_rps(cal, published=set(cal[10:]))
        ck("取不到行情时 main_job 返回非 0（不再谎报 OK）",
           rps.main_job() not in (0, None))

    with sandbox():
        rps, _ = load_rps(cal, published=set(cal))
        rps.INDUSTRY_BUDGET_SEC = 5
        ck("正常跑通时返回 0", rps.main_job() == 0)

    src = open(os.path.join(ROOT, "daily_rps_pro.py"), encoding="utf-8").read()
    ck("入口用 sys.exit(main_job()...) 把返回值透到进程退出码",
       "sys.exit(main_job()" in src)
    ck("main_job 失败分支有 return 1（AST 层面确认存在非 0 返回）",
       any(isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
           and n.value.value == 1
           for f in ast.walk(ast.parse(src))
           if isinstance(f, ast.FunctionDef) and f.name == "main_job"
           for n in ast.walk(f)))


# ================= 4) 看门狗判据必须逐产物覆盖 =================

def test_watchdog_probes() -> None:
    print("\n--- 4) 看门狗探针覆盖 ---")
    sys.modules.pop("tools_gh_watchdog", None)
    import tools_gh_watchdog as wd  # noqa: PLC0415

    paths = [p for _, p, _ in wd.PROBES]
    for need in ("data/strong_stocks.csv", "data/breakout_stocks.csv",
                 "data/limit_ladder.json", "data/digest/latest.json"):
        ck(f"看门狗探针覆盖 {need}", need in paths, str(paths))

    # 取值方式必须按产物各写一份：RPS 是中文列名，突破池是 update_date
    spec = {p: s for _, p, s in wd.PROBES}
    ck("RPS 用 csv:更新日期 取值", spec.get("data/strong_stocks.csv") == "csv:更新日期",
       str(spec.get("data/strong_stocks.csv")))
    ck("突破池用 csv:update_date 取值",
       spec.get("data/breakout_stocks.csv") == "csv:update_date",
       str(spec.get("data/breakout_stocks.csv")))

    # 取日期：CSV 必须取最后一行（与 run_daily 的 FRESHNESS 口径一致）
    csv_body = "ts_code,更新日期\n600000.SH,2026-08-31\n600001.SH,2026-09-01\n"
    ck("CSV 取最后一行日期", wd._extract_date(csv_body, "csv:更新日期") == "20260901",
       wd._extract_date(csv_body, "csv:更新日期"))
    ck("JSON 取指定键并归一化",
       wd._extract_date('{"date": "2026-09-01 22:05:08"}', "json:date") == "20260901")

    # 缺列 / 空表必须抛异常，不能静默返回空串——静默会让「解析失败」伪装成「数据过期」
    try:
        wd._extract_date("ts_code,更新日期\n600000.SH,2026-08-31\n", "csv:update_date")
        ck("CSV 缺列时抛 KeyError（不静默）", False, "居然没抛")
    except KeyError as e:
        ck("CSV 缺列时抛 KeyError 且列出实际列名", "更新日期" in str(e), str(e)[:80])
    try:
        wd._extract_date("ts_code,更新日期\n", "csv:更新日期")
        ck("CSV 空表时抛 ValueError（不静默）", False, "居然没抛")
    except ValueError as e:
        ck("CSV 空表时抛 ValueError", True, str(e)[:60])

    # 判定逻辑：任一产物落后即视为不新鲜
    saved = wd._get
    try:
        def fake_get(url, tok=None, raw=False):
            if url.endswith("strong_stocks.csv"):
                return 200, "ts_code,更新日期\n600000.SH,2026-08-31\n"
            if url.endswith("breakout_stocks.csv"):
                return 200, "ts_code,update_date\n600000.SH,2026-09-01\n"
            return 200, '{"date": "20260901"}'
        # stub 必须打在主通道（curl 在前）与备用通道两处，
        # 否则主通道直连真实网络，测试变成对线上现状的「复读」。
        wd._get = fake_get
        wd._get_via_curl = lambda url: fake_get(url)
        fresh, lines = wd.check_freshness("20260901")
        # check_freshness 返回三态 verdict（fresh/stale/blind），不是布尔——
        # 任一产物落后即 "stale"（RPS 落后、其余同日）。
        ck("只有 RPS 落后时看门狗判「不新鲜」（09-01 漏判已堵）", fresh == "stale",
           f"verdict={fresh} | " + " | ".join(l.strip() for l in lines))
        ck("日志逐产物列出实际日期（便于人工定位是哪一步失败）",
           any("RPS" in l and "20260831" in l for l in lines), str(lines))
    finally:
        wd._get = saved


# ================= 5) 前端必须显示数据日期并在过期时警示 =================

def test_freshness_layer() -> None:
    print("\n--- 5) 新鲜度计算层 ---")
    sys.modules.pop("data_freshness", None)
    import data_freshness as fr  # noqa: PLC0415

    today = dt.date(2026, 9, 2)
    v = fr.verdict("2026-08-31", reference_date="20260901", today=today)
    ck("同批落后 → stale", v["status"] == "stale", str(v))
    ck("stale 理由点明「跑批没有成功」", "跑批" in v["reason"], v["reason"])
    ck("stale 理由带两个可对账日期",
       "2026-08-31" in v["reason"] and "2026-09-01" in v["reason"], v["reason"])

    ck("同批一致 → ok",
       fr.verdict("20260901", reference_date="20260901", today=today)["status"] == "ok")
    ck("本表比参考更新 → ok（不误报）",
       fr.verdict("20260902", reference_date="20260901", today=today)["status"] == "ok")
    ck("整批一起过期 → stale（同批比对失效时的兜底）",
       fr.verdict("20260820", reference_date="20260820", today=today)["status"] == "stale")
    ck("周五数据周一判定 → ok（周末不误报）",
       fr.verdict("20260828", reference_date="20260828",
                  today=dt.date(2026, 8, 31))["status"] == "ok")
    ck("缺日期戳 → unknown（与 stale 分状态：要修产物而不是补跑）",
       fr.verdict("")["status"] == "unknown")

    ck("norm_date 兼容三种写法",
       fr.norm_date("20260901") == fr.norm_date("2026-09-01")
       == fr.norm_date("2026-09-01 22:05:08") == "20260901")
    ck("norm_date 取不出时返回空串（不返回今天）", fr.norm_date("暂无") == "")

    df = pd.DataFrame({"ts_code": ["600000.SH"], "update_date": ["2026-09-01"]})
    ck("pick_date 支持第二候选列名（列名不统一时不误判为无日期）",
       fr.pick_date(df) == "20260901", fr.pick_date(df))
    ck("pick_date 对空表返回空串", fr.pick_date(pd.DataFrame()) == "")

    # 计算层纯度：必须能脱 streamlit 单测
    tree = ast.parse(open(os.path.join(ROOT, "data_freshness.py"), encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    ck("计算层未 import streamlit", "streamlit" not in mods, str(sorted(mods)))
    ck("计算层未 import 网络库",
       not ({"requests", "urllib", "http", "socket", "tushare", "akshare"} & mods),
       str(sorted(mods)))


def _called_names(tree: ast.AST, func_name: str) -> set[str]:
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            target = node
            break
    if target is None:
        return set()
    out = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _first_call_line(tree: ast.AST, func_name: str, callee: str) -> int | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for n in ast.walk(node):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                        and n.func.id == callee:
                    return n.lineno
    return None


def _first_attr_call_line(tree: ast.AST, func_name: str, attr: str) -> int | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            hits = [n.lineno for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == attr]
            return min(hits) if hits else None
    return None


def _disclaimer_line(tree: ast.AST, func_name: str) -> int | None:
    """定位「免责声明」那次 st.info 的行号。

    不能简单取函数体内第一个 st.info：render_stock_content 开头还有一个
    「暂无数据」早退分支的 st.info，取它会让位置断言无从判定。
    这里按实参文本里是否含「不构成投资建议」来认人。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for n in ast.walk(node):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                        and n.func.attr in ("info", "warning"):
                    txt = " ".join(ast.dump(a) for a in n.args)
                    if "不构成投资建议" in txt:
                        return n.lineno
    return None


def test_app_wiring() -> None:
    print("\n--- 6) 前端接线（AST：真的被调用，而非只定义） ---")
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    ck("app.py 定义了 render_freshness_banner", "render_freshness_banner" in defined)
    ck("app.py 定义了 site_reference_date", "site_reference_date" in defined)
    ck("app.py 导入了 data_freshness 计算层", "import data_freshness" in src)

    for fn in ("render_stock_content", "render_breakout_content", "render_etf_content"):
        called = _called_names(tree, fn)
        ck(f"{fn} 真的调用了 render_freshness_banner",
           "render_freshness_banner" in called, f"实际调用 {sorted(called)[:8]}")

    # 位置约束：横幅必须排在榜单渲染之前，放页脚等于没放
    for fn, widget in (("render_stock_content", "dataframe"),
                       ("render_breakout_content", "dataframe"),
                       ("render_etf_content", "dataframe")):
        banner = _first_call_line(tree, fn, "render_freshness_banner")
        table = _first_attr_call_line(tree, fn, widget)
        ck(f"{fn} 的新鲜度横幅在 st.{widget} 之前",
           banner is not None and table is not None and banner < table,
           f"banner@{banner} table@{table}")

    # 强势股页：新鲜度横幅还要排在免责声明之前（「哪天的数据」比「不构成建议」更基础）
    banner = _first_call_line(tree, "render_stock_content", "render_freshness_banner")
    info = _disclaimer_line(tree, "render_stock_content")
    ck("强势股页新鲜度横幅在免责声明之前",
       banner is not None and info is not None and banner < info,
       f"banner@{banner} disclaimer@{info}")

    body = "\n".join(src.splitlines()[
        next(n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "render_freshness_banner") - 1:
        next(n.end_lineno for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef) and n.name == "render_freshness_banner")])
    ck("过期走 st.error（醒目级别，不是 caption 一行小字）", "st.error" in body)
    ck("缺日期戳走 st.warning，与过期分开报", "st.warning" in body)
    ck("正常时显示数据日期（st.caption）", "st.caption" in body)
    ck("横幅文案说明「按这个日期理解表内数字」",
       "这个日期理解" in body or "不是最新交易日" in body, body[:60])


# ================= 造错反向验证 =================
# 每条都把代码退回「事故当天的样子」，对应断言必须炸。
# 若造错后仍然全绿，说明那条断言是假断言，比没有断言更危险。

def reverse_checks() -> None:
    print("\n" + "=" * 60)
    print("造错反向验证（每项都必须抓到失败）")
    print("=" * 60)
    cal = _weekdays("20260902", 300)

    # R1 目标日退回「直取日历首位」→ 锚定断言必须炸
    rps, _ = load_rps(cal, published=set(cal[1:]))
    orig = rps.get_trading_dates

    def naive(end_date):
        c = rps._load_calendar(end_date)
        d = {"now": c[0], "prev": c[1]}
        for n in rps.RPS_N:
            if len(c) > n:
                d[n] = c[n]
        return d
    rps.get_trading_dates = naive
    with capture() as box:
        dates = rps.get_trading_dates("20260902")
        ck("行情未发布时回退到上一交易日（不再空转）", dates["now"] == cal[1])
    rev("R1 直取日历首位 → 锚定断言炸", bool(box), f"抓到 {box}")
    rps.get_trading_dates = orig

    # R2 窗口按日历首位数（锚定日却已回退）→ 窗口漂移断言必须炸
    with capture() as box:
        d = {"now": cal[1], "prev": cal[2]}
        for n in rps.RPS_N:
            d[n] = cal[n]          # 错：没加 anchor_i
        for n in rps.RPS_N:
            ck(f"RPS_{n} 窗口从锚定日往前数（防口径悄悄漂移）", d.get(n) == cal[1 + n])
    rev("R2 窗口不加 anchor 偏移 → 窗口漂移断言炸", len(box) == len(rps.RPS_N),
        f"抓到 {len(box)} 条")

    # R3 退回「题材先抓、后写盘」→ 主产物落盘断言必须炸
    with sandbox():
        rps3, _ = load_rps(cal, set(cal), industry_fn=_hanging_industry(30))
        rps3.INDUSTRY_BUDGET_SEC = 2
        rps3.INDUSTRY_SOCKET_TIMEOUT = 2
        saved_save = rps3.save_ranking
        rps3.save_ranking = lambda df: None      # 模拟「写盘排在题材之后、还没轮到」
        with capture() as box:
            rps3.main_job()
            ck("题材挂住时榜单本体已落盘（主产物不被装饰字段绑架）",
               os.path.exists(rps3.STOCK_PATH))
        rev("R3 榜单不落盘 → 主产物断言炸", bool(box), f"抓到 {box}")
        rps3.save_ranking = saved_save

    # R4 退回「恒返回 None」→ 退出码断言必须炸
    with sandbox():
        rps4, _ = load_rps(cal, published=set(cal[10:]))
        rps4.main_job = lambda: None
        with capture() as box:
            ck("取不到行情时 main_job 返回非 0（不再谎报 OK）",
               rps4.main_job() not in (0, None))
        rev("R4 main_job 恒返回 None → 退出码断言炸", bool(box), f"抓到 {box}")

    # R5 看门狗退回只探 2 个产物 → 覆盖断言必须炸
    sys.modules.pop("tools_gh_watchdog", None)
    import tools_gh_watchdog as wd  # noqa: PLC0415
    saved_probes = wd.PROBES
    wd.PROBES = [("连板天梯", "data/limit_ladder.json", "json:date"),
                 ("收盘摘要", "data/digest/latest.json", "json:date")]
    with capture() as box:
        paths = [p for _, p, _ in wd.PROBES]
        for need in ("data/strong_stocks.csv", "data/breakout_stocks.csv"):
            ck(f"看门狗探针覆盖 {need}", need in paths)
        # 事故当天的判定：两个 JSON 都是新的 → 旧代码判「已就位」
        saved_get = wd._get
        saved_curl = wd._get_via_curl
        wd._get = lambda url, tok=None, raw=False: (200, '{"date": "20260901"}')
        wd._get_via_curl = lambda url: (200, '{"date": "20260901"}')
        fresh, _ = wd.check_freshness("20260901")
        ck("只有 RPS 落后时看门狗判「不新鲜」（09-01 漏判已堵）", fresh == "stale",
           f"verdict={fresh}")
        wd._get = saved_get
        wd._get_via_curl = saved_curl
    rev("R5 看门狗只探 2 个产物 → 覆盖与漏判断言炸", len(box) >= 3, f"抓到 {len(box)} 条")
    wd.PROBES = saved_probes

    # R6 新鲜度判定去掉同批比对 → stale 断言必须炸
    sys.modules.pop("data_freshness", None)
    import data_freshness as fr  # noqa: PLC0415
    with capture() as box:
        v = fr.verdict("2026-08-31", reference_date=None, today=dt.date(2026, 9, 2))
        ck("同批落后 → stale", v["status"] == "stale")
    rev("R6 去掉同批比对 → 落后一天抓不到（stale 断言炸）", bool(box), f"抓到 {box}")

    # R7 前端只定义不接线 → 接线断言必须炸
    fake_app = """
import streamlit as st
def render_freshness_banner(df, label):
    st.caption(label)
def render_stock_content(df):
    st.info("免责声明")
    st.dataframe(df)
"""
    with capture() as box:
        t = ast.parse(fake_app)
        called = _called_names(t, "render_stock_content")
        ck("render_stock_content 真的调用了 render_freshness_banner",
           "render_freshness_banner" in called)
    rev("R7 只定义不接线 → 接线断言炸", bool(box), f"抓到 {box}")

    # R8 横幅放页脚（在 dataframe 之后）→ 位置断言必须炸
    tail_app = """
import streamlit as st
def render_freshness_banner(df, label):
    st.caption(label)
def render_stock_content(df):
    st.dataframe(df)
    render_freshness_banner(df, "强势股")
"""
    with capture() as box:
        t = ast.parse(tail_app)
        b = _first_call_line(t, "render_stock_content", "render_freshness_banner")
        tb = _first_attr_call_line(t, "render_stock_content", "dataframe")
        ck("render_stock_content 的新鲜度横幅在 st.dataframe 之前", b < tb)
    rev("R8 横幅放页脚 → 位置断言炸", bool(box), f"抓到 {box}")

    # R9 横幅排在免责声明之后 → 顺序断言必须炸。
    #    同时验证 _disclaimer_line 认的是「含不构成投资建议」那次调用，
    #    而不是开头「暂无数据」那个 st.info——认错人这条断言就恒真。
    after_disclaimer = """
import streamlit as st
def render_freshness_banner(df, label):
    st.caption(label)
def render_stock_content(df):
    if df is None:
        st.info("暂无数据，请等待每日更新")
        return
    st.info("本表不构成投资建议")
    render_freshness_banner(df, "强势股")
    st.dataframe(df)
"""
    with capture() as box:
        t = ast.parse(after_disclaimer)
        b = _first_call_line(t, "render_stock_content", "render_freshness_banner")
        dis = _disclaimer_line(t, "render_stock_content")
        ck("强势股页新鲜度横幅在免责声明之前", b < dis)
    rev("R9 横幅排在免责声明之后 → 顺序断言炸", bool(box), f"抓到 {box}")


# ================= 7) ETF 同一坑位（含「周六日期」老 bug） =================

def test_etf_anchor_and_date() -> None:
    print("\n--- 7) ETF 榜单日期锚定 ---")
    cal = _weekdays("20260902", 300)

    etf1, _ = load_etf(cal, published=set(cal[1:]))
    d = etf1.get_trading_dates("20260902")
    ck("ETF 行情未发布时回退上一交易日", d is not None and d["now"] == cal[1],
       f"now={d and d['now']} 期望 {cal[1]}")
    for n in etf1.RPS_N:
        ck(f"ETF RPS_{n} 窗口从锚定日往前数", d.get(n) == cal[1 + n],
           f"{d.get(n)} 期望 {cal[1 + n]}")

    etf2, _ = load_etf(cal, published=set(cal[10:]))
    ck("ETF 连续多日无行情 → 返回 None（不静默用旧数据）",
       etf2.get_trading_dates("20260902") is None)
    ck("ETF MAX_ANCHOR_BACK 是有限值（与 RPS 同口径）",
       0 < etf2.MAX_ANCHOR_BACK <= 5, str(etf2.MAX_ANCHOR_BACK))

    # 老 bug：更新日期写系统日期 → 08-30 00:25 那班把「2026-08-29（周六）」写进了 CSV。
    # 这里刻意用一份与系统日期无关的日历：若锚定日恰好等于今天，
    # 「写锚定日」和「写系统日期」两种实现的产物一模一样，断言就退化成恒真。
    cal_far = _weekdays("20260610", 300)
    bj_today = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y%m%d")
    ck("用例本身有区分度（锚定日 != 系统日期）", cal_far[0] != bj_today,
       f"anchor={cal_far[0]} today={bj_today}")
    with sandbox():
        etf3, _ = load_etf(cal_far, published=set(cal_far))
        code = etf3.main_job()
        ck("ETF main_job 正常返回 0", code == 0, f"code={code}")
        df = pd.read_csv(etf3.ETF_PATH)
        want = f"{cal_far[0][:4]}-{cal_far[0][4:6]}-{cal_far[0][6:]}"
        ck("ETF 更新日期写锚定交易日（不再把系统日期/周末写进 CSV）",
           str(df["更新日期"].iloc[-1]) == want,
           f"{df['更新日期'].iloc[-1]} 期望 {want}")
        ck("ETF 落盘榜单非空", len(df) > 0, f"{len(df)} 行")

    with sandbox():
        etf4, _ = load_etf(cal, published=set(cal[10:]))
        ck("ETF 取不到行情时返回非 0（不再谎报 OK）",
           etf4.main_job() not in (0, None))

    # ETF 快照缓存同样必须交副本：main_job 对返回值做 rename(inplace=True)
    etf5, pro5 = load_etf(cal, published=set(cal))
    etf5.get_trading_dates("20260902")
    before = list(pro5.daily_calls)
    s1 = etf5.get_etf_snapshot(cal[0])
    ck("ETF 锚定探测的行情进了缓存（同一天不重复拉取）",
       len(pro5.daily_calls) == len(before), f"{before[-3:]} -> {pro5.daily_calls[-3:]}")
    s1.rename(columns={"close_val": "price_now"}, inplace=True)
    ck("ETF 缓存交出的是副本（调用方改列名不污染缓存）",
       "close_val" in etf5.get_etf_snapshot(cal[0]).columns,
       f"第二次拿到列 {list(etf5.get_etf_snapshot(cal[0]).columns)}")

    src = open(os.path.join(ROOT, "daily_etf_pro.py"), encoding="utf-8").read()
    ck("ETF 入口用 sys.exit(main_job()...)", "sys.exit(main_job()" in src)
    ck("ETF 日期用北京时间（不是裸 datetime.now）",
       "utcnow() + datetime.timedelta(hours=8)" in src)
    ck("ETF 更新日期由锚定交易日推出（源码级：不是 strftime 系统日期）",
       'today_fmt = f"{trading_date[:4]}' in src)


# ================= 8) 编排器必须自己判出「某一步落后」 =================

def _write_probe_fixture(root: str, rps_date: str) -> None:
    """在临时目录里复刻一批跑批产物。rps_date 单独可控，用来复刻 09-01 现场：
    只有 strong_stocks.csv 停在前一天，其余四个产物都是当天。
    正向用例与反向验证共用这一份，避免两处各写一遍再慢慢漂移。"""
    files = {
        "data/strong_stocks.csv": f"ts_code,更新日期\n600000.SH,{rps_date}\n",
        "data/breakout_stocks.csv": "ts_code,update_date\n600000.SH,2026-09-01\n",
        "data/strong_etfs.csv": "ts_code,更新日期\n510050.SH,2026-09-01\n",
        "data/limit_ladder.json": '{"date": "2026-09-01"}',
        "data/digest/latest.json": '{"date": "20260901"}',
    }
    for rel, body in files.items():
        with open(os.path.join(root, rel), "w", encoding="utf-8") as f:
            f.write(body)


def test_orchestrator_consistency() -> None:
    print("\n--- 8) 跑批汇总须自动点名落后产物 ---")
    sys.modules.pop("run_daily", None)
    import run_daily as rd  # noqa: PLC0415

    paths = [p for _, p, _ in rd.DATE_PROBES]
    for need in ("data/strong_stocks.csv", "data/breakout_stocks.csv",
                 "data/strong_etfs.csv", "data/limit_ladder.json",
                 "data/digest/latest.json"):
        ck(f"编排器日期判据覆盖 {need}", need in paths, str(paths))

    tmp = tempfile.mkdtemp(prefix="rd_probe_")
    saved_root = rd.REPO_ROOT
    try:
        rd.REPO_ROOT = tmp
        os.makedirs(os.path.join(tmp, "data", "digest"), exist_ok=True)
        _write_probe_fixture(tmp, rps_date="2026-08-31")   # 复刻 09-01 现场

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            n = rd.check_date_consistency()
        out = buf.getvalue()
        ck("复刻 09-01 现场时判出 1 项落后", n == 1, f"n={n}")
        ck("落后产物被点名（不用人肉比日期）",
           "RPS 强势股" in out and "落后" in out,
           str([l for l in out.splitlines() if "落后" in l][:1]))
        ck("::warning:: 注解写明要补跑该步骤", "需要补跑该步骤" in out)

        # 全部一致 → 不得误报
        _write_probe_fixture(tmp, rps_date="2026-09-01")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            n2 = rd.check_date_consistency()
        ck("全部同日 → 0 项落后（不误报）", n2 == 0, f"n={n2}")

        # 取不到日期的产物不能被算成「落后」——那是产物结构问题，不是跑批问题
        os.remove(os.path.join(tmp, "data/strong_etfs.csv"))
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            n3 = rd.check_date_consistency()
        ck("产物缺失只报「取不到日期」，不计入落后数", n3 == 0, f"n={n3}")
        ck("缺失产物在日志里点名", "ETF 榜单" in buf3.getvalue())

        # 缺列必须点出实际列名：否则「取不到日期」这行日志等于没说
        with open(os.path.join(tmp, "data/strong_etfs.csv"), "w", encoding="utf-8") as f:
            f.write("ts_code,upd\n510050.SH,2026-09-01\n")
        buf4 = io.StringIO()
        with contextlib.redirect_stdout(buf4):
            rd.check_date_consistency()
        ck("缺列时报错带上实际列名（便于定位改名）",
           "upd" in buf4.getvalue() and "缺列" in buf4.getvalue(),
           str([l for l in buf4.getvalue().splitlines() if "ETF" in l][:1]))
    finally:
        rd.REPO_ROOT = saved_root
        shutil.rmtree(tmp, ignore_errors=True)

    ck("finish() 里调用了 check_date_consistency（真的接线）",
       "check_date_consistency" in _called_names(
           ast.parse(open(os.path.join(ROOT, "run_daily.py"), encoding="utf-8").read()),
           "finish"))


# ============ 第 7、8 组的造错反向验证（R10 / R11）============

def _load_patched(filename: str, repl: list[tuple[str, str]], modname: str):
    """把某个模块的源码退回「事故当天的写法」后加载。

    直接改源码字符串再 exec，而不是 monkey-patch 函数——反向验证要证明的是
    「如果代码还是旧的样子，断言会炸」，patch 掉整个函数就等于换了一份实现，
    证不到真正那一行。锚点找不到时立即抛错：锚点失配会让反向验证悄悄空转，
    那比断言恒真更糟。
    """
    src = open(os.path.join(ROOT, filename), encoding="utf-8").read()
    for old, new in repl:
        if old not in src:
            raise AssertionError(f"反向验证锚点在 {filename} 里找不到：{old[:50]!r}")
        src = src.replace(old, new, 1)
    mod = types.ModuleType(modname)
    mod.__file__ = os.path.join(ROOT, filename)
    exec(compile(src, mod.__file__, "exec"), mod.__dict__)   # noqa: S102
    return mod


def reverse_checks_extra() -> None:
    print("\n--- 造错反向验证 R10 / R11（ETF 与编排器）---")
    cal = _weekdays("20260902", 300)

    # R10a ETF 退回「直取日历首位」→ 锚定与窗口断言必须炸
    fake_pro = FakePro(cal, published=set(cal[1:]))
    fake_ts = types.ModuleType("tushare")
    fake_ts.set_token = lambda *a, **k: None
    fake_ts.pro_api = lambda *a, **k: fake_pro
    sys.modules["tushare"] = fake_ts
    naive = _load_patched(
        "daily_etf_pro.py",
        [("if not get_etf_snapshot(cal[i]).empty:", "if True:")],
        "etf_naive",
    )
    naive.pro = fake_pro
    naive._ETF_SNAP_CACHE.clear()
    with capture() as box:
        d = naive.get_trading_dates("20260902")
        ck("ETF 行情未发布时回退上一交易日", d is not None and d["now"] == cal[1])
        for n in naive.RPS_N:
            ck(f"ETF RPS_{n} 窗口从锚定日往前数", d.get(n) == cal[1 + n])
    rev("R10a ETF 直取日历首位 → 锚定+窗口断言炸", len(box) >= 4, f"抓到 {len(box)} 条")

    # R10b ETF 更新日期退回系统日期 → 「周六日期」那条断言必须炸
    cal_far = _weekdays("20260610", 300)
    pro_far = FakePro(cal_far, published=set(cal_far))
    fake_ts.pro_api = lambda *a, **k: pro_far
    sysdate = _load_patched(
        "daily_etf_pro.py",
        [('today_fmt = f"{trading_date[:4]}-{trading_date[4:6]}-{trading_date[6:]}"',
          "today_fmt = beijing_time.strftime('%Y-%m-%d')")],
        "etf_sysdate",
    )
    sysdate.pro = pro_far
    sysdate._ETF_SNAP_CACHE.clear()
    with sandbox():
        with capture() as box:
            sysdate.main_job()
            df = pd.read_csv(sysdate.ETF_PATH)
            want = f"{cal_far[0][:4]}-{cal_far[0][4:6]}-{cal_far[0][6:]}"
            ck("ETF 更新日期写锚定交易日（不再把系统日期/周末写进 CSV）",
               str(df["更新日期"].iloc[-1]) == want)
    rev("R10b ETF 写系统日期 → 更新日期断言炸", bool(box), f"抓到 {box}")

    # R11 编排器退回「只打印日期不判定」→ 落后点名断言必须炸
    stale_blind = _load_patched(
        "run_daily.py", [("if d < ref:", "if False:")], "rd_blind")
    tmp = tempfile.mkdtemp(prefix="rd_rev_")
    try:
        stale_blind.REPO_ROOT = tmp
        os.makedirs(os.path.join(tmp, "data", "digest"), exist_ok=True)
        _write_probe_fixture(tmp, rps_date="2026-08-31")
        with capture() as box:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                n = stale_blind.check_date_consistency()
            out = buf.getvalue()
            ck("复刻 09-01 现场时判出 1 项落后", n == 1)
            ck("落后产物被点名（不用人肉比日期）",
               "RPS 强势股" in out and "落后" in out)
            ck("::warning:: 注解写明要补跑该步骤", "需要补跑该步骤" in out)
        rev("R11 编排器只打印不判定 → 落后点名断言炸", len(box) >= 3, f"抓到 {len(box)} 条")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("RPS 跑批链路自检（离线 stub，不需要 tushare token）")
    print("=" * 60)
    test_anchor_backoff()
    test_two_phase_save()
    test_exit_code_honesty()
    test_watchdog_probes()
    test_freshness_layer()
    test_app_wiring()
    test_etf_anchor_and_date()
    test_orchestrator_consistency()
    reverse_checks()
    reverse_checks_extra()

    print("-" * 60)
    if FAIL:
        print(f"❌ 断言失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    if FAKE:
        print(f"⚠️ 存在假断言 {len(FAKE)} 项（造错后仍全绿）：{FAKE}")
        sys.exit(3)
    print("✅ 全部通过，且所有断言均经造错反向验证")
