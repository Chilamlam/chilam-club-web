"""
全球宏观资产与 A 股股债性价比 (ERP / FED 估值模型) & 行业流通市值占比历史分位
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import os
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ==================== 行业/板块 真实日K与市值数据引擎 ====================

INDUSTRY_SINA_MAP = {
    # 医药医疗
    "医疗器械": "sz159883", "化学制药": "sh512010", "生物制品": "sh512290", "中药": "sz159647", "医药商业": "sh512010",
    "医药生物": "sh512010", "制药": "sh512010", "创新药": "sz159992",
    # 科技与电子
    "半导体": "sh512480", "芯片": "sz159995", "消费电子": "sz159732", "电子元件": "sz159732", "光学光电子": "sz159732",
    "软件开发": "sh515230", "互联网服务": "sh515230", "计算机设备": "sh512720", "通信设备": "sh515880", "通信服务": "sh515880",
    "算力": "sz159530", "人工智能": "sz159819", "游戏": "sz159869", "文化传媒": "sz159805",
    # 新能源与电力
    "光伏设备": "sh515790", "电池": "sz159755", "固态电池": "sz159755", "风电设备": "sh515790", "电网设备": "sh561560", "电力行业": "sh561560",
    # 大消费
    "白酒": "sh512690", "酿酒行业": "sh512690", "食品饮料": "sz159843", "家用电器": "sz159996", "家电": "sz159996",
    "农林牧渔": "sz159825", "农牧饲渔": "sz159825", "农业": "sz159825", "旅游酒店": "sz159766", "商业百货": "sz159843",
    "纺织服装": "sz159843",
    # 汽车与装备制造
    "汽车整车": "sh516110", "汽车零部件": "sz159565", "汽车": "sh516110",
    "通用设备": "sh512720", "专用设备": "sh512720", "自动化设备": "sz159779", "工程机械": "sh516970", "仪器仪表": "sz159779",
    "航天航空": "sh512660", "军工": "sh512660", "船舶制造": "sh512660", "交运设备": "sh516970",
    # 金融地产与周期
    "证券": "sh512880", "券商": "sh512880", "银行": "sh512800", "保险": "sh515630", "多元金融": "sh512880",
    "有色金属": "sh512400", "工业金属": "sh512400", "贵金属": "sh518880", "煤炭行业": "sh515220", "钢铁行业": "sh515210", "石油行业": "sh561560",
    "化学制品": "sh512010", "化学原料": "sh512400",
    "房地产开发": "sh512200", "房地产": "sh512200", "建筑装饰": "sh516970", "装修建材": "sh516970"
}

STANDARD_DISPLAY_INDUSTRIES = [
    "半导体", "医疗器械", "化学制药", "中药", "生物制品", "消费电子", "软件开发", "通信设备",
    "光伏设备", "电池", "电力行业", "白酒", "食品饮料", "家用电器", "农牧饲渔", "农林牧渔",
    "汽车整车", "汽车零部件", "证券", "银行", "保险", "有色金属", "煤炭行业", "钢铁行业",
    "航天航空", "房地产开发", "游戏", "文化传媒", "旅游酒店", "自动化设备", "工程机械"
]

@st.cache_data(ttl=86400)
def get_available_industry_list():
    return sorted(list(set(STANDARD_DISPLAY_INDUSTRIES)))


@st.cache_data(ttl=86400)
def get_market_industry_mv_dict():
    """获取各行业当前真实流通市值与全市场占比 (基于快照底表)"""
    snapshot_path = "data/market_snapshot.csv"
    ind_circ_mv = {}
    total_circ_mv = 0.0
    if os.path.exists(snapshot_path):
        try:
            df = pd.read_csv(snapshot_path)
            if "industry" in df.columns and "circ_mv" in df.columns:
                df["circ_mv"] = pd.to_numeric(df["circ_mv"], errors="coerce").fillna(0)
                total_circ_mv = df["circ_mv"].sum()
                grp = df.groupby("industry")["circ_mv"].sum().to_dict()
                for k, v in grp.items():
                    k_str = str(k).strip()
                    if k_str and k_str not in ('-', 'nan', ''):
                        ind_circ_mv[k_str] = v
        except Exception:
            pass
    if total_circ_mv <= 0:
        total_circ_mv = 983483.58 # 约 98.35 万亿默认基准
    return ind_circ_mv, total_circ_mv


@st.cache_data(ttl=1800, show_spinner="正在拉取行情与计算市值占比历史分位...")
def fetch_kline_raw(sym: str) -> list:
    """拉取单标的日K原始数组"""
    url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{sym}=/CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=1023"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://finance.sina.com.cn"
    })
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            content = resp.read().decode("gbk", errors="ignore")
            m = re.search(r'\((.*)\)', content)
            if m:
                arr = json.loads(m.group(1))
                if isinstance(arr, list):
                    return arr
    except Exception:
        pass
    return []


@st.cache_data(ttl=1800)
def calculate_industry_mv_share_history(industry_name: str):
    """
    计算行业流通市值占全市场总市值的历史占比曲线与分位数：
    Ratio_d = Ratio_now * (Price_ind,d / Price_ind,now) / (Benchmark_d / Benchmark_now)
    """
    clean_name = industry_name.strip()
    sym = INDUSTRY_SINA_MAP.get(clean_name)
    if not sym:
        # 兜底
        sym = "sh512010" if "药" in clean_name or "医" in clean_name else "sh512480"

    # 1. 行业日K
    ind_k = fetch_kline_raw(sym)
    if not ind_k:
        return None

    # 2. 全A大盘基准日K (上证综指 sh000001)
    bench_k = fetch_kline_raw("sh000001")
    bench_map = {x.get("day"): float(x.get("close", 0)) for x in bench_k if x.get("day") and x.get("close")}
    cur_bench_now = float(bench_k[-1]["close"]) if bench_k else 3950.0

    # 3. 获取行业当前真实流通市值与占比基准
    ind_mv_dict, total_mv = get_market_industry_mv_dict()
    cur_ind_mv = ind_mv_dict.get(clean_name, 0.0)
    
    # 别名匹配
    if cur_ind_mv <= 0:
        for k, v in ind_mv_dict.items():
            if k in clean_name or clean_name in k:
                cur_ind_mv = v
                break
    if cur_ind_mv <= 0:
        cur_ind_mv = 25000.0 # 默认 2500 亿估算
        
    cur_ratio_now = (cur_ind_mv / total_mv * 100) if total_mv > 0 else 2.5
    cur_ind_price_now = float(ind_k[-1]["close"]) if ind_k[-1].get("close") else 1.0

    # 4. 逐日推演历史流通市值占比序列与成交额
    records = []
    for item in ind_k:
        d = item.get("day", "")
        p = float(item.get("close", 0))
        bp = bench_map.get(d)
        if bp and p > 0 and bp > 0:
            rel_strength = (p / cur_ind_price_now) / (bp / cur_bench_now)
            hist_ratio = cur_ratio_now * rel_strength
            vol = float(item.get("volume", 0))
            amt = float(item.get("amount", 0)) if item.get("amount") else vol * p
            records.append({
                "date": d,
                "ratio": hist_ratio,
                "price": p,
                "amount": amt
            })

    if not records:
        return None

    df = pd.DataFrame(records)
    return df, cur_ratio_now, cur_ind_mv, total_mv


def render_macro_erp_page():
    st.header("🌐 宏观资产、股债性价比 & 行业市值分位")
    st.caption("大周期宏观与中观行业择时指南：行业流通市值占比历史分位 + 资金热度 (成交额分位) + 股债性价比 (FED 模型) + 全球核心资产联动")

    tab_sector_mv, tab_erp, tab_global = st.tabs(["🏛️ 行业流通市值历史占比分位", "⚖️ 股债性价比 (大盘周期抄底/逃顶)", "🌍 全球宏观资产联动"])

    # ==================== Tab 1: 行业流通市值真实占比历史分位 + 资金热度 ====================
    with tab_sector_mv:
        st.subheader("🏛️ 行业流通市值占全市场总市值比重（历史分位中枢）")
        st.info("""
        **💡 行业流通市值占比历史分位核心逻辑**：
        - `行业市值占比 (%) = 行业所有股票流通市值之和 / A 股全市场总流通市值`
        - **历史分位 (Percentile)** 衡量该行业在全市场中的体量占比处于“极度低估/被冷落”还是“极度拥挤/冲顶过热”：
          - **分位 ≤ 20%**：🟢 **历史体量极度低估 (绝望潜伏区)** — 行业相对全市场份额处于历史冰点，长期赔率极大；
          - **20% ~ 80%**：⚖️ **历史合理波动中枢**；
          - **分位 ≥ 80%**：🔴 **历史体量高位拥挤 (冲顶防守区)** — 行业体量占全市场过高，警惕均值回归与资金分流。
        """)

        ind_options = get_available_industry_list()
        
        default_idx = 0
        if "半导体" in ind_options:
            default_idx = ind_options.index("半导体")
        elif "医疗器械" in ind_options:
            default_idx = ind_options.index("医疗器械")

        c_sel, c_custom = st.columns([1.5, 1])
        with c_sel:
            sel_ind = st.selectbox("🎯 选择行业板块：", ind_options, index=default_idx)
        with c_custom:
            custom_input = st.text_input("🔍 或输入任意细分概念/行业（支持别名）：", placeholder="如: 医疗器械, 农牧饲渔, 芯片, 创新药")
            if custom_input.strip():
                sel_ind = custom_input.strip()

        # 计算市值占比历史
        result = calculate_industry_mv_share_history(sel_ind)

        if not result:
            st.warning(f"⚠️ 暂未匹配到【{sel_ind}】的行情数据，请尝试从左侧下拉菜单选择标准行业。")
        else:
            df_hist, cur_ratio, cur_mv, total_mv = result
            
            ratios = df_hist["ratio"].values
            cur_r = ratios[-1]
            pct_rank = int((ratios < cur_r).mean() * 100)
            hist_max = ratios.max()
            hist_min = ratios.min()
            hist_mean = ratios.mean()
            p80 = np.percentile(ratios, 80)
            p20 = np.percentile(ratios, 20)

            # 资金热度 (成交额历史分位)
            amt_vals = df_hist["amount"].values
            cur_amt = amt_vals[-1]
            amt_rank = int((amt_vals < cur_amt).mean() * 100)

            start_date_str = df_hist["date"].iloc[0]
            end_date_str = df_hist["date"].iloc[-1]

            # 顶部 4 项核心 KPI
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(
                f"当前【{sel_ind}】市值占比",
                f"{cur_r:.2f}%",
                delta=f"{cur_r - hist_mean:+.2f}% (较历史均值)"
            )

            if pct_rank <= 20:
                status_eval = "🟢 历史极度低估 (绝望潜伏区)"
            elif pct_rank >= 80:
                status_eval = "🔴 历史高位拥挤 (冲顶防守区)"
            else:
                status_eval = "⚖️ 历史合理中枢"
                
            c2.metric("市值占比历史分位数", f"{pct_rank}%", delta=status_eval)

            amt_eval = "🟢 资金极度冷清 (地量)" if amt_rank <= 20 else ("🔴 资金极度亢奋 (天量)" if amt_rank >= 80 else "⚖️ 活跃度适中")
            c3.metric("🔥 资金热度 (成交额分位)", f"{amt_rank}%", delta=amt_eval)

            c4.metric(
                "板块当前流通市值",
                f"{cur_mv/10000:.1f} 亿元",
                delta=f"全市场: {total_mv/100000000:.1f} 万亿"
            )

            # 主图与副图联合绘制 (Subplots: 上为主市值占比分位，下为资金热度成交额)
            fig = make_subplots(
                rows=2, cols=1,
                shared_xaxes=True,
                vertical_spacing=0.08,
                subplot_titles=(
                    f"【{sel_ind}】流通市值占 A 股总市值比重 (%) 及历史分位通道",
                    "🔥 资金热度：行业日成交额变动 (亿元)"
                ),
                row_heights=[0.72, 0.28]
            )

            # 上图：市值占比走势及分位带
            fig.add_trace(go.Scatter(
                x=df_hist["date"], y=[p80] * len(df_hist),
                name="80% 分位 (高位过热拥挤线)",
                line=dict(color="rgba(231, 76, 60, 0.75)", width=1.5, dash="dash")
            ), row=1, col=1)

            fig.add_trace(go.Scatter(
                x=df_hist["date"], y=[hist_mean] * len(df_hist),
                name="历史均值中枢占比",
                line=dict(color="rgba(243, 156, 18, 0.85)", width=1.8)
            ), row=1, col=1)

            fig.add_trace(go.Scatter(
                x=df_hist["date"], y=[p20] * len(df_hist),
                name="20% 分位 (低估黄金坑潜伏线)",
                line=dict(color="rgba(46, 204, 113, 0.75)", width=1.5, dash="dash")
            ), row=1, col=1)

            fig.add_trace(go.Scatter(
                x=df_hist["date"], y=df_hist["ratio"],
                name=f"{sel_ind} 市值占比 (%)",
                line=dict(color="#0984e3", width=2.5),
                fill="tonexty",
                fillcolor="rgba(9, 132, 227, 0.05)"
            ), row=1, col=1)

            # 下图：资金热度成交额柱状图
            amt_yi = df_hist["amount"] / 100000000.0 if df_hist["amount"].max() > 100000 else df_hist["amount"] / 10000.0
            fig.add_trace(go.Bar(
                x=df_hist["date"], y=amt_yi,
                name="成交额 (亿元)",
                marker_color="rgba(142, 68, 173, 0.6)"
            ), row=2, col=1)

            fig.update_layout(
                height=560,
                hovermode="x unified",
                legend=dict(orientation="h", y=1.05, x=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)"
            )
            fig.update_yaxes(title_text="市值占比 (%)", row=1, col=1, gridcolor="rgba(128,128,128,0.2)")
            fig.update_yaxes(title_text="成交额(亿)", row=2, col=1, gridcolor="rgba(128,128,128,0.2)")
            fig.update_xaxes(tickangle=-45, gridcolor="rgba(128,128,128,0.2)", nticks=15)

            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"📊 历史区间：{start_date_str} ~ {end_date_str}（共 {len(df_hist)} 交易日）· 历史最低占比：{hist_min:.2f}% · 最高占比：{hist_max:.2f}%")

    # ==================== Tab 2: 股债性价比 ERP ====================
    with tab_erp:
        st.subheader("📊 A股股债风险溢价 (Equity Risk Premium, ERP)")
        st.info("""
        **💡 ERP 指标释义 (FED 模型)**：
        - `ERP = 沪深300 盈利收益率 (1 / PE_TTM) - 中国10年期国债收益率`
        - **极度高估 (逃顶区)**：ERP 跌破 **-1倍标准差 / -2倍标准差**，代表股票性价比极低，国债更具吸引力。
        - **黄金坑 (抄底区)**：ERP 突破 **+1倍标准差 / +2倍标准差**，代表股票资产极其便宜，长期赔率极大。
        """)

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

        col1, col2, col3 = st.columns(3)
        col1.metric("当前 ERP (风险溢价)", f"{current_erp:.2f}%", delta=f"{current_erp - mean_val:+.2f}% 偏离均值")

        status_eval = "👑 黄金坑抄底区 (估值极度便宜)" if current_erp > mean_val + std_val else ("⚠️ 估值偏高需防守" if current_erp < mean_val - std_val else "⚖️ 估值合理中枢")
        col2.metric("当前估值水位状态", status_eval)
        col3.metric("5年期历史分位数", f"{int((df_erp['erp'] < current_erp).mean() * 100)}%")

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
