import tushare as ts
import pandas as pd
import datetime
import os
import time

# ================= 配置区 =================
# 👇👇👇 本地运行时，请务必在这里填入 Token 👇👇👇
LOCAL_TOKEN = '' 

# 优先读取环境变量
MY_TOKEN = os.getenv('TUSHARE_TOKEN', LOCAL_TOKEN)

RPS_N = [50, 120, 250] 
ETF_PATH = "data/strong_etfs.csv"

# 初始化
try:
    if MY_TOKEN and len(MY_TOKEN) > 10:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
        print("✅ Token 配置成功")
    else:
        print("⚠️ 警告：Token 未配置！")
        pro = ts.pro_api('') 
except Exception as e:
    print(f"❌ Token 设置异常: {e}")

# ================= 核心工具函数 =================

def get_trading_dates(end_date):
    """获取交易日历"""
    print("📅 正在获取交易日历...")
    try:
        start_date = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime('%Y%m%d')
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty: return None
        dates = {'now': df.loc[0, 'cal_date']}
        for n in RPS_N:
            if len(df) > n:
                dates[n] = df.loc[n, 'cal_date']
        return dates
    except Exception as e:
        print(f"❌ 获取日历失败: {e}")
        return None

def get_snapshot_by_date(target_codes, date_str):
    """
    获取历史行情 (V4 暴力版)
    ★ 策略：直接拉取该日期【全市场】所有基金的行情，然后在本地过滤。
    ★ 优势：避开了 Tushare 对 ts_code 列表长度的限制，极度稳定。
    """
    print(f"   -> 正在拉取 {date_str} 全市场基金行情...")
    
    try:
        # 不传 ts_code，直接拿全量 (2100积分支持此操作)
        df = pro.fund_daily(trade_date=date_str, fields='ts_code,close')
        
        if df.empty:
            print(f"      ⚠️ Tushare 返回空数据 (可能是非交易日或权限波动)")
            return pd.DataFrame()
            
        # 本地过滤：只保留我们要的那 100 个
        # 这一步在本地做，速度极快
        df_target = df[df['ts_code'].isin(target_codes)].copy()
        
        if df_target.empty:
            print(f"      ⚠️ 数据拉取成功但未匹配到目标 ETF (异常情况)")
            return pd.DataFrame()
            
        df_target['close_val'] = df_target['close']
        return df_target[['ts_code', 'close_val']]

    except Exception as e:
        print(f"      ⚠️ 获取失败: {e}")
        return pd.DataFrame()

def calculate_rps(top100_df, dates):
    """计算 RPS"""
    print(f"🧮 正在计算 RPS...")
    
    # 1. 准备今日数据
    df_now = top100_df[['ts_code', 'close']].copy()
    df_now.rename(columns={'close': 'base_now'}, inplace=True)
    
    final_df = df_now.copy()
    target_codes = final_df['ts_code'].tolist()
    
    # 2. 回溯历史
    for n in RPS_N:
        if n not in dates: continue
        
        # 这里的 dates[n] 已经是 trade_cal 确认过的交易日，所以直接查
        df_past = get_snapshot_by_date(target_codes, dates[n])
        
        if df_past.empty: 
            print(f"   ⚠️ 依然无法获取 {n} 日前数据，该列将为空")
            continue
            
        df_past = df_past.rename(columns={'close_val': 'base_past'})
        
        # 合并计算
        temp = pd.merge(final_df, df_past, on='ts_code', how='left')
        
        # 避免除以0
        temp['base_past'] = temp['base_past'].replace(0, pd.NA)
        
        temp[f'pct_{n}'] = (temp['base_now'] - temp['base_past']) / temp['base_past']
        temp[f'RPS_{n}'] = temp[f'pct_{n}'].rank(pct=True) * 100
        final_df = temp.drop(columns=['base_past'])
        
        # 休息一下，防止接口频率过快
        time.sleep(0.3)
        
    return final_df

def get_top100_etfs(date_str):
    """筛选 Top 100"""
    print("🔍 正在筛选 Top 100 ETF...")
    try:
        # 1. 获取今日全市场行情
        df_daily = pro.fund_daily(trade_date=date_str, fields='ts_code,amount,close')
        if df_daily.empty:
            print("❌ 今日无行情 (可能今日数据尚未更新或Token限制)")
            return pd.DataFrame()
            
        # 2. 获取基础信息
        df_basic = pro.fund_basic(market='E', status='L', fields='ts_code,name,fund_type')
        
        # 3. 过滤 & 排序
        valid_etfs = df_basic[~df_basic['fund_type'].str.contains('货币')]
        merged = pd.merge(df_daily, valid_etfs, on='ts_code', how='inner')
        
        top100 = merged.sort_values('amount', ascending=False).head(100)
        top100['amount_亿'] = top100['amount'] / 10000 / 10000 * 1000
        
        print(f"✅ 筛选完成！门槛: {top100['amount_亿'].iloc[-1]:.2f} 亿")
        return top100[['ts_code', 'name', 'fund_type', 'amount_亿', 'close']]
        
    except Exception as e:
        print(f"❌ 筛选失败: {e}")
        return pd.DataFrame()

def main_job():
    print("🚀 启动 ETF 专项扫描 (V4 暴力全量版)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 调试用：如果今天是周末，请改成周五
    # today_str = '20260123' 

    dates = get_trading_dates(today_str)
    if not dates: return
    
    os.makedirs("data", exist_ok=True)

    # 1. 拿名单
    top100_df = get_top100_etfs(dates['now'])
    if top100_df.empty: return

    # 2. 算 RPS
    rps_df = calculate_rps(top100_df, dates)
    
    if rps_df is not None:
        # 3. 合并
        final = pd.merge(rps_df, top100_df[['ts_code', 'name', 'fund_type', 'amount_亿']], on='ts_code', how='inner')
        final['更新日期'] = today_fmt
        final['price_now'] = final['base_now']
        final['eastmoney_url'] = final['ts_code'].apply(lambda x: f"https://quote.eastmoney.com/{x.split('.')[1].lower()}{x.split('.')[0]}.html")
        
        # 容错保存
        save_cols = [c for c in ['ts_code', 'name', 'fund_type', 'amount_亿', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', 'eastmoney_url', '更新日期'] if c in final.columns]
        
        final[save_cols].round(3).to_csv(ETF_PATH, index=False)
        print(f"🎉 成功！Top 100 ETF 数据已保存至 {ETF_PATH}")

if __name__ == "__main__":
    main_job()
