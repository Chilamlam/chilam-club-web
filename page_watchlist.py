"""
个人自选股雷达页面 (与 Supabase 云端持久化绑定)
"""
import streamlit as st
import pandas as pd
import os
import auth
import database

def render_watchlist_page():
    st.header("⭐ 个人自选股雷达 (云端持久化)")
    st.caption("绑定个人账号，随时追踪自选标的的 RPS 动量强度、突破状态与生命周期")

    if not auth.is_logged_in():
        st.warning("🔒 自选股雷达需要登录账号后使用，以便云端同步您的自选清单。")
        if st.button("去登录 / 注册 🔐"):
            st.switch_page("pages/auth.py")
        return

    user_id = auth.get_user_id()
    email = auth.get_user_email()
    
    # 1. 读取当前自选列表
    if "user_watchlist" not in st.session_state:
        st.session_state.user_watchlist = database.get_user_watchlist(user_id)

    cur_watchlist = st.session_state.user_watchlist

    # 2. 添加/管理自选表单
    with st.expander("➕ 添加 / 批量管理自选代码", expanded=(len(cur_watchlist) == 0)):
        with st.form("add_stock_form"):
            new_input = st.text_input("输入股票代码 (多个可用逗号隔开，如 002603, 000703, 600519)", placeholder="000001, 600519")
            submitted = st.form_submit_button("添加到自选 💾")
            
            if submitted:
                if new_input:
                    tokens = [x.strip() for x in new_input.replace("，", ",").split(",") if x.strip()]
                    updated_list = list(dict.fromkeys(cur_watchlist + tokens))
                    ok = database.update_user_watchlist(user_id, updated_list)
                    if ok:
                        st.session_state.user_watchlist = updated_list
                        st.success(f"已成功添加并同步至云端！当前自选共 {len(updated_list)} 只。")
                        st.rerun()
                    else:
                        st.session_state.user_watchlist = updated_list
                        st.info("已在本地更新自选清单")
                        st.rerun()

    # 3. 关联市场大表与 RPS 动量数据
    if not cur_watchlist:
        st.info("💡 您的自选清单为空，请先在上方输入股票代码添加。")
        return

    st.subheader(f"📋 我的自选清单 ({len(cur_watchlist)} 只)")

    # 读取全市场快照与 RPS 表
    df_snap = pd.read_csv("data/market_snapshot.csv") if os.path.exists("data/market_snapshot.csv") else pd.DataFrame()
    df_rps = pd.read_csv("data/strong_stocks.csv") if os.path.exists("data/strong_stocks.csv") else pd.DataFrame()
    df_break = pd.read_csv("data/breakout_stocks.csv") if os.path.exists("data/breakout_stocks.csv") else pd.DataFrame()

    records = []
    for code in cur_watchlist:
        code_clean = code.split('.')[0]
        rec = {
            "代码": code,
            "名称": "-",
            "题材行业": "-",
            "现价": 0.0,
            "RPS50动量": "普通 (<87)",
            "突破状态": "震荡蓄势",
            "雪球": f"https://xueqiu.com/S/{'SH' if code_clean.startswith('6') else 'SZ'}{code_clean}"
        }

        # 匹配快照
        if not df_snap.empty:
            match_s = df_snap[df_snap['ts_code'].str.contains(code_clean, na=False)]
            if not match_s.empty:
                r = match_s.iloc[0]
                rec["名称"] = r.get('name', '-')
                rec["题材行业"] = r.get('industry', '-')
                rec["现价"] = float(r.get('close', 0.0))

        # 匹配 RPS
        if not df_rps.empty:
            match_r = df_rps[df_rps['ts_code'].str.contains(code_clean, na=False)]
            if not match_r.empty:
                r = match_r.iloc[0]
                rec["RPS50动量"] = f"🔥 RPS: {r.get('RPS_50', 0):.1f} (在榜{r.get('连续天数', 1)}天)"
                if rec["名称"] == "-": rec["名称"] = r.get('name', '-')

        # 匹配突破
        if not df_break.empty:
            match_b = df_break[df_break['ts_code'].str.contains(code_clean, na=False)]
            if not match_b.empty:
                r = match_b.iloc[0]
                rec["突破状态"] = r.get('level', '新高突破')

        records.append(rec)

    df_show = pd.DataFrame(records)

    st.dataframe(
        df_show,
        column_config={
            "代码": st.column_config.TextColumn("代码"),
            "雪球": st.column_config.LinkColumn("雪球", display_text="❄️"),
            "名称": st.column_config.TextColumn("名称"),
            "题材行业": st.column_config.TextColumn("题材行业"),
            "现价": st.column_config.NumberColumn("现价", format="%.2f"),
            "RPS50动量": st.column_config.TextColumn("RPS 动量身位"),
            "突破状态": st.column_config.TextColumn("突破状态")
        },
        use_container_width=True,
        hide_index=True
    )

    # 移除自选操作
    st.markdown("---")
    del_code = st.selectbox("🗑️ 选择要移出的自选标的：", ["请选择..."] + cur_watchlist)
    if st.button("确认移出 ❌") and del_code != "请选择...":
        updated_list = [c for c in cur_watchlist if c != del_code]
        database.update_user_watchlist(user_id, updated_list)
        st.session_state.user_watchlist = updated_list
        st.success(f"已移出 {del_code}")
        st.rerun()
