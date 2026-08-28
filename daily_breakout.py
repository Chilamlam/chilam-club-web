"""
阶段新高突破池扫描引擎 (欧奈尔 Stage 2 突破动量策略)
功能：
1. 扫描创 60日 / 120日 / 250日 (1年) 新高标的
2. 过滤成交量放量突破 (当日成交量 > 5日均量 1.5倍)
3. 关联行业、市值、PE(TTM)、换手率及雪球链接
4. 输出到 data/breakout_stocks.csv
"""
import os
import datetime
import pandas as pd
import tushare as ts
import warnings

warnings.filterwarnings("ignore")

MY_TOKEN = os.getenv('TUSHARE_TOKEN', '')
OUTPUT_PATH = "data/breakout_stocks.csv"

if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

def get_latest_trade_date(max_days=10):
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    for i in range(max_days):
        date_obj = beijing_time - datetime.timedelta(days=i)
        date_str = date_obj.strftime('%Y%m%d')
        try:
            df = pro.daily(trade_date=date_str)
            if not df.empty:
                return date_str, date_obj.strftime('%Y-%m-%d')
        except Exception:
            pass
    return None, None

def run_breakout_scan():
    print("🚀 正在启动【阶段新高突破池】量化扫描...")
    trade_date, trade_date_fmt = get_latest_trade_date()
    if not trade_date:
        print("❌ 未获取到有效交易日，扫描终止")
        return

    print(f"📅 目标交易日: {trade_date}")
    
    try:
        # 1. 获取最近 260 个交易日日历
        beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        start_date = (beijing_time - datetime.timedelta(days=450)).strftime('%Y%m%d')
        cal_df = pro.trade_cal(exchange='', is_open='1', end_date=trade_date, start_date=start_date)
        cal_df = cal_df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        
        if len(cal_df) < 60:
            print("❌ 日历数据不足，无法计算新高")
            return
            
        d_60 = cal_df.loc[min(60, len(cal_df)-1), 'cal_date']
        d_120 = cal_df.loc[min(120, len(cal_df)-1), 'cal_date']
        d_250 = cal_df.loc[min(250, len(cal_df)-1), 'cal_date']
        
        # 2. 抓取当日全市场行情 + 基本面
        df_today = pro.daily(trade_date=trade_date)
        df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
        df_daily_basic = pro.daily_basic(trade_date=trade_date, fields='ts_code,turnover_rate,pe_ttm,circ_mv')
        
        if df_today.empty:
            print("❌ 今日行情为空")
            return
            
        df_merge = pd.merge(df_today, df_basic, on='ts_code', how='inner')
        df_merge = pd.merge(df_merge, df_daily_basic, on='ts_code', how='left')
        
        # 过滤次新与退市股
        df_merge = df_merge[~df_merge['name'].str.contains('ST|退', na=False)]
        
        # 3. 抓取区间最高价进行比对
        print("🔍 正在计算各周期最高价及放量比率...")
        
        # 抓取 60日/120日/250日 历史最高价
        breakout_records = []
        
        # 为了高效计算，针对涨幅 > 2% 的活跃股做深度历史比对
        active_candidates = df_merge[df_merge['pct_chg'] > 1.5].copy()
        print(f"📊 初筛涨幅 > 1.5% 标的共 {len(active_candidates)} 只，开始回测新高...")
        
        for _, row in active_candidates.iterrows():
            code = row['ts_code']
            today_close = row['close']
            today_high = row['high']
            today_vol = row['vol']
            
            try:
                hist_df = pro.daily(ts_code=code, start_date=d_250, end_date=trade_date)
                if len(hist_df) < 30:
                    continue
                    
                hist_sorted = hist_df.sort_values('trade_date', ascending=False).reset_index(drop=True)
                
                # 剔除当天之后的历史
                prev_hist = hist_sorted.iloc[1:]
                if prev_hist.empty: continue
                
                # 5日均量
                v5 = prev_hist.head(5)['vol'].mean()
                vol_ratio = round(today_vol / v5, 2) if v5 > 0 else 1.0
                
                # 60日最高收盘价与最高价
                p60 = prev_hist.head(60)
                max_60 = p60['close'].max()
                
                # 120日最高收盘价
                p120 = prev_hist.head(120)
                max_120 = p120['close'].max()
                
                # 250日最高收盘价 (年线新高)
                max_250 = prev_hist['close'].max()
                
                is_60 = today_close >= max_60
                is_120 = today_close >= max_120
                is_250 = today_close >= max_250
                
                if is_60 or is_120 or is_250:
                    # 确定突破等级
                    if is_250:
                        level = "🏆 创一年(250日)新高"
                    elif is_120:
                        level = "🔥 创半年(120日)新高"
                    else:
                        level = "⚡ 创60日新高"
                        
                    # 雪球链接
                    num, suffix = code.split('.')
                    link_code = suffix.upper() + num
                    xueqiu_url = f"https://xueqiu.com/S/{link_code}"
                    
                    circ_mv_yi = round(row['circ_mv'] / 10000, 2) if pd.notna(row.get('circ_mv')) else 0
                    
                    breakout_records.append({
                        "ts_code": code,
                        "name": row['name'],
                        "industry": row['industry'],
                        "level": level,
                        "close": today_close,
                        "pct_chg": row['pct_chg'],
                        "vol_ratio": vol_ratio,
                        "turnover_rate": round(row.get('turnover_rate', 0), 2),
                        "mv_亿": circ_mv_yi,
                        "pe_ttm": round(row.get('pe_ttm', 0), 1) if pd.notna(row.get('pe_ttm')) else "-",
                        "xueqiu_url": xueqiu_url,
                        "update_date": trade_date_fmt
                    })
            except Exception:
                continue
                
        if breakout_records:
            df_out = pd.DataFrame(breakout_records)
            df_out = df_out.sort_values(by=['pct_chg', 'vol_ratio'], ascending=[False, False])
            os.makedirs("data", exist_ok=True)
            df_out.to_csv(OUTPUT_PATH, index=False)
            print(f"✅ 突破池计算完成！共捕获 {len(df_out)} 只突破标的，已保存至 {OUTPUT_PATH}")
        else:
            print("⚠️ 今日无满足条件的突破标的")
            
    except Exception as e:
        print(f"❌ 突破池计算异常: {e}")

if __name__ == "__main__":
    run_breakout_scan()
