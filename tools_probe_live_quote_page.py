# -*- coding: utf-8 -*-
"""行情页无头渲染自检：确认页面真的能画出来，而不只是底层函数能取到数。

底层探针（tools_probe_quote_api.py）只验数据通道，验不出「Streamlit 组件挂了」
或「撞号时选择器没出现」。这里用 streamlit.testing 起无头 runtime 实跑页面：
  1) 默认标的（600519）能渲染且无异常
  2) 撞号代码 000831 必须出现候选选择器，且默认选中「中国稀土」
  3) 输入显式前缀 sh000831 时**不**出现选择器（不能拿消歧去烦已经说清楚的用户）
  4) 名称搜索框输入「中国稀土」后要出现可点的结果按钮
"""
import pathlib
import sys
import tempfile

from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parent
# 壳脚本写到系统临时目录，不在仓库里留垃圾（AppTest 只接受文件路径，故必须落盘）
SCRIPT = pathlib.Path(tempfile.gettempdir()) / "_lq_probe_app.py"
SCRIPT.write_text(
    "import sys\n"
    f"sys.path.insert(0, r'{ROOT}')\n"
    "from page_live_quote import render_live_quote_page\n"
    "render_live_quote_page()\n",
    encoding="utf-8",
)

fail = 0


def bad(msg):
    global fail
    fail += 1
    print(f"[FAIL] {msg}")


def run(symbol=None, search=None, timeout=90):
    at = AppTest.from_file(str(SCRIPT), default_timeout=timeout)
    if symbol:
        at.session_state["active_symbol"] = symbol
    if search:
        at.session_state["lq_search_kw"] = search
    at.run()
    return at


def texts(at):
    out = []
    for coll in (at.markdown, at.caption, at.warning, at.error, at.info):
        out += [e.value for e in coll]
    return "\n".join(out)


print("=" * 68)
print("行情页无头渲染自检")
print("=" * 68)

# 1. 默认标的
at = run("600519")
if at.exception:
    bad(f"600519 渲染抛异常: {at.exception[0].message}")
else:
    metric_vals = [m.value for m in at.metric]
    print(f"  OK   600519 渲染无异常，metric={metric_vals[:2]}")

# 2. 撞号代码必须出现选择器，且默认选「中国稀土」
at = run("000831")
if at.exception:
    bad(f"000831 渲染抛异常: {at.exception[0].message}")
else:
    radios = [r for r in at.radio if "哪一个" in (r.label or "")]
    if not radios:
        bad("000831: 撞号时没有出现候选选择器 —— 系统在静默替用户决定")
    else:
        opts = list(radios[0].options)
        if "中国稀土" not in (opts[0] if opts else ""):
            bad(f"000831: 默认选项不是中国稀土，实为 {opts[:1]}")
        elif not any("500低贝" in o for o in opts):
            bad(f"000831: 候选里丢了沪市指数 500低贝，实为 {opts}")
        else:
            print(f"  OK   000831 出现选择器，默认={opts[0]}｜另有 {opts[1:]}")
        if "多个市场都有标的" not in texts(at):
            bad("000831: 没有给出撞号提示文案")

# 3. 显式前缀不该再问一遍
at = run("sh000831")
if at.exception:
    bad(f"sh000831 渲染抛异常: {at.exception[0].message}")
else:
    radios = [r for r in at.radio if "哪一个" in (r.label or "")]
    names = [m.value for m in at.metric]
    if radios:
        bad("sh000831: 显式前缀仍弹出选择器 —— 用户已经说清楚了，不该再问")
    elif not any("500低贝" in str(v) for v in names):
        bad(f"sh000831: 未命中 500低贝，metric={names[:2]}")
    else:
        print("  OK   sh000831 直接命中 500低贝，未弹选择器")

# 4. 名称搜索
# 注意: PRESETS 里也有一个「中国稀土 `sz000831`」按钮，只判名称会在搜索挂掉时**假通过**。
# 搜索结果按钮的标签是 f"{name}\n`{query}`"（换行），预设是空格分隔 —— 用换行区分。
at = run("600519", search="中国稀土")
if at.exception:
    bad(f"名称搜索渲染抛异常: {at.exception[0].message}")
else:
    hit = [b.label for b in at.button if "中国稀土" in b.label and "\n`" in b.label]
    if not hit:
        labels = [b.label for b in at.button]
        bad(f"搜索「中国稀土」未生成结果按钮（按钮={labels[:6]}）")
    elif not any("00769" in l for l in hit):
        bad(f"搜索结果缺了港股同名标的 00769，实为 {hit}")
    else:
        print(f"  OK   搜索「中国稀土」生成 {len(hit)} 个结果按钮: {hit}")

print(f"\n{'=' * 68}\n失败项: {fail}")
sys.exit(1 if fail else 0)
