import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests

def render_fibonacci_chart():
    st.header("📏 主升浪黄金分割预测系统")
    st.caption("直连腾讯极速 K 线接口，动态生成斐波那契扩展线，寻找主升浪目标位。")

    # ==========================
    # 1. 侧边栏控制区
    # ==========================
    col1, col2, col3 = st.columns(3)
    with col1:
        symbol = st.text_input("股票代码", value="601869", help="输入6位数字代码")
    with col2:
        start_date = st.date_input("起始日期", pd.to_datetime("2021-01-01"))
    with col3:
        end_date = st.date_input("结束日期", pd.to_datetime("today"))

    # 腾讯 API 需要使用带横杠的日期格式
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    # ==========================
    # 2. 获取数据 (直连腾讯底层接口，免疫东方财富封锁)
    # ==========================
    @st.cache_data(ttl=3600) 
    def get_kline_data(code, start, end):
        try:
            # 自动判断沪深前缀 (6,5,9开头为沪市，其余暂归深市)
            market = "sh" if str(code).startswith(("6", "5", "9")) else "sz"
            sym = f"{market}{code}"
            
            # 腾讯极速 K 线 API (自带 qfq 前复权)
            url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,{start},{end},1000,qfq"
            
            resp = requests.get(url, timeout=10)
            data = resp.json()
            
            stock_data = data['data'].get(sym, {})
            # 优先获取前复权数据 (qfqday)，如果没有则退化为不复权 (day)
            kline_list = stock_data.get('qfqday', stock_data.get('day', []))
            
            if not kline_list:
                st.warning("未能获取到K线数据，请检查日期或代码是否正确。")
                return pd.DataFrame()
                
            # 解析腾讯返回的列表 [日期, 开盘, 收盘, 最高, 最低, 成交量...]
            df = pd.DataFrame(kline_list)
            df = df.iloc[:, 0:5] # 我们只需要前5列
            df.columns = ['date', 'open', 'close', 'high', 'low']
            
            # 转换数据类型为浮点数
            for col in ['open', 'close', 'high', 'low']:
                df[col] = df[col].astype(float)
                
            return df
            
        except Exception as e:
            st.error(f"获取数据失败，请检查网络或代码: {e}")
            return pd.DataFrame()

    df = get_kline_data(symbol, start_str, end_str)

    if not df.empty:
        # ==========================
        # 3. 黄金分割基准点自动提取与微调
        # ==========================
        # 自动寻找这段时间内的绝对最低点和最高点作为默认值
        auto_min = float(df['low'].min())
        auto_max = float(df['high'].max())

        st.markdown("---")
        st.subheader("⚙️ 黄金分割基准点设置")
        cc1, cc2 = st.columns(2)
        with cc1:
            price_0 = st.number_input("【0 轴】价格 (基准低点)", value=auto_min, step=0.5, format="%.2f")
        with cc2:
            price_1 = st.number_input("【1 轴】价格 (第一波高点)", value=auto_max, step=0.5, format="%.2f")

        base_range = price_1 - price_0

        # ==========================
        # 4. Plotly 绘制 K线与斐波那契线
        # ==========================
        fig = go.Figure()

        # 添加 K 线图
        fig.add_trace(go.Candlestick(
            x=df['date'],
            open=df['open'], high=df['high'],
            low=df['low'], close=df['close'],
            name="K线",
            increasing_line_color='red',    # A股习惯红涨
            decreasing_line_color='green'   # A股习惯绿跌
        ))

        # 剔除周末和节假日导致的 K 线断层空白
        fig.update_xaxes(type='category')

        # 定义截图中出现的所有斐波那契档位
        fib_levels = [0, 0.5, 0.618, 1, 1.618, 2, 2.382, 2.618, 3, 3.382, 3.618, 4, 4.382, 4.618, 5, 5.382, 6, 7, 8, 9]
        
        # 配色方案循环
        colors = ['#2ca02c', '#1f77b4', '#ff7f0e', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']

        # 循环绘制每一条水平线
        for i, level in enumerate(fib_levels):
            # 核心计算公式
            target_price = price_0 + (base_range * level)
            
            # 只显示在合理价格范围内的线（防止图表被极限目标价压缩得太小）
            if target_price > df['high'].max() * 3.5 or target_price < df['low'].min() * 0.5: 
                continue

            color = colors[i % len(colors)]
            
            # 画水平线
            fig.add_hline(
                y=target_price, 
                line_dash="dash" if level not in [0, 1] else "solid", # 0和1用实线，其他用虚线
                line_color=color, 
                line_width=1 if level not in [0, 1] else 2,
                annotation_text=f"{level} ({target_price:.2f})",
                annotation_position="right",
                annotation_font_color=color
            )

        # 图表布局优化
        fig.update_layout(
            title=f"{symbol} 黄金分割扩展预测图",
            yaxis_title="价格",
            xaxis_title="日期",
            height=700,
            template="plotly_white",
            margin=dict(l=50, r=80, t=50, b=50),
            showlegend=False,
            xaxis_rangeslider_visible=False # 隐藏下方讨厌的 range slider
        )

        # 在 Streamlit 中渲染图表
        st.plotly_chart(fig, use_container_width=True)
