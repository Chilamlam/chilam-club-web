"""投资驾驶舱导航分组；页面名称与 app.py 路由保持一致。"""

NAV_GROUPS = {
    "我的": (
        "我的复盘",
        ["🗂 我的复盘", "📒 研究档案"],
    ),
    "看市场": (
        "市场",
        ["🛸 全市场看板", "🔄 板块轮动", "🧩 题材跟踪", "🏭 行业信号", "🌐 宏观与股债性价比", "📰 资讯雷达"],
    ),
    "找标的": (
        "选股",
        ["🔥 强势股", "🧭 多策略选股", "📐 强势股区间偏离", "⚡ 实时行情 + 技术分析", "⚡ 投机与套利"],
    ),
    "做复盘": (
        "跟踪与记录",
        ["⭐ 我的池子 (每日复盘)", "📊 名单表现", "📮 收盘摘要", "📋 晚间复盘与次日计划", "📝 复盘答卷", "🎯 战绩回看"],
    ),
    "工具与帮助": (
        "工具与帮助",
        ["📚 机构持仓披露", "📏 黄金分割参考位", "📖 使用说明文档"],
    ),
}

NAV_PAGES = tuple(
    page
    for _, items in NAV_GROUPS.values()
    for page in items
)

DEFAULT_PAGE = "🗂 我的复盘"
SECTION_PAGE = "📋 晚间复盘与次日计划"
PAGE_ALIASES = {
    "👁 观察记录": "📒 研究档案",
    "🧭 复盘工作台": "🧭 多策略选股",
    "🚨 核心龙头雷达": "📐 强势股区间偏离",
    "📏 黄金分割预测": "📏 黄金分割参考位",
    "📈 命运K线 (人生运势)": DEFAULT_PAGE,
    "📚 投资作业本": "📚 机构持仓披露",
}


def canonical_page(requested) -> str:
    """Resolve an old or unknown page name to a current route."""
    value = str(requested or DEFAULT_PAGE)
    value = PAGE_ALIASES.get(value, value)
    return value if value in NAV_PAGES else DEFAULT_PAGE


def render_navigation():
    """Visible one-click destinations; URL supports reload, links and browser back."""
    import streamlit as st

    raw_requested = str(st.query_params.get("view", DEFAULT_PAGE) or DEFAULT_PAGE)
    requested = canonical_page(raw_requested)
    if raw_requested != requested:
        st.query_params["view"] = requested
    if st.session_state.get("nav_url") != requested:
        st.session_state["active_page"] = requested
        st.session_state["nav_url"] = requested
    page = st.session_state.get("active_page", DEFAULT_PAGE)

    def select(target):
        st.session_state["active_page"] = target
        st.session_state["nav_url"] = target
        st.query_params["view"] = target
        if target != SECTION_PAGE and "section" in st.query_params:
            del st.query_params["section"]

    def buttons(items):
        for target in items:
            st.button(target, key=f"nav_direct_{target}", width="stretch",
                      type="primary" if page == target else "tertiary",
                      on_click=select, args=(target,))

    for group, (label, items) in NAV_GROUPS.items():
        with st.expander(label, expanded=page in items):
            buttons(items)
    with st.expander("页面快捷方式"):
        from urllib.parse import quote
        st.caption("任何页面都可直接访问。收藏下面的链接，下次从你选择的页面开始。")
        target = st.selectbox("下次从哪里开始", NAV_PAGES, index=NAV_PAGES.index(page), key="navigation_start_page")
        st.link_button("打开并收藏此页", "?view=" + quote(target), width="stretch")
    return page
