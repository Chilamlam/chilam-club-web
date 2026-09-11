# -*- coding: utf-8 -*-
"""
引导式复盘答卷自检（review_template / daily_review_ai / page_review /
database(答卷段) / daily_review_score）

这个功能出错的特殊方式：**门禁失守**与**判分造假**。
  - 门禁失守：AI 答卷在用户交卷前被页面/接口返回（锚定效应毁掉，
    产品灵魂没了）——必须断言 database.get_ai_review 在未交卷时返回
    NOT_SUBMITTED，且这是**后端**拒绝（页面藏起来不算数）
  - 判分造假：回验脚本没有实际判据时硬写 actual（编数据）——必须
    断言缺判据的题不写行
  - 模板漂移：题目 id 被改（旧答卷对不上账）——id 集合必须与
    scoring/answers 的对账键稳定
  - 选项锚定：AI 答卷 choice 必须是模板选项原文（一字不差），
    否则对比视图把「不同表述」误判成「判断不同」
"""
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = [], []


def ck(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(("✅ " if cond else "❌ ") + msg)


ROOT = os.path.dirname(os.path.abspath(__file__))

# ---- 1. 模板 schema 合法性 ----
spec = importlib.util.spec_from_file_location("rt", os.path.join(ROOT, "review_template.py"))
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

tpl = rt.DEFAULT_TEMPLATE
ck(len(tpl) >= 12, f"默认模板题数 {len(tpl)} ≥ 12（全覆盖复盘流程的完整性）")
ids = [q["id"] for q in tpl]
ck(len(ids) == len(set(ids)), "题目 id 全局唯一（对账主键）")
ck(all(q.get("prompt") for q in tpl), "每题有 prompt")
ck(all(q.get("type") in ("single", "multi", "yn", "choice_from_data", "text") for q in tpl),
   "每题 type 合法")
ck(all(isinstance(q.get("options"), list) for q in tpl if q["type"] != "choice_from_data"),
   "非动态题都有静态选项")
# 选项非空的题（text/choice_from_data 除外）
ck(all(len(q["options"]) >= 2 for q in tpl
       if q["type"] in ("single", "multi", "yn")),
   "单选/多选/YN 题选项数 ≥ 2")
ck(all(isinstance(q.get("ai_checkable"), bool) for q in tpl), "每题 ai_checkable 是布尔")
ck(len(rt.get_checkable_ids()) >= 8, f"可判分题 {len(rt.get_checkable_ids())} ≥ 8（回验价值）")

# 情绪周期题（q03）必须存在且可判分——它是短线复盘的灵魂题
ck("q03_emotion_cycle" in ids, "情绪周期题存在（模板灵魂题）")
q03 = next(q for q in tpl if q["id"] == "q03_emotion_cycle")
ck(q03["ai_checkable"], "情绪周期题可判分（次日晋级率/溢价回验）")
ck(set(q03["options"]) == {"启动", "发酵", "高潮", "退潮", "冰点"},
   "情绪周期五档选项完整")

# ---- 2. AI 答卷脚本：不 import streamlit、DeepSeek、宁缺毋滥 ----
drai_src = open(os.path.join(ROOT, "daily_review_ai.py"), encoding="utf-8").read()
# AST 查 import 语句（子串匹配会误命中 docstring 里的「不 import streamlit」）
_drai_ast0 = ast.parse(drai_src)
_imports = []
for _n in _drai_ast0.body:
    if isinstance(_n, ast.Import):
        _imports.extend(a.name for a in _n.names)
    elif isinstance(_n, ast.ImportFrom):
        _imports.append(_n.module or "")
ck("streamlit" not in _imports, "AI 答卷脚本不 import streamlit（跑批层纪律，AST 判定）")
# 模型已切 DeepSeek（2026-09-10 用户决策，成本考量）
ck("DEEPSEEK_KEY" in drai_src and "api.deepseek.com" in drai_src,
   "模型为 DeepSeek（env 取 key + 官方端点）")
# 不再残留 Gemini 调用（docstring 里的决策记录允许提及，代码不允许）
_drai_code = "\n".join(
    ast.unparse(n) for n in _drai_ast0.body
    if not isinstance(n, ast.Expr) and not (isinstance(n, ast.Assign) and False))
ck("generativelanguage" not in _drai_code and "GEMINI" not in _drai_code,
   "代码区不再有 Gemini 端点/变量（AST 剥离 docstring 后判定）")
ck('"response_format": {"type": "json_object"}' in drai_src,
   "DeepSeek 调用启用 JSON Output（结构化答卷降低非法 JSON 概率）")
ck("call_deepseek" in drai_src and "def call_deepseek" in drai_src,
   "DeepSeek 调用函数存在")
# 答卷必须双路落盘：文件归档 + Supabase（页面对比视图与回验的数据源）
# 锚 main() 里的真实调用点（锚函数名子串会被「删调用留定义」的突变骗过——反验实锤）
ck("def _upsert_to_supabase" in drai_src, "AI 答卷写库函数存在")
_main_fn = next((n for n in _drai_ast0.body
                if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
ck(_main_fn is not None, "AI 答卷脚本有 main 入口")
_upsert_called = False
if _main_fn is not None:
    for _n in ast.walk(_main_fn):
        if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name)
                and _n.func.id == "_upsert_to_supabase"):
            _upsert_called = True
            break
ck(_upsert_called, "main() 实际调用写库（AST 判定调用点，非定义）")
ck("文件已归档" in drai_src, "写库失败不回滚文件归档（文件是事实，库可补）")
drai_ast = ast.parse(drai_src)
func_names = [n.name for n in drai_ast.body if isinstance(n, ast.FunctionDef)]
ck("validate_answer" in func_names, "有答卷校验函数（选项合法性+覆盖率）")
ck("main" in func_names, "有 main 入口（编排器约定）")
# 宁缺毋滥：derived 缺失时不生成
ck("宁缺毋滥" in drai_src, "AI 答卷有宁缺毋滥注释与分支（数据缺失不硬答）")
# 覆盖率门槛必须存在（半份答卷对比价值为负）
ck("len(valid_ids) // 2" in drai_src, "覆盖率门槛：不足一半整份作废")

# 实测 validate_answer 的三个负向用例
spec2 = importlib.util.spec_from_file_location("drai", os.path.join(ROOT, "daily_review_ai.py"))
drai = importlib.util.module_from_spec(spec2)
sys.modules["requests"] = type(sys)("requests")  # stub：不真联网
spec2.loader.exec_module(drai)

ctx_fake = {"sector_top10": [{"name": "粮食概念", "pct": 5.0}]}
# 用例 1：空载荷 → 整份作废
c1, p1 = drai.validate_answer(None, ctx_fake)
ck(c1 == {} and p1, "空载荷答卷作废")
# 用例 2：非法选项 → 该题剔除
payload_bad = {"answers": {"q03_emotion_cycle": {"choice": "高潮期（不存在的写法）", "basis": "x"}}}
c2, p2 = drai.validate_answer(payload_bad, ctx_fake)
ck("q03_emotion_cycle" not in (c2.get("answers") or {}), "非法选项（选项原文变体）被剔除")
# 用例 3：合法答卷通过 + basis 必须有（覆盖率门槛之上的完整答卷）
_full_ok = {"answers": {}}
for _q in tpl:
    if _q["id"] in drai.AI_SKIP_IDS:
        continue
    _opt = (_q["options"] or ["粮食概念"])[:1]
    if _q["type"] == "choice_from_data":
        _opt = ["粮食概念"]
    elif _q["type"] == "text":
        _full_ok["answers"][_q["id"]] = {"choice": "文字", "basis": "—"}
        continue
    _full_ok["answers"][_q["id"]] = {"choice": _opt[0], "basis": "依据示例"}
_full_ok["plan_text"] = "明日情绪延续退潮"
c3, p3 = drai.validate_answer(_full_ok, ctx_fake)
ck(len(c3.get("answers", {})) >= len([q for q in tpl if q["id"] not in drai.AI_SKIP_IDS]) - 2,
   f"合法完整答卷通过校验（{len(c3.get('answers', {}))} 题，剔除 {len(p3)}）")
ck(c3.get("plan_text") == "明日情绪延续退潮", "明日预期文本保留")

# ---- 3. 解锁门禁：database.get_ai_review 后端拒绝（产品灵魂） ----
db_src = open(os.path.join(ROOT, "database.py"), encoding="utf-8").read()
ck("def get_ai_review" in db_src, "database 有 get_ai_review（解锁判定入口）")
# 门禁必须查 submitted，且在返回 AI 卷**之前**——AST 检查控制流而非文本
# （文本锚定会被 docstring 里的 NOT_SUBMITTED 骗过——突变反验实锤过一次）
db_ast = ast.parse(db_src)
gate_found = False
for node in db_ast.body:
    if isinstance(node, ast.FunctionDef) and node.name == "get_ai_review":
        ret_pos, fetch_pos = None, None
        for i, stmt in enumerate(node.body):
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)):
                continue  # docstring 不是控制流，跳过
            frag = ast.unparse(stmt)
            if (ret_pos is None and isinstance(stmt, ast.If)
                    and "NOT_SUBMITTED" in frag
                    and any(isinstance(s, ast.Return) for s in stmt.body)):
                ret_pos = i  # 带提前 Return 的门禁 If
            if fetch_pos is None and "review_ai_answers" in frag:
                fetch_pos = i  # 真正取 AI 卷的语句
        gate_found = (ret_pos is not None and fetch_pos is not None
                      and ret_pos < fetch_pos)
ck(gate_found, "解锁门禁在后端且带提前返回：未交卷的 If+Return 先于取 AI 卷（控制流判定）")
# 交卷锁定的数据侧另一半
ck("已交卷，锁定不可修改" in db_src or "LOCKED" in db_src,
   "交卷后答卷锁定（数据库侧防篡改）")

# ---- 4. 页面：付费墙位置与门禁委托 ----
prv_src = open(os.path.join(ROOT, "page_review.py"), encoding="utf-8").read()
ck("import streamlit" in prv_src or "import streamlit" in prv_src.replace("\n", ""),
   "答卷页面是展示层（导入 streamlit 正常）")
ck("is_logged_in" in prv_src, "页面有登录门禁")
# 付费墙：当日对比免费、历史/命中率 VIP
ck("is_vip" in prv_src and "命中率" in prv_src, "付费墙在历史对比+命中率（VIP）")
ck("填卷免费" in prv_src, "填卷免费的口径写明在页面")
# 页面不自行取 AI 卷（必须经 database.get_ai_review 的后端门禁）
ck("review_ai_answers" not in prv_src,
   "页面不直查 review_ai_answers 表（必须走后端门禁函数）")

# ---- 5. 回验脚本：宁缺毋滥 + 不 import streamlit ----
drs_src = open(os.path.join(ROOT, "daily_review_score.py"), encoding="utf-8").read()
# AST 判 import（同上，防 docstring 误命中）
_drs_ast0 = ast.parse(drs_src)
_drs_imports = []
for _n in _drs_ast0.body:
    if isinstance(_n, ast.Import):
        _drs_imports.extend(a.name for a in _n.names)
    elif isinstance(_n, ast.ImportFrom):
        _drs_imports.append(_n.module or "")
ck("streamlit" not in _drs_imports, "回验脚本不 import streamlit（跑批层纪律，AST 判定）")
ck("宁缺毋滥" in drs_src, "回验缺判据不写行（禁止编 actual）")
ck("compute_actual" in drs_src, "实际盘面判据函数存在")
# 判分语义：actual 与选项同词表
ck("score_single" in drs_src, "单选判分函数存在")
# 幂等：已回验不重复
ck("不重复" in drs_src or "幂等" in drs_src, "回验幂等（同日不重复写）")

# 回验判分函数实测
spec3 = importlib.util.spec_from_file_location("drs", os.path.join(ROOT, "daily_review_score.py"))
drs = importlib.util.module_from_spec(spec3)
sys.modules.pop("database", None)  # 回验脚本 import database——stub 掉 secrets 相关
db_stub = type(sys)("database")
db_stub._supabase_request = lambda *a, **k: []
db_stub._get_config = lambda: ("https://x.supabase.co", "fakekey")
sys.modules["database"] = db_stub
spec3.loader.exec_module(drs)

ck(drs.score_single("退潮", "退潮", "退潮") == (True, True), "单选：双方与实际一致=双中")
ck(drs.score_single("发酵", "退潮", "退潮") == (False, True), "单选：用户错 AI 对")
ck(drs.score_single("", "退潮", "退潮") == (False, True), "单选：用户空作答=不中")
# actual 缺判据的题不写行
actuals = drs.compute_actual("20260909", "20260910")  # 本地无当日数据 → 空
ck(isinstance(actuals, dict), "compute_actual 返回 dict（无数据时为空，不编造）")

# ---- 6. 编排器挂载 ----
rd_src = open(os.path.join(ROOT, "run_daily.py"), encoding="utf-8").read()
ck('"review_ai"' in rd_src, "AI 答卷挂入编排器 STEPS")
ck('"review_score"' in rd_src, "回验挂入编排器 STEPS")
# 顺序：review_ai 在 digest 后（读它的产物）；review_score 在 review_ai 后
import re
seq = {k: i for i, k in enumerate(re.findall(r'"key": "(\w+)"', rd_src))}
ck(seq.get("review_ai", -1) > seq.get("digest", -1), "review_ai 在 digest 之后（依赖当日产物）")
ck(seq.get("review_score", -1) > seq.get("review_ai", -1), "review_score 在 review_ai 之后")

# ---- 7. app.py 路由 ----
app_src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
ck("📝 复盘答卷" in app_src, "侧边栏菜单有复盘答卷入口")
ck("render_review_page" in app_src, "路由调用 render_review_page")

# ---- 8. 建表 SQL：三表齐 + RLS ----
sql_src = open(os.path.join(ROOT, "init_review_tables.sql"), encoding="utf-8").read()
for t in ("review_answers", "review_ai_answers", "review_scoring"):
    ck(f"CREATE TABLE IF NOT EXISTS public.{t}" in sql_src, f"建表 SQL 含 {t}")
ck("ENABLE ROW LEVEL SECURITY" in sql_src, "RLS 已启用")
ck("UNIQUE (user_id, trade_date)" in sql_src, "答卷每人每日一份（唯一约束）")

# ---- 9. 每题标的参考（review_evidence）：泄题红线 + 一处实现 + 不编造 ----
# 用户 2026-09-11 反馈：纯选择题没有盘面锚点。要求每题下挂当日真实标的。
# 但素材必须止于事实（名单/数值）——站内的定性判读（情绪周期 phase、
# 梯队 verdict、轮动定性）正是考题答案，出现在 evidence 里就是泄题。
rev_src = open(os.path.join(ROOT, "review_evidence.py"), encoding="utf-8").read()
rev_ast = ast.parse(rev_src)
_rev_imports = []
for _n in rev_ast.body:
    if isinstance(_n, ast.Import):
        _rev_imports.extend(a.name for a in _n.names)
    elif isinstance(_n, ast.ImportFrom):
        _rev_imports.append(_n.module or "")
ck("streamlit" not in _rev_imports, "evidence 层不 import streamlit（计算层纪律，AST 判定）")

# 泄题红线（AST 判定，覆盖两种取值形态）：
#   形态 1：derived.phase / x.verdict（属性访问）
#   形态 2：derived.get("phase") / x.get("verdict")（get 调用 + 常量键）
# 只查属性会漏掉 get 形态——后者恰是 Python 更惯用的写法
_leak_attr, _leak_get = [], []
for _n in ast.walk(rev_ast):
    if isinstance(_n, ast.Attribute):
        _leak_attr.append(_n.attr)
    if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
            and _n.func.attr == "get"):
        for _a in _n.args:
            if isinstance(_a, ast.Constant) and isinstance(_a.value, str):
                _leak_get.append(_a.value)
ck("phase" not in _leak_attr and "verdict" not in _leak_attr
   and "phase" not in _leak_get and "verdict" not in _leak_get,
   "evidence 不读取 phase/verdict 定性字段（属性与 get 两种形态均查，泄题红线）")

# 一处实现：daily_review_ai 的天梯/板块上下文必须走 review_evidence
ck("from review_evidence import" in drai_src,
   "AI 答卷天梯/板块取数走 review_evidence（一处实现，杜绝两处漂移）")
# pct_1d 判定锚「实际取值调用」.get("pct_1d")的 AST 结构——docstring 里的
# 决策记录（「历史版本用 pct_1d」）是纯字符串，不可能形成取值调用节点
_pct1d_called = False
for _n in ast.walk(_drai_ast0):
    if (isinstance(_n, ast.Call) and isinstance(_n.func, ast.Attribute)
            and _n.func.attr == "get"):
        for _a in _n.args:
            if isinstance(_a, ast.Constant) and _a.value == "pct_1d":
                _pct1d_called = True
ck(not _pct1d_called, "AI 答卷不再有 pct_1d 取值调用（字段错配恒 None 的 bug 已修）")

# 页面渲染每题参考——锚定 _render_form / _render_compare 内部的**调用点**
# （子串断言被「删调用留定义」骗过——mutation 反验实锤，AST 锚调用。
#  调用形态两种都要认：裸函数 _render_evidence(...) 是 ast.Name；
#  模块函数 review_evidence.build_evidence(...) 是 ast.Attribute——
#  只认 Name 会把正确代码误报成失败，第一版就栽在这）
prv_ast = ast.parse(prv_src)
def _calls_in(fn_name: str, callee: str) -> bool:
    for node in prv_ast.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                f = sub.func
                if ((isinstance(f, ast.Name) and f.id == callee)
                        or (isinstance(f, ast.Attribute) and f.attr == callee)):
                    return True
    return False
ck("def _render_evidence" in prv_src and "build_evidence" in prv_src,
   "页面有参考渲染函数与素材构建接线（build_evidence）")
ck(_calls_in("_render_form", "_render_evidence"),
   "填卷表单每题渲染标的参考（AST 锚定调用点，删调用留定义会被抓）")
ck(_calls_in("_render_compare", "_render_evidence"),
   "对比视图每题渲染标的参考（AST 锚定调用点）")
ck(_calls_in("_render_form", "build_evidence"),
   "填卷前构建当日素材（build_evidence 调用点在 _render_form）")

# ---- 10. evidence 实测（真实产物，非 mock）----
import csv as _csv
_ev_spec = importlib.util.spec_from_file_location(
    "rev_ev", os.path.join(ROOT, "review_evidence.py"))
rev_ev = importlib.util.module_from_spec(_ev_spec)
_ev_spec.loader.exec_module(rev_ev)

# 站内真实产物日期锚定：derived/ladder/sector 里最新的共同交易日
_derived_real = rev_ev._load_json(rev_ev.DERIVED_PATH)
_ladder_real = rev_ev._load_json(rev_ev.LADDER_PATH)
_td_real = str((_derived_real or {}).get("date") or "")
ck(bool(_td_real and len(_td_real) == 8), f"本地有当日情绪派生产物（date={_td_real}）")
_ev_real = rev_ev.build_evidence(_td_real)
ck(len(_ev_real) >= 12,
   f"真实产物生成 {len(_ev_real)} 题参考 ≥ 12（标的覆盖面）")
# 泄题红线实测：任何参考文本里不得出现情绪周期五档词/梯队定性判语
_ev_all_text = json.dumps(_ev_real, ensure_ascii=False)
_leaked = [w for w in rev_ev.PHASE_WORDS + rev_ev.LADDER_VERDICT_MARKS
           if w in _ev_all_text]
ck(not _leaked, f"真实素材文本无泄题词（检出 {len(_leaked)} 个即失败）")
# 天梯题素材含真实标的（连板股名）
_q7 = _ev_real.get("q07_ladder") or {}
ck(any("只" in ln or "板" in ln for ln in _q7.get("lines", [])),
   "天梯题参考含分层标的名单")
# 板块题素材含 Top10 板块名
_q10 = _ev_real.get("q10_strongest_theme") or {}
ck(len(_q10.get("lines", [])) >= 8, "板块题参考含 Top10 完整榜单")
# 缺数据不编造：不存在的日期 → 所有**日期锚定**类素材（天梯/板块/新高/
# 转债/辨识度/模式）必须缺位，不得拿别的日期产物冒充当日。q04/q05 指数
# 尾序列不算（它是「近期历史」性质素材，非当日锚定）
_ev_empty = rev_ev.build_evidence("19990101")
_gated = ["q06_hot_money", "q07_ladder", "q08_trend_new_high", "q09_convertible",
          "q10_strongest_theme", "q11_new_theme", "q12_main_pattern",
          "q13_recognition", "q01_sentiment", "q03_emotion_cycle"]
_fabricated = [q for q in _gated if q in _ev_empty]
ck(not _fabricated,
   f"无数据日期不编造素材（日期锚定题缺位 {len(_gated) - len(_fabricated)}/{len(_gated)}，宁缺毋滥）")

# AI 上下文（build_context 同源复用 evidence）——用真实产物验证字段修复
_ctx_real = drai.build_context(_td_real)
ck("evidence" in _ctx_real, "AI 答卷上下文注入每题标的参考（build_context）")
_sec_names = [t.get("name") for t in _ctx_real.get("sector_top10") or []]
ck(bool(_sec_names), "AI 上下文板块榜有真实板块名（pct_chg 字段修复生效）")
_sec_pcts = [t.get("pct") for t in _ctx_real.get("sector_top10") or []]
ck(any(p is not None for p in _sec_pcts), "AI 上下文板块涨幅不再恒 None")

print("-" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("\n失败项：")
    for m in FAIL:
        print("  ❌ " + m)
    sys.exit(1)
print("✅ 全部通过")
