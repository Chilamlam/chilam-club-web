import streamlit as st
import pandas as pd
import json
import os

def render_core_driver_page():
    st.header("🚨 核心龙头生命周期与避险雷达")
    st.caption("过滤盘面噪音，专注核心资产。每日盘后云计算自动生成，网页秒开。")

    col_t1, col_t2 = st.columns([1, 2])
    with col_t1:
        period_choice = st.radio(
            "选择异动监管周期",
            options=["10个交易日 (100%红线)", "30个交易日 (200%红线)"],
            index=0
        )
    with col_t2:
        st.info("""
        **👑 核心龙骨法则：**
        1. **入场券**：区间内某日涨幅 ≥ 9.5%（涨停或大阳线爆发）方可入池。
        2. **死神线**：自入池日起，只要某日收盘价跌破入池日收盘价的 **12%**，立刻除名！
        3. **高低切**：坚决回避 `💀极限高危`，优先寻找同身位、同题材的 `🟢安全主升` 进行承接。
        """)

    # 极速读取后台已经算好的静态文件
    json_path = "data/radar_data.json"
    if not os.path.exists(json_path):
        st.warning("⏳ 数据尚未生成，请去 GitHub Actions 手动运行一次流水线。")
        return
        
    with open(json_path, "r", encoding="utf-8") as f:
        radar_data = json.load(f)
        
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
        def make_xq_link(code):
            try:
                # 把 000001.SZ 转换成 SZ000001 格式
                market = code.split('.')[-1].upper()
                num = code.split('.')[0]
                return f"https://xueqiu.com/S/{market}{num}"
            except: return ""
            
        df_show['xueqiu_url'] = df_show['ts_code'].apply(make_xq_link)
        
        # 将 xueqiu_url 加入显示列表的最右侧
        display_cols = ['ts_code', 'name', 'industry', '偏离值(%)', '状态评估', '入池日', '最大回撤(%)', '个股涨幅(%)', 'xueqiu_url']
        
        st.dataframe(
            df_show[display_cols].rename(columns={'ts_code': '代码', 'name': '名称', 'industry': '行业板块'}).set_index('代码'),
            column_config={
                "xueqiu_url": st.column_config.LinkColumn("盘面走势", display_text="❄️ 看图")
            },
            use_container_width=True,
            height=700
        )
    else:
        st.error(f"未能生成数据。后台提示: {msg}")
