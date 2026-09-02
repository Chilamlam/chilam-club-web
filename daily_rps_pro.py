import tushare as ts
import pandas as pd
import datetime
import os
import sys
import time
import akshare as ak
import concurrent.futures

# ================= 配置区 =================
LOCAL_TOKEN = '' 
MY_TOKEN = os.getenv('TUSHARE_TOKEN', LOCAL_TOKEN)

RPS_N = [50, 120, 250] 
THRESHOLD = 87
STOCK_PATH = "data/strong_stocks.csv"

# 锚定「行情已发布的最新交易日」时，最多往前回退几个交易日。
# 3 天足以覆盖「跑批过零点」与「接口当日延迟发布」；再多就不是延迟而是故障，
# 应当报错而不是静默拿一份更旧的数据充当今日榜单。
MAX_ANCHOR_BACK = 3

# 细分行业抓取的整体时间预算（秒）。akshare 单只请求没有超时保护，
# 2026-09-01 那班就是 8 线程抓到 100/102 后剩余任务永久挂住，
# 把已经算好的榜单一起拖进 1800s 超时被杀。预算到点就放弃题材、保榜单。
INDUSTRY_BUDGET_SEC = 240
# 单只请求的 socket 超时。akshare 底层走 requests，不设这个的话
# 一个不返回的连接能挂到进程被杀。
INDUSTRY_SOCKET_TIMEOUT = 12

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

def _load_calendar(end_date):
    """返回降序交易日列表（最新在前）。"""
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    start_date = (beijing_time - datetime.timedelta(days=400)).strftime('%Y%m%d')
    try:
        df = pro.trade_cal(exchange='', is_open='1', end_date=end_date, start_date=start_date)
        df = df.sort_values('cal_date', ascending=False).reset_index(drop=True)
        if df.empty:
            return []
        return df['cal_date'].astype(str).tolist()
    except Exception as e:
        print(f"❌ 获取日历失败: {e}")
        return []


def get_trading_dates(end_date):
    """锚定「行情已发布的最新交易日」，并按它往前数出 RPS 各窗口的对照日。

    这里有一个曾经让整条链路静默空转的坑：交易日历只回答「这天开不开市」，
    不回答「这天的日线数据发布了没有」。跑批被平台延迟到过零点时，日历给出的
    最新交易日就是当天，而当天距开盘还有好几个小时，pro.daily 必然为空，
    原实现直接放弃、打印一行提示后以退出码 0 结束——榜单文件停在昨天，
    编排器却报 OK。2026-09-01 有三班跑批栽在这上面。

    修法与 daily_scorecard.py 的 latest_trade_date() 保持一致：往前逐日探测，
    以「真的取到行情」为锚。回退上限 MAX_ANCHOR_BACK 是刻意的——连续多日
    取不到行情说明是 token 失效或接口故障，不该靠回退掩盖，要报错出来。

    RPS 各窗口的对照日必须从锚定日开始数，不能从日历首位数。回退一天却仍按
    首位取 50 格，实际窗口只有 49 天，会算出一份口径悄悄漂移的榜单。
    """
    print("📅 [个股] 正在获取交易日历...")
    cal = _load_calendar(end_date)
    if not cal:
        return None

    anchor_i = None
    for i in range(min(MAX_ANCHOR_BACK, len(cal))):
        if not get_snapshot(cal[i]).empty:
            anchor_i = i
            break
        print(f"   ⚠️ {cal[i]} 行情尚未发布，回退到上一交易日")
    if anchor_i is None:
        print(f"❌ 连续探测最近 {min(MAX_ANCHOR_BACK, len(cal))} 个交易日均无行情，"
              f"请检查 TUSHARE_TOKEN 与接口额度")
        return None

    dates = {
        'now': cal[anchor_i],
        'prev': cal[anchor_i + 1] if len(cal) > anchor_i + 1 else None,
    }
    for n in RPS_N:
        if len(cal) > anchor_i + n:
            dates[n] = cal[anchor_i + n]
    return dates


# 行情快照缓存：锚定探测与后续计算取的是同一天，不缓存等于把全市场行情
# 白拉两遍。只缓存非空结果——把一次瞬时失败的空表缓存下来，会让同一天在
# 本次进程内永远「没有数据」。
_SNAP_CACHE = {}


def get_snapshot(date_str):
    if date_str in _SNAP_CACHE:
        # 交出副本而不是缓存对象本身：calculate_rps_logic 会对返回值做
        # rename(inplace=True)，直接交出缓存对象等于让调用方改写缓存，
        # 下一次命中缓存拿到的就是列名已被改过的表。
        return _SNAP_CACHE[date_str].copy()
    print(f"   正在获取 {date_str} 的行情...")
    try:
        df_daily = pro.daily(trade_date=date_str, fields='ts_code,close')
        df_adj = pro.adj_factor(trade_date=date_str, fields='ts_code,adj_factor')
        
        if df_daily.empty or df_adj.empty: return pd.DataFrame()
        
        df = pd.merge(df_daily, df_adj, on='ts_code')
        df['close_val'] = df['close'] * df['adj_factor'] 
        df['display_val'] = df['close'] 
        
        out = df[['ts_code', 'close_val', 'display_val']].copy()
        _SNAP_CACHE[date_str] = out
        return out.copy()
    except Exception as e:
        print(f"Error: {e}")
        return pd.DataFrame()

def get_fundamental_smart(date_str, backup_date_str=None):
    print(f"📊 正在获取基本面数据...")
    fields = 'ts_code,turnover_rate,pe_ttm,pb,circ_mv'
    df = pro.daily_basic(trade_date=date_str, fields=fields)
    
    if df.empty and backup_date_str:
        print(f"   ⚠️ {date_str} 数据未出，切换至上一交易日 {backup_date_str}...")
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


def _install_request_timeout(seconds):
    """给 requests 装一个默认超时，返回还原函数。

    akshare 内部不暴露 timeout 参数，requests 在 timeout=None 时会把 socket
    设成永久阻塞，一个不返回的连接就能挂到进程被外层杀掉——线程池的 atexit
    钩子还会 join 这些线程，连「放弃题材、正常退出」都做不到。
    只在题材抓取阶段生效，抓完立即还原：全局 12s 读超时会误伤 tushare 的
    全市场大请求。
    """
    try:
        import requests
    except Exception:  # noqa: BLE001
        return lambda: None
    orig = requests.Session.send

    def patched(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = seconds
        return orig(self, request, **kwargs)

    requests.Session.send = patched
    return lambda: setattr(requests.Session, "send", orig)


def fetch_detailed_industries(ts_codes):
    """抓细分题材。带整体时间预算，到点就交回已拿到的部分。

    返回值可能是空字典或只覆盖一部分代码——调用方必须能接受残缺，
    因为这是装饰性字段，不值得让整份榜单为它冒风险。
    """
    total = len(ts_codes)
    print(f"🏭 [Akshare] 抓取 {total} 只个股的细分题材"
          f"（整体预算 {INDUSTRY_BUDGET_SEC}s，超时即放弃、不影响已落盘榜单）...")
    industry_map = {}
    restore = _install_request_timeout(INDUSTRY_SOCKET_TIMEOUT)
    # 不用 with：ThreadPoolExecutor.__exit__ 是 shutdown(wait=True)，
    # 挂住的线程会把「已放弃」重新变成无限等待。
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
    try:
        futures = {executor.submit(get_industry_worker, code): code for code in ts_codes}
        count = 0
        try:
            for future in concurrent.futures.as_completed(futures, timeout=INDUSTRY_BUDGET_SEC):
                code, industry = future.result()
                industry_map[code] = industry
                count += 1
                if count % 50 == 0:
                    print(f"   🚀 进度: {count}/{total}...")
        except concurrent.futures.TimeoutError:
            print(f"   ⚠️ 题材抓取超出 {INDUSTRY_BUDGET_SEC}s 预算，"
                  f"已拿到 {count}/{total} 只，剩余放弃")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        restore()
    return industry_map


def save_ranking(df):
    """把榜单写盘。抽成函数是为了让「先落盘、再补题材」能复用同一套列顺序，
    两处各写一遍列名清单必然漂移。"""
    base_cols = ['ts_code', 'name', '细分行业', 'price_now', 'RPS_50', 'rps_50_chg',
                 'RPS_120', 'RPS_250', '连续天数']
    extra_cols = ['pe_ttm', 'mv_亿', 'turnover_rate', 'xueqiu_url', '更新日期', '初次入选']
    save_cols = [c for c in base_cols + extra_cols if c in df.columns]
    df[save_cols].round(2).to_csv(STOCK_PATH, index=False)

def process_history_and_change(new_df, file_path, date_str):
    """
    date_str: 这里必须传入【真实的交易日期】，而不是系统日期
    """
    history_map = {}
    yesterday_rps_map = {}
    today_change_map = {}
    
    if os.path.exists(file_path):
        try:
            old_df = pd.read_csv(file_path)
            old_df['更新日期'] = old_df['更新日期'].astype(str)
            
            for _, row in old_df.iterrows():
                code = row['ts_code']
                last_update = row.get('更新日期', '')
                
                history_map[code] = {
                    'first': row.get('初次入选', date_str),
                    'days': row.get('连续天数', 0),
                    'last_update': last_update
                }

                # 智能继承变动值逻辑
                if last_update == date_str:
                    if 'rps_50_chg' in row:
                        today_change_map[code] = row['rps_50_chg']
                else:
                    if 'RPS_50' in row:
                        yesterday_rps_map[code] = row['RPS_50']
                        
        except Exception as e:
            print(f"⚠️ 读取历史文件微瑕: {e}")

    res = []
    for _, row in new_df.iterrows():
        code = row['ts_code']
        first_date = date_str
        days_count = 1
        
        # 连板逻辑
        if code in history_map:
            hist = history_map[code]
            # 如果上次更新日期 == 今天的交易日期 -> 说明今天已经跑过一次了，天数不加
            # 如果上次更新日期 != 今天的交易日期 -> 说明是新的一天交易日，天数+1
            if hist['last_update'] == date_str:
                days_count = hist['days']
                first_date = hist['first']
            else:
                days_count = hist['days'] + 1
                first_date = hist['first']
        
        row['初次入选'] = first_date
        row['连续天数'] = days_count
        
        # 变动值逻辑
        if code in today_change_map:
            row['rps_50_chg'] = today_change_map[code]
        elif code in yesterday_rps_map:
            row['rps_50_chg'] = row['RPS_50'] - yesterday_rps_map[code]
        else:
            row['rps_50_chg'] = 999 
            
        # 雪球链接
        if '.' in code:
            num, suffix = code.split('.')
            link_code = suffix.upper() + num 
            row['xueqiu_url'] = f"https://xueqiu.com/S/{link_code}"
        else:
            row['xueqiu_url'] = ""
            
        res.append(row)
    return pd.DataFrame(res)

def main_job():
    print("🚀 启动 A股 RPS 更新 (全天候交易日自动追溯版)...")
    
    # 强制锁定北京时间
    beijing_time = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_sys = beijing_time.strftime('%Y%m%d')
    
    # 获取交易所日历信息（内部已锚定「行情已发布」的交易日）
    dates = get_trading_dates(today_sys)
    if not dates: 
        print("❌ 无法确定可用交易日（日历为空或连续多日无行情），退出")
        return 1
    
    trading_date = dates['now'] # 已确认当天行情真的取得到
    
    # 周末/节假日不是错误，只做提示继续跑；行情未发布也走同一条提示分支，
    # 因为对使用者来说结果一样：处理的是最近一个有数据的交易日。
    if today_sys != trading_date:
        print(f"😴 今天 ({today_sys}) 不是行情已发布的交易日...")
        print(f"🔄 自动回退到最近一个有行情的交易日 ({trading_date}) 进行处理")
    else:
        print(f"✅ 今天是交易日 ({trading_date})，行情已发布，开始执行计算...")
    
    # 将 YYYYMMDD 转为 YYYY-MM-DD 格式用于 CSV 保存
    trading_date_fmt = f"{trading_date[:4]}-{trading_date[4:6]}-{trading_date[6:]}"

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
            
            # ★ 更新日期锚定交易日，配合下方的查重逻辑，完美杜绝连板虚增
            strong_stock['更新日期'] = trading_date_fmt
            
            # 3. 细分行业：先用 tushare 的粗行业垫底，保证「即使题材抓取全军覆没，
            #    榜单也有一列可用的行业信息」，而不是一片 '-'。
            if 'industry' in strong_stock.columns:
                strong_stock['细分行业'] = strong_stock['industry'].fillna('-')
            else:
                strong_stock['细分行业'] = '-'

            # 4. 处理历史 (传入交易日期)
            #    只算一次。两阶段落盘若各算一次，第二次会读到自己刚写的文件，
            #    连续天数与变动值的推断基准就变了。
            final_stock = process_history_and_change(strong_stock, STOCK_PATH, trading_date_fmt)

            # 5. 第一阶段落盘：榜单本体先进磁盘。
            #    这是本次改造的核心——原实现把「抓 100 多个题材」放在写盘之前，
            #    2026-09-01 那班 RPS 全部算完、只差题材，却因题材接口挂住被超时
            #    杀掉，磁盘上一个字都没留下，榜单静默停在前一天。
            #    装饰性字段绝不能挡在主产物前面。
            save_ranking(final_stock)
            print(f"✅ 榜单已落盘（{len(final_stock)} 只，交易日 {trading_date_fmt}）")

            # 6. 第二阶段：尽力补细分题材，成功多少覆盖多少。
            #    整段 try 兜住：题材是加分项，它的任何异常都不该让已落盘的榜单
            #    变成「本次跑批失败」。
            codes_list = final_stock['ts_code'].tolist()
            if codes_list:
                try:
                    industry_map = fetch_detailed_industries(codes_list)
                    got = {k: v for k, v in industry_map.items() if v and v != '-'}
                    if got:
                        final_stock['细分行业'] = (
                            final_stock['ts_code'].map(got)
                            .fillna(final_stock['细分行业'])
                        )
                        save_ranking(final_stock)
                        print(f"✅ 细分题材已补齐 {len(got)}/{len(codes_list)} 只并覆盖写入")
                    else:
                        print("   ⚠️ 未取到任何细分题材，保留粗行业（榜单不受影响）")
                except Exception as e:
                    print(f"   ⚠️ 细分题材抓取异常 {type(e).__name__}: {e}，"
                          f"保留粗行业（榜单已落盘，不受影响）")

            print(f"✅ 交易日数据更新完成！")
            return 0

        except Exception as e:
            print(f"❌ 处理出错: {e}")
            import traceback
            traceback.print_exc()
            return 1
    else:
        print("⚠️ 未获取到行情数据")
        return 1

if __name__ == "__main__":
    # 退出码必须如实反映结果。原实现无论成败都 return None → 退出码 0，
    # 于是「榜单没更新」在跑批汇总里显示为 [OK]，掩盖了故障本身。
    sys.exit(main_job() or 0)
