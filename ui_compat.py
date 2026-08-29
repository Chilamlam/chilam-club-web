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
