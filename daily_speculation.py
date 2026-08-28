import tushare as ts
import pandas as pd
import datetime
import os
import json
import requests

# 配置
MY_TOKEN = os.getenv('TUSHARE_TOKEN', '')
SPECULATION_PATH = "data/speculation_data.json"

if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

def get_latest_trade_date(api_func, max_days=5):
    """
    核心机制：强制使用北京时间，自动往前寻找最近有数据的一个交易日。
    解决白天运行脚本时，Tushare当天数据尚未生成导致页面空白的问题。
    """
    # ★ 核心修复 1：无论服务器在哪，强制锁定北京时间 (UTC+8)
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    
    for i in range(max_days):
        date_str = (beijing_time - datetime.timedelta(days=i)).strftime('%Y%m%d')
        df = api_func(trade_date=date_str)
        if not df.empty:
            if i > 0:
                print(f"⚠️ 注意: Tushare 今日数据未就绪，自动回退抓取 {date_str} 的数据")
            else:
                print(f"✅ 成功获取今日 ({date_str}) 最新数据")
            return date_str, df
    return None, pd.DataFrame()

def get_cb_opportunities():
    """
    1. 可转债投机：放弃爬虫！使用纯数学公式自己计算真实溢价率（100%不被封）
    """
    print("🛡️ [1/2] 正在扫描可转债市场...")
    try:
        # 1. 自动寻找最新交易日数据，告别空窗期
        trade_date, df_cb = get_latest_trade_date(pro.cb_daily)
        if df_cb.empty:
            print("❌ 连续5天未获取到可转债行情")
            return []
            
        print(f"   📅 开始本地计算溢价率...")
        
        # 2. 获取基础信息 (包含计算必须的：转股价 conv_price 和 正股代码 stk_code)
        basic = pro.cb_basic(fields="ts_code,bond_short_name,stk_code,conv_price,remain_size")
        df = pd.merge(df_cb, basic, on='ts_code', how='left')
        
        if df['remain_size'].median() > 10000:
             df['remain_size'] = df['remain_size'] / 100000000
        df = df[df['remain_size'] > 0].copy()

        # 3. 获取同一天的正股行情
        df_stk = pro.daily(trade_date=trade_date)
        if not df_stk.empty:
            df_stk = df_stk[['ts_code', 'close']].rename(columns={'ts_code': 'stk_code', 'close': 'stk_close'})
            df = pd.merge(df, df_stk, on='stk_code', how='left')
        else:
            df['stk_close'] = 0.0
            
        # ★★★ 核心突破：利用公式自行算出真实的溢价率，彻底摆脱防火墙 ★★★
        df['premium_rate'] = 0.0
        # 筛选条件：转股价和正股价都必须大于0
        mask = (df['conv_price'] > 0) & (df['stk_close'] > 0)
        
        # 华尔街公式计算
        df.loc[mask, 'conv_value'] = 100 / df.loc[mask, 'conv_price'] * df.loc[mask, 'stk_close']
        df.loc[mask, 'premium_rate'] = (df.loc[mask, 'close'] / df.loc[mask, 'conv_value'] - 1) * 100
        
        # 算出最终的双低值
        df['premium_rate'] = df['premium_rate'].round(2).fillna(0)
        df['double_low'] = (df['close'] + df['premium_rate']).round(2)
        print("   ✅ 溢价率计算完成！")

        # 4. 策略归类
        small_cap = df[
            (df['remain_size'] < 3.0) & 
            (df['close'] < 150) &
            (df['close'] > 0)
        ].sort_values('remain_size').head(10).copy()
        small_cap['tag'] = '🛡️ 袖珍小盘'
        
        top_active = df[
            (df['pct_chg'] > 1.5) & 
            (df['amount'] > 15000) 
        ].sort_values('amount', ascending=False).head(10).copy()
        top_active['tag'] = '🔥 活跃妖债'
        
        result = pd.concat([small_cap, top_active]).drop_duplicates(subset=['ts_code'])
        result['desc'] = result.apply(lambda x: f"规模:{round(x['remain_size'], 2)}亿", axis=1)
        
        export_cols = ['ts_code', 'bond_short_name', 'tag', 'desc', 'close', 'pct_chg', 'premium_rate', 'double_low']
        return result[export_cols].to_dict('records')
        
    except Exception as e:
        print(f"❌ 可转债获取失败: {e}")
        return []

def get_fund_arbitrage():
    """
    2. 基金套利：自动回溯防空窗期 + 腾讯 API
    """
    print("🧱 [2/2] 正在扫描基金异动...")
    try:
        # 使用新机制，防止白天运行抓不到数据
        trade_date, df = get_latest_trade_date(pro.fund_daily)
        if df.empty: return []
        
        df_active = df[df['amount'] > 5000].copy()
        top_gainers = df_active.sort_values('pct_chg', ascending=False).head(20).copy()
        
        # 腾讯 API 获取名称
        print("   🔄 正在通过腾讯 API 解析基金名称...")
        name_map = {}
        query_list = []
        for code in top_gainers['ts_code']:
            num, market = code.split('.')
            prefix = 'sh' if market == 'SH' else 'sz'
            query_list.append(f"{prefix}{num}")
            
        try:
            tc_url = f"http://qt.gtimg.cn/q={','.join(query_list)}"
            resp = requests.get(tc_url, timeout=5)
            for line in resp.text.split(';'):
                if '="' in line:
                    key_part, val_part = line.split('="')
                    q_code = key_part.split('_')[-1]
                    vals = val_part.split('~')
                    if len(vals) > 1:
                        market_str = 'SH' if q_code.startswith('sh') else 'SZ'
                        ts_code = f"{q_code[2:]}.{market_str}"
                        name_map[ts_code] = vals[1]
            print("   ✅ 基金名称解析成功！")
        except Exception as e:
            print(f"   ⚠️ 腾讯 API 解析失败: {e}")

        # Tushare 获取净值算溢价率
        print("   🔄 正在计算基金溢价率...")
        nav_map = {}
        try:
            start_date = (datetime.datetime.strptime(trade_date, '%Y%m%d') - datetime.timedelta(days=7)).strftime('%Y%m%d')
            nav_df = pro.fund_nav(ts_code=','.join(top_gainers['ts_code'].tolist()), start_date=start_date)
            if not nav_df.empty:
                nav_df = nav_df.sort_values('nav_date', ascending=False).drop_duplicates('ts_code')
                nav_map = dict(zip(nav_df['ts_code'], nav_df['unit_nav']))
        except Exception as e:
            pass

        res = []
        for _, row in top_gainers.iterrows():
            code = row['ts_code']
            name = name_map.get(code, code)
            price = row['close']
            
            premium_rate = 0.0
            if code in nav_map and nav_map[code] > 0:
                premium_rate = (price - nav_map[code]) / nav_map[code] * 100
            
            tag = '📈 强势'
            if any(k in str(name) for k in ['纳指', '标普', '日经', '德国', '法国', '油', '金', '海外', '互联']):
                tag = '🌍 跨境(注意溢价风险)'
            elif 'LOF' in str(name) or code.startswith('16'):
                tag = '🏗️ LOF套利'
            
            res.append({
                "code": code,
                "name": name,  
                "price": price,
                "change": row['pct_chg'],
                "vol_yi": round(row['amount']/100000, 2),
                "tag": tag,
                "premium_rate": round(premium_rate, 2)
            })
            
        return res
        
    except Exception as e:
        print(f"❌ 基金扫描失败: {e}")
        return []

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    
    cb = get_cb_opportunities()
    fund = get_fund_arbitrage()
    
    # ★ 核心修复 2：输出给网页的时间戳，同样强制锁定北京时间，并带上时分秒方便排错
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    
    final_data = {
        "date": beijing_time.strftime('%Y-%m-%d %H:%M:%S'),
        "cb_list": cb,
        "fund_list": fund,
        "ai_analysis": "" 
    }
    
    with open(SPECULATION_PATH, 'w', encoding='utf-8') as f:
        json.dump(final_data, f, ensure_ascii=False, indent=2)
    print("✅ 投机数据生成完毕")
