import tushare as ts
import pandas as pd
import datetime
import os

# ================= 配置区 =================
# 🛡️ 安全模式：从环境变量获取 Token
# 这样别人看你的代码也看不到你的密钥，而在 GitHub 上运行时能自动读到 Secrets
MY_TOKEN = os.getenv('TUSHARE_TOKEN')

RPS_N = [50, 120, 250] 
THRESHOLD = 87
STOCK_PATH = "data/strong_stocks.csv"

# 初始化 Tushare
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        print("⚠️ 提示：未检测到 TUSHARE_TOKEN 环境变量。")
        print("如果是本地运行，请手动填入 Token；如果是上传 GitHub，请忽略此提示。")
        pro = ts.pro_api('') # 避免直接报错，允许程序往下走一步打印错误
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
        df['close_val'] = df['close'] * df['adj_factor'] # 计算用
        df['display_val'] = df['close'] # 展示用
        return df[['ts_code', 'close_val', 'display_val']]
    except Exception as e:
        print(f"Error: {e}")
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
                    'days': row.get('连续天数', 0)
                }
        except: pass

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        if code in history_map:
            row['初次入选'] = history_map[code]['first']
            row['连续天数'] = history_map[code]['days'] + 1
        else:
            row['初次入选'] = date_str
            row['连续天数'] = 1
        
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
    print("🚀 启动个股 RPS 扫描 (GitHub Actions 版)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 检查 Token 是否存在
    if not MY_TOKEN:
        print("❌ 错误：缺少 Token。请确保 GitHub Secrets 中配置了 TUSHARE_TOKEN。")
        return

    dates = get_trading_dates(today_str)
    if not dates: 
        print("❌ 非交易日或无法获取日历，程序结束")
        return
    
    os.makedirs("data", exist_ok=True)

    # === 计算任务 ===
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
            print(f"✅ 成功！已筛选出 {len(final_stock)} 只强势股，保存至 {STOCK_PATH}")
        except Exception as e:
            print(f"❌ 处理出错: {e}")
    else:
        print("⚠️ 未获取到行情数据")

if __name__ == "__main__":
    main_job()
