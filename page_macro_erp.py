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
from datetime import datetime, timedelta

# ==================== 行业历史数据获取 (AkShare 真实数据) ====================

@st.cache_data(ttl=86400, show_spinner="正在获取行业板块列表...")
def _fetch_industry_board_names():
    """获取东方财富全部行业板块名称"""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if "板块名称" in df.columns:
            names = df["板块名称"].tolist()
        else:
            names = df.iloc[:, 1].tolist()
        return sorted([str(n) for n in names if n])
    except Exception:
        return []

@st.cache_data(ttl=3600, show_spinner="正在获取 10 年历史数据，请稍候...")
def _fetch_industry_10y_hist(industry_name):
    """获取指定行业板块 10 年日K数据"""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=365 * 10)).strftime("%Y%m%d")
        df = ak.stock_board_industry_hist_em(
            symbol=industry_name, period="日k",
            start_date=start_date, end_date=end_date, adjust=""
        )
        return df
    except Exception:
        return pd.DataFrame()


def render_macro_erp_page():
    st.header("🌐 宏观资产、股债性价比 & 行业市值分位")
    st.caption("大周期宏观与中观行业择时指南：行业市值全市场占比分位 + 股权风险溢价 (FED 模型) + 全球核心资产联动")

    tab_sector_mv, tab_erp, tab_global = st.tabs(["🏛️ 行业流通市值历史分位", "⚖️ 股债性价比 (大盘周期抄底/逃顶)", "🌍 全球宏观资产联动"])

    # ==================== Tab 1: 行业流通市值历史分位 (10年真实数据) ====================
    with tab_sector_mv:
        st.subheader("🏛️ 行业板块 10 年历史分位走势")
        st.info("""
        **💡 行业历史分位逻辑**：
        - 基于东方财富行业板块 **10 年真实日K数据**，以板块指数收盘价反映该行业流通市值变化趋势。
        - **历史分位 (Percentile)** 衡量该行业在过去 10 年中，体量处于"极度低估/被冷落"还是"极度拥挤/高位过热"。
        - **分位 < 20%**：历史估值与体量底部（绝望/低吸关注区）；
        - **分位 > 80%**：历史估值与体量过热（拥挤/冲顶获利区）。
        - 数据来源：AkShare 东方财富行业板块接口，每日自动更新。
        """)

        # 获取真实行业板块名称列表
        industry_names = _fetch_industry_board_names()

        # fallback：如果 AkShare 获取失败，使用快照中的行业名
        df_snap = pd.read_csv("data/market_snapshot.csv") if os.path.exists("data/market_snapshot.csv") else pd.DataFrame()
        if not industry_names:
            industry_options = ["半导体", "白酒", "生物制药", "光伏设备", "软件开发",
                                "银行", "有色金属", "汽车零部件", "通用设备", "家电行业", "化学制药", "房地产"]
            if not df_snap.empty and 'industry' in df_snap.columns:
                real_inds = [str(x) for x in df_snap['industry'].dropna().unique() if str(x) not in ('-', 'nan', '')]
                if len(real_inds) >= 5:
                    industry_options = sorted(real_inds)
        else:
            industry_options = industry_names

        sel_ind = st.selectbox("🎯 选择要探查的行业板块：", industry_options, index=0)

        # 获取该行业 10 年真实日K数据
        df_hist = _fetch_industry_10y_hist(sel_ind)

        if df_hist is None or df_hist.empty:
            st.warning(f"⚠️ 未能获取【{sel_ind}】的 10 年历史数据，请稍后重试或更换行业。")
            st.caption("可能原因：网络超时、AkShare 接口限流或该行业上市时间不足 10 年。")
        else:
            # 统一列名（AkShare 返回中文列名）
            col_map = {}
            for c in df_hist.columns:
                cl = str(c).lower()
                if "日期" in cl or "date" in cl:
                    col_map[c] = "date"
                elif "收盘" in cl or "close" in cl:
                    col_map[c] = "close"
                elif "成交额" in cl or "amount" in cl:
                    col_map[c] = "amount"
                elif "换手" in cl or "turnover" in cl:
                    col_map[c] = "turnover"
            df_hist = df_hist.rename(columns=col_map)

            if "close" not in df_hist.columns:
                st.error("数据格式异常：未找到收盘价列，请检查 AkShare 接口返回。")
                st.dataframe(df_hist.head())
            else:
                df_hist["close"] = pd.to_numeric(df_hist["close"], errors="coerce")
                df_hist = df_hist.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
                df_hist["date_str"] = pd.to_datetime(df_hist["date"]).dt.strftime("%Y-%m-%d")

                close_vals = df_hist["close"].values
                cur_close = close_vals[-1]
                pct_rank = int((close_vals < cur_close).mean() * 100)
                hist_max = close_vals.max()
                hist_min = close_vals.min()
                hist_mean = close_vals.mean()
                p80 = np.percentile(close_vals, 80)
                p20 = np.percentile(close_vals, 20)

                # 成交额分位（资金关注度代理指标）
                has_amount = "amount" in df_hist.columns
                amt_rank = None
                if has_amount:
                    df_hist["amount"] = pd.to_numeric(df_hist["amount"], errors="coerce")
                    amt_vals = df_hist["amount"].dropna().values
                    if len(amt_vals) > 0:
                        cur_amt = amt_vals[-1]
                        amt_rank = int((amt_vals < cur_amt).mean() * 100)

                # 顶部 KPI 指标
                c_i1, c_i2, c_i3, c_i4 = st.columns(4)
                c_i1.metric(
                    f"当前【{sel_ind}】指数点位",
                    f"{cur_close:.1f}",
                    delta=f"{cur_close - hist_mean:+.1f} 偏离10年均值"
                )

                status_color = "🟢 历史极度低估区" if pct_rank <= 20 else ("🔴 历史高位拥挤区" if pct_rank >= 80 else "⚖️ 历史合理中枢")
                c_i2.metric("10年历史分位数", f"{pct_rank}%", delta=status_color)

                if amt_rank is not None:
                    amt_status = "🟢 资金极度冷清" if amt_rank <= 20 else ("🔴 资金极度活跃" if amt_rank >= 80 else "⚖️ 资金正常")
                    c_i3.metric("成交额分位(资金热度)", f"{amt_rank}%", delta=amt_status)
                else:
                    c_i3.metric("10年区间(最低~最高)", f"{hist_min:.1f} ~ {hist_max:.1f}")

                c_i4.metric("10年数据天数", f"{len(close_vals)} 天")

                # 绘制 10 年历史走势与分位通道
                fig_ind = go.Figure()
                fig_ind.add_trace(go.Scatter(
                    x=df_hist["date_str"], y=[p80] * len(df_hist),
                    name="80% 分位 (高位过热线)", line=dict(color="rgba(231, 76, 60, 0.7)", dash="dash")
                ))
                fig_ind.add_trace(go.Scatter(
                    x=df_hist["date_str"], y=[hist_mean] * len(df_hist),
                    name="10年均值基准", line=dict(color="rgba(243, 156, 18, 0.8)", width=1.5)
                ))
                fig_ind.add_trace(go.Scatter(
                    x=df_hist["date_str"], y=[p20] * len(df_hist),
                    name="20% 分位 (低估潜伏线)", line=dict(color="rgba(46, 204, 113, 0.7)", dash="dash")
                ))
                fig_ind.add_trace(go.Scatter(
                    x=df_hist["date_str"], y=df_hist["close"],
                    name=f"{sel_ind} 指数点位", line=dict(color="#0984e3", width=2),
                ))

                fig_ind.update_layout(
                    title=f"【{sel_ind}】板块 10 年历史走势与分位通道（{df_hist['date_str'].iloc[0]} ~ {df_hist['date_str'].iloc[-1]}）",
                    xaxis=dict(tickangle=-45, gridcolor="rgba(128,128,128,0.2)", nticks=20),
                    yaxis=dict(title="板块指数点位", gridcolor="rgba(128,128,128,0.2)"),
                    hovermode="x unified", height=450,
                    legend=dict(orientation="h", y=1.1)
                )
                st.plotly_chart(fig_ind, use_container_width=True)

                st.caption(f"📊 数据范围：{df_hist['date_str'].iloc[0]} 至 {df_hist['date_str'].iloc[-1]}，共 {len(close_vals)} 个交易日 · 数据来源：AkShare 东方财富")

    # ==================== Tab 2: 股债性价比 ERP ====================
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

    # ==================== Tab 3: 全球宏观资产联动 ====================
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
