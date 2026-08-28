import tushare as ts
import pandas as pd
import datetime
import os
import time

# ================= 配置区 =================
# 优先读取环境变量，本地测试时可填写 LOCAL_TOKEN
LOCAL_TOKEN = '' 
MY_TOKEN = os.getenv('TUSHARE_TOKEN', LOCAL_TOKEN)

# RPS 时间窗口
RPS_N = [50, 120, 250] 
# 强势 ETF 阈值 (RPS 50 大于此值才保留)
THRESHOLD = 87
# 结果保存路径
ETF_PATH = "data/strong_etfs.csv"

# 排除关键词：过滤掉债券、货币、理财以及部分跨境ETF，聚焦A股资产
EXCLUDE_WORDS = ['债', '货币', '理财', '黄金', '石油', '标普', '纳指', '道琼斯', '德国', '法国', '日经', '恒生']

# 初始化 Tushare
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        # 尝试匿名初始化 (通常会失败，需配置 Token)
        pro = ts.pro_api('')
except Exception as e:
    print(f"❌ Token 设置异常: {e}")

# ================= 核心逻辑 =================

def get_trading_dates(end_date):
    """获取必要的交易日期锚点 (今天, 昨天, N天前)"""
    print("📅 [ETF] 正在获取交易日历...")
    # 向前多取一些日子以防假期
    start_date = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty: return None
        
        dates = {
            'now': df.loc[0, 'cal_date'], 
            'prev': df.loc[1, 'cal_date'] if len(df) > 1 else None # 昨天 (用于计算变动)
        }
        # 获取 N 天前的日期
        for n in RPS_N:
            if len(df) > n:
                dates[n] = df.loc[n, 'cal_date']
        return dates
    except Exception as e:
        print(f"❌ 获取日历失败: {e}")
        return None

def get_etf_snapshot(date_str):
    """获取某日全市场场内基金行情"""
    print(f"   正在获取 {date_str} 的 ETF 行情...")
    try:
        # Tushare 接口：fund_daily 获取场内基金日线
        df = pro.fund_daily(trade_date=date_str)
        if df.empty: return pd.DataFrame()
        
        # 仅保留代码和收盘价
        return df[['ts_code', 'close']].rename(columns={'close': 'close_val'})
    except Exception as e:
        print(f"Error fetching ETF data: {e}")
        return pd.DataFrame()

def process_etf_history_and_links(new_df, file_path):
    """
    1. 读取旧文件，计算 RPS 50 的变动值
    2. 生成雪球 (Xueqiu) 跳转链接
    """
    rps_prev_map = {}
    
    # --- 1. 读取旧数据 (如果存在) ---
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            for _, row in old_df.iterrows():
                # 记录昨天的 RPS_50
                if 'RPS_50' in row:
                    rps_prev_map[row['ts_code']] = row['RPS_50']
        except Exception as e:
            print(f"⚠️ 读取旧文件失败，跳过对比: {e}")

    # --- 2. 处理新数据 ---
    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        
        # ★ 计算 RPS 变动 (今天 - 昨天)
        if code in rps_prev_map:
            change = row['RPS_50'] - rps_prev_map[code]
            row['rps_50_chg'] = change
        else:
            # 999 代表新上榜 (New)
            row['rps_50_chg'] = 999 
            
        # ★ 生成雪球链接
        # Tushare 格式: 510050.SH -> 雪球格式: SH510050
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.upper() + num 
            row['xueqiu_url'] = f"https://xueqiu.com/S/{link_code}"
        else:
            row['xueqiu_url'] = ""
            
        res.append(row)
        
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动 ETF 策略更新 (V2.0)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 1. 准备日期
    dates = get_trading_dates(today_str)
    if not dates: return
    
    # 确保 data 目录存在
    os.makedirs("data", exist_ok=True)

    # 2. 获取今日行情作为基准
    df_now = get_etf_snapshot(dates['now'])
    if df_now.empty: 
        print("⚠️ 今日无行情数据，停止运行")
        return

    final_df = df_now.copy()
    final_df.rename(columns={'close_val': 'price_now'}, inplace=True)
    # ETF 这里简单处理，暂不复权 (ETF复权数据较难获取，且短期影响小)
    final_df['base_now'] = final_df['price_now']

    # 3. 循环计算 RPS (50, 120, 250)
    for n in RPS_N:
        if n not in dates: continue
        # 获取 N 天前的行情
        df_past = get_etf_snapshot(dates[n])
        if df_past.empty: continue
        
        # 合并数据
        temp = pd.merge(final_df, df_past, on='ts_code', how='left', suffixes=('', '_past'))
        
        # 计算 N 日涨幅
        temp[f'pct_{n}'] = (temp['base_now'] - temp['close_val']) / temp['close_val']
        
        # 计算 RPS (排名)
        # pct=True 表示返回百分比排名 (0.0~1.0)，乘以 100 变成 0~100 分
        temp[f'RPS_{n}'] = temp[f'pct_{n}'].rank(pct=True) * 100
        
        # 清理临时列，保留 final_df
        final_df = temp.drop(columns=['close_val'])

    # 4. 获取 ETF 基础信息 (用于筛选名称)
    try:
        print("   获取 ETF 基础信息并过滤...")
        # market='E' 代表交易所基金
        basic = pro.fund_basic(market='E') 
        basic = basic[['ts_code', 'name']]
        
        # 合并名称
        df_merged = pd.merge(final_df, basic, on='ts_code', how='inner')
        
        # ★ 过滤逻辑：排除不需要的类型
        mask_name = df_merged['name'].apply(lambda x: not any(w in x for w in EXCLUDE_WORDS))
        df_stock_etf = df_merged[mask_name].copy()
        
        # 5. 筛选强势品种
        # 规则：RPS_50 > 87 且 RPS_120 > 80 (确保中期也够强)
        strong_etf = df_stock_etf[
            (df_stock_etf['RPS_50'] > THRESHOLD) & 
            (df_stock_etf['RPS_120'] > 80)
        ].copy()
        
        strong_etf['更新日期'] = today_fmt

        # 6. ★ 处理历史变动和链接 (新功能核心)
        final_etf = process_etf_history_and_links(strong_etf, ETF_PATH)

        # 7. 保存结果
        # 指定列顺序，保持 CSV 整洁
        cols = ['ts_code', 'name', 'price_now', 'RPS_50', 'rps_50_chg', 'RPS_120', 'RPS_250', 'xueqiu_url', '更新日期']
        save_cols = [c for c in cols if c in final_etf.columns]
        
        final_etf[save_cols].round(2).to_csv(ETF_PATH, index=False)
        print(f"✅ ETF 更新成功！共筛选出 {len(final_etf)} 只，文件已保存至 {ETF_PATH}")

    except Exception as e:
        print(f"❌ 处理 ETF 数据出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main_job()
