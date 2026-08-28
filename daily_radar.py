import tushare as ts
import pandas as pd
import datetime
import os
import json
import warnings

warnings.filterwarnings("ignore")

# 读取环境变量中的 Token
MY_TOKEN = os.getenv('TUSHARE_TOKEN', '')
if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

def process_radar(days):
    try:
        beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        start_cal = (beijing_time - datetime.timedelta(days=100)).strftime('%Y%m%d')
        end_cal = beijing_time.strftime('%Y%m%d')
        
        df_cal = pro.trade_cal(exchange='SSE', is_open='1', start_date=start_cal, end_date=end_cal)
        dates = df_cal['cal_date'].sort_values(ascending=False).tolist()
        if len(dates) < days + 1: return [], "日历数据不足"
            
        target_dates = dates[:days+1]
        target_dates.reverse()

        dfs = []
        for d in target_dates:
            d_daily = pro.daily(trade_date=d)
            d_adj = pro.adj_factor(trade_date=d)
            if not d_daily.empty and not d_adj.empty:
                d_merge = pd.merge(d_daily, d_adj, on=['ts_code', 'trade_date'])
                
                d_merge['close'] = pd.to_numeric(d_merge['close'], errors='coerce')
                d_merge['pct_chg'] = pd.to_numeric(d_merge['pct_chg'], errors='coerce')
                d_merge['amount'] = pd.to_numeric(d_merge['amount'], errors='coerce')
                d_merge['adj_factor'] = pd.to_numeric(d_merge['adj_factor'], errors='coerce')
                
                d_merge['adj_close'] = d_merge['close'] * d_merge['adj_factor']
                dfs.append(d_merge)
                
        if not dfs: return [], "无行情数据"
        
        df_all = pd.concat(dfs, ignore_index=True)
        
        # ★★★ 终极防空窗机制：重新校准真实的 t_now 和 t_past ★★★
        actual_dates = df_all['trade_date'].drop_duplicates().sort_values().tolist()
        t_now = actual_dates[-1]  # 强制使用数据集中真正存在的最后一天
        t_past = actual_dates[0]  # 强制使用数据集中真正存在的第一天
        
        limit_up_events = df_all[df_all['pct_chg'] >= 9.5].copy()
        if limit_up_events.empty: return [], "区间内无强势股"

        entry_points = limit_up_events.groupby('ts_code')['trade_date'].min().reset_index()
        entry_points.columns = ['ts_code', '入池日']
        
        entry_data = pd.merge(entry_points, df_all, left_on=['ts_code', '入池日'], right_on=['ts_code', 'trade_date'])
        entry_data = entry_data[['ts_code', '入池日', 'adj_close', 'close']].rename(columns={'adj_close': '入池复权价', 'close': '入池日收盘价'})
        
        df_check = pd.merge(df_all, entry_data, on='ts_code')
        df_check = df_check[df_check['trade_date'] >= df_check['入池日']]
        
        min_closes = df_check.groupby('ts_code')['adj_close'].min().reset_index().rename(columns={'adj_close': '区间最低复权价'})
        
        pool_eval = pd.merge(entry_data, min_closes, on='ts_code')
        
        pool_eval['区间最低复权价'] = pd.to_numeric(pool_eval['区间最低复权价'], errors='coerce')
        pool_eval['入池复权价'] = pd.to_numeric(pool_eval['入池复权价'], errors='coerce')
        pool_eval['最大回撤'] = (pool_eval['区间最低复权价'] - pool_eval['入池复权价']) / pool_eval['入池复权价']
        
        survivors = pool_eval[pool_eval['最大回撤'] > -0.12].copy()
        
        if survivors.empty: return [], "当前周期内无股票存活（全部破位）"

        idx_codes = ['000002.SH', '399107.SZ', '399102.SZ', '000688.SH']
        index_returns = {}
        
        for code in idx_codes:
            try:
                df_idx = pro.index_daily(ts_code=code, start_date=t_past, end_date=t_now)
                if not df_idx.empty:
                    df_idx = df_idx.sort_values('trade_date').reset_index(drop=True)
                    c_past = pd.to_numeric(df_idx.iloc[0]['close'], errors='coerce')
                    c_now = pd.to_numeric(df_idx.iloc[-1]['close'], errors='coerce')
                    index_returns[code] = (c_now / c_past - 1) * 100
                else:
                    index_returns[code] = 0.0
            except Exception as idx_e:
                print(f"⚠️ 指数 {code} 获取失败: {idx_e}")
                index_returns[code] = 0.0

        df_now = df_all[df_all['trade_date'] == t_now][['ts_code', 'adj_close', 'amount']]
        df_past = df_all[df_all['trade_date'] == t_past][['ts_code', 'adj_close']]
        
        df_merge = pd.merge(df_now, df_past, on='ts_code', suffixes=('_now', '_past'))
        df_merge = pd.merge(df_merge, survivors, on='ts_code') 
        
        df_merge['adj_close_now'] = pd.to_numeric(df_merge['adj_close_now'], errors='coerce')
        df_merge['adj_close_past'] = pd.to_numeric(df_merge['adj_close_past'], errors='coerce')
        df_merge['个股涨幅(%)'] = (df_merge['adj_close_now'] / df_merge['adj_close_past'] - 1) * 100
        
        def match_benchmark(ts_code):
            if ts_code.startswith('688'): return index_returns.get('000688.SH', 0)
            if ts_code.startswith('60'):  return index_returns.get('000002.SH', 0)
            if ts_code.startswith('30'):  return index_returns.get('399102.SZ', 0)
            if ts_code.startswith('00'):  return index_returns.get('399107.SZ', 0)
            return 0

        def get_benchmark_name(ts_code):
            if ts_code.startswith('688'): return "科创50"
            if ts_code.startswith('60'):  return "上证A指"
            if ts_code.startswith('30'):  return "创业综指"
            if ts_code.startswith('00'):  return "深证A指"
            return "上证A指"

        df_merge['基准涨幅(%)'] = df_merge['ts_code'].apply(match_benchmark)
        df_merge['对标指数'] = df_merge['ts_code'].apply(get_benchmark_name)
        df_merge['偏离值(%)'] = df_merge['个股涨幅(%)'] - df_merge['基准涨幅(%)']
        
        basics = pro.stock_basic(fields='ts_code,name,industry')
        df_final = pd.merge(df_merge, basics, on='ts_code')
        df_final = df_final[df_final['amount'] > 15000] 
        
        df_final['最大回撤'] = pd.to_numeric(df_final['最大回撤'], errors='coerce')
        df_final['最大回撤(%)'] = (df_final['最大回撤'] * 100).round(2)
        
        df_final['入池日'] = pd.to_datetime(df_final['入池日']).dt.strftime('%m-%d')
        
        for col in ['偏离值(%)', '个股涨幅(%)', '基准涨幅(%)', '入池日收盘价']:
            df_final[col] = pd.to_numeric(df_final[col], errors='coerce').round(2)
            
        threshold = 100.0 if days == 10 else 200.0
        def get_risk_tag(dev):
            if dev >= threshold * 0.9: return "💀 极限高危"
            elif dev >= threshold * 0.8: return "🔥 随时关门"
            elif dev >= threshold * 0.6: return "⚠️ 注意兑现"
            elif dev >= threshold * 0.35: return "🟢 安全主升"
            else: return "⚪ 底部起势"
            
        df_final['状态评估'] = df_final['偏离值(%)'].apply(get_risk_tag)
        df_final = df_final.sort_values(by='偏离值(%)', ascending=False)
        
        return df_final.to_dict('records'), f"从 {t_past} 追踪至 {t_now}"
        
    except Exception as e:
        print(f"Error in {days} days: {e}")
        return [], f"计算异常: {e}"

if __name__ == "__main__":
    if not MY_TOKEN:
        print("❌ 未配置 Token，脚本退出。")
        exit()
        
    os.makedirs("data", exist_ok=True)
    print("🚀 开始计算 10日/30日 异动雷达数据...")
    
    data_10d, msg_10d = process_radar(10)
    print("✅ 10日数据计算完成")
    
    data_30d, msg_30d = process_radar(30)
    print("✅ 30日数据计算完成")
    
    final_data = {
        "update_time": (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S'),
        "10d": {"data": data_10d, "msg": msg_10d},
        "30d": {"data": data_30d, "msg": msg_30d}
    }
    
    with open("data/radar_data.json", 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
        
    print("💾 异动雷达数据已保存至 data/radar_data.json")
