import tushare as ts
import pandas as pd
import datetime
import os
import time
import akshare as ak
import concurrent.futures

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
    print("📅 [个股] 正在获取交易日历...")
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
        print(f"❌ 获取日历失败: {e}")
        return None

def get_snapshot(date_str):
    print(f"   正在获取 {date_str} 的行情...")
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
    print(f"📊 正在获取基本面数据...")
    fields = 'ts_code,turnover_rate,pe_ttm,pb,circ_mv'
    df = pro.daily_basic(trade_date=date_str, fields=fields)
    
    if df.empty and backup_date_str:
        print(f"   ⚠️ {date_str} 数据未出，切换至昨日 {backup_date_str}...")
        df = pro.daily_basic(trade_date=backup_date_str, fields=fields)
        
    if df.empty: return pd.DataFrame()
    
    df['mv_亿'] = (df['circ_mv'] / 10000).round(2)
    return df[['ts_code', 'pe_ttm', 'pb', 'turnover_rate', 'mv_亿']]

def calculate_rps_logic(dates):
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

# ================= 行业获取 =================

def get_industry_worker(code):
    try:
        symbol = code.split('.')[0] 
        df = ak.stock_individual_info_em(symbol=symbol)
        row = df[df['item'] == '行业']
        if not row.empty:
            return code, row['value'].values[0]
    except:
        pass
    return code, "-"

def fetch_detailed_industries(ts_codes):
    total = len(ts_codes)
    print(f"🏭 [Akshare] 启动多线程加速，正在抓取 {total} 只个股的细分题材...")
    
    industry_map = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_code = {executor.submit(get_industry_worker, code): code for code in ts_codes}
        
        count = 0
        for future in concurrent.futures.as_completed(future_to_code):
            code, industry = future.result()
            industry_map[code] = industry
            count += 1
            if count % 50 == 0: print(f"   🚀 进度: {count}/{total}...")
                
    return industry_map

def process_history_and_change(new_df, file_path, date_str):
    """
    处理历史逻辑 (智能防覆盖版)
    """
    history_map = {}
    
    # 两个 Map 用于处理不同的情况
    yesterday_rps_map = {}  # 存昨天的 RPS (正常情况)
    today_change_map = {}   # 存今天已经算好的 Change (防止重跑变0)
    
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            # 确保日期列是字符串，防止格式问题
            old_df['更新日期'] = old_df['更新日期'].astype(str)
            
            for _, row in old_df.iterrows():
                code = row['ts_code']
                last_update = row.get('更新日期', '')
                
                # 1. 连板历史
                history_map[code] = {
                    'first': row.get('初次入选', date_str),
                    'days': row.get('连续天数', 0),
                    'last_update': last_update
                }

                # 2. 变动值逻辑 (关键修复)
                if last_update == date_str:
                    # 如果文件里的日期已经是今天，说明今天跑过了
                    # 我们要保留当时算出来的 change，而不是拿现在的 RPS 减去文件里的 RPS (那是 0)
                    if 'rps_50_chg' in row:
                        today_change_map[code] = row['rps_50_chg']
                else:
                    # 如果文件里是昨天的，正常记录昨天的 RPS
                    if 'RPS_50' in row:
                        yesterday_rps_map[code] = row['RPS_50']
                        
        except Exception as e:
            print(f"⚠️ 读取历史文件微瑕: {e}")

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        first_date = date_str
        days_count = 1
        
        # --- 连板天数逻辑 ---
        if code in history_map:
            hist = history_map[code]
            if hist['last_update'] == date_str:
                # 同一天重跑，天数保持不变
                days_count = hist['days']
                first_date = hist['first']
            else:
                # 新的一天，+1
                days_count = hist['days'] + 1
                first_date = hist['first']
        
        row['初次入选'] = first_date
        row['连续天数'] = days_count
        
        # --- ★★★ 变动值计算 (智能继承) ★★★ ---
        # 优先级 1: 如果今天已经算过变动值了，直接沿用 (防止变成 0)
        if code in today_change_map:
            row['rps_50_chg'] = today_change_map[code]
            
        # 优先级 2: 如果是第一次跑，就跟昨天比
        elif code in yesterday_rps_map:
            row['rps_50_chg'] = row['RPS_50'] - yesterday_rps_map[code]
            
        # 优先级 3: 既没昨天记录，今天也没跑过 -> 新上榜
        else:
            row['rps_50_chg'] = 999 
            
        # 生成雪球链接
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.upper() + num 
            row['xueqiu_url'] = f"https://xueqiu.com/S/{link_code}"
        else:
            row['xueqiu_url'] = ""
            
        res.append(row)
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动 A股 RPS + 细分题材更新 (V4.0 防覆盖版)...")
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    today_fmt = datetime.datetime.now().strftime('%Y-%m-%d')
    
    dates = get_trading_dates(today_str)
    if not dates: return
    
    os.makedirs("data", exist_ok=True)

    # 1. 计算 RPS
    df_stock = calculate_rps_logic(dates)
    
    if df_stock is not None:
        try:
            print("   合并基础数据...")
            basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
            df_stock = pd.merge(df_stock, basic, on='ts_code', how='left')
            
            fina_df = get_fundamental_smart(dates['now'], dates.get('prev'))
            if not fina_df.empty:
                df_stock = pd.merge(df_stock, fina_df, on='ts_code', how='left')
            
            # 2. 筛选
            mask = (df_stock['RPS_50'] > THRESHOLD) & (df_stock['RPS_120'] > THRESHOLD) & (df_stock['RPS_250'] > THRESHOLD)
            strong_stock = df_stock[mask].copy()
            strong_stock['更新日期'] = today_fmt
            
            # 3. 细分行业获取
            codes_list = strong_stock['ts_code'].tolist()
            if codes_list:
                industry_map = fetch_detailed_industries(codes_list)
                strong_stock['细分行业'] = strong_stock['ts_code'].map(industry_map)
                
                print("🔧 正在修补缺失的题材数据...")
                strong_stock['细分行业'] = strong_stock['细分行业'].fillna('-')
                mask_missing = strong_stock['细分行业'] == '-'
                if 'industry' in strong_stock.columns:
                    strong_stock.loc[mask_missing, '细分行业'] = strong_stock.loc[mask_missing, 'industry']
            else:
                strong_stock['细分行业'] = '-'
            
            # 4. 处理历史
            final_stock = process_history_and_change(strong_stock, STOCK_PATH, today_fmt)
            
            # 5. 保存
            base_cols = ['ts_code', 'name', '细分行业', 'price_now', 'RPS_50', 'rps_50_chg', 'RPS_120', 'RPS_250', '连续天数']
            extra_cols = ['pe_ttm', 'mv_亿', 'turnover_rate', 'xueqiu_url', '更新日期', '初次入选']
            
            save_cols = [c for c in base_cols + extra_cols if c in final_stock.columns]
            
            final_stock[save_cols].round(2).to_csv(STOCK_PATH, index=False)
            print(f"✅ 更新完成！现在可以放心地重复运行了。")
            
        except Exception as e:
            print(f"❌ 处理出错: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⚠️ 未获取到行情数据")

if __name__ == "__main__":
    main_job()
