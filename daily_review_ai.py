# -*- coding: utf-8 -*-
"""
引导式复盘 - AI 答卷生成（跑批层）

职责：读当日全产物，让大模型按模板逐题作答，每题带数据依据，
结果两路落盘：
  1. data/review/ai_answers/YYYYMMDD.json —— 跑批归档（可追溯）
  2. Supabase review_ai_answers 表 —— 页面对比视图与回验脚本的数据源
     （只在 1 成功后写库；库写失败不回滚文件——文件是归档事实，库可补）

模型：DeepSeek（deepseek-chat，OpenAI 兼容格式，api.deepseek.com）。
为什么从 Gemini 换成 DeepSeek（2026-09-10 用户决策，成本考量）：
  - 答卷一次调用约 6K tokens，deepseek-chat 输入 2 元/M、输出 3 元/M，
    单次成本不到 2 分钱，比 Gemini 便宜一个数量级；
  - 国内直连，Actions runner 与本地访问都稳定（Gemini 的
    generativelanguage.googleapis.com 在部分网络下不可达）；
  - OpenAI 兼容格式，requests 直发 chat/completions 即可，无 SDK 依赖。
  - deepseek-chat 支持 JSON Output：response_format={"type":"json_object"}，
    结构化答卷场景用它，非法 JSON 概率大幅低于 Gemini。

设计原则：
  1. **每题必须带 basis**（引用哪个产物、什么数值）——AI 答卷的说服力
     全在「有数据背书」，没有 basis 的题宁可不答。
  2. **合规口径**：明日预期只做结构描述（延续/分歧/修复/加剧），
     严禁个股推荐、目标价、买卖点。prompt 里显式约束。
  3. **失败语义**：任何产物缺失或模型调用失败 → 本脚本退出码 0
     （跑批编排器约定：任一步失败不阻断当日数据落盘），但
     ai_answers 不写文件也不写库，页面显示「今日 AI 答卷未生成」——
     绝不写半份答卷冒充完整。
  4. **幂等**：同日重跑覆盖写（以最新数据重答），不影响已交卷用户
     的解锁逻辑（解锁条件是用户交卷时间，不是 AI 答卷时间）。

分层约束：不 import streamlit；联网仅模型 API 一次调用 + Supabase 写库一次；
产物读取全部走本地 data/（跑批顺序保证 digest 之后执行）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

from review_template import DEFAULT_TEMPLATE
from review_evidence import ladder_snapshot, sector_snapshot, build_evidence

OUT_DIR = os.path.join("data", "review", "ai_answers")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
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
    """组装当日数据上下文（AI 答卷的依据全集）。

    天梯与板块取数走 review_evidence（一处实现）——本函数历史版本取
    板块涨幅用 pct_1d 字段，实际字段名是 pct_chg，恒 None 的静默 bug
    在收口到 ladder_snapshot/sector_snapshot 时一并修复（2026-09-11）。
    另注入每题标的参考（qid → evidence），与页面填卷看到同一份素材，
    「看盘面作答」两侧对齐。"""
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
        snap = ladder_snapshot(ladder)
        ctx["ladder"] = {
            "total_count": snap["total"],
            "max_height": snap["max_height"],
            "top5": snap["top5"],
        }
    if ai_analysis and str(ai_analysis.get("date")).replace("-", "") == trade_date:
        ctx["ai_analysis_main_logic"] = (ai_analysis.get("main_logic") or "")[:600]
        ctx["limit_reasons_top"] = [
            {"name": r.get("name"), "reason": (r.get("reason") or "")[:80]}
            for r in (ai_analysis.get("limit_reasons") or [])[:10]
        ]
    if sector and str(sector.get("date")).replace("-", "") == trade_date:
        ctx["sector_top10"] = sector_snapshot(sector)
    # 每题当日标的/数据参考（用户 2026-09-11 反馈：题目下要有对应标的）。
    # evidence 与页面共用；里含的板块 pct 已是正确字段（pct_chg）。
    ev = build_evidence(trade_date)
    if ev:
        ctx["evidence"] = {
            qid: {"title": e["title"], "lines": e["lines"]}
            for qid, e in ev.items()
        }
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


def call_deepseek(prompt: str) -> dict | None:
    """调 DeepSeek（OpenAI 兼容 chat/completions + JSON Output）。
    返回解析后的 dict；任何失败返回 None（宁可整份不作答，不出半份）。"""
    if not DEEPSEEK_KEY:
        print("⚠️ DEEPSEEK_API_KEY 未配置，AI 答卷跳过")
        return None
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": "你是严谨的A股短线复盘助手，只输出合法 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,   # 事实性作答：低温度减少发挥
                "max_tokens": 3000,
            },
            timeout=90)
        if resp.status_code != 200:
            print(f"⚠️ DeepSeek 调用失败 HTTP {resp.status_code}: {resp.text[:200]}")
            return None
        text = resp.json()["choices"][0]["message"]["content"]
        return json.loads(text)
    except Exception as exc:
        print(f"⚠️ DeepSeek 环节异常：{type(exc).__name__}: {exc}")
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

    print(f"🧠 AI 答卷生成：交易日 {trade_date}，模型 {DEEPSEEK_MODEL}，上下文键：{sorted(ctx.keys())}")
    payload = call_deepseek(build_prompt(trade_date, ctx))
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
        "model": DEEPSEEK_MODEL,
        "answers": cleaned["answers"],
        "plan_text": cleaned["plan_text"],
        "problems": problems,
    }
    # 文件先落（归档事实），再写库（页面与回验的数据源）
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"✅ AI 答卷落盘 {out_path}（{len(cleaned['answers'])} 题）")

    db_err = _upsert_to_supabase(trade_date, doc)
    if db_err:
        print(f"⚠️ 写库失败（{db_err}）——文件已归档，页面暂不可见；"
              "下次跑批同日覆盖会重试写库")
    else:
        print("✅ AI 答卷已写库（review_ai_answers）")
    return 0


def _upsert_to_supabase(trade_date: str, doc: dict) -> str | None:
    """写/覆盖当日 AI 答卷到 Supabase。返回错误描述或 None。
    幂等：先查当日行，有则 PATCH 无则 POST。库写失败不影响文件归档。"""
    try:
        import database as db
    except Exception as exc:
        return f"database 不可用：{type(exc).__name__}"
    # 空配置直接跳过（本地开发态），不算错误
    try:
        url, key = db._get_config()
    except Exception:
        return "配置读取失败"
    if not url or not key:
        return "Supabase 未配置（本地开发态，跳过写库）"
    try:
        date_iso = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:]}"
        payload = {"answers": doc["answers"], "plan_text": doc.get("plan_text", ""),
                  "model_name": doc.get("model", ""), "data_digest": ""}
        existing = db._supabase_request(
            "GET", "review_ai_answers",
            params={"trade_date": f"eq.{date_iso}", "select": "id", "limit": "1"})
        if isinstance(existing, list) and existing:
            row_id = existing[0].get("id")
            res = db._supabase_request(
                "PATCH", f"review_ai_answers?id=eq.{row_id}", json_data=payload)
        else:
            res = db._supabase_request(
                "POST", "review_ai_answers",
                json_data={"trade_date": date_iso, **payload})
        if res is None or (isinstance(res, list) and not res and not existing):
            return "写入返回空"
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


if __name__ == "__main__":
    sys.exit(main())
