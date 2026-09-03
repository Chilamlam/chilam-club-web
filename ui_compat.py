# -*- coding: utf-8 -*-
"""Streamlit 跨版本兼容层。

背景：Streamlit 对「铺满容器宽度」这个参数改过三次名字：
    老版本  st.image(path, use_column_width=True)
    中间版  st.image(path, use_container_width=True)   # 已标记 deprecated
    新版本  st.image(path, width="stretch")            # 上面两个会被移除

Streamlit Cloud 会自动升级依赖（requirements.txt 未锁版本），
一旦升到移除旧参数的版本，页面就会直接 TypeError 崩掉整页。
本模块统一按 新 -> 中 -> 旧 顺序降级调用，任何版本都不会报错。
"""

import streamlit as st


def image_stretch(image, **kwargs):
    """铺满父容器宽度显示图片，自动适配任意 Streamlit 版本。"""
    for attempt in (
        {"width": "stretch"},
        {"use_container_width": True},
        {"use_column_width": True},
        {},
    ):
        try:
            st.image(image, **attempt, **kwargs)
            return
        except TypeError:
            continue
    # 理论上不会走到这里；兜底为默认宽度
    st.image(image, **kwargs)


def html_embed(html: str, width: int = 10, height: int = 10) -> None:
    """
    嵌入一段原始 HTML（用于 GA 埋点等），跨版本安全。

    背景（2026-08-29 发现）：`st.components.v1.html` 已被官方标注
    「will be removed after 2026-06-01」，替代品是 `st.iframe`。
    今天已经过了那个日期，而 Streamlit Cloud 会自动升级依赖——
    这和此前把整页打崩的 `st.image(use_column_width=)` 是完全同一类隐患：
    参数/API 被移除 → 模块顶层调用抛异常 → 整站白屏。

    但 `st.iframe(src=...)` 只接受 URL 或路径，**不接受 HTML 字符串**，
    所以不能直接替换：这里用 data URI 承载内联 HTML。
    调用顺序：新 API（data URI）→ 旧 components.html → 静默放弃。
    埋点失败绝不能影响页面渲染，所以最终一定吞掉异常。
    """
    if hasattr(st, "iframe"):
        try:
            import base64
            b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
            # charset 必须显式声明：data URI 文档无编码声明时，浏览器可能按
            # 本地遗留码页（如中文 Windows 的 GBK）解码 → 中文全变乱码
            # （2026-09-03 板块轮动排名矩阵实测）。meta 兜一层，双保险。
            payload = '<meta charset="utf-8">' + html
            b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
            st.iframe(
                f"data:text/html;charset=utf-8;base64,{b64}",
                width=width, height=height,
            )
            return
        except Exception:
            pass
    try:
        import streamlit.components.v1 as components
        components.html(html, width=width, height=height)
    except Exception:
        # 埋点不是功能，失败就静默跳过，绝不冒风险影响页面
        pass
