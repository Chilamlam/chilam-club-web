# -*- coding: utf-8 -*-
"""行业「低市值占比 + 高资金热度」信号自检（纯离线，不依赖网络与 tushare）。

为什么必须离线可跑：这条链路真实数据要等 tushare 每天 18:30 更新、且跑批在
Actions 上，若自检依赖真实接口，就等于「每天只能验一次、失败还分不清是代码错
还是接口错」。逻辑分支全部用构造数据覆盖，真实数据只用于一次性实测（另跑）。

校验口径（单位与语义错一个都会给出看起来正常的错误结论）：
 1. 分位是中位秩：最小值→0 附近、最大值→100 附近、并列值各分一半
 2. 命中条件是**严格**不等式：mv_pct < 30 且 amt_pct > 80（等于阈值不算命中）
 3. 排序确定：amt_pct 降序，并列时 mv_pct 升序；picks 截断但 hits_count 报全量
 4. 样本不足（<20 个完整截面交易日）→ insufficient，不出结论而不是出错的结论
 5. 窗口不足时**降级并如实上报实际长度**，不假装窗口是满的
 6. 截面残缺（某日行业数腰斩）→ 整日剔除；占比分母是当日全体行业之和，
    半份截面会让每个行业的占比凭空放大，而数值形态完全正常
 7. 缺市值/成交额的行业直接剔除，绝不补 0（补 0 会把它算成占比 0% 的行业）
 8. 计算层不得 import streamlit / 网络库 / tushare
 9. 推送段落：0 命中必须如实播报；缺数据整段不出现并计入 missing；不含操作建议
10. 接线：run_daily 步骤表 / 新鲜度自检 / 跨产物日期判据 / digest 段落全部到位
11. 取数守卫：接口返回的 trade_date 与请求日期不一致时拒收（参数被静默忽略）
12. 残缺截面拒收：行业数不达标时 section_ok=False；历史里同日残留会被清掉
13. 展示页装配：universe_frame 的命中标记必须与结论 picks 完全一致（页面标红的
    必须就是结论里命中的）、阈值跟着 thresholds 走而不是常量；industry_series
    的末点分位必须等於结论里的分位，且**无前视偏差**（追加未来数据后已有各点
    逐点不变）。页面本身的真渲染另有 tools_probe_industry_signal_page.py。
"""
from __future__ import annotations

import ast
import datetime
import json
import os
import sys
import tempfile

import pandas as pd

import industry_signal as isig

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


# ============================ fixture 构造 ============================

def _dates(n: int) -> list[str]:
    base = datetime.date(2026, 1, 1)
    return [(base + datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def _row(date: str, code: str, name: str, l1: str, mv_yi: float,
         amt_yi: float, pct: float = 0.0) -> dict:
    total = None if mv_yi is None else mv_yi * 2
    return {"date": date, "code": code, "name": name, "l1_name": l1,
            "close": 1000.0, "pct_chg": pct, "amount_yi": amt_yi,
            "float_mv_yi": mv_yi, "total_mv_yi": total, "vol": 1.0}


def _base(days: int, n_norm: int = 19) -> list[dict]:
    """n_norm 个常态行业：市值与成交额都恒定。"""
    rows = []
    for d in _dates(days):
        for i in range(n_norm):
            rows.append(_row(d, f"NORM{i:02d}", f"常态{i}", "常态一级", 1000.0, 10.0))
    return rows


def _declining_mv_rising_amt(rows: list[dict], days: int, code: str = "TGT000",
                             name: str = "目标行业", l1: str = "目标一级") -> list[dict]:
    """市值逐日走低（占比一路下降）+ 末日成交额跳升（占比骤增）→ 末日必命中。"""
    ds = _dates(days)
    for i, d in enumerate(ds):
        mv = 1000.0 - 600.0 * i / max(days - 1, 1)      # 1000 → 400
        amt = 200.0 if i == days - 1 else 10.0          # 末日渐升
        rows.append(_row(d, code, name, l1, mv, amt))
    return rows


def _spike_rows(rows: list[dict], days: int, specs: list[tuple[str, int]]) -> list[dict]:
    """构造多个「命中但热度分位各不相同」的行业，用于验证排序与截断。

    specs 是 (code, n_bigger)：n_bigger 表示在 20 日窗口内有多少个历史成交额
    **大于**末值。末值分位 = (19 - n_bigger + 0.5)/20*100，于是
    n_bigger = 0/1/2/3 → 分位 97.5 / 92.5 / 87.5 / 82.5，四档互不相同。
    市值一律线性降到最低，保证 mv_pct 恒在低位（必然满足 < 30）。
    """
    ds = _dates(days)
    for code, n_bigger in specs:
        amt_seq = [10.0] * (days - 1) + [100.0]
        for j in range(n_bigger):
            amt_seq[days - 2 - j] = 500.0
        mv_seq = [1000.0 - 500.0 * i / max(days - 1, 1) for i in range(days)]
        for i, d in enumerate(ds):
            rows.append(_row(d, code, code, "目标一级", mv_seq[i], amt_seq[i]))
    return rows


# ============================ 1. 分位算法 ============================

def test_percentile_rank() -> None:
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    p_min = isig.percentile_rank(vals, 1.0)
    p_max = isig.percentile_rank(vals, 5.0)
    p_mid = isig.percentile_rank(vals, 3.0)
    check("分位：最小值 = 0.5/n（不是 0 也不是负）", p_min is not None and abs(p_min - 10.0) < 1e-9, str(p_min))
    check("分位：最大值 = (n-0.5)/n", p_max is not None and abs(p_max - 90.0) < 1e-9, str(p_max))
    check("分位：中位值 = 50", p_mid is not None and abs(p_mid - 50.0) < 1e-9, str(p_mid))
    check("分位：结果恒落在 [0,100]", all(0 <= p <= 100 for p in (p_min, p_max, p_mid)))

    # 并列值必须各分一半，否则一串相同值会被整体判成高分位
    p_tie = isig.percentile_rank([5.0, 5.0, 5.0, 5.0], 5.0)
    check("分位：全并列 → 50（中位秩，而非 100）",
          p_tie is not None and abs(p_tie - 50.0) < 1e-9, str(p_tie))
    p_two = isig.percentile_rank([1.0, 2.0, 2.0, 3.0], 2.0)
    check("分位：中间并列 → (1 + 0.5*2)/4 = 50", p_two is not None and abs(p_two - 50.0) < 1e-9, str(p_two))

    check("分位：空序列 → None（不返回 0/50 冒充算出来）",
          isig.percentile_rank([], 1.0) is None)
    check("分位：value 为 None → None", isig.percentile_rank(vals, None) is None)
    check("分位：value 为 NaN → None", isig.percentile_rank(vals, float("nan")) is None)
    p_nan = isig.percentile_rank([1.0, None, float("nan"), 3.0], 3.0)
    check("分位：序列里的 None/NaN 不参与计数",
          p_nan is not None and abs(p_nan - 75.0) < 1e-9, str(p_nan))
    check("分位：单元素序列 → 50",
          isig.percentile_rank([7.0], 7.0) == 50.0)


# ============================ 2. 命中与排序 ============================

def test_hit_and_order() -> None:
    days = 30
    rows = _declining_mv_rising_amt(_base(days), days)
    sig = isig.compute_signal(pd.DataFrame(rows))

    check("主检测：status=ok", sig.get("status") == "ok", str(sig.get("reason", "")))
    picks = sig.get("picks") or []
    check("目标行业被选中（低市值占比 + 高热度）",
          any(p["code"] == "TGT000" for p in picks),
          str([p["name"] for p in picks]))

    tgt = next((p for p in picks if p["code"] == "TGT000"), None)
    if tgt:
        # 占比序列单调下降 → 末日最小 → 分位 = 0.5/30*100 = 1.67
        check("市值占比分位 ≈ 0.5/n*100 = 1.7（末日为窗口最小值）",
              abs(tgt["mv_pct"] - 1.7) < 0.15, str(tgt["mv_pct"]))
        # 成交额分位窗口 20 日，末日为最大 → (19+0.5)/20*100 = 97.5
        check("成交额占比分位 = 97.5（末日为 20 日窗口最大值）",
              abs(tgt["amt_pct"] - 97.5) < 1e-9, str(tgt["amt_pct"]))
        check("命中项带一级行业名", tgt.get("l1_name") == "目标一级", str(tgt.get("l1_name")))
        check("命中项带当日涨跌幅字段", "pct_chg" in tgt)
    else:
        check("目标行业分位可核对", False, "未被选中，无法核对分位")

    # 方向性核对：常态行业的占比在目标行业缩水时被动上升 → 分位应高；
    # 成交额占比被动下降 → 分位应低。若两个方向搞反，这里立刻暴露。
    norm = next((i for i in (sig.get("industries") or []) if i["code"] == "NORM00"), None)
    check("方向性：占比被动上升的行业 → 市值分位高",
          norm is not None and norm["mv_pct"] > 90, str(norm and norm["mv_pct"]))
    check("方向性：成交额占比被动下降的行业 → 热度分位低",
          norm is not None and norm["amt_pct"] < 10, str(norm and norm["amt_pct"]))
    check("常态行业不命中（高市值分位 + 低热度分位）",
          norm is not None and not (norm["mv_pct"] < isig.MV_PCT_MAX
                                    and norm["amt_pct"] > isig.AMT_PCT_MIN),
          str(norm and {k: norm[k] for k in ("mv_pct", "amt_pct")}))


def _boundary_case(days: int, n_less: int, code: str) -> pd.DataFrame:
    """构造一个「市值分位恰为 (n_less+0.5)/days*100」的行业 + 20 个恒定填充行业。

    市值序列做成 [200]*k + [50]*n_less + [100]，末值 100 前面有 n_less 个更小值，
    于是末值分位 = (n_less + 0.5)/days*100，可精确命中阈值。
    成交额末日拉满 → 热度分位 97.5，保证「只卡市值这一个条件」。
    """
    ds = _dates(days)
    rows = []
    for d in ds:
        for i in range(20):
            rows.append(_row(d, f"F{i:02d}", f"填充{i}", "填充", 1000.0, 10.0))
    mv_seq = [200.0] * (days - n_less - 1) + [50.0] * n_less + [100.0]
    amt_seq = [10.0] * (days - 1) + [1000.0]
    for i, d in enumerate(ds):
        rows.append(_row(d, code, code, "边界", mv_seq[i], amt_seq[i]))
    return pd.DataFrame(rows)


def test_threshold_boundary() -> None:
    """严格不等式：等于阈值不算命中。分位可精确命中到 0.1，故能真卡边界。

    为什么必须精确卡：把 `<` 写成 `<=` 只会让「恰好等于 30.0」的行业被多选进来，
    在实际数据上一年也未必出现一次，普通断言永远抓不到。
    """
    days = 25
    # n_less=7 → 分位 (7+0.5)/25*100 = 30.0，正好等于 mv_pct_max → 必须不命中
    df30 = _boundary_case(days, 7, "EDGE30")
    sig30 = isig.compute_signal(df30)
    check("阈值边界：构造数据能出结论", sig30.get("status") == "ok", str(sig30.get("reason", "")))
    e30 = next((i for i in (sig30.get("industries") or []) if i["code"] == "EDGE30"), None)
    check("阈值边界：市值分位精确等于 30.0",
          e30 is not None and abs(e30["mv_pct"] - 30.0) < 1e-9, str(e30 and e30["mv_pct"]))
    check("阈值边界：热度分位满足另一条件（97.5）",
          e30 is not None and abs(e30["amt_pct"] - 97.5) < 1e-9, str(e30 and e30["amt_pct"]))
    check("阈值边界：mv_pct == 30.0 不命中（严格 <，不是 <=）",
          "EDGE30" not in {p["code"] for p in (sig30.get("picks") or [])},
          str([p["code"] for p in (sig30.get("picks") or [])]))

    # n_less=6 → 分位 26.0 < 30 → 必须命中
    df26 = _boundary_case(days, 6, "EDGE26")
    sig26 = isig.compute_signal(df26)
    e26 = next((i for i in (sig26.get("industries") or []) if i["code"] == "EDGE26"), None)
    check("阈值边界：市值分位精确等于 26.0",
          e26 is not None and abs(e26["mv_pct"] - 26.0) < 1e-9, str(e26 and e26["mv_pct"]))
    check("阈值边界：mv_pct == 26.0 命中（严格 <30 成立）",
          "EDGE26" in {p["code"] for p in (sig26.get("picks") or [])},
          str([p["code"] for p in (sig26.get("picks") or [])]))

    # 成交额阈值边界：末值若恰好是窗口内第 4 大，分位 = (3+0.5)/20*100 = 17.5 不够；
    # 反过来构造「恰为 80.0」需要 20 日窗口里 3 个更大 + 16 个更小 → (16+0.5)/20*100 = 82.5（命中）。
    # 「恰等于 80」在 20 点窗口上不可整除，故用 20 日窗口验证单调方向 + 82.5 命中，
    # 并单列一条规则断言证明比较符是严格的（用源码级检查，见下条）。
    rows = _base(days)
    _spike_rows(rows, days, [("AMT825", 3)])
    sig = isig.compute_signal(pd.DataFrame(rows))
    a = next((i for i in (sig.get("industries") or []) if i["code"] == "AMT825"), None)
    check("阈值边界：末尾排名第 4 的成交额 → 热度分位 82.5",
          a is not None and abs(a["amt_pct"] - 82.5) < 1e-9, str(a and a["amt_pct"]))
    check("阈值边界：82.5 > 80 命中", "AMT825" in {p["code"] for p in (sig.get("picks") or [])})

    src = open("industry_signal.py", encoding="utf-8").read()
    # 命中判据已收口到 hit_mask()（2026-10-12 加展示页时抽出来，避免「页面高亮的」
    # 与「结论里命中的」两处各写一遍不等式而漂移）。所以这条断言改为直接查
    # hit_mask 函数体里的比较符：必须是 < 和 >，且不得出现 <= / >=。
    # 用 AST 而不是字符串匹配 —— 字符串匹配对「换个写法表达同一个 <=」无感。
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "hit_mask"), None)
    ops: list[str] = []
    if fn is not None:
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare):
                ops += [type(o).__name__ for o in node.ops]
    check("阈值边界：命中判据用严格不等式（AST 级，防改写成 <=）",
          fn is not None and "Lt" in ops and "Gt" in ops
          and not ({"LtE", "GtE"} & set(ops)),
          f"hit_mask 里的比较符 = {ops}")
    check("阈值边界：命中规则只有 hit_mask 一处实现（compute_signal 不自己再写一遍）",
          "hit_mask(universe" in src.replace("'", '"'),
          "compute_signal 里没有调用 hit_mask，等于把不等式又抄了一遍")


def test_order_and_limit() -> None:
    days = 30
    rows = _base(days)
    _declining_mv_rising_amt(rows, days)                                  # TGT000 → 97.5
    _spike_rows(rows, days, [("SPK0", 0), ("SPK1", 1), ("SPK2", 2), ("SPK3", 3)])
    sig = isig.compute_signal(pd.DataFrame(rows))

    check("多命中：hits_count 报全量（5）", sig.get("hits_count") == 5, str(sig.get("hits_count")))
    check("多命中：picks 截断为 MAX_PICKS=3", sig.get("picks_count") == 3, str(sig.get("picks_count")))
    amts = [p["amt_pct"] for p in sig["picks"]]
    check("多命中：picks 按 amt_pct 非增排列",
          all(amts[i] >= amts[i + 1] for i in range(len(amts) - 1)), str(amts))

    # 本质断言（与并列无关）：被选中的最低热度分位，不得低于任何落选命中项
    picked = {p["code"] for p in sig["picks"]}
    losers = [i for i in sig["industries"]
              if i["code"] not in picked
              and i["mv_pct"] < isig.MV_PCT_MAX and i["amt_pct"] > isig.AMT_PCT_MIN]
    min_picked = min(amts)
    check("多命中：截断保留的是热度最高的（落选项热度均不高于入选最低值）",
          all(min_picked >= i["amt_pct"] for i in losers),
          f"入选最低 {min_picked} vs 落选 {[i['amt_pct'] for i in losers]}")
    check("多命中：入选的是 97.5、97.5、92.5（并列第一取两个，再取次高）",
          sorted(amts) == [92.5, 97.5, 97.5], str(sorted(amts)))
    check("多命中：分位 82.5 与 87.5 的两个被截断在外",
          "SPK2" not in picked and "SPK3" not in picked, str(sorted(picked)))

    # 同日两次计算必须完全一致（顺序不确定会让推送内容无故变化）
    again = isig.compute_signal(pd.DataFrame(rows))
    check("确定性：同日两次计算结果完全一致",
          json.dumps(sig, sort_keys=True, ensure_ascii=False)
          == json.dumps(again, sort_keys=True, ensure_ascii=False))
    # 并列键必须存在：把全量行业打乱输入顺序，结果仍应一致
    shuffled = pd.DataFrame(rows).sample(frac=1.0, random_state=7).reset_index(drop=True)
    sig_sh = isig.compute_signal(shuffled)
    check("确定性：打乱输入行序后 picks 顺序不变（排序是全序）",
          [p["code"] for p in sig_sh["picks"]] == [p["code"] for p in sig["picks"]],
          f"{[p['code'] for p in sig_sh['picks']]} vs {[p['code'] for p in sig['picks']]}")

    # 但上面这条**不足以**守住「排序必须自带 code 兜底键」：
    # compute_signal 内部先做过 d.sort_values(["code", "date"])，行序早被规范成
    # code 序，所以就算把 code 从排序键里去掉，打乱输入也照样看不出变化
    # （2026-10-12 造错反验第 4 项实测：去掉后主探针全绿 = 假断言）。
    # 所以直接对这个函数本身断言：构造「前两键完全相同」的帧，
    # 要求输出顺序 (a) 等于 code 升序、(b) 与输入行序无关。
    tie = pd.DataFrame([
        {"code": "C", "amt_pct": 97.5, "mv_pct": 10.0},
        {"code": "A", "amt_pct": 97.5, "mv_pct": 10.0},
        {"code": "B", "amt_pct": 97.5, "mv_pct": 10.0},
        {"code": "D", "amt_pct": 90.0, "mv_pct": 20.0},
    ])
    o1 = isig.sort_by_priority(tie)["code"].tolist()
    o2 = isig.sort_by_priority(
        tie.sample(frac=1.0, random_state=3).reset_index(drop=True))["code"].tolist()
    check("排序：前两键并列时按 code 兜底（并列组内 code 升序）",
          o1 == ["A", "B", "C", "D"], str(o1))
    check("排序：输出与输入行序无关（直接测 sort_by_priority，绕开上游 code 排序）",
          o1 == o2, f"{o1} vs {o2}")
    check("排序：并列数据确实造成了并列（元断言，否则上面两条没区分度）",
          tie["amt_pct"].duplicated().any() and tie["mv_pct"].duplicated().any())


# ============================ 3. 样本与窗口降级 ============================

def test_insufficient_samples() -> None:
    days = isig.MIN_TRADE_DAYS - 1
    rows = _declining_mv_rising_amt(_base(days), days)
    sig = isig.compute_signal(pd.DataFrame(rows))
    check(f"样本 {days} 日（<{isig.MIN_TRADE_DAYS}）→ insufficient",
          sig.get("status") == "insufficient", str(sig.get("status")))
    check("样本不足时不出 picks（绝不给不完整结论）",
          not sig.get("picks") and sig.get("picks_count") in (None, 0), str(sig.get("picks")))
    check("样本不足时如实报可用交易日数",
          sig.get("trade_days_available") == days, str(sig.get("trade_days_available")))

    rows_ok = _declining_mv_rising_amt(_base(isig.MIN_TRADE_DAYS), isig.MIN_TRADE_DAYS)
    sig_ok = isig.compute_signal(pd.DataFrame(rows_ok))
    check(f"样本 {isig.MIN_TRADE_DAYS} 日（=下限）→ ok",
          sig_ok.get("status") == "ok", str(sig_ok.get("status")))


def test_window_degradation() -> None:
    days = 40
    rows = _declining_mv_rising_amt(_base(days), days)
    sig = isig.compute_signal(pd.DataFrame(rows))
    check("窗口降级：mv_window 声明 250", sig.get("mv_window") == 250, str(sig.get("mv_window")))
    check("窗口降级：mv_window_actual 报实际 40（不假装满窗）",
          sig.get("mv_window_actual") == 40, str(sig.get("mv_window_actual")))
    check("窗口降级：amt_window_actual = 20（够用则满窗）",
          sig.get("amt_window_actual") == 20, str(sig.get("amt_window_actual")))
    check("窗口降级：trade_days_available 与归档一致",
          sig.get("trade_days_available") == days, str(sig.get("trade_days_available")))
    md, _ = isig.signal_markdown(sig)
    check("窗口降级：推送段落如实写「近 40 日分位」而不是 250",
          "近 40 日分位" in md and "近 250 日分位" not in md, md.splitlines()[2][:60] if md else "")


# ============================ 4. 截面完整性守卫 ============================

def test_section_guard() -> None:
    days = 30
    rows = _declining_mv_rising_amt(_base(days, n_norm=20), days)
    df = pd.DataFrame(rows)
    check("完整性守卫：正常截面全数保留", len(isig.section_valid_dates(df)) == days,
          f"{len(isig.section_valid_dates(df))}/{days}")

    # 把中间某一天砍掉一半行业 → 该日必须被整日剔除
    bad_date = _dates(days)[10]
    kept = df[~((df["date"] == bad_date) & (df["code"].str.startswith("NORM1")))]
    valid = isig.section_valid_dates(kept)
    check("完整性守卫：行业数腰斩的交易日被整日剔除",
          bad_date not in valid, f"剔除后 {len(valid)} 日")
    check("完整性守卫：其它交易日不受影响",
          len(valid) == days - 1, f"{len(valid)}")

    sig = isig.compute_signal(kept)
    check("完整性守卫：剔除后 trade_days_available 相应减少",
          sig.get("trade_days_available") == days - 1, str(sig.get("trade_days_available")))

    # 残缺日不能被当成 as_of
    sig_bad = isig.compute_signal(kept, as_of=bad_date)
    check("完整性守卫：指定残缺日为 as_of → no_data（不静默退回别的日期）",
          sig_bad.get("status") == "no_data", str(sig_bad.get("status")))


def test_missing_values_not_filled() -> None:
    days = 25
    rows = _declining_mv_rising_amt(_base(days), days)
    # 一个行业市值全缺、一个成交额全缺
    for d in _dates(days):
        rows.append(_row(d, "NOMV", "无市值", "缺", None, 10.0))
        rows.append(_row(d, "NOAMT", "无成交额", "缺", 500.0, None))
    sig = isig.compute_signal(pd.DataFrame(rows))
    codes = {p["code"] for p in (sig.get("industries") or [])}
    check("缺失处理：缺市值的行业被剔除（不按 0 参与占比）", "NOMV" not in codes, str(sorted(codes))[:80])
    check("缺失处理：缺成交额的行业被剔除", "NOAMT" not in codes, str(sorted(codes))[:80])
    check("缺失处理：被剔除的行业不会出现在 picks",
          not ({"NOMV", "NOAMT"} & {p["code"] for p in (sig.get("picks") or [])}))

    d = isig._with_shares(pd.DataFrame(rows))
    sums = d.groupby("date")["mv_share"].sum()
    check("缺失处理：剔除后每日占比之和仍 = 100",
          bool((sums - 100).abs().max() < 1e-6), f"最大偏差 {(sums - 100).abs().max():.2e}")


def test_no_history() -> None:
    check("无归档 → no_data", isig.compute_signal(None).get("status") == "no_data")
    check("空表 → no_data", isig.compute_signal(pd.DataFrame()).get("status") == "no_data")
    sig = isig.compute_signal(pd.DataFrame(_base(10)), as_of="20991231")
    check("指定不存在的日期 → no_data（不静默换日期）",
          sig.get("status") == "no_data", str(sig.get("status")))
    check("no_data 时 signal_markdown 不出段落且给出原因",
          isig.signal_markdown(sig) == ("", isig.signal_markdown(sig)[1])
          and isig.signal_markdown(sig)[0] == ""
          and bool(isig.signal_markdown(sig)[1]))


# ============================ 5. 推送段落 ============================

def test_markdown() -> None:
    days = 30
    rows = _declining_mv_rising_amt(_base(days), days)
    sig = isig.compute_signal(pd.DataFrame(rows))
    md, miss = isig.signal_markdown(sig)
    check("段落：有内容且无缺失原因", bool(md) and miss is None, str(miss))
    check("段落：标题带日期与主题", "低市值占比" in md and sig["date"] in md)
    check("段落：命中项带市值占比、成交额占比、两个分位与当日涨跌幅",
          all(k in md for k in ("市值占比", "成交额占比", "分位", "当日")))
    check("段落：说明口径（申万二级 + 窗口长度）",
          "申万二级行业" in md and "近 30 日分位" in md)
    check("段落：含免责声明且明确不含方向判断",
          "不构成投资建议" in md and "不含方向判断" in md)
    banned = ("建议买入", "可以进场", "目标价", "买入", "卖出", "仓位")
    check("段落：不出现操作建议词", not any(b in md for b in banned),
          str([b for b in banned if b in md]))

    # 0 命中必须如实说
    rows_none = _base(days)
    sig_none = isig.compute_signal(pd.DataFrame(rows_none))
    md_none, miss_none = isig.signal_markdown(sig_none)
    check("段落：0 命中仍出段落（今天没有 ≠ 这一段坏了）",
          bool(md_none) and miss_none is None, str(miss_none))
    check("段落：0 命中如实写明「今日无同时满足两条件的行业」",
          "今日无同时满足两条件的行业" in md_none, md_none[:120])

    check("段落：insufficient → 无段落 + 原因（不让读者以为今天没事）",
          isig.signal_markdown({"status": "insufficient", "reason": "样本不足"})[0] == ""
          and "样本不足" in isig.signal_markdown({"status": "insufficient", "reason": "样本不足"})[1])


def test_digest_wiring() -> None:
    import digest as dg

    days = 30
    rows = _declining_mv_rising_amt(_base(days), days)
    sig = isig.compute_signal(pd.DataFrame(rows))

    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "signal.json")
    old = dg.INDUSTRY_SIGNAL_PATH
    dg.INDUSTRY_SIGNAL_PATH = path
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sig, f, ensure_ascii=False)
        res = dg.build_markdown()
        check("digest 接线：摘要里出现行业信号段",
              "低市值占比" in res["markdown"], "")
        check("digest 接线：行业信号段不在 missing 里",
              not any("行业信号" in m for m in res["missing"]), str(res["missing"]))

        os.remove(path)
        res2 = dg.build_markdown()
        check("digest 接线：信号文件缺失 → 整段不出现且计入 missing",
              "低市值占比" not in res2["markdown"]
              and any("行业信号" in m for m in res2["missing"]), str(res2["missing"]))
    finally:
        dg.INDUSTRY_SIGNAL_PATH = old


# ============================ 6. 接线与静态约束 ============================

def test_no_forbidden_imports() -> None:
    banned = {"streamlit", "requests", "urllib", "http", "socket", "aiohttp", "tushare", "cloudscraper"}
    for path in ("industry_signal.py",):
        tree = ast.parse(open(path, encoding="utf-8").read())
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found += [a.name for a in node.names if a.name.split(".")[0] in banned]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in banned:
                    found.append(node.module)
        check(f"计算层 {path} 不 import streamlit / 网络库 / tushare", not found, str(found))


def test_run_daily_wiring() -> None:
    import run_daily as rd

    steps = {s["key"]: s for s in rd.STEPS}
    check("接线：run_daily 步骤表含 industry_signal", "industry_signal" in steps, str(sorted(steps)))
    st = steps.get("industry_signal") or {}
    check("接线：脚本指向 daily_industry_signal.py",
          st.get("script") == "daily_industry_signal.py", str(st.get("script")))
    check("接线：支持回填 250 个交易日",
          st.get("backfill") == rd.BACKFILL_INLINE and st.get("backfill_cap") == 250,
          f"{st.get('backfill')}/{st.get('backfill_cap')}")
    check("接线：超时足够回填（≥1800s）", (st.get("timeout") or 0) >= 1800, str(st.get("timeout")))
    check("接线：新鲜度自检含行业信号",
          any("行业信号" == lbl for lbl, _ in rd.FRESHNESS),
          str([lbl for lbl, _ in rd.FRESHNESS]))
    check("接线：跨产物日期判据含行业信号",
          any(lbl == "行业信号" for lbl, _, _ in rd.DATE_PROBES),
          str([lbl for lbl, _, _ in rd.DATE_PROBES]))

    # 步骤在 sentiment / scorecard / digest 之前仍安全？行业信号与它们无依赖，
    # 但必须排在 digest 之前，否则当日推送读到的是上一份信号。
    keys = [s["key"] for s in rd.STEPS]
    check("接线：industry_signal 排在 digest 之前",
          keys.index("industry_signal") < keys.index("digest"),
          f"{keys.index('industry_signal')} < {keys.index('digest')}")


# ============================ 7. 取数守卫 ============================

def _fake_pro(raw: pd.DataFrame):
    class _P:
        def sw_daily(self, trade_date: str = ""):
            if trade_date:
                return raw[raw["trade_date"].astype(str) == str(trade_date)].copy()
            return raw.copy()
    return _P()


def _ignoring_pro(raw: pd.DataFrame):
    """模拟「参数被静默忽略」：无论请求哪个日期都返回同一份数据。

    这不是臆造的坏行为：真实探测中 sw_daily 的 start/end 组合就出现过
    返回与请求不符的数据。若不校验，一次静默忽略就会把上一交易日的数据
    盖上今天的日期写进归档，而下游全都看不出异常。
    """
    class _P:
        def sw_daily(self, trade_date: str = ""):
            return raw.copy()
    return _P()


def test_fetch_day_guard() -> None:
    import daily_industry_signal as dis

    wl = pd.DataFrame([("801081.SI", "半导体", "电子"), ("801735.SI", "光伏设备", "电力设备")],
                      columns=["code", "name", "l1_name"])
    raw = pd.DataFrame([
        {"ts_code": "801081.SI", "trade_date": "20261009", "name": "半导体",
         "close": 1000.0, "pct_change": 1.5, "amount": 1000000.0,
         "float_mv": 10000000.0, "total_mv": 20000000.0, "vol": 1.0},
        {"ts_code": "801735.SI", "trade_date": "20261009", "name": "光伏设备",
         "close": 2000.0, "pct_change": -0.5, "amount": 2000000.0,
         "float_mv": 20000000.0, "total_mv": 40000000.0, "vol": 2.0},
        {"ts_code": "801999.SI", "trade_date": "20261009", "name": "不在白名单",
         "close": 1.0, "pct_change": 0.0, "amount": 1.0, "float_mv": 1.0,
         "total_mv": 1.0, "vol": 1.0},
    ])
    df, why = dis.fetch_day(_fake_pro(raw), "20261009", wl)
    check("取数：白名单过滤生效（第 3 行被剔除）",
          df is not None and len(df) == 2, why or str(len(df) if df is not None else -1))
    if df is not None:
        check("取数：单位换算 千元→亿元", abs(df.iloc[0]["amount_yi"] - 10.0) < 1e-9,
              str(df.iloc[0]["amount_yi"]))
        check("取数：单位换算 万元→亿元", abs(df.iloc[0]["float_mv_yi"] - 1000.0) < 1e-9,
              str(df.iloc[0]["float_mv_yi"]))
        check("取数：白名单带来 name / l1_name", df.iloc[0]["l1_name"] == "电子",
              str(df.iloc[0]["l1_name"]))

    # 参数被静默忽略：返回的 trade_date 与请求不符 → 必须拒收
    raw_wrong = raw.copy()
    raw_wrong["trade_date"] = "20261008"
    df2, why2 = dis.fetch_day(_ignoring_pro(raw_wrong), "20261009", wl)
    check("取数：返回日期与请求不符 → 拒收（防参数被静默忽略）",
          df2 is None and "不一致" in why2, str(why2)[:90])
    check("取数：拒收原因里点明实际返回的日期（便于排查）",
          "20261008" in why2, str(why2)[:90])

    empty = pd.DataFrame(columns=raw.columns)
    df3, why3 = dis.fetch_day(_fake_pro(empty), "20261009", wl)
    check("取数：空表 → 返回原因而不是空 DataFrame",
          df3 is None and "空表" in why3, str(why3)[:60])

    raw_other = raw.copy()
    raw_other["ts_code"] = "899999.SI"
    df4, why4 = dis.fetch_day(_fake_pro(raw_other), "20261009", wl)
    check("取数：白名单内 0 行 → 明确报原因（编码口径不符）",
          df4 is None and "0 行" in why4, str(why4)[:60])


def test_section_reject_and_cleanup() -> None:
    import daily_industry_signal as dis
    import daily_sector_rotation as dsr

    wl_n = 124
    check("残缺拒收：123 个行业 < 124×0.9 → False",
          not dis.section_ok(pd.DataFrame({"c": range(110)}), wl_n), "")
    check("残缺拒收：112 个行业 = 124×0.9 → True（边界含）",
          dis.section_ok(pd.DataFrame({"c": range(112)}), wl_n), "")
    check("残缺拒收：111 个行业 → False",
          not dis.section_ok(pd.DataFrame({"c": range(111)}), wl_n), "")
    check("残缺拒收：124 个行业满额 → True",
          dis.section_ok(pd.DataFrame({"c": range(124)}), wl_n), "")

    # 同日替换的幂等性 + 残缺日的清理
    hist = pd.DataFrame([
        {"date": "20261009", "code": "A"}, {"date": "20261009", "code": "B"},
        {"date": "20261008", "code": "A"},
    ])
    day = pd.DataFrame([{"date": "20261009", "code": "A"}, {"date": "20261009", "code": "C"}])
    merged = dis.merge_history(hist, day)
    same_day = merged[merged["date"] == "20261009"]
    check("归档：同日替换（幂等），不产生重复行",
          len(same_day) == 2 and set(same_day["code"]) == {"A", "C"}, str(len(same_day)))
    check("归档：其它日期不受影响", len(merged[merged["date"] == "20261008"]) == 1)

    dropped = dis.drop_date(merged, "20261009")
    check("归档：drop_date 精确移除指定日期",
          len(dropped) == 1 and dropped.iloc[0]["date"] == "20261008", str(len(dropped)))

    # 板块归档的同类清理函数（真实写文件行为）
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "history.csv")
    old = dsr.HISTORY_PATH
    dsr.HISTORY_PATH = path
    try:
        pd.DataFrame([
            {"date": "20261009", "code": "BK1", "name": "残缺日", "pct_chg": 1.0},
            {"date": "20261010", "code": "BK2", "name": "完整日", "pct_chg": 2.0},
        ]).to_csv(path, index=False, encoding="utf-8")
        n = dsr.drop_date_from_history("20261009")
        left = pd.read_csv(path, dtype={"code": str})
        check("板块归档：drop_date_from_history 移除目标日并写回",
              n == 1 and len(left) == 1 and left.iloc[0]["date"] == 20261010, f"移除 {n} 行")
        n2 = dsr.drop_date_from_history("20991231")
        check("板块归档：目标日不存在时不写坏文件", n2 == 0, str(n2))
    finally:
        dsr.HISTORY_PATH = old


def test_sector_rotation_reject_wiring() -> None:
    """板块轮动跑批必须在 incomplete 时不写归档（源码级断言，防回归）。"""
    src = open("daily_sector_rotation.py", encoding="utf-8").read()
    tree = ast.parse(src)

    main_fn = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
    check("板块轮动：main 存在", main_fn is not None)
    if main_fn is None:
        return

    # 找出 `if not skipped and incomplete:` 分支，其内部不得出现 append_history / to_csv
    guard_ok = False
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.dump(node.test)
        if "incomplete" not in test_src:
            continue
        body_src = "".join(ast.dump(b) for b in node.body)
        calls = "append_history" in body_src or "to_csv" in body_src
        if not calls and "drop_date_from_history" in body_src:
            guard_ok = True
    check("板块轮动：incomplete 分支只清理、不写归档（残缺截面拒收）", guard_ok)

    # fetch_snapshot 必须分轮重试失败页，而不是「连续两页失败即放弃」
    snap = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "fetch_snapshot"), None)
    check("板块轮动：fetch_snapshot 存在", snap is not None)
    if snap is not None:
        dump = ast.dump(snap)
        check("板块轮动：fetch_snapshot 采用分轮重试（含 PAGE_ROUNDS）",
              "PAGE_ROUNDS" in dump)
        check("板块轮动：fetch_snapshot 返回失败页列表（可在日志中点名）",
              "failed_pages" in dump or "pending" in dump)
    check("板块轮动：fetch_page 支持轮次偏移（不同轮从不同 CDN 起步）",
          "host_offset" in ast.dump(tree))


def test_page_data_helpers() -> None:
    """展示页专用的纯装配函数：universe_frame / industry_series。

    页面本身另有 AppTest 探针（tools_probe_industry_signal_page.py）跑真渲染；
    这里只验计算层这两个纯函数，因为它们决定了「页面上标红的」是不是
    「结论里命中的」——这两者一旦漂移，页面看起来一切正常。
    """
    rows = _base(40)
    _declining_mv_rising_amt(rows, 40)
    _spike_rows(rows, 40, [("SPK1", 1), ("SPK2", 2), ("SPK3", 3)])
    hist = pd.DataFrame(rows)
    sig = isig.compute_signal(hist)

    # ---- universe_frame ----
    uf = isig.universe_frame(sig)
    check("装配：ok 结论能装配出全量行业表",
          uf is not None and len(uf) == sig["industry_count"],
          f"{None if uf is None else len(uf)} vs {sig['industry_count']}")
    if uf is None:
        return
    check("装配：命中标记数与 hits_count 严格相等",
          int(uf["is_hit"].sum()) == sig["hits_count"],
          f"{int(uf['is_hit'].sum())} vs {sig['hits_count']}")
    # 注意不能反过来要求「命中集合 == picks 集合」：picks 被 MAX_PICKS 截断过，
    # 而页面必须标出**全部**命中（否则图上会漏掉几个真正的命中行业）。
    # 正确的关系是 picks ⊂ 命中集合，且多出来的那部分正是被截断的那些。
    hit_codes = set(uf.loc[uf["is_hit"], "code"])
    pick_codes = {p["code"] for p in sig["picks"]}
    check("装配：picks 必须是命中集合的子集",
          pick_codes <= hit_codes, str(pick_codes - hit_codes))
    check("装配：命中集合里比 picks 多出来的正好是被截断的那些",
          len(hit_codes) - len(pick_codes) == sig["hits_count"] - len(sig["picks"]),
          f"{len(hit_codes)} vs picks {len(pick_codes)} / hits {sig['hits_count']}")
    check("装配：命中数多于 max_picks（元断言：否则「标全部命中」这条没被覆盖）",
          sig["hits_count"] > len(sig["picks"]),
          f"hits={sig['hits_count']} picks={len(sig['picks'])}")
    # 排序：命中的必须整体排在最前（页面按行序渲染，未命中的混进来最扎眼）
    hit_flags = uf["is_hit"].tolist()
    check("装配：命中行全部排在前面（行序 = 优先级序）",
          hit_flags == sorted(hit_flags, reverse=True), str(hit_flags))

    # 阈值必须跟着 sig 走 —— 这条是「页面阈值不许硬编码」在计算层的对应断言
    tampered = json.loads(json.dumps(sig))
    tampered["thresholds"]["mv_pct_max"] = 0.0
    tampered["thresholds"]["amt_pct_min"] = 100.0
    uf_t = isig.universe_frame(tampered)
    check("装配：命中标记跟着 thresholds 变（阈值不是写死的常量）",
          uf_t is not None and int(uf_t["is_hit"].sum()) == 0,
          str(None if uf_t is None else int(uf_t["is_hit"].sum())))
    check("装配：原始 sig 未被改动（装配是纯函数，不改调用方数据）",
          sig["thresholds"]["mv_pct_max"] == isig.MV_PCT_MAX)

    # 降级：非 ok 一律 None，页面据此走明确分支而不是渲染空表
    check("装配：no_data 返回 None（不渲染空表冒充「今天没有」）",
          isig.universe_frame({"status": "no_data"}) is None)
    check("装配：insufficient 返回 None",
          isig.universe_frame({"status": "insufficient"}) is None)
    check("装配：None 输入返回 None", isig.universe_frame(None) is None)
    check("装配：ok 但缺 industries 字段返回 None",
          isig.universe_frame({"status": "ok"}) is None)
    check("装配：ok 但 industries 为空列表返回 None",
          isig.universe_frame({"status": "ok", "industries": []}) is None)

    # 缺列补 NA，绝不补 0（补 0 会在页面上显示成「占比确实是 0%」）
    stripped = json.loads(json.dumps(sig))
    for r in stripped["industries"]:
        r.pop("mv_share", None)
        r.pop("amt_share", None)
    uf_s = isig.universe_frame(stripped)
    check("装配：缺列补 NA 而不是 0",
          uf_s is not None and uf_s["mv_share"].isna().all()
          and not (uf_s["mv_share"].fillna(-1) == 0).any())
    check("装配：缺列时命中标记仍可用（分位在，判定不依赖占比）",
          uf_s is not None and int(uf_s["is_hit"].sum()) == sig["hits_count"])

    # 边界行业（分位恰好等于阈值）在页面上也不许被标成命中
    b = isig.compute_signal(_boundary_case(25, 7, "LOWEXACT"))
    uf_b = isig.universe_frame(b)
    check("装配：恰等于阈值的边界行业不被标成命中（严格不等式与结论一致）",
          uf_b is not None and not bool(
              uf_b.loc[uf_b["code"] == "LOWEXACT", "is_hit"].iloc[0]))

    # ---- industry_series ----
    ser = isig.industry_series(hist, "TGT000")
    check("走势：能取到目标行业序列", ser is not None and len(ser) > 0)
    if ser is None or ser.empty:
        return
    ind = next((i for i in sig["industries"] if i["code"] == "TGT000"), None)
    check("走势：目标行业在结论的全量名单里（元断言，否则下面的比对没意义）",
          ind is not None)
    last = ser.iloc[-1]
    check("走势：末点日期 == 结论日期", str(last["date"]) == sig["date"])
    # 结论里落盘的 mv_pct / amt_pct 是 round(..., 1)，而中位秩会产出 1.25 / 1.75
    # 这类值（(n_less+0.5)/n*100 的分母形态），round(1.25, 1) 走银行家舍入得 1.2。
    # 所以两条路径要在**落盘精度**上比：先各自 round 到 1 位再比，而不是直接相减。
    check("走势：末点市值分位 == 结论里的市值分位（同口径、同精度）",
          ind is not None and round(float(last["mv_pct"]), 1) == ind["mv_pct"],
          f"{round(float(last['mv_pct']), 1)} vs {ind and ind['mv_pct']}")
    check("走势：末点热度分位 == 结论里的热度分位（同口径、同精度）",
          ind is not None and round(float(last["amt_pct"]), 1) == ind["amt_pct"],
          f"{round(float(last['amt_pct']), 1)} vs {ind and ind['amt_pct']}")
    check("走势：窗口长度如实上报（40 日归档 → 末点窗口 40，不假装 250）",
          int(last["mv_window_len"]) == 40 and int(last["amt_window_len"]) == 20,
          f"{int(last['mv_window_len'])} / {int(last['amt_window_len'])}")
    check("走势：最早那个点的窗口不足（如实体现归档起点限制）",
          int(ser["mv_window_len"].iloc[0]) < int(ser["mv_window_len"].max()))

    # 未知行业 / 空历史 → None（页面据此显示「没有可用序列」，不画空图）
    check("走势：未知 code 返回 None",
          isig.industry_series(hist, "NOT_EXIST") is None)
    check("走势：None 历史返回 None", isig.industry_series(None, "TGT000") is None)
    check("走势：空 code 返回 None", isig.industry_series(hist, "") is None)

    # ---- 前视偏差（本组最要紧的一条）----
    # 先只用前 40 个交易日算一遍；再追加 20 个交易日的新数据（**不改动前 40 日
    # 的任何一行**），重算。已有的 40 个点的分位必须逐点完全不变。
    # 如果实现是「先在整段序列上算分位再截取」，追加未来数据会让历史点全部改变
    # —— 曲线当时根本得不到那样的值，而图上没有任何迹象。
    #
    # 追加的那 20 天必须补满**同样多的行业**：只补 TGT000 会让那些日子的截面
    # 从 23 个掉到 20 个，低于中位数的 90%，被截面守卫整日剔除 —— 那样这个用例
    # 就退化成「只比前 40 天」，第一版正是这么写的，len(ser80) 没有变长。
    ds_all = _dates(60)
    rows60 = rows
    extra = []
    for d in ds_all[40:]:
        for i in range(19):
            extra.append(_row(d, f"NORM{i:02d}", f"常态{i}", "常态一级", 1000.0, 10.0))
        for code in ("SPK1", "SPK2", "SPK3"):
            extra.append(_row(d, code, code, "目标一级", 500.0, 10.0))
        extra.append(_row(d, "TGT000", "目标行业", "目标一级", 400.0, 10.0))
    ser60 = isig.industry_series(pd.DataFrame(rows60), "TGT000")
    ser80 = isig.industry_series(pd.DataFrame(rows60 + extra), "TGT000")
    check("走势：追加未来数据后仍能算出（元断言）",
          ser60 is not None and ser80 is not None and len(ser80) > len(ser60),
          f"{None if ser60 is None else len(ser60)} → "
          f"{None if ser80 is None else len(ser80)}")
    if ser60 is not None and ser80 is not None:
        common = ser60["date"].tolist()
        a60 = ser60.set_index("date")["mv_pct"].round(10).to_dict()
        a80 = ser80.set_index("date")["mv_pct"].round(10).to_dict()
        diffs = [d for d in common if a60.get(d) != a80.get(d)]
        check("走势：市值分位无前视偏差（追加未来数据后已有各点逐点不变）",
              not diffs, f"{len(diffs)} 个点变了，例如 {diffs[:3]}")
        b60 = ser60.set_index("date")["amt_pct"].round(10).to_dict()
        b80 = ser80.set_index("date")["amt_pct"].round(10).to_dict()
        diffs2 = [d for d in common if b60.get(d) != b80.get(d)]
        check("走势：热度分位无前视偏差（同上）",
              not diffs2, f"{len(diffs2)} 个点变了，例如 {diffs2[:3]}")
        check("走势：分位不是常量（元断言：否则「逐点不变」恒成立、断言无区分度）",
              len({round(float(v), 6) for v in ser60["mv_pct"]}) > 3,
              str(len({round(float(v), 6) for v in ser60["mv_pct"]})))


def test_architecture_layers() -> None:
    """计算层必须能被脱 runtime 单测：不得 import 展示层或跑批层。"""
    tree = ast.parse(open("industry_signal.py", encoding="utf-8").read())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    check("分层：计算层不 import 跑批/展示模块",
          not (mods & {"daily_industry_signal", "streamlit", "page_sector_rotation"}),
          str(sorted(mods)))


def main() -> int:
    for fn in (test_percentile_rank, test_hit_and_order, test_threshold_boundary,
               test_order_and_limit, test_insufficient_samples, test_window_degradation,
               test_section_guard, test_missing_values_not_filled, test_no_history,
               test_markdown, test_digest_wiring, test_no_forbidden_imports,
               test_run_daily_wiring, test_fetch_day_guard,
               test_section_reject_and_cleanup, test_sector_rotation_reject_wiring,
               test_page_data_helpers, test_architecture_layers):
        print(f"\n=== {fn.__name__} ===")
        fn()

    total = len(FAIL)
    print(f"\n{'=' * 50}\n共 {'通过' if not total else '失败'}：{total} 项失败")
    if total:
        for f in FAIL:
            print(f"  ❌ {f}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
