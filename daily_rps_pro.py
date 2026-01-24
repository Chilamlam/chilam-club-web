import tushare as ts
import pandas as pd
import datetime
import os

# ================= 配置区 =================
# 🛡️ 安全模式：从环境变量获取 Token
MY_TOKEN = os.getenv('TUSHARE_TOKEN')

RPS_N = [50, 120, 250] 
THRESHOLD = 87
STOCK_PATH = "data/strong_stocks.csv"

# 初始化
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        print("⚠️ 提示：本地运行请手动配置 Token")
        pro = ts.pro_api('') 
except Exception as e:
    print(f"❌ Token 设置异常: {e}")

def get_trading_dates(end_date):
    """获取时间锚点"""
    print("📅 正在计算交易日期...")
    start_date = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty: return None
        dates = {'now': df.loc[0, 'cal_date']}
        for n in RPS_N:
            if len(df) > n:
                dates[n] = df.loc[n, 'cal_date']
        return dates
    except Exception as e:
        print(f"❌ 获取交易日历失败: {e}")
        return None

def get_snapshot(date_str):
    """获取个股行情（价格）"""
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

def get_fundamental_data(date_str):
    """
    ★ 新增功能：获取基本面指标 (2100积分专属)
    包含：市盈率(TTM)、市净率、换手率、流通市值
    """
    print(f"📊 正在获取 {date_str} 的基本面数据 (PE/PB/市值)...")
    try:
        # daily_basic 接口需要 2000 积分
        df = pro.daily_basic(trade_date=date_str, 
                             fields='ts_code,turnover_rate,pe_ttm,pb,circ_mv')
        if df.empty:
            print("⚠️ 未获取到基本面数据 (可能是非交易日或权限不足)")
            return pd.DataFrame()
        
        # circ_mv 单位是万，转换为亿，保留2位小数
        df['mv_亿'] = (df['circ_mv'] / 10000).round(2)
        
        # 处理一下 PE，负值通常没意义或亏损
        return df[['ts_code', 'pe_ttm', 'pb', 'turnover_rate', 'mv_亿']]
    except Exception as e:
        print(f"❌ 基本面数据获取失败: {e}")
        return pd.DataFrame()

def calculate_rps_logic(dates):
    """核心 RPS 计算逻辑"""
    # 1. 获取今日数据
    df_now = get_snapshot(dates['now'])
    if df_now.empty: return None
    df_now.rename(columns={'close_val': 'base_now', 'display_val': 'price_now'}, inplace=True)
    
    # 2. 循环计算涨幅
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

def process_history(new_df, file_path, date_str):
    """处理连续上榜历史"""
    history_map = {}
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            for _, row in old_df.iterrows():
                history_map[row['ts_code']] = {
                    'first': row.get('初次入选', date_str),
                    'days': row.get('连续天数', 0),
                    'last_update': row.get('更新日期', '')
                }
        except: pass

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        first_date = date_str
        days_count = 1
        
        if code in history_map:
            hist = history_map[code]
            if hist['last_update'] == date_str:
                days_count = hist['days']
                first_date = hist['first']
            else:
                days_count = hist['days'] + 1
                first_date = hist['first']
        
        row['初次入选'] = first_date
        row['连续天数'] = days_count
        
        # 链接
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.lower() + num
            row['eastmoney_url'] = f"https://quote.eastmoney.com/{link_code}.html"
        else:
            row['eastmoney_url'] = ""
            
        res.append(row)
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动 A股 RPS + 基本面深度扫描...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    if not MY_TOKEN:
        print("⚠️ 警告：环境变量中未检测到 Token")

    dates = get_trading_dates(today_str)
    if not dates: 
        print("❌ 非交易日或无法获取日历，程序结束")
        return
    
    os.makedirs("data", exist_ok=True)

    # 1. 计算 RPS
    df_stock = calculate_rps_logic(dates)
    
    if df_stock is not None:
        try:
            # 2. 获取基础信息 (名称、行业)
            basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
            df_stock = pd.merge(df_stock, basic, on='ts_code', how='left')
            
            # 3. ★ 获取基本面数据 (PE/PB/市值/换手) 并合并
            fina_df = get_fundamental_data(dates['now'])
            if not fina_df.empty:
                df_stock = pd.merge(df_stock, fina_df, on='ts_code', how='left')
            
            # 4. 筛选强势股
            mask = (df_stock['RPS_50'] > THRESHOLD) & (df_stock['RPS_120'] > THRESHOLD) & (df_stock['RPS_250'] > THRESHOLD)
            strong_stock = df_stock[mask].copy()
            strong_stock['更新日期'] = today_fmt
            
            # 5. 处理历史
            final_stock = process_history(strong_stock, STOCK_PATH, today_fmt)
            
            # 6. 保存 (新增了基本面列)
            # 确保列存在 (防止 fundamental 获取失败报错)
            base_cols = ['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', '连续天数']
            extra_cols = ['pe_ttm', 'mv_亿', 'turnover_rate', 'eastmoney_url', '更新日期']
            
            # 动态检查哪些列存在
            save_cols = [c for c in base_cols + extra_cols if c in final_stock.columns]
            
            final_stock[save_cols].round(2).to_csv(STOCK_PATH, index=False)
            print(f"✅ 成功！已更新 {len(final_stock)} 只强势股 (含基本面数据)")
            
        except Exception as e:
            print(f"❌ 处理出错: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️ 未获取到行情数据")

if __name__ == "__main__":
    main_job()
