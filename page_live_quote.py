"""
全市场毫秒级实时行情与多周期 K 线图引擎 (A股/指数/ETF/港股/美股/大宗商品)
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import urllib.request
import json
import re
from datetime import datetime

def _format_symbol(raw_symbol: str) -> tuple[str, str]:
    """
    智能识别输入的代码并格式化为标准市场代码
    返回: (tencent_code, asset_type)
    """
    s = raw_symbol.strip().upper()
    
    # 纯数字
    if re.match(r'^\d{6}$', s):
        if s.startswith(('60', '68', '51', '58', '000', '999')):
            return f"sh{s}", "A股/ETF"
        else:
            return f"sz{s}", "A股/ETF"
            
    # 港股 5位纯数字
    if re.match(r'^\d{5}$', s):
        return f"r_hk{s}", "港股"
        
    # 带前缀的 A股/指数
    if s.startswith(('SH', 'SZ')):
        return s.lower(), "A股/指数"
        
    # 港股带 HK 前缀
    if s.startswith('HK'):
        code_num = s.replace('HK', '')
        return f"r_hk{code_num.zfill(5)}", "港股"
        
    # 美股 (全英文字母)
    if re.match(r'^[A-Z\.]+$', s):
        # 常见商品期货简码
        if s in ('GC', 'GOLD'): return "hf_GC", "商品期货(黄金)"
        if s in ('CL', 'OIL'): return "hf_CL", "商品期货(原油)"
        return f"us{s.replace('.', '')}", "美股"
        
    # 大宗商品期货前缀
    if s.startswith('HF_'):
        return s.lower(), "商品期货"

    return s.lower(), "通用标的"


@st.cache_data(ttl=5)
def get_realtime_quote(code: str) -> dict:
    """获取毫秒级最新逐笔报价"""
    # 处理商品期货
    if code.startswith('hf_'):
        url = f"https://hq.sinajs.cn/list={code}"
        req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn', 'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                content = resp.read().decode('gbk', errors='ignore')
                match = re.search(r'="([^"]+)"', content)
                if match and match.group(1):
                    parts = match.group(1).split(',')
                    return {
                        "name": parts[13] if len(parts) > 13 else code,
                        "price": float(parts[0]),
                        "high": float(parts[4]) if len(parts) > 4 else 0,
                        "low": float(parts[5]) if len(parts) > 5 else 0,
                        "pct_chg": 0.0,
                        "update_time": parts[6] if len(parts) > 6 else ""
                    }
        except Exception:
            return {}

    # 腾讯行情接口
    url = f"http://qt.gtimg.cn/q={code}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            content = resp.read().decode('gbk', errors='ignore')
            match = re.search(r'="([^"]+)"', content)
            if match and match.group(1):
                parts = match.group(1).split('~')
                if len(parts) > 30:
                    return {
                        "name": parts[1],
                        "code": parts[2],
                        "price": float(parts[3]),
                        "last_close": float(parts[4]),
                        "open": float(parts[5]),
                        "vol_shares": float(parts[6]),
                        "pct_chg": float(parts[32]),
                        "high": float(parts[33]),
                        "low": float(parts[34]),
                        "amount_yi": round(float(parts[37]) if parts[37] else 0, 2),
                        "pe": float(parts[39]) if parts[39] else 0,
                        "mv_yi": round(float(parts[45]) if parts[45] else 0, 2),
                        "update_time": parts[30]
                    }
    except Exception:
        pass
    return {}


@st.cache_data(ttl=15)
def get_minute_line(code: str) -> pd.DataFrame:
    """获取今日盘中分时明细"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            min_raw = data.get('data', {}).get(code, {}).get('data', {}).get('data', [])
            if min_raw:
                records = []
                for item in min_raw:
                    parts = item.split()
                    if len(parts) >= 2:
                        t_str = parts[0]
                        time_fmt = f"{t_str[:2]}:{t_str[2:]}"
                        records.append({
                            "time": time_fmt,
                            "price": float(parts[1]),
                            "vol": float(parts[2]) if len(parts) > 2 else 0
                        })
                return pd.DataFrame(records)
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=30)
def get_kline_data(code: str, period: str = "day") -> pd.DataFrame:
    """
    获取多周期 K 线数据 (m5, m15, m30, m60, day)
    """
    # 转换周期参数
    period_map = {
        "5分钟": "m5",
        "15分钟": "m15",
        "30分钟": "m30",
        "60分钟": "m60",
        "日K": "day"
    }
    p_code = period_map.get(period, "m5")
    
    if p_code in ("m5", "m15", "m30", "m60"):
        url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={code},{p_code},,120"
    else:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,120,qfq"

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            res_dict = data.get('data', {}).get(code, {})
            k_raw = res_dict.get(p_code) or res_dict.get('day') or res_dict.get('qfqday')
            if k_raw:
                records = []
                for item in k_raw:
                    # 格式: [date, open, close, high, low, vol, ...]
                    if len(item) >= 6:
                        d_str = str(item[0])
                        # 格式化日期时间
                        if len(d_str) == 12: # 202608281030
                            dt_fmt = f"{d_str[4:6]}-{d_str[6:8]} {d_str[8:10]}:{d_str[10:12]}"
                        elif len(d_str) == 8: # 20260828
                            dt_fmt = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:]}"
                        else:
                            dt_fmt = d_str
                            
                        records.append({
                            "datetime": dt_fmt,
                            "open": float(item[1]),
                            "close": float(item[2]),
                            "high": float(item[3]),
                            "low": float(item[4]),
                            "vol": float(item[5])
                        })
                return pd.DataFrame(records)
    except Exception:
        pass
    return pd.DataFrame()


def render_live_quote_page():
    st.header("⚡ 毫秒级全市场实时行情与 K 线看板")
    st.caption("直连极速行情通道，支持 A股 / 港股 / 美股 / ETF / 指数 / 大宗商品 多周期切换")

    # 快捷热门预设
    st.markdown("**🔥 热门快速直达：**")
    p_cols = st.columns(6)
    presets = [
        ("贵州茅台", "600519"),
        ("上证指数", "sh000001"),
        ("纳斯达克ETF", "513100"),
        ("腾讯控股", "00700"),
        ("英伟达", "NVDA"),
        ("现货黄金", "GC")
    ]
    
    if "active_symbol" not in st.session_state:
        st.session_state.active_symbol = "600519"

    for idx, (label, sym) in enumerate(presets):
        with p_cols[idx]:
            if st.button(f"{label} `{sym}`", key=f"p_{sym}", use_container_width=True):
                st.session_state.active_symbol = sym
                st.rerun()

    # 输入框
    col_input, col_refresh = st.columns([3, 1])
    with col_input:
        user_input = st.text_input(
            "🔍 输入任意标的代码 (支持 600519, 000001, 513100, 00700, AAPL, NVDA, GC 等)", 
            value=st.session_state.active_symbol
        )
    with col_refresh:
        st.write("")
        st.write("")
        if st.button("🔄 刷新最新毫秒报价", use_container_width=True):
            st.rerun()

    if not user_input:
        st.info("请输入标的代码。")
        return

    code, asset_type = _format_symbol(user_input)

    # 获取实时报价
    quote = get_realtime_quote(code)
    
    if not quote:
        st.error(f"❌ 未能匹配到代码 `{user_input}` 的实时行情，请确认代码是否正确。")
        return

    # 顶部 KPI 报价栏
    price = quote.get("price", 0.0)
    pct_chg = quote.get("pct_chg", 0.0)
    name = quote.get("name", user_input)
    is_up = pct_chg >= 0
    delta_str = f"{pct_chg:+.2f}%"

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("标的名称", f"{name}", delta=f"{asset_type}")
    k2.metric("最新现价", f"{price:.2f}", delta=delta_str, delta_color="normal")
    k3.metric("最高 / 最低", f"{quote.get('high', 0):.2f} / {quote.get('low', 0):.2f}")
    if quote.get("amount_yi"):
        k4.metric("成交额", f"{quote.get('amount_yi')} 亿")
    else:
        k4.metric("成交量", f"{quote.get('vol_shares', 0):.0f}")
    k5.metric("更新时间", f"{str(quote.get('update_time', ''))[-8:]}")

    st.markdown("---")

    # K线与分时周期切换
    period = st.radio(
        "📈 选择行情周期", 
        ["分时走势", "5分钟", "15分钟", "30分钟", "60分钟", "日K"], 
        index=0, 
        horizontal=True
    )

    if period == "分时走势":
        df_min = get_minute_line(code)
        if df_min.empty:
            st.info("💡 该标的暂无当日分时明细，可切换至下方分钟 K 线查看。")
        else:
            last_close = quote.get("last_close", df_min['price'].iloc[0])
            fig_min = go.Figure()
            
            # 分时均线
            fig_min.add_trace(go.Scatter(
                x=df_min['time'], 
                y=df_min['price'], 
                mode='lines', 
                name='现价', 
                line=dict(color='#00d2d3', width=2),
                fill='tozeroy',
                fillcolor='rgba(0, 210, 211, 0.08)'
            ))
            
            # 昨收基准参考线
            fig_min.add_hline(y=last_close, line_dash="dash", line_color="gray", annotation_text=f"昨收 {last_close:.2f}")

            fig_min.update_layout(
                title=f"{name} ({user_input}) 当日分时图",
                xaxis=dict(tickangle=-45, gridcolor='rgba(128,128,128,0.2)'),
                yaxis=dict(title="价格", gridcolor='rgba(128,128,128,0.2)'),
                hovermode="x unified",
                height=450
            )
            st.plotly_chart(fig_min, use_container_width=True)

    else:
        # 分钟级 / 日级 K线
        df_k = get_kline_data(code, period)
        if df_k.empty:
            st.info(f"💡 暂未获取到该周期的 K 线数据。")
        else:
            fig_k = go.Figure()
            # 蜡烛图
            fig_k.add_trace(go.Candlestick(
                x=df_k['datetime'],
                open=df_k['open'],
                high=df_k['high'],
                low=df_k['low'],
                close=df_k['close'],
                name=f"{period} K线",
                increasing_line_color='#ff4d4d', # A股红涨
                decreasing_line_color='#2ecc71' # A股绿跌
            ))

            # 5周期 & 20周期 移动平均线
            df_k['ma5'] = df_k['close'].rolling(5).mean()
            df_k['ma20'] = df_k['close'].rolling(20).mean()
            fig_k.add_trace(go.Scatter(x=df_k['datetime'], y=df_k['ma5'], name='MA5', line=dict(color='#f39c12', width=1.5)))
            fig_k.add_trace(go.Scatter(x=df_k['datetime'], y=df_k['ma20'], name='MA20', line=dict(color='#3498db', width=1.5)))

            fig_k.update_layout(
                title=f"{name} ({user_input}) - {period} 走势",
                xaxis=dict(rangeslider=dict(visible=False), tickangle=-45, gridcolor='rgba(128,128,128,0.2)'),
                yaxis=dict(title="价格", gridcolor='rgba(128,128,128,0.2)'),
                hovermode="x unified",
                height=500
            )
            st.plotly_chart(fig_k, use_container_width=True)
