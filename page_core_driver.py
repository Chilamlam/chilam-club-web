import streamlit as st
import pandas as pd
from investor_journey import candidate_actions, explicit_symbol
from site_data import load_json
import symbol_resolve as sr

def render_core_driver_page():
    st.header("🚨 强势股区间偏离雷达")
    st.caption("先看追踪区间、相对基准的涨幅和回撤，再进入技术图核对位置。")

    col_t1, col_t2 = st.columns([1, 2])
    with col_t1:
        period_choice = st.radio(
            "选择观察窗口",
            options=["10个交易日 (参考值100%)", "30个交易日 (参考值200%)"],
            index=0
        )
    with col_t2:
        st.info("""
        **筛选口径**
        - 区间内某日涨幅 ≥ 9.5% 才进入样本；之后收盘较入池价回落超过 12% 的样本被剔除。
        - 偏离值 = 个股区间涨幅 − 对应指数区间涨幅。
        - 100% / 200% 是本站比较用参考值；具体监管认定需核对交易所规则与公告。
        """)

    # 极速读取后台已经算好的静态文件
    radar_data = load_json("data/radar_data.json")
    if not radar_data:
        st.warning("区间数据尚未生成，请稍后查看。")
        return
        
    st.caption(f"🔄 数据最后更新时间: {radar_data.get('update_time', '未知')}")
    
    # 提取数据
    key = "10d" if "10" in period_choice else "30d"
    period_data = radar_data.get(key, {})
    msg = period_data.get("msg", "")
    records = period_data.get("data", [])
    
    if records:
        st.write(f"📅 **追踪区间**: `{msg}`")
        df_show = pd.DataFrame(records)
        
        # ==========================================
        # ★ 新增：生成雪球 K 线图一键跳转链接 ★
        # ==========================================
        df_show['xueqiu_url'] = df_show['ts_code'].map(lambda code: sr.xueqiu_url(explicit_symbol(code)))
        threshold = 100 if key == "10d" else 200
        df_show['参考值占比(%)'] = pd.to_numeric(df_show['偏离值(%)'], errors='coerce') / threshold * 100
        
        # 将 xueqiu_url 加入显示列表的最右侧
        display_cols = ['ts_code', 'name', 'industry', '偏离值(%)', '参考值占比(%)', '入池日', '最大回撤(%)', '个股涨幅(%)', 'xueqiu_url']
        
        st.dataframe(
            df_show[display_cols].rename(columns={'ts_code': '代码', 'name': '名称', 'industry': '行业板块'}).set_index('代码'),
            column_config={
                "xueqiu_url": st.column_config.LinkColumn("盘面走势", display_text="❄️ 看图")
            },
            use_container_width=True,
            height=700
        )
        candidate_actions(df_show, key="radar_next")
    else:
        st.error(f"未能生成数据。后台提示: {msg}")
