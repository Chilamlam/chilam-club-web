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
