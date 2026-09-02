# -*- coding: utf-8 -*-
"""
战绩回看展示层自检（不启动 streamlit runtime，只做 AST 结构断言）

要防的错：
1. 新增的 beta / 有效样本量两块必须真的被 _render_strategy 调用，
   而不是只定义了函数没接上（改动后最常见的静默失败）。
2. 免责声明必须落在「榜单上方」与「战绩页」两处；只写在页脚等于没写。
3. 强势股页不得再出现「只买 RPS 大于 87」这类建议性措辞。
4. 断言必须匹配语法结构（函数调用节点），而非裸子串——
   注释里提到函数名不能算"已调用"。
"""
from __future__ import annotations

import ast
import os
import sys

FAIL = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


ROOT = os.path.dirname(os.path.abspath(__file__))


def _src(fname: str) -> str:
    return open(os.path.join(ROOT, fname), encoding="utf-8").read()


def _called_names(tree: ast.AST, func_name: str) -> set[str]:
    """取出指定函数体内所有被调用的函数名（含 st.xxx 的属性调用）。"""
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


# ---------- 1) 新增两块必须真的接进渲染链 ----------
sc_src = _src("page_scorecard.py")
sc_tree = ast.parse(sc_src)

defined = {n.name for n in ast.walk(sc_tree) if isinstance(n, ast.FunctionDef)}
ck("定义了 _render_beta", "_render_beta" in defined)
ck("定义了 _render_effective_sample", "_render_effective_sample" in defined)

called = _called_names(sc_tree, "_render_strategy")
ck("_render_strategy 里真的调用了 _render_beta（语法结构断言，非子串）",
   "_render_beta" in called, f"实际调用 {sorted(called)}")
ck("_render_strategy 里真的调用了 _render_effective_sample",
   "_render_effective_sample" in called)

# ---------- 2) 计算层字段必须被展示层读到 ----------
for key in ("beta", "effective_sample", "adj_alpha_median", "n_independent", "p_value"):
    ck(f"展示层读取了字段 {key}", f'"{key}"' in sc_src or f"'{key}'" in sc_src)

# ---------- 3) 免责声明落点 ----------
app_src = _src("app.py")
app_tree = ast.parse(app_src)

stock_calls = _called_names(app_tree, "render_stock_content")
ck("强势股榜单函数内有 st.info/st.warning 形式的提示（声明在榜单上方）",
   bool({"info", "warning"} & stock_calls), f"实际调用 {sorted(stock_calls)}")


def _func_src(src: str, tree: ast.AST, name: str) -> str:
    """取指定函数的源码切片。全文扫描会因为别处有同样的字眼而误判。"""
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return "\n".join(lines[node.lineno - 1: node.end_lineno])
    return ""


stock_src = _func_src(app_src, app_tree, "render_stock_content")
ck("强势股函数体内（而非全文别处）含「不构成投资建议」",
   "不构成投资建议" in stock_src, f"函数体 {len(stock_src)} 字符")
ck("强势股函数体内说明了 RPS 是事后统计而非选股建议",
   "不是选股建议" in stock_src)

main_src = _func_src(app_src, app_tree, "main")
ck("侧边栏（main 函数体内）挂了全站免责声明",
   "免责声明" in main_src and "不构成投资建议" in main_src)

sc_calls = _called_names(sc_tree, "render_scorecard_page")
ck("战绩页有 st.warning 形式的风险提示块",
   "warning" in sc_calls, f"实际调用 {sorted(sc_calls)}")
page_src = _func_src(sc_src, sc_tree, "render_scorecard_page")
ck("战绩页函数体内声明含「历史表现不代表未来收益」",
   "历史表现不代表未来收益" in page_src)
ck("战绩页函数体内声明含「不是可交易收益」的口径澄清",
   "不是可交易收益" in page_src)
ck("战绩页函数体内声明含「不构成投资建议」",
   "不构成投资建议" in page_src)

# ---------- 4) 建议性措辞必须清除 ----------
# 坑：免责声明本身就要点名这些禁项（"不提供目标价、买卖点"），
# 直接全文扫描必然误报。先剔除否定语境的行，再扫剩下的正文。
# 这个坑在 tools_probe_digest.py 里已经踩过一次，此处沿用同一处理方式。
NEGATION = ("不提供", "不含", "不构成", "禁止", "不是", "不得", "无法", "别拿", "不预测")


def _positive_lines(src: str) -> list[str]:
    return [ln for ln in src.splitlines()
            if ln.strip() and not any(w in ln for w in NEGATION)]


app_positive = "\n".join(_positive_lines(app_src))
BANNED = ["只买 RPS", "核心玩法：只买", "建议买入", "推荐买入", "目标价", "满仓", "重仓买"]
for w in BANNED:
    ck(f"app.py 正文（剔除否定语境后）无建议性措辞「{w}」", w not in app_positive)

# 反向验证过滤器本身有效：否定语境里的「目标价」应当被剔除，
# 而肯定语境里的必须能抓到。这两条都不通过就说明上面那组断言是假断言。
ck("过滤器保留了肯定语境（自检）",
   "目标价" in "\n".join(_positive_lines("目标价 30 元\n不提供目标价")))
ck("过滤器剔除了否定语境（自检）",
   "目标价" not in "\n".join(_positive_lines("不提供目标价、买卖点")))

# ---------- 5) 计算层门槛常量必须存在 ----------
core = _src("scorecard.py")
for const in ("MIN_DAYS_BETA", "MIN_INDEPENDENT"):
    ck(f"scorecard.py 定义了 {const}", f"{const} = " in core)
ck("MIN_INDEPENDENT 在口径说明里明写成门槛（用户能看到具体数）",
   "sc.MIN_INDEPENDENT" in sc_src)
ck("MIN_DAYS_BETA 不足时的 reason 带上具体阈值",
   "阈值 {MIN_DAYS_BETA}" in core)

# ---------- 6) summarize 必须写出这两块 ----------
summ = _called_names(ast.parse(core), "summarize")
ck("summarize 内调用 estimate_beta", "estimate_beta" in summ, f"实际 {sorted(summ)}")
ck("summarize 内调用 effective_sample", "effective_sample" in summ)

if __name__ == "__main__":
    print("-" * 60)
    if FAIL:
        print(f"❌ 失败 {len(FAIL)} 项：{FAIL}")
        sys.exit(1)
    print("✅ 全部通过")
