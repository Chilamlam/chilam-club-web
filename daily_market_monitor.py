import tushare as ts
import pandas as pd
import datetime
import os
import json
import akshare as ak
import requests
import warnings
import time

warnings.filterwarnings("ignore")

# 配置
MY_TOKEN = os.getenv('TUSHARE_TOKEN', '')
GEMINI_KEY = os.getenv('GEMINI_API_KEY', '')

# 数据路径
INDEX_PATH = "data/index_history.csv"
SENTIMENT_PATH = "data/market_sentiment.csv"
SECTOR_PATH = "data/sector_hot.csv"
AI_RESULT_PATH = "data/ai_market_analysis.json"

if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

def save_csv(row, path):
    if os.path.exists(path):
        try: df = pd.read_csv(path)
        except: df = pd.DataFrame()
    else:
        df = pd.DataFrame(columns=row.keys())
    if not df.empty and row['date'] in df['date'].values:
        df = df[df['date'] != row['date']]
    new_df = pd.DataFrame([row])
    df = pd.concat([df, new_df], ignore_index=True)
    df.sort_values('date', inplace=True)
    df.to_csv(path, index=False)

# ★★★ 新增：全局交易日自动回退引擎 ★★★
def get_latest_trade_date(max_days=10):
    """
    核心机制：强制锁定北京时间，遇周末或节假日自动往前寻找最近有数据的一个交易日。
    """
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    for i in range(max_days):
        date_obj = beijing_time - datetime.timedelta(days=i)
        date_str = date_obj.strftime('%Y%m%d')
        df = pro.daily(trade_date=date_str)
        if not df.empty:
            date_fmt = date_obj.strftime('%Y-%m-%d')
            if i > 0:
                print(f"⚠️ 注意: 今日数据未就绪，自动回退抓取 {date_str} 的数据")
            else:
                print(f"✅ 成功获取今日 ({date_str}) 最新行情数据")
            return date_str, date_fmt, df
    return None, None, pd.DataFrame()

def get_market_data_tushare():
    print("🚀 [1/3] 计算情绪与板块 (Tushare)...")
    
    # 使用防空窗引擎获取正确的交易日数据
    trade_date_str, trade_date_fmt, df_daily = get_latest_trade_date()
    
    if df_daily.empty:
        print("❌ 连续10天未获取到行情，放弃处理。")
        return None, None

    try:
        up = len(df_daily[df_daily['pct_chg'] > 0])
        down = len(df_daily[df_daily['pct_chg'] < 0])
        amt = round(df_daily['amount'].sum() / 100000, 2)
        
        # 写入 CSV 时，使用实际的交易日 (trade_date_fmt)，而不是物理服务器时间
        save_csv({'date': trade_date_fmt, 'up_count': up, 'down_count': down, 'total_amount_yi': amt}, SENTIMENT_PATH)
        
        df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
        df_merge = pd.merge(df_daily, df_basic, on='ts_code', how='left')
        sector_stats = df_merge.groupby('industry').agg({
            'pct_chg': 'mean', 'amount': 'sum', 'ts_code': 'count'
        }).reset_index()
        sector_stats = sector_stats[sector_stats['ts_code'] > 5]
        sector_stats['amount'] = round(sector_stats['amount'] / 100000, 2)
        sector_stats['pct_chg'] = round(sector_stats['pct_chg'], 2)
        sector_stats.sort_values('pct_chg', ascending=False).head(15).to_csv(SECTOR_PATH, index=False)
        print("✅ 板块与情绪数据计算完毕！")
        return trade_date_str, trade_date_fmt
    except Exception as e:
        print(f"❌ 市场数据处理失败: {e}")
        return None, None

# 修改：接收 trade_date_str 确保抓取的是对应交易日的涨停池
def get_akshare_data(trade_date_str):
    all_news = [] 
    top_limit_stocks = []
    
    def clean_and_add(df, source_name):
        if df.empty: return
        cols = df.columns.tolist()
        content_col = next((c for c in cols if '内容' in c), None)
        if not content_col: content_col = next((c for c in cols if '标题' in c), None)
        time_col = next((c for c in cols if '时间' in c), None)
        
        if content_col and time_col:
            print(f"   -> {source_name} 获取到 {len(df)} 条数据")
            for _, row in df.iterrows():
                t_val = str(row[time_col])
                c_val = str(row[content_col]).strip()
                if len(t_val) > 10: t_val = t_val[-8:-3]
                elif len(t_val) == 8: t_val = t_val[:5]
                if len(c_val) > 5:
                    all_news.append({'time': t_val, 'content': c_val})

    try:
        print("📡 [1/2] 抓取东方财富快讯...")
        df_em = ak.stock_info_global_em()
        clean_and_add(df_em, "东方财富")
    except Exception as e:
        print(f"⚠️ 东财抓取失败: {e}")

    try:
        print("📡 [2/2] 抓取财联社快讯...")
        df_cls = ak.stock_info_global_cls()
        clean_and_add(df_cls, "财联社")
    except Exception as e:
        print(f"⚠️ 财联社抓取失败: {e}")

    news_stream = []
    if all_news:
        df_all = pd.DataFrame(all_news)
        df_all = df_all.drop_duplicates(subset=['content'])
        
        mask_time = (df_all['time'] >= '09:00') & (df_all['time'] <= '15:30')
        df_trading = df_all[mask_time].copy()
        
        print(f"🛡️ 时间过滤(09:00-15:30): 从 {len(df_all)} 条 -> {len(df_trading)} 条")
        
        if df_trading.empty and len(df_all) > 0:
            print("⚠️ 警告：过滤后为空！通常是因为深夜/周末运行，接口已被非盘中新闻覆盖。")
            df_all = df_all.sort_values('time') 
            df_trading = df_all.head(50) 
            print("🔄 已启动兜底策略：取缓存中最旧的 50 条数据供 AI 分析")
        
        keywords = ['异动', '拉升', '涨停', '封板', '跳水', '成交', '主线', '翻红', '跌停', '炸板', '指数', '板块']
        mask_key = df_trading['content'].astype(str).str.contains('|'.join(keywords))
        df_final = df_trading[mask_key].copy()
        
        df_final = df_final.sort_values('time')
        
        for _, row in df_final.iterrows():
            news_stream.append(f"[{row['time']}] {row['content']}")
            
        print(f"✅ 最终有效A股盘面快讯: {len(news_stream)} 条")
    else:
        news_stream = ["(数据源为空，请检查网络)"]

    # 修改：强制使用刚才推算出的真实交易日
    try:
        print(f"🔥 正在抓取 {trade_date_str} 涨停池...")
        df_zt = ak.stock_zt_pool_em(date=trade_date_str)
        if df_zt.empty: df_zt = ak.stock_zt_pool_em(date=None)
        if not df_zt.empty:
            cols = df_zt.columns.tolist()
            name_col = next((c for c in cols if '名称' in c), '名称')
            ind_col = next((c for c in cols if '行业' in c), '所属行业')
            lb_col = next((c for c in cols if '连板' in c), '连板数')
            if lb_col in cols:
                df_zt = df_zt.sort_values(lb_col, ascending=False).head(15)
                for _, row in df_zt.iterrows():
                    top_limit_stocks.append({
                        "name": row[name_col],
                        "industry": row[ind_col],
                        "limit_times": row[lb_col]
                    })
    except Exception as e:
        print(f"⚠️ 涨停池获取异常: {e}")

    return news_stream, top_limit_stocks

def get_valid_model_name():
    print("🔍 正在嗅探可用模型列表...")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if 'models' in data:
                for m in data['models']:
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        return m['name']
    except: pass
    return "models/gemini-1.5-flash"

def call_gemini_dynamic(prompt):
    model_full_name = get_valid_model_name() 
    if not model_full_name.startswith("models/"): model_full_name = f"models/{model_full_name}"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/{model_full_name}:generateContent?key={GEMINI_KEY}"
    headers = {'Content-Type': 'application/json'}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    print(f"🔄 正在调用: {model_full_name} ...")
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code == 200: return response.json()
    else: return None

# 修改：传入真实的交易日期
def run_ai_analysis(trade_date_str, trade_date_fmt):
    print("🧠 [3/3] Gemini AI 全盘推演 (时间熔断版)...")
    if not GEMINI_KEY: return

    news_stream, top_limit_stocks = get_akshare_data(trade_date_str)

    if not top_limit_stocks and len(news_stream) <= 1:
        print("❌ 无有效数据，放弃 AI 生成。")
        return

    prompt_text = f"""
    你是一个严谨的A股复盘助手。
    下面是【{trade_date_fmt} 交易时段(09:00-15:30)的真实盘口快讯】（已过滤掉非盘中新闻），以及【涨停龙头】。
    
    数据源：
    【盘面快讯 (Time | Content)】:
    {json.dumps(news_stream, ensure_ascii=False)}
    
    【涨停龙头】:
    {json.dumps(top_limit_stocks, ensure_ascii=False)}
    
    任务 1：提取【盘面实况时间轴】
    - **严格基于提供的快讯流**。
    - 请精选 **20条左右** 关键节点，覆盖从早盘到收盘的全过程。
    - **严禁编造**：如果快讯流里确实没有早盘数据，就只写有的部分，不要瞎编。
    - 格式：HH:MM | 事件
    
    任务 2：主线研判
    - 基于涨停股和快讯，总结今日核心题材。
    
    请输出纯 JSON (无markdown):
    {{
        "main_logic": "200字复盘。",
        "limit_reasons": [
            {{"name": "龙头名", "reason": "简析"}}
        ],
        "timeline": [
            {{"time": "HH:MM", "event": "内容"}} 
        ]
    }}
    """

    try:
        result = call_gemini_dynamic(prompt_text)
        if result:
            try:
                ai_text = result['candidates'][0]['content']['parts'][0]['text']
                ai_text = ai_text.replace("```json", "").replace("```", "").strip()
                data = json.loads(ai_text)
                
                # 记录 JSON 文件的归属日期，防止前端显示错误的时间
                data['date'] = trade_date_fmt
                
                with open(AI_RESULT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("✅ AI 战报生成完毕！")
            except Exception as e:
                print(f"⚠️ 解析失败: {e}")
        else:
            print("❌ 调用失败")
    except Exception as e:
        print(f"❌ 流程异常: {e}")

def update_style_analysis():
    print("⚖️ [风格] 正在恢复 1000 vs 500 数据 (目标列: spread)...")
    try:
        # 强制获取北京时间
        beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
        end_date = beijing_time.strftime("%Y%m%d")
        start_date = (beijing_time - datetime.timedelta(days=365)).strftime("%Y%m%d")
        
        df_1000 = ak.stock_zh_index_daily(symbol="sh000852") 
        df_500 = ak.stock_zh_index_daily(symbol="sh000905") 
        
        for df in [df_1000, df_500]:
            df['date'] = pd.to_datetime(df['date']).dt.date
            df.set_index('date', inplace=True)
        
        df_merge = pd.DataFrame()
        df_merge['zz1000'] = df_1000['close']
        df_merge['zz500'] = df_500['close']
        df_merge.dropna(inplace=True)
        
        mask = (df_merge.index >= pd.to_datetime(start_date).date()) & \
               (df_merge.index <= pd.to_datetime(end_date).date())
        df_merge = df_merge.loc[mask]

        idx1000_norm = df_merge['zz1000'] / df_merge['zz1000'].iloc[0]
        idx500_norm = df_merge['zz500'] / df_merge['zz500'].iloc[0]
        
        df_merge['spread'] = idx1000_norm - idx500_norm
        
        if not os.path.exists("data"):
            os.makedirs("data")
        
        csv_path = "data/index_history.csv"
        df_merge.reset_index(inplace=True)
        df_merge.to_csv(csv_path, index=False)
        print(f"✅ [成功] 风格数据已修复！已写入列 'spread' 到 {csv_path}")

    except Exception as e:
        print(f"❌ [风格] 计算失败: {e}")

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    
    # 获取正确的交易日，并贯穿整个流程
    trade_date_str, trade_date_fmt = get_market_data_tushare()
    
    if trade_date_str:
        run_ai_analysis(trade_date_str, trade_date_fmt)
        update_style_analysis()
    else:
        print("❌ 无法获取有效交易日，全流程终止。")
