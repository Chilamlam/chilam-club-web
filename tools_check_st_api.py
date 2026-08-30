# -*- coding: utf-8 -*-
"""静态扫描：检查仓库里所有 st.xxx(...) 调用的关键字参数在目标 Streamlit 版本是否仍存在。"""
import ast
import inspect
import pathlib
import sys

import streamlit as st

ROOT = pathlib.Path(sys.argv[1])
print("Streamlit", st.__version__)

problems = []
for py in sorted(ROOT.rglob("*.py")):
    if ".git" in py.parts or "venv" in py.parts:
        continue
    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("PARSE-FAIL", py, exc)
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "st"):
            continue
        fn = getattr(st, f.attr, None)
        if fn is None:
            problems.append((py.name, node.lineno, f"st.{f.attr} 不存在"))
            continue

        # ---- switch_page / page_link 的目标必须是「已注册的页面」 ----
        # Streamlit 只认：入口脚本（app.py）与 pages/ 目录下的文件。
        # 传根目录的其它脚本会在运行时抛 StreamlitAPIException，
        # 而 Cloud 上错误详情被脱敏 → 只能看到一个没有信息量的红框。
        # 这类错误静态完全可判定，不该留到线上才发现。
        #
        # 位置很关键：必须放在下面「签名带 **kwargs 就 continue」之前。
        # st.switch_page 被 gather_metrics 装饰，包装函数签名是 (*args, **kwargs)，
        # 放在 continue 之后这段代码永远走不到 —— 一条永不失败的断言等于没有断言。
        if f.attr in ("switch_page", "page_link") and node.args:
            arg0 = node.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                target = arg0.value
                if target.startswith(("http://", "https://")):
                    pass  # page_link 允许外链
                elif target == "app.py":
                    pass  # 入口脚本
                elif not target.startswith("pages/"):
                    problems.append((py.name, node.lineno,
                                     f"st.{f.attr}(\"{target}\") 不是已注册页面："
                                     "只能是 app.py 或 pages/ 下的文件"))
                elif not (ROOT / target).exists():
                    problems.append((py.name, node.lineno,
                                     f"st.{f.attr}(\"{target}\") 指向的文件不存在"))

        try:
            params = inspect.signature(fn).parameters
        except (TypeError, ValueError):
            continue
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            continue
        for kw in node.keywords:
            if kw.arg and kw.arg not in params:
                problems.append((py.name, node.lineno, f"st.{f.attr}() 不支持参数 {kw.arg}"))

if problems:
    for name, line, msg in problems:
        print(f"[X] {name}:{line}  {msg}")
else:
    print("[OK] 未发现不兼容的 Streamlit 调用参数")
