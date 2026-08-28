import os
import time
import random
import pandas as pd
from io import StringIO
import cloudscraper
from config_gurus import GURUS

def fetch_dataroma(scraper, guru_id, source_url):
    print(f"🕵️ 抓取基金经理: {guru_id}...")
    try:
        headers = {'Referer': 'https://www.dataroma.com/'}
        response = scraper.get(source_url, headers=headers, timeout=30)
        
        if "Just a moment" in response.text or response.status_code == 403:
            print(f"⚠️ {guru_id} 遭遇验证码拦截，本次跳过。")
            return

        tables = pd.read_html(StringIO(response.text))
        
        if tables:
            df = tables[0]
            if 'Unnamed: 0' in df.columns:
                df = df.drop(columns=['Unnamed: 0'])
                
            save_path = f"data/gurus/{guru_id}_latest.csv"
            df.to_csv(save_path, index=False)
            print(f"✅ {guru_id} 数据保存成功！")
        else:
            print(f"⚠️ {guru_id} 未找到表格数据。")
            
    except Exception as e:
        print(f"❌ 抓取 {guru_id} 失败: {e}")

if __name__ == "__main__":
    os.makedirs("data/gurus", exist_ok=True)
    
    # 实例化 Cloudscraper
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )
    
    print("🚀 开始获取投资作业本数据 (稳定精简版)...")
    for guru_id, info in GURUS.items():
        fetch_dataroma(scraper, guru_id, info['source_url'])
            
        sleep_time = random.uniform(6, 12)
        print(f"   ⏳ 休息 {sleep_time:.1f} 秒...")
        time.sleep(sleep_time)
            
    print("🎉 投资作业本全部更新完毕！")
