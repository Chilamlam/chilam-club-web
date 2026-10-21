# -*- coding: utf-8 -*-
"""
晚间复盘与次日交易计划全链路自检（tools_probe_evening_plan.py）

覆盖六大维度：
  1. 架构分层铁律：evening_plan.py 纯计算无副作用、无 replace(0, pd.NA)；page_evening_plan.py 只读产物不联网
  2. 手算对账（#125 十点前首板溢价差、#065 中位股亏钱扩散、#097 双冰强反、#081 退潮D3、#145 周五二板）
  3. 六大选股池手算单测（#125 首板池、#033/#049 二板与多龙中庸 PK、#080 狗啃板、#128 MA6/MA12 首阴、#169 揉搓线、#077/#103 强趋势补涨）
  4. 四维风控排雷门禁单测（#096 换手率生死线、#004 一字断层、#032 午后偷袭板、#176 双上影「丄」字顶）与个人池子共振诊断
  5. 181 篇方法论知识库、落盘产物、消化摘要接入、前端页面门禁与合规词扫描
  6. 反向突变自检（mutate 验证断言真能抓出退化，而非恒真摆设）
"""
from __future__ import annotations

import ast
import json
import os
import sys

import pandas as pd

import digest
import evening_plan as ep

PASS = 0
FAIL = 0


def ck(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ================= 1. 架构分层与代码纯净度 =================

def check_layer_purity() -> None:
    print("\n=== 1. 架构分层与代码纯净度 ===")
    ep_src = read_text("evening_plan.py")
    tree = ast.parse(ep_src)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                imports.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    forbidden_ep = {"streamlit", "tushare", "akshare", "urllib", "requests", "database", "auth"}
    ck(not (imports & forbidden_ep), f"evening_plan.py 纯计算层零外部/UI依赖（实际 imports={sorted(imports)}）")
    ck(not any(m.startswith(("daily_", "page_")) for m in imports), "evening_plan.py 不反向依赖跑批或页面模块")
    ck("replace(0, pd.NA)" not in ep_src and "replace(0, np.nan)" not in ep_src,
       "evening_plan.py 严禁使用 replace(0, pd.NA) 触发未来版本递归 Bug")

    ui_src = read_text("page_evening_plan.py")
    ui_tree = ast.parse(ui_src)
    ui_imports = set()
    for node in ast.walk(ui_tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                ui_imports.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            ui_imports.add(node.module.split(".")[0])
    forbidden_ui = {"tushare", "akshare", "urllib", "requests"}
    ck(not (ui_imports & forbidden_ui), f"page_evening_plan.py 展示层不直接联网取数（实际 imports={sorted(ui_imports)}）")
    ck(not any(m.startswith("daily_") for m in ui_imports), "page_evening_plan.py 不 import 跑批脚本")


# ================= 2. 情绪细节与周期法则手算对账 =================

def check_sentiment_and_laws() -> None:
    print("\n=== 2. #125 首板时间切分、#065 中位股亏钱扩散与周期日历法则手算对账 ===")
    # 构造昨日(20261015)与今日(20261016)涨停历史归档：
    #   昨日10:00前首板：000001 (09:32, 今日晋级2板, +10%), 000002 (09:50, 未晋级, +4%)
    #     -> base=2, promoted=1, promo_rate=0.5, median_pct=(10+4)/2 = +7.0%
    #   昨日10:00后首板：000003 (10:30, 未晋级, -3%), 000004 (14:20, 未晋级, -5%)
    #     -> base=2, promoted=0, promo_rate=0.0, median_pct=(-3+-5)/2 = -4.0%
    ladder_hist = pd.DataFrame([
        {"date": "20261015", "code": "000001", "name": "早盘龙A", "limit_times": 1, "first_time": "09:32:00"},
        {"date": "20261015", "code": "000002", "name": "早盘龙B", "limit_times": 1, "first_time": "09:50:00"},
        {"date": "20261015", "code": "000003", "name": "午盘跟风C", "limit_times": 1, "first_time": "10:30:00"},
        {"date": "20261015", "code": "000004", "name": "尾盘偷袭D", "limit_times": 1, "first_time": "14:20:00"},
        {"date": "20261016", "code": "000001", "name": "早盘龙A", "limit_times": 2, "first_time": "09:40:00"},
    ])
    pct_map = {"000001": 10.0, "000002": 4.0, "000003": -3.0, "000004": -5.0}
    res = ep.first_board_time_split(ladder_hist, "20261016", "20261015", pct_map)
    ck(res["status"] == "ok", "#125 首板分时切分返回 ok")
    pre = res["pre_10am"]
    post = res["post_10am"]
    ck(pre["base"] == 2 and post["base"] == 2, "#125 早/晚首板计数准确 (2 vs 2)")
    ck(pre["promo_rate"] == 0.5 and post["promo_rate"] == 0.0, "#125 早/晚首板晋级率手算一致 (50% vs 0%)")
    ck(pre["median_pct"] == 7.0 and post["median_pct"] == -4.0, "#125 早/晚首板次日溢价中位数手算一致 (+7.0% vs -4.0%)")
    ck(res["promo_rate_spread"] == 0.5 and res["median_pct_spread"] == 11.0,
       "#125 早盘首板超额晋级率 +50pp、超额溢价 +11.00pp")

    # 构造 #065 中位股亏钱效应扩散：昨日有 4 板、3 板中位股共 2 只，今日 1 只跌 -7.2%，1 只涨 +2.0% -> 大面率 50% >= 40% 触发 warning
    hist_mid = pd.DataFrame([
        {"date": "20261015", "code": "600001", "name": "高标龙", "limit_times": 5, "first_time": "09:30:00"},
        {"date": "20261015", "code": "600002", "name": "中位A", "limit_times": 4, "first_time": "09:45:00"},
        {"date": "20261015", "code": "600003", "name": "中位B", "limit_times": 3, "first_time": "10:15:00"},
        {"date": "20261015", "code": "600004", "name": "低位C", "limit_times": 2, "first_time": "10:20:00"},
        {"date": "20261016", "code": "600001", "name": "高标龙", "limit_times": 5, "first_time": "09:35:00"},
    ])
    mid_res = ep.mid_tier_loss_diffusion(
        hist_mid, "20261016", "20261015",
        {"600001": 10.0, "600002": -7.2, "600003": 2.0, "600004": 1.0},
    )
    ck(mid_res["status"] == "ok" and mid_res["mid_n"] == 2, "#065 高中低梯队切分准确 (中位 3~5 板共 2 只)")
    ck(mid_res["big_loss_rate"] == 0.5 and mid_res["warning"] is True,
       "#065 中位股大面率 50% >= 40% 准确触发退潮前兆警报")

    # 构造 #097 双冰强反与 #145 周五二板日历效应（2026-10-16 为周五）
    # 构造连续两日 1进2 晋级率极低（<12%，均落入冰点期）的 ladder_hist
    ice_rows = []
    for i in range(10):
        ice_rows.append({"date": "20261014", "code": f"0010{i:02d}", "name": f"A{i}", "limit_times": 1, "first_time": "09:40:00"})
        ice_rows.append({"date": "20261015", "code": f"0020{i:02d}", "name": f"B{i}", "limit_times": 1, "first_time": "09:40:00"})
        ice_rows.append({"date": "20261016", "code": f"0030{i:02d}", "name": f"C{i}", "limit_times": 1, "first_time": "09:40:00"})
    # 加上前面测试的 2 只中位股大面以同步触发 #065
    ice_rows.extend([
        {"date": "20261015", "code": "600002", "name": "中位A", "limit_times": 4, "first_time": "09:45:00"},
        {"date": "20261015", "code": "600003", "name": "中位B", "limit_times": 3, "first_time": "10:15:00"},
    ])
    ice_hist = pd.DataFrame(ice_rows)
    derived_fri = {
        "prev_date": "20261015",
        "phase": {"status": "ok", "phase": "冰点期"},
        "ladder_gap": {"total": 25},
    }
    laws_fri = ep.detect_cycle_and_calendar_laws(
        trade_date="20261016",
        derived=derived_fri,
        ladder_hist=ice_hist,
        breadth_hist=pd.DataFrame([
            {"date": "20261015", "limit_down": 30},
            {"date": "20261016", "limit_down": 6},
        ]),
        pct_map={"600002": -7.5, "600003": -6.0},
    )
    ck(laws_fri["weekday"] == "周五", "日期 20261016 正确识别为周五")
    rule_ids = {r["id"] for r in laws_fri["active_laws"]}
    ck("097" in rule_ids, "连续两日冰点期 → 准确触发 #097 双冰强反黄金节点")
    ck("145" in rule_ids and "121" in rule_ids, "周五准确触发 #145 周五换手二板空间龙与 #121 周五高位分歧效应")
    ck("065" in rule_ids, "中位股大面率超标同步合入法则提醒 (#065)")
    ck("158" in rule_ids, "跌停潮由 30 家骤降至 6 家准确触发 #158 恐慌衰竭信号")

    # 构造 #081 退潮 D3 伪反抽陷阱（周一 2026-10-19：前日冰点 D1 -> 昨日冰点/退潮 D2 -> 今日退潮/分歧）
    d3_rows = []
    for i in range(10):
        d3_rows.append({"date": "20261014", "code": f"0010{i:02d}", "name": f"A{i}", "limit_times": 1, "first_time": "09:40:00"})
        d3_rows.append({"date": "20261015", "code": f"0020{i:02d}", "name": f"B{i}", "limit_times": 1, "first_time": "09:40:00"})
        d3_rows.append({"date": "20261016", "code": f"0030{i:02d}", "name": f"C{i}", "limit_times": 1, "first_time": "09:40:00"})
        d3_rows.append({"date": "20261019", "code": f"0040{i:02d}", "name": f"D{i}", "limit_times": 1, "first_time": "09:40:00"})
    # 让 20261019 有 2/10 = 20% 晋级率（落入退潮/分歧期）
    d3_rows.append({"date": "20261019", "code": "003000", "name": "C0", "limit_times": 2, "first_time": "09:40:00"})
    d3_rows.append({"date": "20261019", "code": "003001", "name": "C1", "limit_times": 2, "first_time": "09:40:00"})
    laws_d3 = ep.detect_cycle_and_calendar_laws(
        trade_date="20261019",
        derived={"prev_date": "20261016", "phase": {"status": "ok", "phase": "退潮期"}, "ladder_gap": {"total": 35}},
        ladder_hist=pd.DataFrame(d3_rows),
    )
    d3_ids = {r["id"] for r in laws_d3["active_laws"]}
    ck("081" in d3_ids, "前日冰点→昨日冰点→今日弱修分歧 准确触发 #081 退潮期 D3 节点预警")
    ck("027" in d3_ids, "周一准确触发 #027 周末/节后首日开盘反向定律")


# ================= 3. 六大选股池手算单测 =================

def check_six_strategy_pools() -> None:
    print("\n=== 3. 六大晚间选股池手算单测 ===")
    ladder = {
        "date": "2026-10-16",
        "stocks": [
            # Pool 1 候选：09:35 首板，低价小盘
            {"code": "002001", "name": "十点首板A", "limit_times": 1, "first_time": "09:35:00", "reason": "固态电池 | 炸板0次 | 封单1.20亿", "industry": "固态电池"},
            # 10:15 首板（不应进 Pool 1，但应因固态电池有趋势锚点进入 Pool 6）
            {"code": "002002", "name": "十点后首板B", "limit_times": 1, "first_time": "10:15:00", "reason": "固态电池 | 炸板0次 | 封单0.80亿", "industry": "固态电池"},
            # Pool 2 候选：换手二板 vs 一字二板
            {"code": "002003", "name": "换手二板龙", "limit_times": 2, "first_time": "09:45:00", "reason": "智能驾驶 | 炸板1次 | 封单1.50亿", "industry": "智能驾驶"},
            {"code": "002004", "name": "一字二板股", "limit_times": 2, "first_time": "09:25:00", "reason": "智能驾驶 | 炸板0次 | 封单3.00亿", "industry": "智能驾驶"},
            # Pool 2 多龙同台 3 板 PK（3 只同身位检验 #049 中庸之道）
            {"code": "002005", "name": "三板龙甲", "limit_times": 3, "first_time": "09:35:00", "reason": "算力 | 炸板0次 | 封单2.00亿", "industry": "算力"},
            {"code": "002006", "name": "三板龙乙", "limit_times": 3, "first_time": "09:42:00", "reason": "算力 | 炸板1次 | 封单1.60亿", "industry": "算力"},
            {"code": "002007", "name": "三板龙丙", "limit_times": 3, "first_time": "10:05:00", "reason": "算力 | 炸板2次 | 封单1.10亿", "industry": "算力"},
            # Pool 3 狗啃板候选：zbc=3 且光刻胶板块独苗
            {"code": "002008", "name": "冰点狗啃龙", "limit_times": 2, "first_time": "09:50:00", "reason": "光刻胶 | 炸板3次 | 封单0.90亿", "industry": "光刻胶"},
            # 北交所干扰项（92/83/43/87 段，必须被全部剔除，不得入池）
            {"code": "920096", "name": "北交首板A", "limit_times": 1, "first_time": "09:32:00", "reason": "固态电池 | 炸板0次 | 封单0.50亿", "industry": "固态电池"},
            {"code": "831071", "name": "北交二板B", "limit_times": 2, "first_time": "09:40:00", "reason": "智能驾驶 | 炸板3次 | 封单0.60亿", "industry": "核聚变"},
        ],
    }
    snap_map = {
        "002001": {"code": "002001", "name": "十点首板A", "industry": "固态电池", "close": 9.5, "circ_mv_yi": 28.0},
        "002002": {"code": "002002", "name": "十点后首板B", "industry": "固态电池", "close": 14.2, "circ_mv_yi": 45.0},
        "002009": {"code": "002009", "name": "冲板炸板股", "industry": "机器人", "close": 10.5, "circ_mv_yi": 35.0},
    }
    turnover_map = {"002001": 8.5, "002002": 12.0, "002003": 22.0, "002004": 1.2, "002008": 31.0}

    # Pool 1: 10:00前首板
    p1 = ep.build_pool_10am_first(ladder, snap_map, turnover_map)
    ck(p1["count"] == 1 and p1["items"][0]["code"] == "002001",
       "Pool 1 严格只收 10:00 前首板 (002001 入选，10:15 的 002002 排除)")
    ck("#030 低价(<12元)" in p1["items"][0]["tags"] and "#030 小市值(<50亿)" in p1["items"][0]["tags"],
       "Pool 1 准确叠加 #030 低价小市值标签")

    # Pool 2: 二板定龙 & 多龙中庸 PK（weekday_idx=4 即周五）
    p2 = ep.build_pool_2b_and_pk(ladder, snap_map, turnover_map, weekday_idx=4)
    p2_map = {r["code"]: r for r in p2["items"]}
    ck(p2_map["002003"]["is_turnover_board"] is True and p2_map["002004"]["is_turnover_board"] is False,
       "Pool 2 准确区分 #033 换手二板与 #004 一字二板")
    ck(any("#145 周五二板空间龙候选" in t for t in p2_map["002003"]["tags"]),
       "Pool 2 周五自动为换手二板挂载 #145 周五二板空间龙候选标签")
    ck(len(p2["pk_groups"]) == 1 and p2["pk_groups"][0]["count"] == 3 and "中庸选股法" in p2["pk_groups"][0]["rule_hint"],
       "Pool 2 对 ≥3 只同身位高标自动生成 #049《中庸》去最高去最低观察提示")

    # Pool 3: 冰点狗啃板与 #035 炸板反包
    # 002009 昨日曾涨停，今日盘中触及涨停(prev_c=10.0, high=11.0)但收盘回落至 10.5(+5.0%)
    k_broken = pd.DataFrame([
        {"date": "20261015", "open": 9.5, "high": 10.0, "low": 9.4, "close": 10.0},
        {"date": "20261016", "open": 10.2, "high": 11.0, "low": 10.1, "close": 10.5},
    ])
    hist_for_p3 = pd.DataFrame([{"date": "20261015", "code": "002009", "name": "冲板炸板股", "limit_times": 1}])
    p3 = ep.build_pool_ice_rebound(ladder, snap_map, turnover_map, kline_map={"002009": k_broken}, ladder_hist=hist_for_p3)
    p3_map = {r["code"]: r for r in p3["items"]}
    ck("002008" in p3_map and "狗啃板(板块独苗)" in p3_map["002008"]["pattern_type"],
       "Pool 3 准确捕获 zbc>=2 且板块唯一涨停的正宗狗啃板(002008)")
    ck("002009" in p3_map and "#035 触板炸板" in p3_map["002009"]["pattern_type"],
       "Pool 3 准确捕获 #035 盘中触板炸板收红盘的反包候选股(002009)")

    # Pool 4: 人气龙头首阴回踩 MA6 / MA12
    hist_ladder = pd.DataFrame([
        {"date": "20261015", "code": "002010", "name": "人气断板龙", "limit_times": 3, "industry": "低空经济"},
    ])
    # 构造 12 根日 K，最后 6 根均价约 10.01，今日收盘 10.06（距 MA6 +0.5%，处于回踩支撑带）
    k_ma6 = pd.DataFrame([
        {"date": f"202610{i:02d}", "open": 9.5, "high": 9.8, "low": 9.4, "close": 9.5} for i in range(1, 7)
    ] + [
        {"date": f"202610{i:02d}", "open": 10.0, "high": 10.4, "low": 9.9, "close": 10.0} for i in range(7, 12)
    ] + [
        {"date": "20261016", "open": 10.3, "high": 10.5, "low": 10.0, "close": 10.06}
    ])
    p4 = ep.build_pool_ma6_pullback("20261016", ladder, hist_ladder, snap_map, turnover_map, kline_map={"002010": k_ma6})
    ck(p4["count"] == 1 and p4["items"][0]["code"] == "002010",
       "Pool 4 准确捕获近期>=2板且今日断板回踩 MA6 的人气龙头(002010)")
    ck("贴近MA6" in p4["items"][0]["support_anchor"], f"Pool 4 支撑位标签含贴近MA6（实际={p4['items'][0]['support_anchor']}）")

    # Pool 5: #169 揉搓线洗盘识别
    # T-1 长上影：open=10.0, close=10.1, high=10.6 (上影0.5/0.62=80.6%>=45%), low=9.98 (下影0.02/0.62=3.2%<=35%)
    # T 长下影：open=10.1, close=10.2, high=10.25 (上影0.05/0.50=10%<=35%), low=9.75 (下影0.35/0.50=70%>=45%)
    k_rub = pd.DataFrame([
        {"date": f"202610{i:02d}", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9} for i in range(1, 11)
    ] + [
        {"date": "20261015", "open": 10.0, "high": 10.6, "low": 9.98, "close": 10.1},
        {"date": "20261016", "open": 10.1, "high": 10.25, "low": 9.75, "close": 10.2},
    ])
    rub = ep.detect_rubbing_line(k_rub)
    ck(rub is not None and rub["prev_upper_ratio"] >= 45.0 and rub["curr_lower_ratio"] >= 45.0,
       "#169 揉搓线识别器准确捕获「前日长上影试盘+今日长下影探底回升」组合")

    strong_df = pd.DataFrame([
        {"ts_code": "002011.SZ", "name": "趋势揉搓股", "细分行业": "固态电池", "RPS_50": 95.0},
        {"ts_code": "002012.SZ", "name": "固态趋势锚B", "细分行业": "固态电池", "RPS_50": 92.0},
    ])
    p5 = ep.build_pool_rubbing_and_breakout("20261016", strong_df, pd.DataFrame(), snap_map, turnover_map, kline_map={"002011": k_rub})
    ck(p5["count"] == 1 and p5["items"][0]["code"] == "002011" and "#169 揉搓线" in p5["items"][0]["pattern"],
       "Pool 5 准确收录触发 #169 揉搓线的趋势股 002011")

    # Pool 6: 强趋势板块低位首板补涨（固态电池板块有 002011, 002012 两只高 RPS 趋势锚点，且今日有首板 002001, 002002；920096 虽同属固态电池但属北交所必须被剔除）
    p6 = ep.build_pool_trend_catchup(ladder, strong_df, pd.DataFrame(), snap_map, turnover_map)
    p6_codes = {r["code"] for r in p6["items"]}
    ck(p6_codes == {"002001", "002002"}, "Pool 6 准确锁定强趋势板块内的沪深低位首板补涨标的，且剔除北交所 920096")

    # 校验北交所过滤函数与全池零北交所泄漏
    ck(ep.is_bj_stock("920096") and ep.is_bj_stock("831071.BJ") and ep.is_bj_stock("bj430139") and not ep.is_bj_stock("002001"),
       "is_bj_stock 准确识别 92/83/43/87/88/899 及 .BJ/bj 前缀标的")
    all_test_pool_codes = {
        r["code"] for p in (p1, p2, p3, p4, p5, p6) for r in p.get("items", [])
    }
    ck(not any(ep.is_bj_stock(c) for c in all_test_pool_codes),
       f"六大选股池单测结果零北交所股票混入（实际入选代码={sorted(all_test_pool_codes)}）")


# ================= 4. 四维风控排雷门禁单测 =================

def check_risk_gates() -> None:
    print("\n=== 4. 四维风控排雷门禁单测（#096 / #004 / #032 / #176） ===")
    # 构造 10 日高位双长上影「丄」字顶 K 线（min_10=9.0, max_10=11.0, cur_close=10.2 < 11.0*0.985, 近5日2根上影>=2.8%）
    k_double_upper = pd.DataFrame([
        {"date": f"202610{i:02d}", "open": 9.1, "close": 9.2, "high": 9.3, "low": 9.0} for i in range(1, 9)
    ] + [
        {"date": "20261015", "open": 10.2, "close": 10.3, "high": 11.0, "low": 10.1},
        {"date": "20261016", "open": 10.2, "close": 10.2, "high": 10.8, "low": 10.0},
    ])
    # 1) 测试 #096 换手率 >= 66.67% + #032 午后频炸尾板 + #176 高位双上影
    w1 = ep.check_stock_risk_gates(
        code="300999",
        name="C次新妖",
        turnover_rate=68.0,
        first_time="14:15:00",
        limit_times=1,
        zbc=2,
        kline_df=k_double_upper,
        ladder_hist=None,
    )
    r1 = {w["rule_id"] for w in w1}
    ck("096" in r1, "换手率 68% >= 66.67% 准确触发 #096 死亡换手门禁")
    ck("032" in r1, "14:15 封板且炸板2次的首板准确触发 #032 午后频炸尾板门禁")
    ck("176" in r1, "阶段高位连续两日长上影滞涨准确触发 #176 双上影「丄」字见顶门禁")

    # 2) 测试 #004 一字连板筹码断层
    w2 = ep.check_stock_risk_gates(
        code="002888",
        name="一字加速股",
        turnover_rate=1.5,
        first_time="09:25:00",
        limit_times=2,
        zbc=0,
    )
    r2 = {w["rule_id"] for w in w2}
    ck("004" in r2, "09:25 封死无炸板二板准确触发 #004 一字筹码断层门禁")


# ================= 5. 产物、知识库、摘要接入与前端门禁自检 =================

def check_artifacts_and_ui() -> None:
    print("\n=== 5. 181 知识库、落盘产物、收盘摘要接入与页面门禁自检 ===")
    ck(os.path.exists("data/methodology_181.json"), "data/methodology_181.json 已生成")
    kb = json.loads(read_text("data/methodology_181.json"))
    ck(kb.get("total_articles") == 181 and len(kb.get("articles", [])) == 181, "181 篇方法论知识库条目完整 (181/181)")
    ck(len(kb.get("evening_steps", [])) == 5, "晚间复盘五步 SOP 元数据完整 (5 步)")

    ck(os.path.exists("data/evening_plan/latest.json"), "data/evening_plan/latest.json 已落盘")
    plan = json.loads(read_text("data/evening_plan/latest.json"))
    ck(bool(plan.get("date")) and plan.get("status") in ("complete", "incomplete"),
       f"latest.json 含有效交易日 ({plan.get('date')}) 且 status 合法 ({plan.get('status')})")
    pools = plan.get("pools") or {}
    expected_pools = {"pool_10am_first", "pool_2b_pk", "pool_ice_rebound", "pool_ma6_pullback", "pool_rubbing_breakout", "pool_trend_catchup"}
    ck(set(pools.keys()) == expected_pools, "latest.json 六大选股池键名齐全")
    bj_in_latest = [
        f"{pk}:{it.get('code')}"
        for pk, p_obj in pools.items()
        for it in (p_obj.get("items") or [])
        if ep.is_bj_stock(it.get("code"))
    ]
    ck(not bj_in_latest, f"latest.json 六大选股池已彻底清除北交所股票（检出={bj_in_latest}）")
    ck(len(plan.get("intraday_timeline") or []) == 5, "latest.json 含次日盘中 5 大时间轴节点")

    # 检查个人自选池诊断函数
    diag = ep.diagnose_watchlist(["002001", "600519"], pools, {"002001": {"name": "新和成", "close": 20.0}}, {"002001": 5.0})
    ck(diag["status"] == "ok" and diag["count"] == 2 and diag["items"][0]["code"] == "002001",
       "diagnose_watchlist 准确输出用户自选池次日策略共振与排雷列表")

    # 检查合规禁用词
    banned_words = (
        "建议买入", "建议卖出", "目标价", "止损位", "建仓价",
        "建议仓位", "满仓", "半仓", "推荐买", "可以买入", "可以卖出",
    )
    kb_str = json.dumps(kb, ensure_ascii=False)
    plan_str = json.dumps(plan, ensure_ascii=False)
    ui_src = read_text("page_evening_plan.py")
    for w in banned_words:
        ck(w not in kb_str, f"181 知识库不含违规词「{w}」")
        ck(w not in plan_str, f"latest.json 产物不含违规词「{w}」")
        ck(w not in ui_src, f"page_evening_plan.py 不含违规词「{w}」")

    # 检查前端页面新鲜度横幅与 VIP 门禁（锁个人视角与历史验证矩阵，不锁六大客观选股池）
    ck("_render_freshness_and_disclaimer" in ui_src and "freshness." in ui_src,
       "page_evening_plan.py 顶部接入产物新鲜度校验横幅")
    ck("auth.is_vip()" in ui_src, "page_evening_plan.py 对个人池子诊断与分周期历史验证矩阵执行 VIP 门禁")
    ck("DISCLAIMER" in ui_src, "page_evening_plan.py 接入合规免责声明")

    app_src = read_text("app.py")
    ck("render_evening_plan_page" in app_src and "📋 晚间复盘与次日计划" in app_src,
       "app.py 已挂载「📋 晚间复盘与次日计划」导航与渲染入口")


# ================= 6. 反向突变自检（Mutate 守卫） =================

def check_mutations() -> None:
    print("\n=== 6. 反向突变自检（验证断言能抓出退化） ===")
    # 突变 1：破坏 #125 首板 10:00 分界线（让 is_pre_10am 恒返 True），验证早盘首板计数会失真并被抓出
    orig_fn = ep.is_pre_10am
    try:
        ep.is_pre_10am = lambda s: True  # 恒定 True，导致 10:30 也被算进 10:00 前
        ladder_hist = pd.DataFrame([
            {"date": "20261015", "code": "000001", "limit_times": 1, "first_time": "09:32:00"},
            {"date": "20261015", "code": "000003", "limit_times": 1, "first_time": "10:30:00"},
            {"date": "20261016", "code": "000001", "limit_times": 2, "first_time": "09:40:00"},
        ])
        mut_res = ep.first_board_time_split(ladder_hist, "20261016", "20261015", {"000001": 5.0, "000003": -2.0})
        caught_1 = (mut_res["pre_10am"]["base"] != 1)
    finally:
        ep.is_pre_10am = orig_fn
    ck(caught_1, "【突变自检 1】破坏 #125 is_pre_10am 分界时，pre_10am.base 偏离手算值，断言可精确捕获")

    # 突变 2：抹去揉搓线次日长下影线，验证 detect_rubbing_line 必须拒绝
    k_rub = pd.DataFrame([
        {"date": f"202610{i:02d}", "open": 9.8, "high": 10.0, "low": 9.7, "close": 9.9} for i in range(1, 11)
    ] + [
        {"date": "20261015", "open": 10.0, "high": 10.6, "low": 9.98, "close": 10.1},
        {"date": "20261016", "open": 10.1, "high": 10.25, "low": 9.75, "close": 10.2},
    ])
    k_bad = k_rub.copy()
    k_bad.loc[k_bad.index[-1], "low"] = 10.09
    bad_rub = ep.detect_rubbing_line(k_bad)
    ck(bad_rub is None, "【突变自检 2】抹去次日长下影线后，#169 揉搓线识别器立即返回 None 拒绝匹配")

    # 突变 3：破坏 is_bj_stock 门禁（恒返 False），验证北交所股票 920096 会漏进 Pool 1 并被抓出
    orig_bj = ep.is_bj_stock
    try:
        ep.is_bj_stock = lambda c: False
        mut_p1 = ep.build_pool_10am_first(
            {"stocks": [{"code": "920096", "name": "北交测试", "limit_times": 1, "first_time": "09:35:00"}]},
            {}, {},
        )
        caught_3 = any(it["code"] == "920096" for it in mut_p1["items"])
    finally:
        ep.is_bj_stock = orig_bj
    ck(caught_3, "【突变自检 3】关闭 is_bj_stock 过滤时，920096 立即混入选股池并被断言捕获")


def main() -> None:
    check_layer_purity()
    check_sentiment_and_laws()
    check_six_strategy_pools()
    check_risk_gates()
    check_artifacts_and_ui()
    check_mutations()
    print("-" * 60)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL > 0:
        sys.exit(1)
    print("✅ 晚间复盘与次日交易计划全链路自检全部通过")


if __name__ == "__main__":
    main()
