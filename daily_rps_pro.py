import tushare as ts
import pandas as pd
import datetime
import os

# ================= 配置区 =================
LOCAL_TOKEN = '' 
MY_TOKEN = os.getenv('TUSHARE_TOKEN', LOCAL_TOKEN)

RPS_N = [50, 120, 250] 
THRESHOLD = 87
STOCK_PATH = "data/strong_stocks.csv"

# 初始化
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        pro = ts.pro_api('') 
except Exception as e:
    print(f"❌ Token 设置异常: {e}")

# ================= 工具函数 =================

def get_trading_dates(end_date):
    """获取时间锚点"""
    print("📅 正在计算交易日期...")
    start_date = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty: return None
        
        dates = {
            'now': df.loc[0, 'cal_date'], 
            'prev': df.loc[1, 'cal_date'] if len(df) > 1 else None
        }
        for n in RPS_N:
            if len(df) > n:
                dates[n] = df.loc[n, 'cal_date']
        return dates
    except Exception as e:
        print(f"❌ 获取交易日历失败: {e}")
        return None

def get_snapshot(date_str):
    """获取个股行情"""
    print(f"   正在获取 {date_str} 的价格数据...")
    try:
        df_daily = pro.daily(trade_date=date_str, fields='ts_code,close')
        df_adj = pro.adj_factor(trade_date=date_str, fields='ts_code,adj_factor')
        if df_daily.empty or df_adj.empty: return pd.DataFrame()
        df = pd.merge(df_daily, df_adj, on='ts_code')
        df['close_val'] = df['close'] * df['adj_factor'] 
        df['display_val'] = df['close'] 
        return df[['ts_code', 'close_val', 'display_val']]
    except Exception as e:
        print(f"Error: {e}")
        return pd.DataFrame()

def get_fundamental_smart(date_str, backup_date_str=None):
    """智能基本面获取"""
    print(f"📊 正在尝试获取基本面数据...")
    fields = 'ts_code,turnover_rate,pe_ttm,pb,circ_mv'
    df = pro.daily_basic(trade_date=date_str, fields=fields)
    
    if df.empty and backup_date_str:
        print(f"   ⚠️ 今日({date_str})无数据，切换至昨日({backup_date_str})...")
        df = pro.daily_basic(trade_date=backup_date_str, fields=fields)
        
    if df.empty: return pd.DataFrame()
    
    df['mv_亿'] = (df['circ_mv'] / 10000).round(2)
    return df[['ts_code', 'pe_ttm', 'pb', 'turnover_rate', 'mv_亿']]

def calculate_rps_logic(dates):
    """核心 RPS 计算"""
    df_now = get_snapshot(dates['now'])
    if df_now.empty: return None
    df_now.rename(columns={'close_val': 'base_now', 'display_val': 'price_now'}, inplace=True)
    
    final_df = df_now.copy()
    for n in RPS_N:
        if n not in dates: continue
        df_past = get_snapshot(dates[n])
        if df_past.empty: continue
        df_past = df_past[['ts_code', 'close_val']].rename(columns={'close_val': 'base_past'})
        temp = pd.merge(final_df, df_past, on='ts_code', how='left')
        temp[f'pct_{n}'] = (temp['base_now'] - temp['base_past']) / temp['base_past']
        temp[f'RPS_{n}'] = temp[f'pct_{n}'].rank(pct=True) * 100
        final_df = temp.drop(columns=['base_past'])
    return final_df

def process_history_and_change(new_df, file_path, date_str):
    """
    处理历史连板天数 + ★ 计算RPS变化值
    """
    history_map = {}
    rps_prev_map = {} # 用来存昨天的 RPS
    
    # 1. 读取旧文件
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            for _, row in old_df.iterrows():
                # 记录连板历史
                history_map[row['ts_code']] = {
                    'first': row.get('初次入选', date_str),
                    'days': row.get('连续天数', 0),
                    'last_update': row.get('更新日期', '')
                }
                # 记录昨天的 RPS_50
                if 'RPS_50' in row:
                    rps_prev_map[row['ts_code']] = row['RPS_50']
        except: pass

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        first_date = date_str
        days_count = 1
        
        # 处理连板天数
        if code in history_map:
            hist = history_map[code]
            if hist['last_update'] == date_str: # 避免同一天重复跑
                days_count = hist['days']
                first_date = hist['first']
            else:
                days_count = hist['days'] + 1
                first_date = hist['first']
        
        row['初次入选'] = first_date
        row['连续天数'] = days_count
        
        # ★ 计算 RPS 50 变化
        if code in rps_prev_map:
            # 变化值 = 今天 - 昨天
            change = row['RPS_50'] - rps_prev_map[code]
            row['rps_50_chg'] = change 
        else:
            # 如果昨天不在榜单里，说明是新晋级的，变化值设为空或特殊标记
            row['rps_50_chg'] = 999 # 用 999 标记为 NEW
            
        # ... (前面的代码保持不变)

        # ★ 修改部分：将链接改为雪球 (Xueqiu)
        if '.' in code:
            num, suffix = code.split('.')
            # 雪球的格式是：大写市场代码 + 数字，例如 SZ000001
            link_code = suffix.upper() + num 
            row['xueqiu_url'] = f"https://xueqiu.com/S/{link_code}"
        else:
            row['xueqiu_url'] = ""
            
        res.append(row)

# ... (后面的代码保持不变)
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动 A股 RPS + 变化监测 (V4)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    dates = get_trading_dates(today_str)
    if not dates: return
    
    os.makedirs("data", exist_ok=True)

    # 1. 计算
    df_stock = calculate_rps_logic(dates)
    
    if df_stock is not None:
        try:
            print("   正在合并基础信息...")
            basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
            df_stock = pd.merge(df_stock, basic, on='ts_code', how='left')
            
            fina_df = get_fundamental_smart(dates['now'], dates.get('prev'))
            if not fina_df.empty:
                df_stock = pd.merge(df_stock, fina_df, on='ts_code', how='left')
            
            # 2. 筛选
            mask = (df_stock['RPS_50'] > THRESHOLD) & (df_stock['RPS_120'] > THRESHOLD) & (df_stock['RPS_250'] > THRESHOLD)
            strong_stock = df_stock[mask].copy()
            strong_stock['更新日期'] = today_fmt
            
            # 3. ★ 处理历史和变化 (传入旧文件路径)
            final_stock = process_history_and_change(strong_stock, STOCK_PATH, today_fmt)
            
            # 4. 保存 (注意把 eastmoney_url 改成了 xueqiu_url)
            base_cols = ['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'rps_50_chg', 'RPS_120', 'RPS_250', '连续天数']
            extra_cols = ['pe_ttm', 'mv_亿', 'turnover_rate', 'xueqiu_url', '更新日期']
            
            save_cols = [c for c in base_cols + extra_cols if c in final_stock.columns]
            
            final_stock[save_cols].round(2).to_csv(STOCK_PATH, index=False)
            print(f"✅ 成功！已更新，包含 RPS 变化数据")
            
        except Exception as e:
            print(f"❌ 处理出错: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️ 未获取到行情数据")

if __name__ == "__main__":
    main_job()

