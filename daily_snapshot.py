import tushare as ts
import pandas as pd
import datetime
import os

MY_TOKEN = os.getenv('TUSHARE_TOKEN', '')
if MY_TOKEN:
    ts.set_token(MY_TOKEN)
    pro = ts.pro_api()

def generate_snapshot():
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    for i in range(10):
        date_str = (beijing_time - datetime.timedelta(days=i)).strftime('%Y%m%d')
        try:
            df_daily = pro.daily(trade_date=date_str)
            if not df_daily.empty:
                df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
                df_mv = pro.daily_basic(trade_date=date_str, fields='ts_code,circ_mv')
                
                # 拼接成一张全市场大表
                df = pd.merge(df_basic, df_daily[['ts_code', 'close', 'amount']], on='ts_code')
                if not df_mv.empty:
                    df = pd.merge(df, df_mv, on='ts_code', how='left')
                else:
                    df['circ_mv'] = 999999999 # 兜底极大值
                    
                os.makedirs("data", exist_ok=True)
                df.to_csv("data/market_snapshot.csv", index=False)
                print(f"✅ 全市场快照 ({date_str}) 生成完毕并保存为 data/market_snapshot.csv")
                return
        except Exception as e:
            print(f"⚠️ {date_str} 快照生成异常: {e}")
            continue
            
    print("❌ 连续10天未获取到行情快照。")

if __name__ == "__main__":
    if not MY_TOKEN:
        print("❌ 未配置 Token，脚本退出。")
    else:
        generate_snapshot()
