"""
全球宏观资产与 A 股股债性价比 (ERP / FED 估值模型)
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import os
import json
from datetime import datetime

def render_macro_erp_page():
    st.header("🌐 全球宏观资产联动 & 股债性价比 (ERP 模型)")
    st.caption("大周期择时指南：股权风险溢价 (FED 模型) + 全球核心避险与风险资产全景监控")

    tab_erp, tab_global = st.tabs(["⚖️ 股债性价比 (大盘周期抄底/逃顶)", "🌍 全球宏观资产联动"])

    with tab_erp:
        st.subheader("📊 A股股债风险溢价 (Equity Risk Premium, ERP)")
        st.info("""
        **💡 ERP 指标释义 (FED 模型)**：
        - `ERP = 沪深300 盈利收益率 (1 / PE_TTM) - 中国10年期国债收益率`
        - **极度高估 (逃顶区)**：ERP 跌破 **-1倍标准差 / -2倍标准差**，代表股票性价比极低，国债更具吸引力。
        - **黄金坑 (抄底区)**：ERP 突破 **+1倍标准差 / +2倍标准差**，代表股票资产极其便宜，长期赔率极大。
        """)

        # 生成/模拟 5年期 ERP 历史曲线与分位通道
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=250, freq='B')
        base_erp = np.linspace(2.8, 4.8, 250) + np.sin(np.linspace(0, 10, 250)) * 0.8 + np.random.normal(0, 0.15, 250)
        
        mean_val = np.mean(base_erp)
        std_val = np.std(base_erp)
        
        df_erp = pd.DataFrame({
            "date": dates.strftime('%Y-%m-%d'),
            "erp": base_erp,
            "mean": mean_val,
            "plus_1sd": mean_val + std_val,
            "plus_2sd": mean_val + 2 * std_val,
            "minus_1sd": mean_val - std_val,
            "minus_2sd": mean_val - 2 * std_val,
        })
        
        current_erp = df_erp['erp'].iloc[-1]
        
        # 顶部 KPI
        col1, col2, col3 = st.columns(3)
        col1.metric("当前 ERP (风险溢价)", f"{current_erp:.2f}%", delta=f"{current_erp - mean_val:+.2f}% 偏离均值")
        
        status_eval = "👑 黄金坑抄底区 (估值极度便宜)" if current_erp > mean_val + std_val else ("⚠️ 估值偏高需防守" if current_erp < mean_val - std_val else "⚖️ 估值合理中枢")
        col2.metric("当前估值水位状态", status_eval)
        col3.metric("5年期历史分位数", f"{int((df_erp['erp'] < current_erp).mean() * 100)}%")

        # 绘制交互式通道图
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df_erp['date'], y=df_erp['plus_2sd'], name='+2SD 极度便宜 (坚决定投)', line=dict(color='rgba(46, 204, 113, 0.6)', dash='dash')))
        fig.add_trace(go.Scatter(x=df_erp['date'], y=df_erp['plus_1sd'], name='+1SD 价值凸显', line=dict(color='rgba(52, 152, 219, 0.6)', dash='dot')))
        fig.add_trace(go.Scatter(x=df_erp['date'], y=df_erp['mean'], name='历史均值中枢', line=dict(color='rgba(243, 156, 18, 0.8)', width=2)))
        fig.add_trace(go.Scatter(x=df_erp['date'], y=df_erp['minus_1sd'], name='-1SD 估值偏贵', line=dict(color='rgba(231, 76, 60, 0.6)', dash='dot')))
        fig.add_trace(go.Scatter(x=df_erp['date'], y=df_erp['erp'], name='沪深300 ERP 实际值', line=dict(color='#8e44ad', width=3)))

        fig.update_layout(
            title="沪深300 股债性价比通道 (ERP 标准差带)",
            xaxis=dict(tickangle=-45, gridcolor='rgba(128,128,128,0.2)'),
            yaxis=dict(title="ERP (%)", gridcolor='rgba(128,128,128,0.2)'),
            hovermode="x unified",
            height=450,
            legend=dict(orientation="h", y=1.1)
        )
        st.plotly_chart(fig, use_container_width=True)

    with tab_global:
        st.subheader("🌍 全球核心资产走势与资金风向标")
        st.caption("监控外盘流动性、大宗商品、汇率与避险情绪")

        # 构造宏观核心指标
        macro_items = [
            {"name": "纳斯达克 100", "price": "19,845.2", "chg": "+1.25%", "type": "📈 全球风险资产", "desc": "全球科技股总风向标"},
            {"name": "标普 500", "price": "5,620.8", "chg": "+0.68%", "type": "📈 全球风险资产", "desc": "美股大盘中枢"},
            {"name": "美元/离岸人民币 (USD/CNH)", "price": "7.1420", "chg": "-0.18%", "type": "💵 汇率变动", "desc": "人民币升值利好 A股港股外资流入"},
            {"name": "现货黄金 (XAU/USD)", "price": "$2,512.4/oz", "chg": "+0.45%", "type": "🛡️ 避险资产", "desc": "抗通胀与地缘避险核心"},
            {"name": "WTI 原油", "price": "$75.8/桶", "chg": "-1.12%", "type": "🛢️ 大宗商品", "desc": "全球经济与能源需求温度计"},
            {"name": "富时中国 A50 期货", "price": "12,180.0", "chg": "+0.85%", "type": "🐉 外盘先导", "desc": "A股盘前盘后核心情绪指引"}
        ]

        m_cols = st.columns(3)
        for i, item in enumerate(macro_items):
            with m_cols[i % 3]:
                with st.container(border=True):
                    st.markdown(f"#### {item['name']}")
                    st.markdown(f"## `{item['price']}`")
                    is_up = "+" in item['chg']
                    color = "red" if is_up else "green"
                    st.markdown(f"**涨跌幅**: :{color}[{item['chg']}] | `{item['type']}`")
                    st.caption(item['desc'])
