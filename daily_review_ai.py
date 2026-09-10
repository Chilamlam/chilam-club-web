# -*- coding: utf-8 -*-
"""
引导式复盘 - AI 答卷生成（跑批层）

职责：读当日全产物，让 Gemini 按模板逐题作答，每题带数据依据，
结果写 data/review/ai_answers/YYYYMMDD.json（落盘归档，不入库——
入库由应用层在读到时懒同步，或后续按需加步骤）。

设计原则：
  1. **每题必须带 basis**（引用哪个产物、什么数值）——AI 答卷的说服力
     全在「有数据背书」，没有 basis 的题宁可不答。
  2. **合规口径**：明日预期只做结构描述（延续/分歧/修复/加剧），
     严禁个股推荐、目标价、买卖点。prompt 里显式约束。
  3. **失败语义**：任何产物缺失或 Gemini 失败 → 本脚本退出码 0
     （跑批编排器约定：任一步失败不阻断当日数据落盘），但
     ai_answers 不写文件，页面显示「今日 AI 答卷未生成」——
     绝不写半份答卷冒充完整。
  4. **幂等**：同日重跑覆盖写（以最新数据重答），不影响已交卷用户
     的解锁逻辑（解锁条件是用户交卷时间，不是 AI 答卷时间）。

分层约束：不 import streamlit；联网仅 Gemini 一次调用；产物读取
全部走本地 data/（跑批顺序保证 digest 之后执行）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

from review_template import DEFAULT_TEMPLATE

OUT_DIR = os.path.join("data", "review", "ai_answers")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
CST = timezone(timedelta(hours=8))

# AI 不答的题（主观题）由模板 ai_checkable=False 且 prompt 注明；
# 但 q02（用户心理感受）AI 确实不该答——双保险在这里也排除一次。
AI_SKIP_IDS = {"q02_mood"}

DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
LADDER_PATH = os.path.join("data", "limit_ladder.json")
ANALYSIS_PATH = os.path.join("data", "ai_market_analysis.json")
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _snapshot_trade_date() -> str:
    """答卷针对的交易日：与 digest 一致取 derived.json 的 date；
    无 derived 时退化为「15:00 前算昨天」（与板块轮动锚定同一套退化规则）。"""
    derived = _load_json(DERIVED_PATH)
    d = (derived or {}).get("date")
    if d and len(str(d)) == 8:
        return str(d)
    now = datetime.now(CST)
    if now.weekday() == 5:      # 周六 → 周五
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    if now.weekday() == 6:      # 周日 → 周五
        return (now - timedelta(days=2)).strftime("%Y%m%d")
    if now.hour < 15:           # 收盘前 → 昨天
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    return now.strftime("%Y%m%d")


def build_context(trade_date: str) -> dict:
    """组装当日数据上下文（AI 答卷的依据全集）。"""
    derived = _load_json(DERIVED_PATH)
    ladder = _load_json(LADDER_PATH)
    ai_analysis = _load_json(ANALYSIS_PATH)
    sector = _load_json(SECTOR_PATH)
    ctx = {"trade_date": trade_date}
    if derived and str(derived.get("date")) == trade_date:
        ctx["derived"] = {
            "phase": (derived.get("phase") or {}).get("phase"),
            "basis": (derived.get("phase") or {}).get("basis"),
            "promotion": derived.get("promotion"),
            "premium": derived.get("premium"),
            "ladder_gap": derived.get("ladder_gap"),
        }
    if ladder and str(ladder.get("date")).replace("-", "") == trade_date:
        stocks = ladder.get("stocks") or []
        ctx["ladder"] = {
            "total_count": ladder.get("total_count"),
            "max_height": ladder.get("max_height"),
            "top5": [
                {"name": s.get("name"), "limit_times": s.get("limit_times"),
                 "industry": s.get("industry")}
                for s in sorted(stocks, key=lambda x: -(x.get("limit_times") or 0))[:5]
            ],
        }
    if ai_analysis and str(ai_analysis.get("date")).replace("-", "") == trade_date:
        ctx["ai_analysis_main_logic"] = (ai_analysis.get("main_logic") or "")[:600]
        ctx["limit_reasons_top"] = [
            {"name": r.get("name"), "reason": (r.get("reason") or "")[:80]}
            for r in (ai_analysis.get("limit_reasons") or [])[:10]
        ]
    if sector and str(sector.get("date")).replace("-", "") == trade_date:
        top = sector.get("top") or []
        ctx["sector_top10"] = [
            {"name": t.get("name"), "pct": t.get("pct_1d")}
            for t in top[:10]
        ]
    return ctx


def build_prompt(trade_date: str, ctx: dict) -> str:
    """题目清单 + 数据上下文 → Gemini prompt。"""
    questions = []
    for q in DEFAULT_TEMPLATE:
        if q["id"] in AI_SKIP_IDS:
            continue
        if q["type"] == "text":
            questions.append(
                f"- {q['id']}（{q['prompt']}）：这是用户自由发挥题，你也要给出你的明日预期文字，"
                "但只描述结构（情绪/梯队/题材格局怎么演变），严禁个股推荐、目标价、买卖点。")
            continue
        opts = "、".join(q["options"]) if q.get("options") else "（选项来自当日板块涨幅榜，见数据）"
        if q["type"] == "choice_from_data":
            names = "、".join(t["name"] for t in (ctx.get("sector_top10") or []))
            opts = f"从当日板块涨幅榜选择：{names}"
        questions.append(f"- {q['id']}（{q['prompt']}）选项：{opts}")
    qtext = "\n".join(questions)

    return f"""
你是一个严谨的A股短线复盘助手，正在填写一份结构化复盘答卷。今天是 {trade_date} 收盘后。

【当日站内数据】（你作答的唯一依据，禁止编造没给出的数字）：
{json.dumps(ctx, ensure_ascii=False, indent=1)}

【答卷题目】：
{qtext}

【作答规则】：
1. 逐题作答，输出严格的 JSON（不要 markdown 代码块包裹，不要多余文字）。
2. 结构：{{"answers": {{题目id: {{"choice": "选项原文", "basis": "依据（引用哪个数据、什么数值）"}}}}, "plan_text": "明日预期（结构描述）"}}
3. 每题 choice 必须是题目给出的选项原文之一（一字不差）。
4. 每题 basis 必须引用上面数据的真实数值，格式如「连板溢价 -1.0%（18 只）」；没有依据就不要编。
5. plan_text 只描述盘面结构预期（情绪修复/延续/分歧/加剧，梯队与题材格局），严禁出现具体股票代码、目标价、买卖点。
6. 数据里没有的题，choice 填 "无法判断"，basis 写明缺哪个数据。
"""


def call_gemini(prompt: str) -> dict | None:
    """调 Gemini（模型嗅探逻辑与 daily_market_monitor 一致，独立实现避免 import 跑批层）。"""
    if not GEMINI_KEY:
        print("⚠️ GEMINI_API_KEY 未配置，AI 答卷跳过")
        return None
    try:
        models_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
        resp = requests.get(models_url, timeout=10)
        model = "models/gemini-1.5-flash"
        if resp.status_code == 200:
            for m in resp.json().get("models", []):
                if "generateContent" in m.get("supportedGenerationMethods", []):
                    model = m["name"]
                    break
        url = f"https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={GEMINI_KEY}"
        resp = requests.post(
            url, headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=60)
        if resp.status_code != 200:
            print(f"⚠️ Gemini 调用失败 HTTP {resp.status_code}")
            return None
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        # 剥掉可能的 markdown 代码块包裹
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as exc:
        print(f"⚠️ Gemini 环节异常：{type(exc).__name__}: {exc}")
        return None


def validate_answer(payload: dict | None, ctx: dict) -> tuple[dict, list]:
    """校验 AI 答卷：题目覆盖、选项合法性、basis 非空。
    返回 (清洗后的答卷, 问题清单)。问题严重（覆盖不足一半）→ 整份作废。"""
    if not payload or not isinstance(payload.get("answers"), dict):
        return {}, ["答卷不是合法 JSON 或缺 answers"]
    problems = []
    valid_ids = {q["id"] for q in DEFAULT_TEMPLATE} - AI_SKIP_IDS
    answered = {k: v for k, v in payload["answers"].items()
                if k in valid_ids and isinstance(v, dict) and v.get("choice")}
    # 选项合法性（choice_from_data 的合法集来自上下文板块名）
    sector_names = {t["name"] for t in (ctx.get("sector_top10") or [])}
    cleaned = {}
    for q in DEFAULT_TEMPLATE:
        qid = q["id"]
        if qid in AI_SKIP_IDS:
            continue
        a = answered.get(qid)
        if not a:
            problems.append(f"{qid}: 未作答")
            continue
        choice = str(a.get("choice", "")).strip()
        if q["type"] == "choice_from_data":
            ok = choice in sector_names or choice == "无法判断"
        elif q["type"] in ("single", "multi", "yn") and q.get("options"):
            ok = all(c in q["options"] for c in ([choice] if "," not in choice else choice.split(","))) \
                 or choice == "无法判断"
        else:
            ok = True
        if not ok:
            problems.append(f"{qid}: 选项「{choice}」不在合法集")
            continue
        if not str(a.get("basis", "")).strip() and choice != "无法判断":
            problems.append(f"{qid}: basis 为空")
            continue
        cleaned[qid] = {"choice": choice, "basis": str(a.get("basis", ""))[:200]}
    # 覆盖率不足一半 → 作废（宁缺毋滥，半份答卷对比价值为负）
    if len(cleaned) < len(valid_ids) // 2:
        return {}, [f"覆盖率 {len(cleaned)}/{len(valid_ids)} 不足一半，整份作废"] + problems
    return {"answers": cleaned, "plan_text": str(payload.get("plan_text", ""))[:800]}, problems


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    trade_date = _snapshot_trade_date()
    ctx = build_context(trade_date)

    # 数据上下文太薄（连情绪派生都没有）→ 不硬答
    if "derived" not in ctx:
        print(f"⚠️ 当日（{trade_date}）情绪派生指标缺失，AI 答卷不生成（宁缺毋滥）")
        return 0

    print(f"🧠 AI 答卷生成：交易日 {trade_date}，上下文键：{sorted(ctx.keys())}")
    payload = call_gemini(build_prompt(trade_date, ctx))
    cleaned, problems = validate_answer(payload, ctx)
    if not cleaned:
        print("❌ AI 答卷作废：", "; ".join(problems[:5]))
        return 0
    if problems:
        print(f"⚠️ {len(problems)} 题被剔除：", "; ".join(problems[:5]))

    out_path = os.path.join(OUT_DIR, f"{trade_date}.json")
    doc = {
        "date": trade_date,
        "template_version": __import__("review_template").TEMPLATE_VERSION,
        "generated_at": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "model": "gemini",
        "answers": cleaned["answers"],
        "plan_text": cleaned["plan_text"],
        "problems": problems,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"✅ AI 答卷落盘 {out_path}（{len(cleaned['answers'])} 题）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
