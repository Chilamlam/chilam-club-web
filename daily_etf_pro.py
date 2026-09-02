import tushare as ts
import pandas as pd
import datetime
import os
import sys
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

# 锚定「行情已发布的最新交易日」时最多往前回退几个交易日。
# 与 daily_rps_pro.py 保持同一口径：超过就报错，不靠回退掩盖 token 失效。
MAX_ANCHOR_BACK = 3

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
    """锚定「ETF 行情已发布的最新交易日」，并按它往前数出各窗口对照日。

    与 daily_rps_pro.py 同一个坑、同一个修法：交易日历只回答「这天开不开市」，
    不回答「这天的行情发布了没有」。跑批被 GitHub Actions 延迟到过零点时，
    日历首位就是当天，而当天距开盘还有几小时，fund_daily 必然为空。
    原实现直接用 df.loc[0] 当目标日，遇到这种情况整步空转、榜单停在前一天。

    另有一处独立的老 bug：更新日期原先写的是系统日期而不是锚定交易日，
    08-30 00:25 那班就把「2026-08-29（周六）」写进了 strong_etfs.csv，
    让一份周五的数据对外显示成周六的。这里统一返回锚定日，由调用方使用。

    各窗口对照日必须从锚定日往前数（anchor_i + n），不能仍按日历首位数——
    回退一天却按首位取 50 格，实际窗口只有 49 天，口径会悄悄漂移。
    """
    print("📅 [ETF] 正在获取交易日历...")
    # 向前多取一些日子以防假期
    start_date = (datetime.datetime.now() - datetime.timedelta(days=400)).strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty: return None
        cal = df['cal_date'].astype(str).tolist()

        anchor_i = None
        for i in range(min(MAX_ANCHOR_BACK, len(cal))):
            if not get_etf_snapshot(cal[i]).empty:
                anchor_i = i
                break
            print(f"   ⚠️ {cal[i]} ETF 行情尚未发布，回退到上一交易日")
        if anchor_i is None:
            print(f"❌ 连续探测最近 {min(MAX_ANCHOR_BACK, len(cal))} 个交易日均无 ETF 行情，"
                  f"请检查 TUSHARE_TOKEN 与接口额度")
            return None

        dates = {
            'now': cal[anchor_i],
            'prev': cal[anchor_i + 1] if len(cal) > anchor_i + 1 else None,
        }
        # 获取 N 天前的日期（从锚定日往前数）
        for n in RPS_N:
            if len(cal) > anchor_i + n:
                dates[n] = cal[anchor_i + n]
        return dates
    except Exception as e:
        print(f"❌ 获取日历失败: {e}")
        return None


# ETF 行情快照缓存。锚定探测与后续计算取的是同一天，不缓存等于白拉两遍。
# 只缓存非空结果：把一次瞬时失败的空表缓存下来，会让这一天在本进程内永远「没数据」。
_ETF_SNAP_CACHE = {}


def get_etf_snapshot(date_str):
    """获取某日全市场场内基金行情"""
    if date_str in _ETF_SNAP_CACHE:
        return _ETF_SNAP_CACHE[date_str].copy()
    print(f"   正在获取 {date_str} 的 ETF 行情...")
    try:
        # Tushare 接口：fund_daily 获取场内基金日线
        df = pro.fund_daily(trade_date=date_str)
        if df.empty: return pd.DataFrame()
        
        # 仅保留代码和收盘价
        out = df[['ts_code', 'close']].rename(columns={'close': 'close_val'}).copy()
        _ETF_SNAP_CACHE[date_str] = out
        return out.copy()
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
    # 北京时间口径。原来用 datetime.now()，在 UTC runner 上会取到前一天。
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_str = beijing_time.strftime('%Y%m%d')

    # 1. 准备日期（内部已锚定「行情已发布」的交易日）
    dates = get_trading_dates(today_str)
    if not dates:
        print("❌ 无法确定可用交易日（日历为空或连续多日无 ETF 行情），退出")
        return 1

    trading_date = dates['now']
    # ★ 更新日期必须锚定真实交易日，不能用系统日期：
    #   跑批过零点时系统日期已是次日，写进 CSV 就成了「周六的 ETF 榜单」。
    today_fmt = f"{trading_date[:4]}-{trading_date[4:6]}-{trading_date[6:]}"
    if today_str != trading_date:
        print(f"🔄 今天 ({today_str}) 不是行情已发布的交易日，"
              f"回退到最近一个有行情的交易日 ({trading_date})")

    # 确保 data 目录存在
    os.makedirs("data", exist_ok=True)

    # 2. 获取今日行情作为基准
    df_now = get_etf_snapshot(dates['now'])
    if df_now.empty: 
        print("⚠️ 今日无行情数据，停止运行")
        return 1

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
        return 0

    except Exception as e:
        print(f"❌ 处理 ETF 数据出错: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    # 退出码必须如实：原实现无论成败都 return None → 退出码 0，
    # 跑批汇总里「榜单没更新」会显示成 [OK]，等于没有监控。
    sys.exit(main_job() or 0)
