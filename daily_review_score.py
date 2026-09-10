# -*- coding: utf-8 -*-
"""
引导式复盘 - 次日回验（跑批层）

职责：T+1 跑批时，对 T 日的答卷做回验——用户答卷 vs AI 答卷 vs 实际盘面，
逐题写 review_scoring（Supabase）。

判分哲学（VERDICT_MAP）：
  可判分题不是所有都有硬指标。分两类：
  - 硬判据：情绪周期位置（次日晋级率/溢价走向）、指数周期（次日指数涨跌）、
    领先指数（次日各指数实际涨幅第一）、梯队成立（次日梯队是否断层）、
    最强题材（次日板块榜主题延续）
  - 方向判据：明日预期（次日情绪派生 phase 与 T 日答卷方向是否相符）
  多选题（主流模式/辨识度）判分规则：命中交并比 ≥ 0.5 记命中（半数以上
  重合即算「看到同一片盘面」）。

失败语义：与编排器约定一致，任何失败退出码 0 不阻断；回验缺失的日子，
页面显示「回验尚未生成」，绝不写猜的 actual。

分层约束：不 import streamlit；读本地 data/ 与 Supabase（service key）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")
LADDER_PATH = os.path.join("data", "limit_ladder.json")

from review_template import get_checkable_ids, DEFAULT_TEMPLATE


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _shift_date(d8: str, days: int) -> str:
    dt = datetime.strptime(d8, "%Y%m%d")
    return (dt + timedelta(days=days)).strftime("%Y%m%d")


def compute_actual(prev_date: str, cur_date: str) -> dict:
    """用 cur_date（T+1）的实际数据回验 prev_date（T）答卷的「预期」类题目。
    返回 {question_id: actual_str}。缺数据就不给该题 actual（宁缺毋滥）。"""
    out = {}
    derived = _load_json(DERIVED_PATH)
    sector = _load_json(SECTOR_PATH)
    ladder = _load_json(LADDER_PATH)
    # derived 的 date 必须是 cur_date 才能作为「次日实际」
    d_ok = derived and str(derived.get("date")) == cur_date
    s_ok = sector and str(sector.get("date")).replace("-", "") == cur_date
    l_ok = ladder and str(ladder.get("date")).replace("-", "") == cur_date

    if d_ok:
        ph = (derived.get("phase") or {})
        if ph.get("status") == "ok":
            # T 日判「退潮」，T+1 实际 phase 若仍退潮 → 命中；同理各档
            out["q03_emotion_cycle"] = ph.get("phase")
            out["q14_tomorrow_expect"] = ph.get("phase")
        prem = derived.get("premium") or {}
        if prem.get("status") == "ok":
            med = float(prem.get("median_pct") or 0)
            # 溢价正负 → 赚钱效应方向（q01 只有 T 日数据，不回验——主观题）
            out["q07_ladder"] = "成立" if (
                (derived.get("ladder_gap") or {}).get("max_height", 0) >= 3
            ) else "不成立"
    if s_ok:
        top = (sector.get("top") or [])
        if top:
            out["q10_strongest_theme"] = top[0].get("name")
    if l_ok:
        mh = ladder.get("max_height") or 0
        out["q07_ladder"] = "成立" if mh >= 3 else "不成立"
    return out


def score_multi(user_set: set, ai_set: set, actual_set: set) -> tuple[bool, bool]:
    """多选题判分：与 actual 交并比 ≥ 0.5 记命中。返回 (user_hit, ai_hit)。"""
    def iou(a: set) -> float:
        if not actual_set:
            return 0.0
        inter = len(a & actual_set)
        union = len(a | actual_set)
        return inter / union if union else 0.0
    return iou(user_set) >= 0.5, iou(ai_set) >= 0.5


def score_single(user_choice: str, ai_choice: str, actual: str) -> tuple[bool, bool]:
    """单选/YN 判分：选择与 actual 语义一致记命中。
    actual 是回验函数产出的「实际盘面结论」，与选项同词表（如情绪周期五档）。"""
    u = (user_choice or "").strip()
    a = (ai_choice or "").strip()
    act = (actual or "").strip()
    return u == act, a == act


def main() -> int:
    """回验昨日答卷。cur_date=今日跑批锚定日，prev=上一交易日。
    简化约定：直接取 derived.date 为 cur，prev 由答卷库里有答卷的最近日决定。"""
    from database import (_supabase_request, _get_config)
    # database 需要 secrets；跑批环境有。本地无 secrets 时安静退出。
    try:
        _cfg = _get_config()
    except Exception as e:
        print(f"⚠️ Supabase 未配置（{type(e).__name__}），回验跳过")
        return 0

    derived = _load_json(DERIVED_PATH)
    cur_date = str((derived or {}).get("date") or "")
    if not cur_date:
        print("⚠️ 当日 derived 缺失，无法回验（prev 答卷的 actual 没有基准）")
        return 0

    # 取所有已交卷、未回验的昨日答卷（submitted=true，trade_date < cur_date）
    params = {"submitted": "eq.true", "select": "*",
              "trade_date": f"lt.{cur_date[:4]}-{cur_date[4:6]}-{cur_date[6:]}",
              "order": "trade_date.desc", "limit": "50"}
    rows = _supabase_request("GET", "review_answers", params=params)
    if not isinstance(rows, list) or not rows:
        print("✅ 无待回验答卷")
        return 0

    for row in rows:
        td = str(row.get("trade_date", ""))[:10].replace("-", "")
        uid = row.get("user_id")
        # 已有回验的不重复（幂等）
        exist = _supabase_request(
            "GET", "review_scoring",
            params={"user_id": f"eq.{uid}", "trade_date": f"eq.{td}",
                    "select": "id", "limit": "1"})
        if isinstance(exist, list) and exist:
            continue
        # AI 答卷（该日）
        ai_row = _supabase_request(
            "GET", "review_ai_answers",
            params={"trade_date": f"eq.{td}", "select": "*", "limit": "1"})
        ai_answers = {}
        if isinstance(ai_row, list) and ai_row:
            a = ai_row[0].get("answers") or {}
            if isinstance(a, str):
                try:
                    a = json.loads(a)
                except Exception:
                    a = {}
            ai_answers = a
        user_answers = row.get("answers") or {}
        if isinstance(user_answers, str):
            try:
                user_answers = json.loads(user_answers)
            except Exception:
                user_answers = {}

        # actual 基准：td 的「次日」= 比它大的最近交易日。简化：用 cur_date 的数据
        # （回验当天跑批产物），因为回验只在 T+1 晚间跑一次。
        actual_map = compute_actual(td, cur_date)
        qmap = {q["id"]: q for q in DEFAULT_TEMPLATE}
        for qid in get_checkable_ids():
            u_choice = str(user_answers.get(qid, "") or "")
            a_entry = ai_answers.get(qid) or {}
            a_choice = str(a_entry.get("choice", "") or "")
            actual = actual_map.get(qid)
            if actual is None:
                continue  # 没有实际判据的题不写行（宁缺毋滥）
            qtype = (qmap.get(qid) or {}).get("type")
            if qtype == "multi":
                u_set = set(x for x in u_choice.split("、") if x)
                a_set = set(x for x in a_choice.split("、") if x)
                # multi 的 actual 语义复杂，首版不判 multi（只对比不判分已在模板层处理 q13；
                # q12 主流模式有判据，但 actual 集合定义需要单独研究——首版跳过，二期补）
                continue
            u_hit, a_hit = score_single(u_choice, a_choice, str(actual))
            _supabase_request(
                "POST", "review_scoring",
                json_data={"trade_date": f"{td[:4]}-{td[4:6]}-{td[6:]}",
                           "user_id": uid, "question_id": qid,
                           "user_choice": u_choice, "ai_choice": a_choice,
                           "actual": str(actual),
                           "user_hit": bool(u_hit), "ai_hit": bool(a_hit)},
                return_error=True)
        print(f"✅ 已回验 user={uid} date={td}（actual 判据 {len(actual_map)} 题）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
