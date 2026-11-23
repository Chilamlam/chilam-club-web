import hashlib
import importlib
from pathlib import Path
import review_runtime

# The refresh helper can itself be cached across a Streamlit deployment.
# Refresh its dependency list before asking it to refresh page modules.
with review_runtime._lock:
    _runtime_path = Path(review_runtime.__file__).resolve()
    _runtime_source = _runtime_path.read_bytes()
    _runtime_digest = hashlib.sha256(_runtime_source).digest()
    if getattr(review_runtime, "_bootstrap_source", None) != _runtime_digest:
        compile(_runtime_source, str(_runtime_path), "exec")
        _runtime_cache = getattr(review_runtime, "__cached__", None)
        if _runtime_cache and Path(_runtime_cache).resolve().parent == _runtime_path.parent / "__pycache__":
            Path(_runtime_cache).unlink(missing_ok=True)
        importlib.invalidate_caches()
        importlib.reload(review_runtime)
        review_runtime._bootstrap_source = _runtime_digest

from review_runtime import refresh_review_modules

refresh_review_modules()

import streamlit as st
import pandas as pd
import os
import json
import plotly.graph_objects as go
import plotly.express as px
from page_fibonacci import render_fibonacci_chart
from config_gurus import GURUS
from page_core_driver import render_core_driver_page
from page_macro_erp import render_macro_erp_page
from page_watchlist import render_watchlist_page
from page_live_quote import render_live_quote_page
from page_scorecard import render_scorecard_page
from page_sentiment import render_sentiment_block
from page_digest import render_digest_page
from page_review import render_review_page
from page_sector_rotation import render_sector_rotation_page
from page_industry_signal import render_industry_signal_page
from page_evening_plan import render_evening_plan_page
from page_decision_workbench import render_decision_workbench_page
from page_convertible import render_convertible_block
from ui_compat import image_stretch, html_embed
import data_freshness as freshness
from publication_status import publication_status
import auth
from navigation import NAV_GROUPS, SECTION_PAGE, render_navigation
from page_news import render_news_page
from ladder_themes import attribution_for, source_status_text
from investor_journey import apply_handoff, candidate_actions, page_link, WATCH_PAGE, REVIEW_PAGE
from fund_review import display_funds

# ================= 1. 基础配置 (必须放在最前面) =================
st.set_page_config(
    page_title="Chilam Club - 投资驾驶舱", 
    page_icon="🚀", 
    layout="wide"
)

# ================= 1.1 侧边栏店铺入口 =================
# 显隐、文案、链接**都不在这里写死**：存在 Supabase 的 site_settings 表里，
# 由后台管理页「站点设置」维护（见 site_settings.py / init_site_settings.sql）。
# 这样关店或换商品时点一下即可，不用改代码、不用等重新部署。
# 这里的 site_settings 已经是「读 DB（失败则回落默认值）后的最终结论」。
import site_settings
import feedback_ui

def inject_ga_safe():
    GA_ID = "G-1HFTXNLL20"
    ga_code = f"""
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());
      gtag('config', '{GA_ID}');
    </script>
    """
    # st.components.v1.html 已被官方标注 2026-06-01 后移除，
    # 走 ui_compat.html_embed 统一降级（同 image_stretch 的处理思路）
    html_embed(ga_code, width=10, height=10)

inject_ga_safe()

# ================= 2. 核心辅助函数 =================

def _data_file(path):
    full_path = path if os.path.isabs(path) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), path
    )
    try:
        stat = os.stat(full_path)
        return full_path, (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return full_path, None


@st.cache_data(ttl=600, max_entries=128)
def _read_csv(full_path, revision):
    try:
        return pd.read_csv(full_path)
    except (OSError, ValueError, pd.errors.ParserError):
        return None


@st.cache_data(ttl=600, max_entries=128)
def _read_json(full_path, revision):
    try:
        with open(full_path, 'r', encoding='utf-8') as f: return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def load_data(path):
    full_path, revision = _data_file(path)
    return _read_csv(full_path, revision) if revision is not None else None


def load_json(path):
    full_path, revision = _data_file(path)
    return _read_json(full_path, revision) if revision is not None else None


# 站内参考日期：取同批产物里最新的日期戳。
# 为什么需要一个「参考」而不是直接跟今天比：跑批时间跨零点、周末、节假日
# 都会让「今天」不等于「最新交易日」，直接比日历必然误报。而同一次跑批的
# 产物本该同日，用批内最新值当基准，能精确抓出「某一步单独失败」——
# 2026-09-01 就是这种情况：突破池/连板/摘要都是 09-01，只有 RPS 停在 08-31。
def site_reference_date():
    cands = []
    for path, cols in (
        ("data/market_sentiment.csv", ("date",)),
        ("data/breakout_stocks.csv", ("update_date",)),
        ("data/strong_etfs.csv", ("更新日期",)),
        ("data/strong_stocks.csv", ("更新日期",)),
    ):
        df = load_data(path)
        if df is not None:
            cands.append(freshness.pick_date(df, cols))
    d = load_json("data/digest/latest.json") or {}
    cands.append(freshness.norm_date(d.get("date")))
    cands = [c for c in cands if c]
    return max(cands) if cands else ""


def render_market_update_status(df_sent, df_sector):
    dates = {"市场概览": freshness.pick_date(df_sent),
             "领涨行业": freshness.pick_date(df_sector)}
    for label, path in (("个股 RPS", "data/strong_stocks.csv"),
                        ("ETF RPS", "data/strong_etfs.csv"),
                        ("突破池", "data/breakout_stocks.csv")):
        dates[label] = freshness.pick_date(load_data(path))
    for label, path in (("连板天梯", "data/limit_ladder.json"),
                        ("短线情绪", "data/sentiment/derived.json"),
                        ("板块轮动", "data/sector_rotation/analysis.json"),
                        ("行业信号", "data/industry_signal/signal.json")):
        dates[label] = (load_json(path) or {}).get("date")
    result = publication_status(dates, calendar=load_json("data/trading_calendar.json"))
    market_date = freshness.norm_date(dates["市场概览"])
    shown = f"{market_date[:4]}-{market_date[4:6]}-{market_date[6:]}" if market_date else "未知"
    st.caption(f"📅 市场概览数据日期：**{shown}** · 状态核对时间：{result['checked_at']}（北京时间）")
    if result["status"] in ("delayed", "unknown"):
        st.warning(result["message"])
    elif result["status"] in ("scheduled", "waiting"):
        st.info(result["message"])
    else:
        st.caption(result["message"])
    with st.expander("查看各模块的数据日期与更新安排"):
        for label, date in result["dates"].items():
            shown = f"{date[:4]}-{date[4:6]}-{date[6:]}" if date else "未知"
            mark = " · 待更新" if label in result["stale"] else ""
            if label in result["unknown"]:
                mark = " · 日期无法确认"
            st.write(f"{label}：{shown}{mark}")
        st.caption("交易日计划 18:37 开始，目标 19:00 前完成；19:35 巡检，22:20 兜底补跑。"
                   "任务排队或数据源延迟可能推迟完成时间，请以各模块实际日期为准。")
        if st.button("重新读取数据", key="refresh_market_data"):
            _read_csv.clear()
            _read_json.clear()
            st.rerun()


def render_freshness_banner(df, label, date_cols=("更新日期", "update_date", "date")):
    """在榜单上方显示数据日期，过期时给出明确警示。

    刻意放在榜单渲染之前：一份过期一天的榜单看起来跟正常榜单一模一样，
    提示放在页脚等于没放。返回判定结果供调用方决定是否继续渲染。
    """
    v = freshness.verdict(freshness.pick_date(df, date_cols),
                          reference_date=site_reference_date())
    shown = f"{v['date'][:4]}-{v['date'][4:6]}-{v['date'][6:]}" if v["date"] else "未知"
    if v["status"] == "stale":
        st.error(
            f"⚠️ **{label}数据可能未更新：{v['reason']}。** "
            f"请按 {shown} 这个日期理解表内所有数字——它不是最新交易日的结果。"
            f"若持续多日如此，说明跑批该步骤失败，需要人工补跑。",
            icon="🚨",
        )
    elif v["status"] == "unknown":
        # 与 stale 分开报：缺日期列要改产物结构，跟跑批没跑成不是一回事。
        st.warning(f"⚠️ {label}：{v['reason']}。无法确认数据是否为最新，请谨慎使用。")
    else:
        st.caption(f"📅 {label}数据日期：**{shown}**")
    return v

def format_rps_show(df, rps_col='RPS_50', chg_col='rps_50_chg'):
    if df is None or df.empty: return df
    if chg_col not in df.columns:
        df[f'{rps_col}_Show'] = df[rps_col].map(lambda x: f"{x:.1f}")
        return df
    def _fmt(row):
        val = row[rps_col]
        chg = row[chg_col]
        if chg == 999: return f"{val:.1f} 🆕"
        elif chg > 0: return f"{val:.1f} 🔺{abs(chg):.1f}"
        elif chg < 0: return f"{val:.1f} 🔻{abs(chg):.1f}"
        return f"{val:.1f} -"
    df[f'{rps_col}_Show'] = df.apply(_fmt, axis=1)
    return df

def get_market_snapshot_local():
    df = load_data("data/market_snapshot.csv")
    if df is not None and not df.empty:
        return df
    return pd.DataFrame()


# ================= 3. 页面渲染模块 =================
def render_market_dashboard():
    st.header("全市场看板")
    df_index = load_data("data/index_history.csv")
    df_sent = load_data("data/market_sentiment.csv")
    df_sector = load_data("data/sector_hot.csv")
    render_market_update_status(df_sent, df_sector)

    # 「AI 首席策略官复盘」板块已摘除（2026-09-14 用户决策）：
    # 该板块由 Gemini 生成的 ai_market_analysis.json 驱动，Gemini 端点在
    # Actions 机房经常不可达，产物常年滞后于其他数据，观感「不同步」。
    # 产物文件保留：daily_review_ai 仍作 AI 答卷依据（带日期等值门控）。

    # === B. 核心 KPI ===
    if df_sent is not None and not df_sent.empty:
        last = df_sent.iloc[-1]
        up_count = int(last["up_count"])
        down_count = int(last["down_count"])
        amount = float(last["total_amount_yi"])
        net_up = up_count - down_count
        st.markdown(
            f"""
            <style>
              @media (max-width: 640px) {{
                .market-kpi-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }}
                .market-kpi-grid .market-kpi-wide {{ grid-column: 1 / -1; }}
              }}
            </style>
            <div class="market-kpi-grid" style="display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
                        gap:0.55rem;margin:0.85rem 0 1rem;">
              <div style="border:1px solid rgba(128,128,128,.18);border-radius:10px;
                          padding:0.7rem 0.75rem;background:rgba(128,128,128,.035);min-width:0">
                <div style="font-size:.76rem;color:#6b7280">上涨家数</div>
                <div style="font-size:1.35rem;font-weight:700;color:#d94f43;line-height:1.35">{up_count:,}</div>
                <div style="font-size:.72rem;color:#6b7280">涨跌净值 {net_up:+,}</div>
              </div>
              <div style="border:1px solid rgba(128,128,128,.18);border-radius:10px;
                          padding:0.7rem 0.75rem;background:rgba(128,128,128,.035);min-width:0">
                <div style="font-size:.76rem;color:#6b7280">下跌家数</div>
                <div style="font-size:1.35rem;font-weight:700;color:#178f70;line-height:1.35">{down_count:,}</div>
                <div style="font-size:.72rem;color:#6b7280">与上涨家数同日</div>
              </div>
              <div class="market-kpi-wide" style="border:1px solid rgba(128,128,128,.18);border-radius:10px;
                          padding:0.7rem 0.75rem;background:rgba(128,128,128,.035);min-width:0"
                   title="结合近期成交额比较放量或缩量；不同市场规模下不套用固定金额阈值">
                <div style="font-size:.76rem;color:#6b7280">全市场成交</div>
                <div style="font-size:1.35rem;font-weight:700;line-height:1.35;white-space:nowrap">{amount:,.2f}<span style="font-size:.72rem;font-weight:500;margin-left:.2rem">亿元</span></div>
                <div style="font-size:.72rem;color:#6b7280">绝对值，不代表净流入</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    
    overview, relay, liquidity = st.tabs(["涨跌与板块", "连板与晋级", "成交与风格"])
    with overview:
        # === C. 赚钱效应趋势 ===
        st.subheader("涨跌分布")
        if df_sent is not None and not df_sent.empty:
            df_sent = df_sent.sort_values('date').copy()
            df_sent['up_ma5'] = df_sent['up_count'].rolling(window=5, min_periods=1).mean()
            df_show = df_sent.tail(60).copy()
            df_show['date_str'] = df_show['date'].astype(str)

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_show['date_str'], y=df_show['up_count'], name='上涨家数', line=dict(color='#ff4d4d', width=1.5), opacity=0.8, mode='lines'))
            fig.add_trace(go.Scatter(x=df_show['date_str'], y=df_show['up_ma5'], name='上涨家数5日均值', line=dict(color='#FF9900', width=3), mode='lines'))
            fig.update_layout(title="上涨家数与5日均值", xaxis=dict(type='category', tickangle=-45, tickmode='auto', nticks=15, gridcolor='rgba(128,128,128,0.2)'), yaxis=dict(title="上涨家数", gridcolor='rgba(128,128,128,0.2)'), legend=dict(orientation="h", y=1.05, x=0, xanchor="left"), margin=dict(l=0, r=0, t=40, b=0), hovermode="x unified", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("暂无历史情绪数据，请等待数据积累。")
        
        # === D. 大军团作战 ===
        st.subheader("领涨行业")
        if df_sector is not None and not df_sector.empty:
            fig_sec = px.bar(df_sector, x='industry', y='pct_chg', color='pct_chg', text='amount', labels={'pct_chg': '涨幅(%)', 'industry': '板块'}, color_continuous_scale='Reds', height=350)
            fig_sec.update_traces(texttemplate='%{text}亿', textposition='outside')
            st.plotly_chart(fig_sec, use_container_width=True)

    with relay:
        # === D2. 短线接力统计（晋级比例 / 样本涨跌幅 / 梯队高度） ===
        # 放在连板天梯之前：先给"这堆涨停意味着什么"的结论，再给明细名单。
        render_sentiment_block()

        # === D3. 连板情绪天梯 (涨停高度与梯队) ===
        st.subheader("连板梯队")
        ladder_raw = load_json("data/limit_ladder.json")
        
        if ladder_raw and "stocks" in ladder_raw and len(ladder_raw["stocks"]) > 0:
            total_zt = ladder_raw.get("total_count", len(ladder_raw["stocks"]))
            max_h = ladder_raw.get("max_height", 1)
            st.caption(f"👑 统计日涨停共 **{total_zt}** 家 | 市场最高板：**{max_h} 连板** (统计日期: `{ladder_raw.get('date', '未知')}`)")
            theme_data = load_json("data/limit_themes.json")
            theme_count = sum(attribution_for(r.get("code"), ladder_raw.get("date"), theme_data) is not None
                              for r in ladder_raw["stocks"])
            if theme_data and freshness.norm_date(theme_data.get("date")) == freshness.norm_date(ladder_raw.get("date")):
                st.caption(f"当日涨停归因覆盖 {theme_count}/{len(ladder_raw['stocks'])} 只 · 来源状态：{source_status_text(theme_data)}")
            else:
                st.caption("当日题材归因尚未生成；主营行业仅作公司分类，不代表当天炒作题材。")

            df_zt_all = pd.DataFrame(ladder_raw["stocks"])
            df_zt_all['limit_times'] = pd.to_numeric(df_zt_all['limit_times'], errors='coerce').fillna(1).astype(int)

            # 梯队折叠展示
            for lb in sorted(df_zt_all['limit_times'].unique(), reverse=True):
                sub = df_zt_all[df_zt_all['limit_times'] == lb]
                badge = f"🔥 {lb} 连板" if lb > 1 else "🌱 首板"
                with st.expander(f"{badge} ({len(sub)} 家)", expanded=(lb >= 2)):
                    cols = st.columns(min(len(sub), 4))
                    for idx, (_, r) in enumerate(sub.iterrows()):
                        with cols[idx % 4]:
                            st.markdown(f"**{r['name']}** `{r.get('code', '')}`")
                            attribution = attribution_for(r.get('code'), ladder_raw.get('date'), theme_data)
                            if attribution:
                                if attribution['theme']:
                                    st.markdown(f"**题材：{attribution['theme']}**")
                                if attribution['reason']:
                                    st.caption(f"涨停原因：{attribution['reason']}")
                                st.caption(f"{attribution['source']} · {ladder_raw['date']}")
                            else:
                                st.caption("当日题材归因暂缺")
                            st.caption(f"主营行业：{r.get('industry', '-')}")
                            if r.get('reason'):
                                st.caption(f"盘口：{r['reason']}")
            candidate_actions(df_zt_all, key="ladder_next", code_col="code", equities=True)
        else:
            st.info("今日暂无涨停连板梯队数据。")

    with liquidity:
        from workbench_insights_ui import load_insights, render_funding
        render_funding(load_insights(freshness.pick_date(df_sent)))
        # === E. 风格监控 ===
        st.subheader("⚖️ 风格监控 (500 vs 1000)")
        if df_index is not None:
            df_show = df_index.head(250).sort_values('date')
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['zz500'], name='中证500', line=dict(color='#ff9f43')))
            fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['zz1000'], name='中证1000', line=dict(color='#00d2d3')))
            fig.add_trace(go.Scatter(x=df_show['date'], y=df_show['spread'], name='价差', line=dict(color='gray', dash='dot'), yaxis='y2'))
            fig.update_layout(yaxis2=dict(overlaying='y', side='right', showgrid=False), height=350, legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, use_container_width=True)

def render_stock_content(df):
    if df is None or df.empty: 
        st.info("暂无数据，请等待每日更新")
        return
    # 新鲜度横幅排在免责声明之前：「这份数据是哪天的」比「这份数据不构成建议」更基础。
    # 2026-09-01 本表停在 08-31 而页面毫无提示，用户只能靠肉眼比对才发现。
    render_freshness_banner(df, "强势股榜单")
    st.caption("RPS 是过去涨幅的相对排名。筛出候选后，查看它相对前高与各条均线的位置，"
               "再记录自己的观察条件；筛选规则的历史表现可到“战绩回看”核对。")
    with st.expander("🔍 深度筛选 (点击展开/收起)", expanded=True):
        sc1, sc2, sc3, sc4 = st.columns([1, 1, 1, 1.5])
        min_d = sc1.slider("连榜天数", 1, 30, 1)
        min_rps = sc2.slider("最低 RPS", 50, 99, 87)
        max_pe = 1000
        if 'pe_ttm' in df.columns: max_pe = sc3.slider("最大 PE(TTM)", 0, 200, 100)
        opts = ["全部"]
        if '细分行业' in df.columns: opts += sorted([x for x in df['细分行业'].dropna().unique() if str(x) != 'nan'])
        ind = sc4.selectbox("主营行业", opts)
        kw = st.text_input("搜索代码/名称", placeholder="输入代码或名称...")

    mask = (df['连续天数'] >= min_d) & (df['RPS_50'] >= min_rps)
    if 'pe_ttm' in df.columns: mask &= (df['pe_ttm'] <= max_pe) & (df['pe_ttm'] > 0)
    if ind != "全部": mask &= (df['细分行业'] == ind)
    if kw.strip(): mask &= (df['ts_code'].astype('string').str.contains(kw.strip(), case=False, regex=False, na=False) | df['name'].astype('string').str.contains(kw.strip(), case=False, regex=False, na=False))
    
    show_df = df[mask].sort_values('RPS_50', ascending=False).copy()
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')
    cols = ['ts_code', 'name', '细分行业', 'price_now', 'pe_ttm', 'mv_亿', 'turnover_rate', 'RPS_50_Show', 'RPS_120', 'RPS_250', '连续天数', 'xueqiu_url']
    final_cols = [c for c in cols if c in show_df.columns]
    
    # 顶部统计 KPI
    col_k1, col_k2, col_k3 = st.columns(3)
    col_k1.metric("入选强势标的", f"{len(show_df)} 只")
    if 'mv_亿' in show_df.columns and not show_df.empty:
        col_k2.metric("中位数市值", f"{show_df['mv_亿'].median():.1f} 亿")
    if '连续天数' in show_df.columns and not show_df.empty:
        col_k3.metric("最长在榜", f"{show_df['连续天数'].max()} 天")

    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50 (强度)"),
            "细分行业": st.column_config.TextColumn("主营行业"),
            "price_now": st.column_config.NumberColumn("现价", format="%.2f"),
            "pe_ttm": st.column_config.NumberColumn("PE(TTM)", format="%.1f"),
            "mv_亿": st.column_config.NumberColumn("市值(亿)", format="%.1f"),
            "turnover_rate": st.column_config.NumberColumn("换手%", format="%.1f"),
        }, use_container_width=True, hide_index=True, height=800
    )

    candidate_actions(show_df, key="rps_next")
    page_link("查看榜单后续表现 →", "🎯 战绩回看", key="rps_score")

    # 快捷下载 CSV
    csv_bytes = show_df[final_cols].to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 导出筛选出的强势股 (CSV)",
        data=csv_bytes,
        file_name="strong_stocks_export.csv",
        mime="text/csv"
    )

def render_breakout_content(df):
    if df is None or df.empty: 
        st.info("💡 暂无阶段新高突破标的数据，将在每日收盘后计算生成。")
        return

    # 先说清数据日期，再说「今日共捕获」——否则「今日」二字本身就是误导。
    render_freshness_banner(df, "突破池")
    st.caption(f"该数据日共 {len(df)} 只达到突破筛选条件；重点核对突破位置、放量和回落后的失效价位。")
    
    with st.expander("🔍 突破维度筛选", expanded=True):
        b1, b2, b3 = st.columns([1.5, 1.5, 1])
        level_opts = ["全部"] + sorted(df['level'].unique().tolist())
        sel_level = b1.selectbox("突破周期级别", level_opts)
        
        min_vol_ratio = b2.slider("放量倍数 (相比5日均量)", 1.0, 5.0, 1.2, step=0.1)
        kw_b = b3.text_input("搜代码/名称", placeholder="搜索...", key="kw_breakout")

    mask = df['vol_ratio'] >= min_vol_ratio
    if sel_level != "全部":
        mask &= (df['level'] == sel_level)
    if kw_b.strip():
        mask &= (df['ts_code'].astype('string').str.contains(kw_b.strip(), case=False, regex=False, na=False) | df['name'].astype('string').str.contains(kw_b.strip(), case=False, regex=False, na=False))

    show_df = df[mask].sort_values(by=['pct_chg', 'vol_ratio'], ascending=[False, False]).copy()
    
    # 统计卡片
    c1, c2, c3 = st.columns(3)
    c1.metric("筛选标的数", f"{len(show_df)} 只")
    if not show_df.empty:
        c2.metric("平均当日涨幅", f"+{show_df['pct_chg'].mean():.2f}%")
        c3.metric("最高放量倍数", f"{show_df['vol_ratio'].max():.1f} 倍")

    cols = ['ts_code', 'name', 'level', 'industry', 'close', 'pct_chg', 'vol_ratio', 'turnover_rate', 'mv_亿', 'pe_ttm', 'xueqiu_url']
    final_cols = [c for c in cols if c in show_df.columns]
    
    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "level": st.column_config.TextColumn("突破级别"),
            "name": st.column_config.TextColumn("名称"),
            "industry": st.column_config.TextColumn("主营行业"),
            "close": st.column_config.NumberColumn("现价", format="%.2f"),
            "pct_chg": st.column_config.NumberColumn("涨幅%", format="%.2f %%"),
            "vol_ratio": st.column_config.NumberColumn("放量(倍)", format="%.2f"),
            "turnover_rate": st.column_config.NumberColumn("换手%", format="%.2f"),
            "mv_亿": st.column_config.NumberColumn("市值(亿)", format="%.1f"),
            "pe_ttm": st.column_config.TextColumn("PE(TTM)")
        }, use_container_width=True, hide_index=True, height=700
    )
    candidate_actions(show_df, key="breakout_next")

def render_etf_content(df):
    if df is None or df.empty: 
        st.info("暂无数据")
        return
    render_freshness_banner(df, "ETF 榜单")
    st.caption("用相对强度筛选方向，再看技术图中的位置；折溢价需到“投机与套利”核对价格与净值日期。")
    st.success(f"📈 捕捉到 {len(df)} 只强势 ETF")
    kw = st.text_input("🔍 搜 ETF")
    show_df = df.copy()
    if kw.strip(): show_df = show_df[show_df['name'].astype('string').str.contains(kw.strip(), case=False, regex=False, na=False) | show_df['ts_code'].astype('string').str.contains(kw.strip(), case=False, regex=False, na=False)]
    show_df = format_rps_show(show_df, 'RPS_50', 'rps_50_chg')
    target_cols = ['ts_code', 'name', 'price_now', 'RPS_50_Show', 'RPS_120', 'RPS_250', 'xueqiu_url']
    final_cols = [c for c in target_cols if c in show_df.columns]
    st.dataframe(
        show_df[final_cols], 
        column_config={
            "ts_code": st.column_config.TextColumn("代码"),
            "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "RPS_50_Show": st.column_config.TextColumn("RPS 50"),
            "price_now": st.column_config.NumberColumn("现价", format="%.3f"),
        }, use_container_width=True, hide_index=True, height=800
    )
    candidate_actions(show_df, key="etf_next")

def render_arbitrage_page():
    st.header("⚡ 投机与套利")
    st.caption("可转债收盘强弱与模式统计；场内基金核对价格、净值日期及折溢价。")
    data = load_json("data/speculation_data.json") or {}

    def make_xq_link(code):
        try:
            market = code.split('.')[-1].upper()
            num = code.split('.')[0]
            return f"https://xueqiu.com/S/{market}{num}"
        except: return ""

    st.markdown("---")
    render_convertible_block(load_json("data/convertible.json"))

    st.markdown("---")
    st.subheader("🧱 场内基金：价格与净值核对")
    st.caption("范围为成交额达标、涨幅居前的20只场内基金，包含境内ETF、LOF和REIT等。"
               "同日净值才计算折溢价；滞后净值只显示参考价差。实际申赎还需核对额度、到账时间与费用。")
    if data.get('fund_list'):
        fund_df = pd.DataFrame(display_funds(data['fund_list']))
        fund_df['xueqiu_url'] = fund_df['code'].apply(make_xq_link)
        fund_cols = ['code', 'name', 'price', 'quote_date', 'change', 'nav', 'nav_date',
                     'premium_rate', 'reference_gap', 'nav_status', 'vol_yi', 'xueqiu_url']
        fund_cols = [c for c in fund_cols if c in fund_df.columns]
        valid = int(fund_df['premium_rate'].notna().sum())
        st.caption(f"同日净值已核对 {valid}/{len(fund_df)} 只；空白表示无法计算，不表示零溢价。")
        st.dataframe(
            fund_df[fund_cols], 
            column_config={
                "code": st.column_config.TextColumn("代码"), "name": st.column_config.TextColumn("名称"),
                "price": st.column_config.NumberColumn("收盘价", format="%.3f"), "change": st.column_config.NumberColumn("涨幅%", format="%.2f %%"),
                "quote_date": "行情日期", "nav": st.column_config.NumberColumn("单位净值", format="%.4f"),
                "nav_date": "净值日期", "nav_status": "核对状态",
                "premium_rate": st.column_config.NumberColumn("同日折溢价%", format="%.2f %%"),
                "reference_gap": st.column_config.NumberColumn("对滞后净值价差%", format="%.2f %%"),
                "vol_yi": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
                "xueqiu_url": st.column_config.LinkColumn("雪球", display_text="❄️")
            }, use_container_width=True, hide_index=True
        )
        candidate_actions(fund_df, key="fund_next", code_col="code")
    else:
        st.info("基金行情尚未生成，等待下一次收盘更新。")

def render_guru_tracker():
    st.header("📚 机构公开持仓")
    st.caption("查看公开申报中的持仓快照，比较披露变化，并回到原始数据源核对。")
    st.info(
        "13F 只反映申报人填写的季度末持仓，通常要到季度结束后约 45 天才公开；"
        "披露后仓位可能已经变化，不能据此推断当前资金意图或后续方向。"
    )

    if "selected_guru" not in st.session_state: st.session_state.selected_guru = None
    if st.session_state.selected_guru is None:
        st.subheader("📄 基金经理公开披露")
        cols = st.columns(3)
        for index, (gid, info) in enumerate(GURUS.items()):
            col = cols[index % 3]
            with col:
                with st.container(border=True):
                    if os.path.exists(info['avatar']): image_stretch(info['avatar'])
                    else: st.warning("照片缺失")
                    st.subheader(info['name'])
                    st.caption(info['company'])
                    st.markdown(f"**风格**: {info['style']}")
                    if st.button("查看披露记录 🔍", key=f"btn_{gid}"):
                        st.session_state.selected_guru = gid
                        st.rerun()
    else:
        gid = st.session_state.selected_guru
        info = GURUS[gid]
        if st.button("⬅️ 返回大厅"):
            st.session_state.selected_guru = None
            st.rerun()
        st.divider()
        col1, col2 = st.columns([1, 4])
        with col1:
            if os.path.exists(info['avatar']): image_stretch(info['avatar'])
        with col2:
            st.markdown(f"## {info['name']}")
            st.markdown(f"**{info['company']}** | *{info['style']}*")
            st.markdown(f"[🔗 查看原始数据源]({info.get('source_url', '')})")
            
        st.subheader("📝 公开持仓快照")
        csv_path = f"data/gurus/{gid}_latest.csv"
        df = load_data(csv_path)
        if df is not None:
            st.dataframe(df, use_container_width=True, height=500)
            st.caption(
                "表格字段沿用原始数据源；申报日期、报告期与币种请在“原始数据源”中再次核对。"
                "持仓占比和近期活动是历史披露，不是当前建议。"
            )
        else:
            st.warning(f"🚧 暂未获取到 {info['name']} 的数据。请等待后台自动抓取脚本运行。")

def render_manual_page():
    st.header("📖 Chilam Club 驾驶舱使用指南")
    st.caption("“工欲善其事，必先利其器” —— 投资逻辑与指标释义")
    
    with st.expander("🔥 强势股 (RPS 动量策略) 玩法说明", expanded=True):
        st.markdown("""
        **1. 什么是 RPS (欧奈尔相对强度)？**
        RPS 衡量的是某只股票在过去一段时间内，涨幅超越全市场多少比例的股票。
        - **RPS_50 = 90**：代表该股过去 50 天的涨幅，超过了全市场 90% 的股票。
        - **本站筛选规则**：RPS_50 / RPS_120 / RPS_250 三个窗口**同时**大于 87，
          即中短长期涨幅都排在全市场前 13%。越靠近 100 说明历史动量越强。
        - **它是什么**：一个纯粹的**事后统计排名**，描述「过去涨得比谁多」。
        - **它不是什么**：不是买入信号，不含估值、基本面、筹码结构的判断，
          也不预测后续走势。RPS 高只说明该股已经涨过一大段——
          这既可能是趋势延续的起点，也可能是行情的末段。

        **2. 从榜单继续研究**
        - 选择标的进入日K，核对近期涨幅、成交变化，以及价格相对前高和均线的位置。
        - 在技术页记录观察依据、待核对条件和失效条件，加入自选后持续核对。
        - 「战绩回看」展示已核对记录、观察期限与基准差值；同一股票可能重复上榜。

        > ⚠️ 本站所有榜单均由程序按固定规则自动计算，**不构成投资建议**，
        > 不提供目标价、买卖点或仓位指引。历史动量不代表未来收益，据此操作风险自担。
        """)

    with st.expander("🛡️ 可转债投机台：收盘分类候选与十日模式", expanded=False):
        st.markdown("""
        **1. 收盘筛选缩小观察范围。** 当前只有日线，不能确认盘中股债是否联动。
        - **C 股债同向候选**：正股与转债同向且达到幅度阈值，再对照股债分时走势确认联动。
        - **A 债强于股候选**：转债涨幅比正股高至少 1.5 个百分点。
        - **B 股强于债候选**：正股涨幅比转债高至少 2 个百分点且正股涨幅≥2%。
        - **N 待进一步判断**：未满足现有阈值；完整规则在「判定规则」页签。

        **2. 「十日模式」统计的是什么？**
        - 每个已归档交易日取成交额≥3000万、当日涨幅最强的第一与第二名，记录跨日（昨收→次日开盘）、开盘至收盘（次日开盘→次日收盘）和开盘下方 3% 触价组（最低价穿越阈值后，按阈值到收盘计算）的价格差。
        - 「次日最低→次日收盘」非负，仅作事后空间参考，不统计正收益占比。
        - B 类统计正股收盘涨幅较高、收盘位于日内上沿且转债落后的样本；全天强度仍须看分时。
        - C 类按六个节奏阶段分组统计次日结果；各阶段同时列出记录数和结果分布。
        - 页面上的百分比是**正收益占比**（样本中结果大于 0 的比例），只描述已归档样本；
          所有结果均为价格之间的比较，未扣交易费用与滑点，也不代表订单能够成交。

        **3. 双低值（保留为估值参考列）**
        `双低值 = 可转债价格 + 转股溢价率`。双低值越小，价格越便宜、转股溢价越低。
        """)

    with st.expander("🧱 场内基金价格与净值 怎么核对", expanded=False):
        st.markdown("""
        **1. 什么是溢价率？**
        溢价率 = `(场内交易价格 - 基金实际净值) / 基金实际净值`
        - 先看价格日和净值日：只有同日数据才显示溢价率；净值滞后时只显示对照差额。
        - 表格覆盖成交额达标的场内基金涨幅前20，不是全市场套利扫描。选中标的可进入技术页。
        - 判断能否申赎套利，还需核对申赎开放、额度、转托管时间、成本和到账前价格变化。
        """)

    with st.expander("🌡️ 短线情绪派生指标 怎么读（2026-08 新增）", expanded=True):
        st.markdown("""
        这些指标记录收盘后的样本表现，具体口径如下：

        - **1进2 晋级率**：昨日首板股在今日继续涨停的比例，同时展示分子、分母。
        - **昨日连板股今日中位涨跌幅**：以昨日收盘价为基准统计今日表现。
          上涨占比为样本中涨幅大于零的比例，未模拟实际进出价格和交易费用。
        - **梯队缺档**：上下高度都有股票时，中间某一高度没有股票。与最高连板数下降分别记录。
        - **均值−中位差**：全市场个股涨跌幅的算术均值减中位数，单位为百分点。
        - **统计档位**：按历史分位数对连板样本分组；核对项在下一交易日收盘后比较。
        """)

    with st.expander("🎯 战绩回看 怎么读（2026-08 新增）", expanded=False):
        st.markdown("""
        - 展示归档名单在 T+1、T+5、T+10 的表现，以及同期沪深300的差值。
        - 跑赢基准的占比不等于交易胜率。中位数和四分位数描述现有记录。
        - 同一股票重复上榜、相邻观察窗口重叠，记录之间可能相关。
        - 回归 beta 为这段历史内的拟合系数；回归剩余部分不等于选股能力。
        - 起点为上榜日收盘，未模拟次日实际成交、手续费、滑点和涨跌停限制。
        - 历史回溯只重建 RPS 榜；其他榜单使用当日留存的快照。
        """)

    with st.expander("📮 收盘摘要 与推送 说明（2026-08 新增）", expanded=False):
        st.markdown("""
        正文汇总市场较上一交易日的变化、当日表现靠前的题材，以及近10日靠前题材的内部表现。
        接力样本、战法池、行业信号和榜单放在明细中；个性化推送还包含自选股当日表现。

        - **摘要内容始终免费可看**（就在本站「收盘摘要」页）；付费部分是**投递**——
          收盘后自动推到你的微信（在「收盘摘要」或「我的池子」页扫码绑定一次即可），
          并且带上用你自己自选股算的「你的池子今日」那一段。**投递主通道是微信**；
          站点开通邮件通道后，未绑微信的订阅用户会改收到邮件版摘要。
          （另：「找回密码」是**独立**通道，重置码会发到你的微信或注册邮箱，
          与摘要投递互不影响。）
        - **数据缺失就少说一段，绝不编**。任一段的数据源没取到，该段整段不出现，
          并在末尾统一列出"本次缺失"，让你能区分"今天没事发生"和"数据没到"。
        - **关键数据全缺时不发送**。宁可今天不推，也不推一条"今天没有数据"的骚扰消息。
        - 历史摘要保留当时的文字和统计口径；最新复盘使用当前的生成规则。
        """)

    with st.expander("🏭 行业信号 怎么读（2026-10 新增）", expanded=False):
        st.markdown("""
        「行业信号」页每天在**申万二级行业**里找同时满足两个条件的行业，
        并把结果作为一段放进收盘摘要的推送里。两个条件是：

        1. **市值占比处在自己的历史低位** —— 占参与行业市值合计的比重，在近250个交易日中的位置偏低；
        2. **成交额占比处在自己的历史高位** —— 占参与行业成交合计的比重，在近20个交易日中的位置偏高。

        接下来对照行业价格、成分股涨跌和历史命中结果。低占比不等于低估，高成交占比也不等于资金净流入。

        **必须知道的几点**

        - **两个窗口不一样长是刻意的**：占比是慢变量，短期内受单日行情和解禁事件影响较大，所以用 250 日；
          成交额是快变量，只有 20 日才能体现"最近正在挤进来"而不是"一直很热"。
        - **分位是含当日的中位秩分位**（0 = 窗口内最小，100 = 最大）。
          用中位秩是为了让并列值各分一半——否则一串相同的值会被整体判成高分位。
        - **命中条件是严格不等式**：市值分位低于阈值**且**热度分位高于阈值，
          恰好等于阈值不算命中。阈值随每日结论一起落盘，页面上的图与表都读它——
          不存在"图按一套阈值画、结论按另一套"的可能。
        - **它天然偏小行业**：占比序列的波动幅度与行业体量相关，小行业更容易走到
          极端分位。这是指标性质，不是筛选偏好——看图时请注意纵轴的体量含义。
        - **每天只推热度分位最高的 3 个**，但页面上的明细表是**完整**的（全部参与行业），
          且推送里会写明"另有 N 个满足条件"。不会让你以为只有那几个。
        - **命中 0 个是一项筛选结果**。推送里会如实写"今日无同时满足两条件的行业"，
          它与"这一段的数据没取到"是**两句不同的话**，页面也分开显示。
        - **截面残缺日整日剔除**：某天归档的行业数明显少于常态时，那一天不参与计算。
          因为占比的分母是当日全体行业之和，少一半行业会让每个行业的占比凭空翻倍，
          而结果看起来仍是一组正常的百分数——这类错误没有任何可见迹象。
        - **分位所需历史**：滚动分位至少需要 20 个完整截面交易日；
          不足时只标注可用长度，尚未进入分位计算。
        - **页面底部有「历史命中回顾」**：把同一套规则在过去每个交易日回放一遍，
          给出命中之后 T+1/T+3/T+5/T+10 的涨跌，并与同期"全行业等权平均"对比。
          两点必须知道：只有**分位窗口满额**的交易日才纳入回算（归档开头那段被跳过，
          页面上会写清跳过了多少天）；表格里的「—」表示那一天还没到期，
          **不是涨跌为零**。它是描述性统计，**不是回测**——没有交易成本、滑点、
          仓位与择时规则，且同一行业连续多日命中会被重复计入，样本并不相互独立。
        """)

def render_login_lock(feature_name: str):
    """登录门禁卡片（不是付费墙）。

    「我的池子」锁的是登录、不是 VIP：功能依赖用户自己的账号数据，
    没账号无处存放，但免费账号登录后功能完整可用。
    旧版这里写「VIP 会员专享模块」，与实际门禁不符，会把人误导去付费。
    """
    st.warning(f"🔒 **{feature_name}** 需要登录后使用")
    st.info("这个模块保存的是你自己的池子与复盘记录，需要一个账号来存放——"
            "**免费注册即可完整使用，无需开通 VIP**。")
    if st.button("👉 登录 / 注册（免费）", key=f"btn_login_{feature_name}"):
        st.switch_page("pages/auth.py")

def main():
    pending = bool(st.session_state.get("journey_target"))
    apply_handoff(st.session_state)
    if pending and st.session_state.get("active_page"):
        st.query_params["view"] = st.session_state["active_page"]
        if (
            st.session_state["active_page"] != SECTION_PAGE
            and "section" in st.query_params
        ):
            del st.query_params["section"]
    st.markdown("""<style>
      [data-testid="stSidebarNav"] {display:none;}
      .block-container {padding-top:2rem; padding-bottom:2rem;}
      [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:0.25rem;}
      [data-testid="stSidebar"] button {text-align:left; min-height:2rem;}
      [data-testid="stSidebar"] button > div {justify-content:flex-start !important;}
      [data-testid="stSidebar"] button p {font-size:0.88rem;}
      [data-testid="stMetric"] {padding:0.5rem 0;}
      [data-testid="stAppDeployButton"],
      [data-testid="stMainMenu"] {display:none !important;}
    </style>""", unsafe_allow_html=True)
    with st.sidebar:
        st.markdown("### Chilam.Club")
        page = render_navigation()
        with st.expander("账号与站点"):
            # 登录状态展示
            if auth.is_logged_in():
                u_email = auth.get_user_email()
                if auth.is_admin():
                    st.caption(f"👤 `{u_email}` (🛡️管理员)")
                elif auth.is_vip():
                    st.caption(f"👤 `{u_email}` (👑VIP)")
                else:
                    st.caption(f"👤 `{u_email}` (普通用户)")

                c_m1, c_m2 = st.columns(2)
                with c_m1:
                    if st.button("会员中心", key="sb_dash", use_container_width=True):
                        st.switch_page("pages/dashboard.py")
                with c_m2:
                    if st.button("退出", key="sb_logout", use_container_width=True):
                        feedback_ui.request_feedback_session_clear()
                        auth.logout()
                        st.rerun()
                if auth.is_admin():
                    if st.button("⚙️ 后台管理", key="sb_admin", use_container_width=True):
                        # 必须是 pages/ 下的路径：switch_page 只认入口脚本与 pages/ 目录，
                        # 传 "admin.py" 会抛 StreamlitAPIException（Cloud 上错误还被脱敏）。
                        st.switch_page("pages/admin.py")
            else:
                st.caption("👤 游客未登录")
                if st.button("🔐 登录 / 注册", key="sb_login", use_container_width=True):
                    st.switch_page("pages/auth.py")

            _shop = site_settings.shop_entry_config()
            if _shop.get("enabled"):
                st.link_button(_shop["label"], _shop["url"], width="stretch")
            st.caption("数据来自第三方，可能延迟或缺失。历史统计供研究使用，不构成收益承诺。")
            if os.path.exists("donate.jpg"):
                st.image("donate.jpg", caption="请喝咖啡 ☕")

        feedback_ui.render_sidebar_feedback(page)

    # ================= 路由 =================
    # 门禁原则（2026-08-29 调整）：
    #   全市场客观数据（强势股/投机套利/机构持仓披露/战绩回看）一律开放——
    #   这些内容在同花顺、东财上免费就能看到近似替代品，锁它们只会在用户
    #   建立信任之前先把人赶走。
    #   真正的付费墙放在「个性化视角」与「主动推送」上：这两样依赖用户自己的
    #   数据与时间积累，别处抄不走，也是唯一让人愿意按月付费的东西。
    if page == "🗂 我的复盘":
        render_decision_workbench_page("home")
    elif page == "🧭 多策略选股":
        render_decision_workbench_page("candidates")
    elif page == "🧩 题材跟踪":
        render_decision_workbench_page("themes")
    elif page == "📊 名单表现":
        render_decision_workbench_page("outcomes")
    elif page == "📒 研究档案":
        render_decision_workbench_page("tracking")
    elif page == "🛸 全市场看板":
        render_market_dashboard()
    elif page == "🔄 板块轮动":
        render_sector_rotation_page()
    elif page == "🏭 行业信号":
        # 新鲜度基准从外面传进去，而不是让页面自己再算一遍 site_reference_date：
        # 一份规则两处实现必然漂移，哪天同批比对的产物清单改了只会改到一处。
        render_industry_signal_page(site_reference_date())
    elif page == "📮 收盘摘要":
        # 免费内容先展示市场叙事，再展示原始摘要；投递权益在页内处理。
        render_digest_page()
    elif page == "📋 晚间复盘与次日计划":
        # 六大客观选股池与181法则库免费引流；战法分周期胜率矩阵与自选股战法共振诊断锁 VIP（页内处理）
        render_evening_plan_page()
    elif page == "📝 复盘答卷":
        # 填卷免费养成每日习惯；历史对比+命中率是 VIP（页内自行处理付费墙）
        render_review_page()
    elif page == "⭐ 我的池子 (每日复盘)":
        if not auth.is_logged_in():
            render_login_lock("⭐ 我的池子 (个人每日复盘)")
        else:
            render_watchlist_page()
    elif page == "🎯 战绩回看":
        render_scorecard_page()
    elif page == "⚡ 实时行情 + 技术分析":
        render_live_quote_page()
    elif page == "🔥 强势股":
        df_stock = load_data("data/strong_stocks.csv")
        df_breakout = load_data("data/breakout_stocks.csv")
        df_etf = load_data("data/strong_etfs.csv")
        t1, t2, t3 = st.tabs(["🐉 RPS 个股", "🚀 阶段新高突破", "💰 强势 ETF"])
        with t1: render_stock_content(df_stock)
        with t2: render_breakout_content(df_breakout)
        with t3: render_etf_content(df_etf)
    elif page == "🌐 宏观与股债性价比":
        render_macro_erp_page()
    elif page == "⚡ 投机与套利":
        render_arbitrage_page()
    elif page == "📐 强势股区间偏离":
        render_core_driver_page()
    elif page == "📚 机构持仓披露":
        render_guru_tracker()
    elif page == "📏 黄金分割参考位":
        render_fibonacci_chart()
    elif page == "📖 使用说明文档":
        render_manual_page()
    elif page == "📰 资讯雷达":
        render_news_page()

if __name__ == "__main__":
    main()
