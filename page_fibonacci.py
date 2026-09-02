"""主升浪黄金分割预测（斐波那契扩展线）。

标的解析与日 K 取数**全部复用 page_live_quote**，不再自己写一套：
  · resolve_candidates() —— 沪深北撞号消歧。老实现是 `6/5/9 开头算沪市否则深市`，
    这条规则对 `000300`（沪深300）直接取不到数，对 `000831` 会无条件当成深市个股
    「中国稀土」而拿不到沪市指数「500低贝」，对北交所 `920982` 两个前缀都是空。
    更坏的是腾讯对错前缀**不容错**：`sz000905` 返回的是「厦门港务」而不是中证500，
    图能正常画出来，只是画的是另一只标的 —— 静默取错比取不到更贵。
  · get_daily_kline_range() —— 按区间取前复权日 K，内部处理了三个静默陷阱：
    qfq 的 limit>=801 会退回 640 根、end 参数排他、newfqkline 忽略区间。
  · _render_search_box() —— 名称/拼音搜索，与行情页同一套双源实现。
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

import page_live_quote as lq


def render_fibonacci_chart():
    st.header("📏 主升浪黄金分割预测系统")
    st.caption("与「实时行情 + 技术分析」页共用同一套标的解析与前复权日 K 通道，"
               "支持 A股 / ETF / 指数 / 北交所 / 可转债；动态生成斐波那契扩展线，寻找主升浪目标位。")

    if "fib_symbol" not in st.session_state:
        st.session_state.fib_symbol = "601869"
    # 日期用 session_state 存默认值而不是给 date_input 传 value：
    # 两者同时给会触发 Streamlit 的 "created with a default value but also had
    # its value set via Session State" 警告，也让无头探针无法注入自定义区间。
    if "fib_start" not in st.session_state:
        st.session_state.fib_start = pd.to_datetime("2021-01-01").date()
    if "fib_end" not in st.session_state:
        st.session_state.fib_end = pd.Timestamp.today().date()

    lq._render_search_box(key_tag="fib_sr", state_key="fib_symbol",
                          widget_key="fib_search_kw")

    col1, col2, col3 = st.columns(3)
    with col1:
        # 刻意**不给 key**：带 key 的 widget 在 rerun 时优先用自己存的旧值、忽略
        # value= 参数，搜索结果按钮改了 fib_symbol 也刷不进这个框（点了没反应）。
        # 不给 key 时 widget 身份含 value，fib_symbol 一变就重建 —— 与行情页同款。
        symbol = st.text_input("股票代码", value=st.session_state.fib_symbol,
                               help="6 位数字，或带市场前缀（sh000831 沪市指数 / "
                                    "sz000831 深市个股 / bj920982 北交所）")
    with col2:
        start_date = st.date_input("起始日期", key="fib_start")
    with col3:
        end_date = st.date_input("结束日期", key="fib_end")

    if not symbol or not symbol.strip():
        st.info("请输入标的代码，或用上面的名称搜索。")
        return

    raw = symbol.strip()
    if start_date > end_date:
        st.error("起始日期晚于结束日期，请调整区间。")
        return

    if not lq.resolve_symbol(raw):
        st.error(f"无法识别代码 `{raw}`。A股/北交所填 6 位数字（或 sh/sz/bj 前缀）。")
        lq._fallback_to_search(raw, key_tag="fib_fb", state_key="fib_symbol")
        return

    cands = lq.resolve_candidates(raw)
    if not cands:
        st.error(f"未取到 `{raw}` 的行情，请确认代码是否正确。")
        lq._fallback_to_search(raw, key_tag="fib_fb", state_key="fib_symbol")
        return

    picked = lq._pick_candidate(raw, cands, key_prefix="fib")
    sym, quote = picked["sym"], picked["quote"]
    if sym["market"] not in ("A_SHARE", "BJ_SHARE"):
        st.warning(f"本页只支持 A股 / ETF / 指数 / 北交所 / 可转债；"
                   f"`{raw}` 属于{lq.MARKET_LABEL.get(sym['market'], '其它市场')}，"
                   f"请到「⚡ 实时行情 + 技术分析」页查看。")
        return

    df = lq.get_daily_kline_range(sym["tx_code"], sym["market"],
                                  start_date.strftime("%Y-%m-%d"),
                                  end_date.strftime("%Y-%m-%d"))
    if df.empty:
        st.warning(f"未能取到 `{sym['tx_code']}` 在 {start_date} ~ {end_date} 的日 K 数据。"
                   f"该区间可能早于标的上市日期，或数据源临时无响应。")
        return

    st.caption(f"**{quote.get('name', '')}** `{sym['tx_code']}` · "
               f"共 {len(df)} 根日 K（前复权）· "
               f"实际区间 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

    # ========== 黄金分割基准点：默认取区间内的绝对高低点，允许手动微调 ==========
    auto_min = float(df["low"].min())
    auto_max = float(df["high"].max())

    st.markdown("---")
    st.subheader("⚙️ 黄金分割基准点设置")
    # widget key 必须绑定「标的 + 区间」：带 key 的 number_input 在 rerun 时用自己
    # 存的旧值、忽略 value=。若 key 写死，从 601869(≈400) 换到 600519(≈1300) 后
    # 0/1 轴还留着上一个标的的价位，扩展线全错且图面看不出异常 —— 换标的即换 key。
    ident = f"{sym['tx_code']}_{df['date'].iloc[0]}_{df['date'].iloc[-1]}"
    cc1, cc2 = st.columns(2)
    with cc1:
        price_0 = st.number_input("【0 轴】价格 (基准低点)", value=auto_min,
                                  step=0.5, format="%.2f", key=f"fib_p0_{ident}")
    with cc2:
        price_1 = st.number_input("【1 轴】价格 (第一波高点)", value=auto_max,
                                  step=0.5, format="%.2f", key=f"fib_p1_{ident}")

    base_range = price_1 - price_0
    if base_range <= 0:
        st.error("【1 轴】必须高于【0 轴】，否则扩展线会倒挂。请调整基准点。")
        return

    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=df["date"],
        open=df["open"], high=df["high"],
        low=df["low"], close=df["close"],
        name="K线",
        increasing_line_color="red",     # A股习惯红涨
        decreasing_line_color="green",   # A股习惯绿跌
    ))
    fig.update_xaxes(type="category")    # 剔除周末/节假日的断层空白

    fib_levels = [0, 0.5, 0.618, 1, 1.618, 2, 2.382, 2.618, 3, 3.382,
                  3.618, 4, 4.382, 4.618, 5, 5.382, 6, 7, 8, 9]
    colors = ["#2ca02c", "#1f77b4", "#ff7f0e", "#d62728", "#9467bd",
              "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

    hi_cap = float(df["high"].max()) * 3.5
    lo_cap = float(df["low"].min()) * 0.5
    for i, level in enumerate(fib_levels):
        target_price = price_0 + base_range * level
        # 只画合理价格范围内的线，否则极限目标价会把图纵向压扁
        if target_price > hi_cap or target_price < lo_cap:
            continue
        color = colors[i % len(colors)]
        fig.add_hline(
            y=target_price,
            line_dash="dash" if level not in (0, 1) else "solid",
            line_color=color,
            line_width=1 if level not in (0, 1) else 2,
            annotation_text=f"{level} ({target_price:.2f})",
            annotation_position="right",
            annotation_font_color=color,
        )

    fig.update_layout(
        title=f"{quote.get('name', '')} {sym['tx_code']} 黄金分割扩展预测图",
        yaxis_title="价格", xaxis_title="日期",
        height=700, template="plotly_white",
        margin=dict(l=50, r=80, t=50, b=50),
        showlegend=False,
        xaxis_rangeslider_visible=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("斐波那契扩展位是几何推演出的参考价位，不代表价格必然到达，"
               "也不构成投资建议。")
