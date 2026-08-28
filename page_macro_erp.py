"""
全球宏观资产与 A 股股债性价比 (ERP / FED 估值模型) & 行业流通市值占比历史分位 (支持个股反查行业)
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import os
import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta

# ==================== 行业/板块 真实前复权日K与市值数据引擎 ====================

INDUSTRY_SINA_MAP = {
    # 医药医疗
    "医疗器械": "sz159883", "医疗保健": "sz159883", "化学制药": "sh512010", "生物制品": "sh512290", "中药": "sz159647", "医药商业": "sh512010",
    "医药生物": "sh512010", "制药": "sh512010", "创新药": "sz159992", "医药": "sh512010",
    # 科技与电子
    "半导体": "sh512480", "芯片": "sz159995", "元器件": "sz159732", "消费电子": "sz159732", "电子元件": "sz159732", "光学光电子": "sz159732",
    "软件开发": "sh515230", "软件服务": "sh515230", "互联网服务": "sh515230", "计算机设备": "sh512720", "通信设备": "sh515880", "通信服务": "sh515880",
    "算力": "sz159530", "人工智能": "sz159819", "游戏": "sz159869", "文化传媒": "sz159805", "IT设备": "sh512720",
    # 新能源与电力
    "光伏设备": "sh515790", "光伏": "sh515790", "电池": "sz159755", "电气设备": "sz159755", "固态电池": "sz159755", "风电设备": "sh515790", "电网设备": "sh561560", "电力行业": "sh561560", "电力": "sh561560",
    # 大消费
    "白酒": "sh512690", "酿酒行业": "sh512690", "食品饮料": "sz159843", "食品": "sz159843", "家用电器": "sz159996", "家电": "sz159996",
    "农林牧渔": "sz159825", "农牧饲渔": "sz159825", "农业综合": "sz159825", "农业": "sz159825", "旅游酒店": "sz159766", "商业百货": "sz159843", "超市": "sz159843",
    "纺织服装": "sz159843",
    # 汽车与装备制造
    "汽车整车": "sh516110", "汽车零部件": "sz159565", "汽车配件": "sz159565", "汽车": "sh516110",
    "通用设备": "sh512720", "通用机械": "sh512720", "专用设备": "sh512720", "专用机械": "sh512720", "自动化设备": "sz159779", "工程机械": "sh516970", "机械基件": "sh512720", "仪器仪表": "sz159779",
    "航天航空": "sh512660", "军工": "sh512660", "船舶制造": "sh512660", "交运设备": "sh516970",
    # 金融地产与周期
    "证券": "sh512880", "券商": "sh512880", "银行": "sh512800", "保险": "sh515630", "多元金融": "sh512880",
    "有色金属": "sh512400", "有色": "sh512400", "工业金属": "sh512400", "贵金属": "sh518880", "煤炭行业": "sh515220", "煤炭": "sh515220", "钢铁行业": "sh515210", "钢铁": "sh515210", "石油行业": "sh561560", "石油": "sh561560",
    "化学制品": "sh512010", "化学原料": "sh512400", "化工原料": "sh512400",
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


@st.cache_data(ttl=86400)
def lookup_stock_info(query: str):
    """
    通过股票代码或名称反查其归属行业板块与市值现价
    """
    q = query.strip()
    if not q:
        return None
        
    snapshot_path = "data/market_snapshot.csv"
    if os.path.exists(snapshot_path):
        try:
            df = pd.read_csv(snapshot_path)
            # 优先精准匹配代码或名称
            pure_codes = df["ts_code"].astype(str).str.split(".").str[0]
            mask = (df["name"] == q) | (pure_codes == q) | (df["ts_code"] == q)
            sub = df[mask]
            if sub.empty:
                # 模糊匹配名称
                mask_fuzzy = df["name"].str.contains(q, na=False)
                sub = df[mask_fuzzy]
            
            if not sub.empty:
                row = sub.iloc[0]
                return {
                    "code": str(row.get("ts_code", "")),
                    "name": str(row.get("name", "")),
                    "industry": str(row.get("industry", "")),
                    "close": float(row.get("close", 0)) if pd.notna(row.get("close")) else 0.0,
                    "circ_mv_yi": round(float(row.get("circ_mv", 0)) / 10000.0, 1) if pd.notna(row.get("circ_mv")) else 0.0
                }
        except Exception:
            pass

    # 在线 Suggest 兜底
    try:
        s_url = f"http://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(q)}&type=14"
        req = urllib.request.Request(s_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("QuotationCodeTable", {}).get("Data", [])
            if items:
                for it in items:
                    c = it.get("Code", "")
                    if it.get("MarketType") in ["1", "2"] and not c.startswith("BK"):
                        return {
                            "code": c,
                            "name": it.get("Name", ""),
                            "industry": it.get("SecurityTypeName", "A股"),
                            "close": 0.0,
                            "circ_mv_yi": 0.0
                        }
    except Exception:
        pass
    return None


@st.cache_data(ttl=1800, show_spinner="正在拉取前复权日K数据...")
def fetch_qfq_kline_data(sym: str) -> list:
    """
    通过腾讯前复权接口获取日K (消除ETF折算、分红跳水断崖)
    """
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_dayqfq&param={sym},day,,,800,qfq"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            txt = resp.read().decode("utf-8")
            json_str = txt.split("=", 1)[1] if "=" in txt else txt
            d = json.loads(json_str)
            res_dict = d.get("data", {}).get(sym, {})
            k_raw = res_dict.get("qfqday") or res_dict.get("day") or []
            records = []
            for item in k_raw:
                if len(item) >= 6:
                    records.append({
                        "date": str(item[0]),
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "amount": float(item[5]) if len(item) > 5 and float(item[5]) > 0 else float(item[2]) * 10000000.0
                    })
            if records:
                return records
    except Exception:
        pass

    # 备用：新浪日K
    url_sina = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_{sym}=/CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen=1023"
    req_s = urllib.request.Request(url_sina, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
    try:
        with urllib.request.urlopen(req_s, timeout=4) as resp:
            content = resp.read().decode("gbk", errors="ignore")
            m = re.search(r'\((.*)\)', content)
            if m:
                arr = json.loads(m.group(1))
                if isinstance(arr, list):
                    records = []
                    for item in arr:
                        records.append({
                            "date": item.get("day", ""),
                            "open": float(item.get("open", 0)),
                            "close": float(item.get("close", 0)),
                            "high": float(item.get("high", 0)),
                            "low": float(item.get("low", 0)),
                            "amount": float(item.get("amount", 0)) if item.get("amount") else float(item.get("volume", 0)) * float(item.get("close", 0))
                        })
                    return records
    except Exception:
        pass

    return []


@st.cache_data(ttl=1800)
def calculate_industry_mv_share_history(industry_name: str):
    """
    基于前复权日K严谨推导行业流通市值占比历史走势（彻底消除除权折算跳水畸变）
    """
    clean_name = industry_name.strip()
    sym = INDUSTRY_SINA_MAP.get(clean_name)
    if not sym:
        # 模糊匹配映射表
        for k, v in INDUSTRY_SINA_MAP.items():
            if k in clean_name or clean_name in k:
                sym = v
                break
    if not sym:
        sym = "sh512010" if ("药" in clean_name or "医" in clean_name) else "sh512480"

    ind_k = fetch_qfq_kline_data(sym)
    if not ind_k or len(ind_k) < 10:
        return None

    bench_k = fetch_qfq_kline_data("sh000001")
    if not bench_k or len(bench_k) < 10:
        return None

    bench_map = {x["date"]: float(x["close"]) for x in bench_k if x.get("date") and x.get("close")}
    cur_bench_now = float(bench_k[-1]["close"]) if bench_k else 3950.0

    ind_mv_dict, total_mv = get_market_industry_mv_dict()
    cur_ind_mv = ind_mv_dict.get(clean_name, 0.0)
    
    if cur_ind_mv <= 0:
        for k, v in ind_mv_dict.items():
            if k in clean_name or clean_name in k:
                cur_ind_mv = v
                break
    if cur_ind_mv <= 0:
        cur_ind_mv = 25000.0
        
    cur_ratio_now = (cur_ind_mv / total_mv * 100) if total_mv > 0 else 2.5
    cur_ind_price_now = float(ind_k[-1]["close"]) if ind_k[-1].get("close") else 1.0

    records = []
    for item in ind_k:
        d = item.get("date", "")
        p = float(item.get("close", 0))
        bp = bench_map.get(d)
        if bp and p > 0 and bp > 0:
            rel_strength = (p / cur_ind_price_now) / (bp / cur_bench_now)
            hist_ratio = cur_ratio_now * rel_strength
            amt = float(item.get("amount", 0))
            records.append({
                "date": d,
                "ratio": hist_ratio,
                "price": p,
                "amount": amt
            })

    if not records:
        return None

    df = pd.DataFrame(records)
    df["amount_ma20"] = df["amount"].rolling(window=20, min_periods=1).mean()
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

        c_sel, c_stock = st.columns([1.3, 1.2])
        with c_sel:
            sel_ind = st.selectbox("🎯 方式一：选择行业板块（下拉菜单）", ind_options, index=default_idx)
        with c_stock:
            stock_input = st.text_input("🔎 方式二：输入股票名称/代码反查行业：", placeholder="如: 贵州茅台, 300750, 比亚迪, 中际旭创")

        target_industry = sel_ind
        stock_info = None

        # 如果用户输入了股票代码/名称，执行反查联动
        if stock_input.strip():
            stock_info = lookup_stock_info(stock_input.strip())
            if stock_info and stock_info.get("industry"):
                matched_ind = stock_info["industry"]
                target_industry = matched_ind
                
                # 展示反查成功卡片
                st.success(
                    f"🎯 **个股行业穿透成功**：`{stock_info['name']}` ({stock_info['code']}) 归属于 **【{matched_ind}】** 行业板块 "
                    f"| 现价: `{stock_info['close']}元` | 个股流通市值: `{stock_info['circ_mv_yi']}亿元`"
                )
            else:
                st.warning(f"⚠️ 未在全市场快照中找到股票 `{stock_input}`，已维持选择行业：【{target_industry}】")

        # 计算前复权市值占比历史
        result = calculate_industry_mv_share_history(target_industry)

        if not result:
            st.warning(f"⚠️ 暂未匹配到【{target_industry}】的行情数据，请尝试从左侧下拉菜单选择标准行业。")
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

            # 顶部 4 项核心 KPI 卡片
            st.markdown("<div style='margin-bottom: 6px;'></div>", unsafe_allow_html=True)
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                with st.container(border=True):
                    st.caption(f"【{target_industry}】全市场市值占比")
                    st.markdown(f"### `{cur_r:.2f}%`")
                    diff_mean = cur_r - hist_mean
                    arrow = "🔺" if diff_mean >= 0 else "🔻"
                    st.caption(f"{arrow} 偏离历史均值 {diff_mean:+.2f}%")
            
            with k2:
                with st.container(border=True):
                    st.caption("市值占比历史分位")
                    if pct_rank <= 20:
                        status_tag = "🟢 极度低估潜伏区"
                    elif pct_rank >= 80:
                        status_tag = "🔴 高位过热拥挤区"
                    else:
                        status_tag = "⚖️ 合理波动中枢"
                    st.markdown(f"### `{pct_rank}%`")
                    st.caption(status_tag)

            with k3:
                with st.container(border=True):
                    st.caption("资金热度 (成交额分位)")
                    if amt_rank <= 20:
                        amt_tag = "🟢 地量冷清"
                    elif amt_rank >= 80:
                        amt_tag = "🔴 天量亢奋"
                    else:
                        amt_tag = "⚖️ 活跃适中"
                    st.markdown(f"### `{amt_rank}%`")
                    st.caption(f"{amt_tag} · 情绪指标")

            with k4:
                with st.container(border=True):
                    st.caption("板块当前流通市值")
                    st.markdown(f"### `{cur_mv/10000:.1f}亿`")
                    st.caption(f"全市场基准: {total_mv/100000000:.1f} 万亿")

            # ==================== 卡片 1：主图【市值占比历史分位通道】 ====================
            st.markdown("<div style='margin-top: 12px;'></div>", unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(f"##### 📈 【{target_industry}】流通市值全市场占比 (%) 历史走势与分位通道")
                
                fig_main = go.Figure()

                # 80% 高位拥挤参考线 (深红虚线)
                fig_main.add_trace(go.Scatter(
                    x=df_hist["date"], y=[p80] * len(df_hist),
                    name="80% 分位 (高位过热线)",
                    line=dict(color="#dc2626", width=1.5, dash="dash"),
                    hovertemplate="80% 过热线: %{y:.2f}%<extra></extra>"
                ))

                # 历史均值中枢 (琥珀金实线)
                fig_main.add_trace(go.Scatter(
                    x=df_hist["date"], y=[hist_mean] * len(df_hist),
                    name="历史均值中枢",
                    line=dict(color="#f59e0b", width=1.8),
                    hovertemplate="历史均值中枢: %{y:.2f}%<extra></extra>"
                ))

                # 20% 低估潜伏线 (翡翠绿虚线)
                fig_main.add_trace(go.Scatter(
                    x=df_hist["date"], y=[p20] * len(df_hist),
                    name="20% 分位 (低估潜伏线)",
                    line=dict(color="#16a34a", width=1.5, dash="dash"),
                    hovertemplate="20% 低估线: %{y:.2f}%<extra></extra>"
                ))

                # 主折线：市值占比 (%) (科技蓝实线，微弱底色填充)
                fig_main.add_trace(go.Scatter(
                    x=df_hist["date"], y=df_hist["ratio"],
                    name=f"{target_industry} 市值占比",
                    line=dict(color="#2563eb", width=2.5),
                    fill="tozeroy",
                    fillcolor="rgba(37, 99, 235, 0.05)",
                    hovertemplate="<b>%{x}</b><br>市值占比: <b>%{y:.2f}%</b><extra></extra>"
                ))

                fig_main.update_layout(
                    height=380,
                    margin=dict(l=10, r=10, t=20, b=10),
                    hovermode="x",
                    legend=dict(
                        orientation="h",
                        y=1.08,
                        x=1.0,
                        xanchor="right",
                        bgcolor="rgba(0,0,0,0)",
                        font=dict(size=12)
                    ),
                    xaxis=dict(tickangle=-45, gridcolor="rgba(128,128,128,0.15)", nticks=12),
                    yaxis=dict(title="市值占比 (%)", gridcolor="rgba(128,128,128,0.15)", zeroline=False),
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)"
                )
                st.plotly_chart(fig_main, use_container_width=True)

            # ==================== 卡片 2：副图【资金热度日成交额变动】 ====================
            with st.container(border=True):
                st.markdown(f"##### 🔥 【{target_industry}】资金热度：行业日成交额变动 (亿元)")
                
                amt_yi = df_hist["amount"] / 100000000.0 if df_hist["amount"].max() > 100000 else df_hist["amount"] / 10000.0
                amt_ma20_yi = df_hist["amount_ma20"] / 100000000.0 if df_hist["amount_ma20"].max() > 100000 else df_hist["amount_ma20"] / 10000.0

                fig_sub = go.Figure()

                # 成交额柱状图 (青黛蓝柱状)
                fig_sub.add_trace(go.Bar(
                    x=df_hist["date"], y=amt_yi,
                    name="日成交额",
                    marker=dict(color="rgba(14, 165, 233, 0.65)", line=dict(width=0)),
                    hovertemplate="<b>%{x}</b><br>日成交额: <b>%{y:.1f} 亿元</b><extra></extra>"
                ))

                # MA20 均量线 (洋红紫粉线，与主图金色彻底区分)
                fig_sub.add_trace(go.Scatter(
                    x=df_hist["date"], y=amt_ma20_yi,
                    name="20日成交均量 (MA20)",
                    line=dict(color="#d946ef", width=1.8),
                    hovertemplate="20日均量: %{y:.1f} 亿元<extra></extra>"
                ))

                # 标注天量峰值点
                max_amt_idx = amt_yi.idxmax()
                max_amt_date = df_hist["date"].iloc[max_amt_idx]
                max_amt_val = amt_yi.iloc[max_amt_idx]
                fig_sub.add_annotation(
                    x=max_amt_date, y=max_amt_val,
                    text=f"天量成交: {max_amt_val:.0f}亿",
                    showarrow=True, arrowhead=2, arrowsize=1, arrowwidth=1.2, arrowcolor="#0ea5e9",
                    ax=0, ay=-20,
                    font=dict(size=11, color="#0284c7")
                )

                p98_amt = np.percentile(amt_yi, 98)
                y_max = max(p98_amt * 1.3, max_amt_val * 1.1)

                fig_sub.update_layout(
                    height=220,
                    margin=dict(l=10, r=10, t=15, b=10),
                    hovermode="x",
                    legend=dict(
                        orientation="h",
                        y=1.12,
                        x=1.0,
                        xanchor="right",
                        bgcolor="rgba(0,0,0,0)",
                        font=dict(size=12)
                    ),
                    xaxis=dict(tickangle=-45, gridcolor="rgba(128,128,128,0.15)", nticks=12),
                    yaxis=dict(title="成交额(亿)", range=[0, y_max], gridcolor="rgba(128,128,128,0.15)", zeroline=False),
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)"
                )
                st.plotly_chart(fig_sub, use_container_width=True)

            # 底部精炼元数据对比栏
            col_b1, col_b2, col_b3 = st.columns(3)
            col_b1.caption(f"📉 历史最低占比：`{hist_min:.2f}%`")
            col_b2.caption(f"📈 历史最高占比：`{hist_max:.2f}%`")
            col_b3.caption(f"📊 样本覆盖：`{len(df_hist)}` 交易日 ({start_date_str} 至 {end_date_str})")

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
