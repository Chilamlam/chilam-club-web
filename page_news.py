"""Public news headlines, with bounded fetching and explicit source attribution."""
from datetime import datetime
from html import escape, unescape
import json
import re
import urllib.request

import streamlit as st


def parse_news(payload):
    rows = payload.get("data", {}).get("fastNewsList", [])
    unique = {}
    for row in rows:
        code = str(row.get("code", ""))
        title = unescape(re.sub(r"<[^>]*>", "", str(row.get("title", "")))).strip()
        if code.isdigit() and title:
            unique[code] = {"title": title, "time": str(row.get("showTime", "")),
                            "url": f"https://finance.eastmoney.com/a/{code}.html"}
    if not unique:
        raise ValueError("No valid news headlines")
    return sorted(unique.values(), key=lambda row: row["time"], reverse=True)


@st.cache_data(ttl=90, show_spinner=False)
def fetch_news():
    url = "https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=40&req_trace=1"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=6) as response:
            rows = parse_news(json.load(response))
        return rows, datetime.now().astimezone().isoformat(timespec="seconds"), None
    except (OSError, ValueError, TypeError, AttributeError):
        return [], "", "资讯源暂不可用，请重试或通过下方链接阅读原站。"


def render_news_page():
    st.header("📰 资讯雷达")
    st.caption("来源：东方财富公开快讯。展示标题并链接原文，信息以来源页面为准。")
    cols = st.columns(3)
    for col, label, url in zip(cols, ["财联社电报", "东方财富快讯", "股掌财经 7×24"],
                               ["https://www.cls.cn/telegraph", "https://kuaixun.eastmoney.com/", "https://724.guzhang.com/"]):
        col.link_button(label, url)
    if st.button("刷新快讯", key="refresh_news"):
        fetch_news.clear()
    with st.spinner("正在读取快讯…"):
        rows, fetched, error = fetch_news()
    if error:
        st.warning(error)
        return
    st.caption(f"抓取时间 {fetched} · 缓存 90 秒 · 手动刷新，非自动推送。每条新闻的发布时间见标题上方。")
    query = st.text_input("筛选公司或主题", placeholder="例如：半导体、银行、回购")
    shown = [row for row in rows if query.strip().casefold() in row["title"].casefold()]
    if not shown:
        st.info("最近 40 条快讯中没有匹配项，不代表该主题没有新闻。")
    for row in shown:
        st.markdown(f'<div style="padding:14px 0;border-bottom:1px solid #8884">'
                    f'<small>{escape(row["time"])}</small><br>'
                    f'<a href="{row["url"]}" target="_blank" rel="noopener noreferrer">'
                    f'{escape(row["title"])}</a></div>', unsafe_allow_html=True)
