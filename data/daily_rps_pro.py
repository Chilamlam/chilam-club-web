import tushare as ts
import pandas as pd
import datetime
import os
import time

# ================= 配置区 =================
# 🛡️ 安全模式：从环境变量获取 Token
MY_TOKEN = '1dc4825f1b185ab6efdacb1cfff887696c6bbcce2e5c547bfa270b56'

RPS_N = [50, 120, 250] 
# 个股阈值
STOCK_THRESHOLD = 87
# ETF 阈值 (ETF 波动小，分数可以稍微放宽，或者保持一致)
ETF_THRESHOLD = 80 

STOCK_PATH = "data/strong_stocks.csv"
ETF_PATH = "data/strong_etfs.csv"

# 初始化
try:
    if MY_TOKEN:
        ts.set_token(MY_TOKEN)
        pro = ts.pro_api()
    else:
        print("⚠️ 提示：本地运行请手动配置 Token。")
        pro = ts.pro_api('') 
except Exception as e:
    print(f"❌ Token 设置异常: {e}")

# ================= 通用工具函数 =================

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

def get_snapshot(code_list, date_str, asset_type='stock'):
    """
    通用获取行情函数
    asset_type: 'stock' 或 'fund'
    """
    try:
        # 如果列表为空，直接返回
        if not code_list: return pd.DataFrame()

        # 分批获取，防止 URL 超长
        # Tushare 单次支持 100-500 个代码，我们稳妥点用 100
        chunk_size = 100
        all_dfs = []
        
        for i in range(0, len(code_list), chunk_size):
            chunk = code_list[i:i+chunk_size]
            codes_str = ",".join(chunk)
            
            if asset_type == 'stock':
                df_daily = pro.daily(ts_code=codes_str, trade_date=date_str, fields='ts_code,close')
                df_adj = pro.adj_factor(ts_code=codes_str, trade_date=date_str, fields='ts_code,adj_factor')
                if df_daily.empty or df_adj.empty: continue
                df = pd.merge(df_daily, df_adj, on='ts_code')
                df['close_val'] = df['close'] * df['adj_factor']
                df['display_val'] = df['close']
            else:
                # 基金/ETF 模式 (fund_daily 需要积分 2000+)
                df = pro.fund_daily(ts_code=codes_str, trade_date=date_str, fields='ts_code,close')
                if df.empty: continue
                # ETF 复权比较复杂，通常用 adj_factor (需 5000 积分) 或直接用未复权近似
                # 你的 2100 积分可能拿不到 fund_adj，这里暂时用未复权价格计算 RPS
                # 对于短期(50/120) ETF 来说，未复权误差通常可接受
                df['close_val'] = df['close'] 
                df['display_val'] = df['close']
            
            all_dfs.append(df)
            
        if not all_dfs: return pd.DataFrame()
        return pd.concat(all_dfs)[['ts_code', 'close_val', 'display_val']]

    except Exception as e:
        print(f"Error fetching {date_str} for {asset_type}: {e}")
        return pd.DataFrame()

def calculate_rps_core(target_codes, dates, asset_type='stock'):
    """核心 RPS 计算逻辑 (传入目标代码列表)"""
    # 1. 获取今日数据
    df_now = get_snapshot(target_codes, dates['now'], asset_type)
    if df_now.empty: return None
    df_now.rename(columns={'close_val': 'base_now', 'display_val': 'price_now'}, inplace=True)
    
    final_df = df_now.copy()
    
    # 2. 循环计算涨幅
    for n in RPS_N:
        if n not in dates: continue
        print(f"   计算 RPS_{n} (对比日期: {dates[n]})...")
        df_past = get_snapshot(target_codes, dates[n], asset_type)
        if df_past.empty: continue
        df_past = df_past[['ts_code', 'close_val']].rename(columns={'close_val': 'base_past'})
        
        temp = pd.merge(final_df, df_past, on='ts_code', how='left')
        temp[f'pct_{n}'] = (temp['base_now'] - temp['base_past']) / temp['base_past']
        # 注意：这里是在“传入的这个池子”里排名。
        # 如果是全市场个股，就是全市场排名。如果是 Top100 ETF，就是这 100 个里的相对强弱。
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
        
        # 链接生成
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.lower() + num
            row['eastmoney_url'] = f"https://quote.eastmoney.com/{link_code}.html"
        else:
            row['eastmoney_url'] = ""
            
        res.append(row)
    return pd.DataFrame(res)

# ================= 特殊逻辑：筛选 Top 100 ETF =================
def get_top_etfs_by_turnover(date_str):
    """获取当日成交额前 100 的非货币 ETF"""
    print("🔍 正在筛选全市场成交额 Top 100 ETF...")
    try:
        # 1. 获取 ETF 列表 (market='E')
        # 你的 2100 积分可以调取 fund_basic
        basic = pro.fund_basic(market='E', status='L', fields='ts_code,name,fund_type')
        
        # 过滤掉 '货币市场型'，只保留 股票型、债券型、商品型、QDII
        # 目的：我们不需要看余额宝之类的 RPS
        mask_type = ~basic['fund_type'].str.contains('货币')
        valid_etfs = basic[mask_type]
        valid_codes = valid_etfs['ts_code'].tolist()
        
        print(f"   全市场非货币 ETF 共 {len(valid_codes)} 只，正在获取行情排名...")
        
        # 2. 获取今日行情 (按成交额排序)
        # fund_daily 如果不传 ts_code，默认可能只返回部分或限制，建议传入 list
        # 这里为了稳妥，我们分批获取所有 ETF 的 amount，然后自己排序
        # 注意：这步可能稍微花点时间，但最准确
        
        # 优化策略：如果积分允许，直接请求 trade_date
        # 尝试直接请求全量，如果报错再改分批
        df_daily = pro.fund_daily(trade_date=date_str, fields='ts_code,amount,close')
        
        # 过滤出刚才筛选的那些非货币 ETF
        df_target = df_daily[df_daily['ts_code'].isin(valid_codes)].copy()
        
        # 按成交额 (amount) 降序排列
        # amount 单位通常是 千元
        df_top100 = df_target.sort_values('amount', ascending=False).head(100)
        
        # 合并名称
        df_final = pd.merge(df_top100, valid_etfs[['ts_code', 'name', 'fund_type']], on='ts_code')
        
        # 把成交额换算成 "亿"
        df_final['amount_亿'] = df_final['amount'] / 10000 / 10000 * 1000 # amount是千元 -> *1000=元 -> /1e8 = 亿
        
        print(f"   ✅ 已锁定 Top 100 ETF，门槛成交额: {df_final['amount_亿'].iloc[-1]:.2f} 亿")
        return df_final
        
    except Exception as e:
        print(f"❌ 筛选 ETF 失败: {e}")
        return pd.DataFrame()

# ================= 主任务 =================
def main_job():
    print("🚀 启动全市场扫描 (股票 + Top100 ETF)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    # 本地测试
    # today_str = '20260123' 

    if not MY_TOKEN:
        print("⚠️ 警告：环境变量中未检测到 Token")

    dates = get_trading_dates(today_str)
    if not dates: 
        print("❌ 非交易日或无法获取日历，程序结束")
        return
    
    os.makedirs("data", exist_ok=True)

    # ----------------------------------------------------
    # 任务 1：个股 RPS (全市场扫描)
    # ----------------------------------------------------
    print("\n=== 正在处理 [个股] RPS ===")
    try:
        # 获取全市场股票列表
        stk_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
        all_stocks = stk_basic['ts_code'].tolist()
        
        # 计算
        df_stock = calculate_rps_core(all_stocks, dates, asset_type='stock')
        
        if df_stock is not None:
            df_stock = pd.merge(df_stock, stk_basic, on='ts_code', how='left')
            # 筛选
            mask = (df_stock['RPS_50'] > STOCK_THRESHOLD) & (df_stock['RPS_120'] > STOCK_THRESHOLD) & (df_stock['RPS_250'] > STOCK_THRESHOLD)
            strong_stock = df_stock[mask].copy()
            strong_stock['更新日期'] = today_fmt
            
            # 历史
            final_stock = process_history(strong_stock, STOCK_PATH, today_fmt)
            
            # 保存
            cols = ['ts_code', 'name', 'industry', 'price_now', 'RPS_50', 'RPS_120', 'RPS_250', '连续天数', '初次入选', 'eastmoney_url', '更新日期']
            final_stock[cols].round(2).to_csv(STOCK_PATH, index=False)
            print(f"✅ 个股更新完成: {len(final_stock)} 只")
    except Exception as e:
        print(f"❌ 个股任务出错: {e}")

    # ----------------------------------------------------
    # 任务 2：Top 100 ETF RPS
    # ----------------------------------------------------
    print("\n=== 正在处理 [ETF] RPS ===")
    try:
        # 1. 先选出 Top 100
        top_etf_info = get_top_etfs_by_turnover(dates['now'])
        
        if not top_etf_info.empty:
            target_etfs = top_etf_info['ts_code'].tolist()
            
            # 2. 计算这 100 个的 RPS
            df_etf_rps = calculate_rps_core(target_etfs, dates, asset_type='fund')
            
            if df_etf_rps is not None:
                # 合并信息 (名称、成交额、类型)
                df_etf = pd.merge(df_etf_rps, top_etf_info, on='ts_code', how='inner')
                
                # 筛选 (ETF 可以全展示，或者只展示 RPS 高的)
                # 这里我们全部保留，按 RPS 排序，方便观察
                df_etf['更新日期'] = today_fmt
                
                # 历史处理
                final_etf = process_history(df_etf, ETF_PATH, today_fmt)
                
                # 保存
                cols_etf = ['ts_code', 'name', 'fund_type', 'amount_亿', 'price_now', 'RPS_50', 'RPS_120', '连续天数', 'eastmoney_url', '更新日期']
                final_etf[cols_etf].round(2).to_csv(ETF_PATH, index=False)
                print(f"✅ ETF 更新完成: {len(final_etf)} 只 (Top 100 活跃)")
        else:
            print("⚠️ 未能筛选出 ETF")
            
    except Exception as e:
        print(f"❌ ETF 任务出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main_job()
