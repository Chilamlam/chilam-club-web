import tushare as ts
import pandas as pd
import datetime
import os

# ================= 配置区 =================
# 原来的代码是这样的（安全模式）：
MY_TOKEN = os.getenv('TUSHARE_TOKEN')

# 👇 请临时改成这样（填入你的真实 Token，记得加引号）：
#MY_TOKEN = ''

RPS_N = [50, 120, 250] 
THRESHOLD = 87
STOCK_PATH = "data/strong_stocks.csv"

# 初始化
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        print("⚠️ 提示：本地运行请手动配置 Token，或忽略此提示。")
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
    """获取个股行情"""
    print(f"   正在获取 {date_str} 的数据...")
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

def calculate_rps_logic(dates):
    """核心 RPS 计算逻辑"""
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

def process_history(new_df, file_path, date_str):
    """
    处理连续上榜历史 (带防重复逻辑)
    """
    history_map = {}
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            # 建立历史索引：code -> {初次入选, 连续天数, 上次更新日期}
            for _, row in old_df.iterrows():
                history_map[row['ts_code']] = {
                    'first': row.get('初次入选', date_str),
                    'days': row.get('连续天数', 0),
                    'last_update': row.get('更新日期', '') # 读取旧数据的日期
                }
        except: pass

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        
        # 默认值
        first_date = date_str
        days_count = 1
        
        if code in history_map:
            # 取出历史记录
            hist = history_map[code]
            first_date = hist['first']
            prev_days = hist['days']
            last_update = hist['last_update']
            
            # ★★★ 关键修正逻辑 ★★★
            if last_update == date_str:
                # 如果旧数据的日期就是今天，说明是当天重复运行
                # 保持天数不变，不增加
                days_count = prev_days
            else:
                # 如果是新的一天，天数 +1
                days_count = prev_days + 1
        
        row['初次入选'] = first_date
        row['连续天数'] = days_count
        
        # 链接生成
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.lower() + num
            row['eastmoney_url'] = f"https://quote.eastmoney.com/{link_code}.html"
        else:
            row['eastmoney_url'] = ""
            
        res.append(row)
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动个股 RPS 扫描 (智能计数版)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 本地测试时，如果要模拟昨天的数据，可以在这里改
    # today_str = '20260124' 

    if not MY_TOKEN:
        print("⚠️ 警告：环境变量中未检测到 Token (GitHub Actions 需配置)")
        # 仅本地调试用，上传前请确保这里是空或注掉
        # global pro
        # ts.set_token('你的本地Token')
        # pro = ts.pro_api()

    dates = get_trading_dates(today_str)
    if not dates: 
        print("❌ 非交易日或无法获取日历，程序结束")
        return
    
    os.makedirs("data", exist_ok=True)

    df_stock = calculate_rps_logic(dates)
    if df_stock is not None:
        try:
            basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
            df_stock = pd.merge(df_stock, basic, on='ts_code', how='left')
            
            mask = (df_stock['RPS_50'] > THRESHOLD) & (df_stock['RPS_120'] > THRESHOLD) & (df_stock['RPS_250'] > THRESHOLD)
            strong_stock = df_stock[mask].copy()
            strong_stock['更新日期'] = today_fmt
            
            final_stock = process_history(strong_stock, STOCK_PATH, today_fmt)
            
            cols = ['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', '初次入选', 'eastmoney_url', '更新日期']
            final_stock[cols].round(2).to_csv(STOCK_PATH, index=False)
            print(f"✅ 成功！已更新 {len(final_stock)} 只强势股 (智能去重)")
        except Exception as e:
            print(f"❌ 处理出错: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️ 未获取到行情数据")

if __name__ == "__main__":
    main_job()
