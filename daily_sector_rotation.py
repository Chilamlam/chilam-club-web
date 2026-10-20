"""板块轮动跑批：拉取东财概念板块全量快照 → 归档 → 计算分析 → 落盘。

数据源与口径
------------
东财 push2 clist（fs=m:90+t:3 = 概念板块），翻页拉全量（约 504 个），
一次请求组即可同时拿到当日涨跌幅与多窗口涨幅：

    f3=今日涨跌幅  f109=5日  f160=10日  f110=20日  f24=60日
    （窗口字段语义已用东财自家板块日K逐一交叉验证，误差 ≤0.06pp；
     注意 f110 是 20 日、f160 才是 10 日，与直觉相反，勿再「纠回」）

为什么不是同花顺题材：同花顺无免费的全量日频接口（名称接口无涨跌幅，
历史接口逐板块拉取且限流 429/503），无法每日全量刷新；东财概念板块口径
覆盖等价的题材集合且一次请求组拿全量多窗口涨幅。前端口径说明如实披露。

为什么不用逐板块历史K线回填：push2his 对高频探测会 IP 级限流（项目前科），
且 clist 已直接给出窗口涨幅，无需回填。

失败语义（与 daily_sentiment 同款三态）
--------------------------------------
  complete   退出码 0：全量取到（≥400 个板块，且 ≥ 服务端 total 的 95%），
             归档 + 分析完整落盘
  incomplete 退出码 2：只取到部分（50~399，或缺页导致低于 total 的 95%）——
             **拒绝写入 history.csv**，并清除该日可能已存在的残留行
  failed     退出码 3：取不到有效数据，不动历史归档，绝不用旧值顶替当日

  ★ incomplete 由「照实归档」改为「拒收」（2026-10-12）：
    2026-09-21 起 daily 归档每天只有 100~300 个板块（应为 504），持续 10 个
    交易日无人发现——因为每天都有一份数据落地，新鲜度检查看不出来。
    半份快照写进累积归档后，任何基于它的占比/分位/榜单都会被静默算错，
    而数值形态完全正常。缺一天只是少一天，坏一天会一直坏下去。
    抓取侧同时改为「分轮补齐失败页」（见 fetch_snapshot），
    从「让残缺数据可见」和「别产生残缺数据」两头一起收。

其他守则
--------
- 周末不落盘（clist 周末返回的是上一交易日收盘数据，写进今天会污染归档）。
- 非交易日守卫：整份快照与归档最后一个交易日几乎完全相同（涨跌幅一致率
  ≥98%）时判定为非交易日/数据停滞，跳过落盘。只判周末与数据停滞，
  不判节假日（过期假日表会悄悄漏掉整个假期）。
- 同日重跑：替换当日行（幂等），不是追加重复。
- history.csv 只保留最近 MAX_HISTORY_DAYS 个交易日（轮动分析只需 ~11 日，
  控制归档体积增长）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.request

import pandas as pd

import sector_rotation as sr

OUT_DIR = "data/sector_rotation"
HISTORY_PATH = os.path.join(OUT_DIR, "history.csv")
ANALYSIS_PATH = os.path.join(OUT_DIR, "analysis.json")
MAX_HISTORY_DAYS = 60

# push2 对数据中心 IP（GitHub Actions runner）按边缘拒绝：2026-09-02/03 连续
# 三班 Actions 跑批全部 HTTP 502，本机同一时刻 5 个域名全通——是 IP 级封锁。
# 分片域名（1/23/99）与延迟域名（push2delay）走不同 CDN 边缘，重试时逐次换域名。
# 收盘后跑批对延迟容忍（快照=当日收盘态），故 push2delay 也可作为兜底。
EM_HOSTS = (
    "push2.eastmoney.com",
    "1.push2.eastmoney.com",
    "23.push2.eastmoney.com",
    "99.push2.eastmoney.com",
    "push2delay.eastmoney.com",
)

CLIST_URL = ("https://{host}/api/qt/clist/get"
             "?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3"
             "&fields=f12,f14,f2,f3,f109,f160,f110,f24,f6,f8,f104,f105")

# 东财字段 → 归档列名（窗口语义见模块 docstring，f110=20日 f160=10日）
FIELD_MAP = {
    "f12": "code", "f14": "name", "f2": "close", "f3": "pct_chg",
    "f109": "pct_5d", "f160": "pct_10d", "f110": "pct_20d", "f24": "pct_60d",
    "f6": "amount", "f8": "turnover", "f104": "up_count", "f105": "down_count",
}

MIN_COMPLETE = 400   # 全量约 504，低于此视为 incomplete
MIN_VALID = 50       # 低于此视为 failed

# 分页抓取的轮数与页间间隔。3 轮的含义是「同一页最多被独立尝试 3×5 次」，
# 而不是把 15 次重试压在同一个坏边缘上（见 fetch_snapshot）。
PAGE_ROUNDS = 3
PAGE_SLEEP = 0.4

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}


def fail(msg: str) -> None:
    print(f"❌ [failed] {msg}")
    sys.exit(3)


def _cst_now() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def fetch_page(pn: int, host_offset: int = 0, retries: int = len(EM_HOSTS)) -> dict | None:
    """拉一页 clist。每次重试换一个域名，最终失败返回 None。

    为什么换域名：502 是东财对机房 IP 的边缘级拒绝，同一域名重试无意义；
    轮换分片/延迟域名才可能落到不同边缘。

    host_offset 让不同轮次从不同域名起步：若总是从 push2.eastmoney.com 开始，
    而恰好只有它被拒，每轮都会把重试次数耗在同一个坏边缘上。"""
    for attempt in range(retries):
        host = EM_HOSTS[(attempt + host_offset) % len(EM_HOSTS)]
        url = CLIST_URL.format(host=host, pn=pn)
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data and isinstance(data.get("data"), dict):
                return data["data"]
            print(f"⚠️ {host} 第 {pn} 页返回结构异常（第 {attempt + 1} 次）")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ {host} 第 {pn} 页请求失败（第 {attempt + 1} 次）：{type(exc).__name__}: {exc}")
        time.sleep(1.5 * (attempt + 1))
    return None


def fetch_snapshot(max_pages: int = 8) -> tuple[list[dict], int | None, list[int]]:
    """翻页拉全量概念板块快照，返回 (行列表, 服务端 total, 最终仍失败的页号)。

    ★ 分轮补齐（2026-10-12 修）。旧逻辑是「连续 2 页失败就整体放弃」，
    后果实测于线上归档：2026-09-21 起每天只落 100~300 个板块（应为 504），
    因为第 1 页成功、第 2、3 页失败即触发放弃——**残缺截面被照实写进历史**，
    而跑批日志里只有一条 warning，前端与看板全都看不出来。
    现在改为：第 1 轮把所有页都试一遍，第 2、3 轮只重试仍失败的页，
    且每轮从不同的 CDN 域名起步。

    只有「第 1 页在第 1 轮就失败」才立刻放弃：第 1 页都拿不到说明接口整体
    不可用，继续翻页只是把超时预算耗光。
    """
    by_page: dict[int, list[dict]] = {}
    total = None
    pending = list(range(1, max_pages + 1))

    for rnd in range(PAGE_ROUNDS):
        if not pending:
            break
        still: list[int] = []
        done = False
        for pn in pending:
            data = fetch_page(pn, host_offset=rnd)
            if data is None:
                if pn == 1 and rnd == 0:
                    print("⚠️ 第 1 页第 1 轮即失败，判定接口整体不可用，放弃本次取数")
                    return [], None, [1]
                still.append(pn)
                continue
            diff = data.get("diff") or []
            total = data.get("total", total)
            if diff:
                by_page[pn] = diff
            got = sum(len(v) for v in by_page.values())
            if total is not None and got >= int(total):
                done = True
                break
            time.sleep(PAGE_SLEEP)
        if done:
            pending = []
            break
        pending = still
        if pending:
            print(f"⚠️ 第 {rnd + 1} 轮后仍失败 {len(pending)} 页：{pending}")
            time.sleep(2.0)

    rows = [r for pn in sorted(by_page) for r in by_page[pn]]
    return rows, total, pending


def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    recs = []
    for r in rows:
        rec = {col: r.get(f) for f, col in FIELD_MAP.items()}
        recs.append(rec)
    df = pd.DataFrame(recs)
    df["code"] = df["code"].astype(str)
    # 成交额单位 元 → 亿元（先算再删原始列，随后统一数值化）
    df["amount_yi"] = pd.to_numeric(df["amount"], errors="coerce") / 1e8
    df = df.drop(columns=["amount"])
    for c in sr.NUMERIC_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # 领涨字段缺失（"-" 已被 coerce 成 NaN）的行照常保留，涨幅列缺失才影响参榜
    return df


# ---------------------------------------------------------------- 兜底通道
# 为什么必须有兜底（2026-10-20 加）
# ---------------------------------
# 东财 push2 clist 会按**机房 IP** 做边缘级拒绝：2026-09-21 起 Actions 每天只
# 取到 100~300 个板块，2026-10-12 起连本机都近乎全拒，归档在 09-18 之后断了
# 16 个交易日，页面判读退化成「样本不连续」。单靠一条被针对性限流的通道，
# 「残缺拒收」只能防止写坏数据，**不能防止继续缺数据**。
#
# Tushare 的 dc_index / dc_daily 转发的**就是同一份东财概念板块数据**，只是
# 换了一条不被掐断的管子。口径已用归档已有的 09-18 / 10-13 逐字段反验：
# 直取列 506/506 行完全相等，窗口列自算最大差 0.0798pp。
#
# 语义边界（很重要）：兜底成功 = 本条通道成功取到**全量**数据，可以直接归档；
# 兜底失败 = 两条通道都不可用，此时继续走原来的 failed / incomplete 语义，
# **绝不**拿半份兜底数据假装成功，也绝不用旧的兜底结果顶替当日。
def _archive_anchor() -> tuple[str | None, dict[str, float]]:
    """归档里最近一个交易日的 (日期, {板块: close})。

    用途只有一个：让兜底数据的 close 列与归档**同一尺度**（东财板块指数存在
    被重新定基的情况，直接拿 Tushare 原始收盘价会与相邻归档日差 1000 倍）。
    窗口涨跌幅是比值，与尺度无关，不依赖它。
    """
    if not os.path.exists(HISTORY_PATH):
        return None, {}
    try:
        hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 读归档取锚点失败（{type(exc).__name__}），兜底数据不做 close 折算")
        return None, {}
    if hist.empty or "date" not in hist.columns or "close" not in hist.columns:
        return None, {}
    last = sorted(hist["date"].astype(str).unique())[-1]
    d = hist[hist["date"].astype(str) == last]
    return last, d.set_index("code")["close"].dropna().to_dict()


def fetch_tushare_snapshot(today: str) -> tuple[pd.DataFrame | None, dict]:
    """兜底取数：用 Tushare 同源通道重建当日全量快照。

    返回 (DataFrame 或 None, 诊断 dict)。**None 表示兜底不可用**，调用方必须
    继续按原失败语义处理——把「兜底也失败」当成「取到了」是本函数最危险的误用。
    """
    try:
        import sector_dc_source as dc  # noqa: PLC0415  （按需导入：无 token 环境不必加载）
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"模块不可用：{type(exc).__name__}: {exc}"}
    anchor_date, anchor_close = _archive_anchor()
    try:
        df, info = dc.fetch_snapshot(today, anchor_date=anchor_date,
                                     anchor_close=anchor_close)
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"{type(exc).__name__}: {exc}", "anchor": anchor_date}
    if df is None or len(df) < MIN_COMPLETE:
        return None, {"error": f"行数不足：{0 if df is None else len(df)}",
                      "anchor": anchor_date, **info}
    print(f"[i] Tushare 兜底取到 {len(df)} 个概念板块"
          f"（锚 {anchor_date}，窗口缺失 {info.get('window_missing')}）")
    return df, {"anchor": anchor_date, **info}


def probe_fallback_channel(today: str) -> dict:
    """兜底通道探活：只调一次 trade_cal，确认 token 与接口都真的可用。

    为什么每天探：兜底是「clist 挂了才用」的通道。等到真挂的那天才发现它没权限，
    就已经又缺了一天数据 —— 而这件事**无法从 clist 的健康状况里看出来**。
    探活成本是一次轻量请求，失败绝不影响主流程（只是写进 analysis 供人看）。
    """
    if not os.getenv("TUSHARE_TOKEN"):
        return {"ok": False, "error": "环境里没有 TUSHARE_TOKEN（Actions 需在 secrets 配置）"}
    try:
        import sector_dc_source as dc  # noqa: PLC0415
        opens = dc.trade_cal(today, today)
        return {"ok": True, "checked_date": today, "is_trade_day": bool(opens)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}


def non_trading_day(today: str, df_today: pd.DataFrame) -> bool:
    """数据停滞守卫：与归档最后一个交易日的涨跌幅几乎完全一致 → 非交易日/停滞。

    只在「归档里还没有今天」时生效——同日重跑是幂等替换，本就该与已有
    当日数据一致，不能被此守卫挡住。
    """
    if not os.path.exists(HISTORY_PATH):
        return False
    try:
        hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})
    except Exception:
        return False
    if hist.empty:
        return False
    dates = sorted(hist["date"].astype(str).unique())
    if dates[-1] == today:
        return False  # 同日重跑：走幂等替换，不做停滞判定
    last = hist[hist["date"].astype(str) == dates[-1]]
    if "pct_chg" not in last.columns:
        return False
    merged = pd.merge(
        df_today[["code", "pct_chg"]], last[["code", "pct_chg"]],
        on="code", suffixes=("_new", "_old"), how="inner",
    )
    if len(merged) < 300:
        return False
    same = (merged["pct_chg_new"].round(2) == merged["pct_chg_old"].round(2)).mean()
    if same >= 0.98:
        print(f"⏭️ 快照与 {dates[-1]} 涨跌幅一致率 {same:.0%}，判定非交易日/数据停滞，不落盘")
        return True
    return False


def append_history(today: str, df_today: pd.DataFrame) -> pd.DataFrame:
    """同日替换（幂等）+ 追加，返回整理后的完整历史（已按体积截断）。"""
    if os.path.exists(HISTORY_PATH):
        hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})
        hist = hist[hist["date"].astype(str) != today]
    else:
        hist = pd.DataFrame()
    full = pd.concat([hist, df_today], ignore_index=True)
    full = full.sort_values(["date", "code"]).reset_index(drop=True)
    dates = sorted(full["date"].astype(str).unique())
    if len(dates) > MAX_HISTORY_DAYS:
        keep = set(dates[-MAX_HISTORY_DAYS:])
        full = full[full["date"].astype(str).isin(keep)].reset_index(drop=True)
    return full


def drop_date_from_history(today: str) -> int:
    """从 history.csv 移除某交易日的所有行，返回移除行数。

    只用于「本日快照残缺」这一种情形：把上一次跑批可能已写进去的半份截面清掉。
    其它日期一行都不动——历史归档是累积资产，回填/清洗必须由显式的清理脚本做，
    不能藏在日常跑批里。
    """
    if not os.path.exists(HISTORY_PATH):
        return 0
    try:
        hist = pd.read_csv(HISTORY_PATH, dtype={"code": str})
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 清理 {today} 时读归档失败（{type(exc).__name__}），跳过清理")
        return 0
    if hist.empty or "date" not in hist.columns:
        return 0
    kept = hist[hist["date"].astype(str) != str(today)]
    removed = len(hist) - len(kept)
    if removed:
        kept.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
    return removed


def _snapshot_trade_date() -> str:
    """快照内容对应的交易日（YYYY-MM-DD）。

    clist 是实时盘口：收盘后到次日开盘前，内容都是最近一个已完成
    交易日的收盘态。之前直接拿日历「今天」当标签，过零点跑批会多标
    一天（2026-09-04 00:51 实测：标签 09-04、内容是 09-03 收盘）。
    锚定规则对齐 daily_breakout.get_latest_trade_date：从今天往回找
    第一个 Tushare 有日线数据的日期；token 不可用时退化为
    「CST 15:00 收盘前算昨天 + 周末回退到周五」（节假日不识别，
    与原 weekday 判断同级，由数据停滞守卫兜底）。
    """
    now = _cst_now()
    token = os.getenv("TUSHARE_TOKEN", "")
    if token:
        try:
            import tushare as ts  # noqa: PLC0415

            ts.set_token(token)
            pro = ts.pro_api()
            for i in range(10):
                d = now - datetime.timedelta(days=i)
                ds = d.strftime("%Y%m%d")
                try:
                    if not pro.daily(trade_date=ds).empty:
                        return d.strftime("%Y-%m-%d")
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    d = now if now.hour >= 15 else now - datetime.timedelta(days=1)
    while d.weekday() >= 5:  # 周六=5 周日=6，回退到周五
        d -= datetime.timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    today = _snapshot_trade_date()
    weekday = _cst_now().weekday()

    rows, total, failed_pages = fetch_snapshot()
    df_today = rows_to_df(rows) if rows else pd.DataFrame()
    if not df_today.empty:
        df_today.insert(0, "date", today)
    clist_rows = len(df_today)
    if clist_rows:
        print(f"[i] clist 取到 {clist_rows} 个概念板块（服务端 total={total}）")
    else:
        print(f"⚠️ clist 未取到任何板块（total={total}, 失败页={failed_pages}）")
    if failed_pages:
        # 必须点名是哪几页：只报「取到 400 个」看不出少的是哪一段，
        # 而不同页对应不同板块，缺哪页决定了榜单会怎么偏。
        print(f"⚠️ 最终仍失败的页：{failed_pages}")

    # 覆盖度判定：优先对服务端 total（可发现「只拿到半份」），total 未知才退回阈值。
    # 且以 MIN_COMPLETE 为下限——只按 total*0.95 判定时，若服务端 total 本身
    # 被污染成小值，残缺截面会被判成 complete。
    expected = float(total) if total else float(MIN_COMPLETE)
    incomplete = clist_rows < max(float(MIN_COMPLETE), expected * 0.95)

    skipped = False
    if weekday >= 5:
        print(f"⏭️ 今天是周{'六' if weekday == 5 else '日'}，clist 返回的是上一交易日收盘数据，不落盘")
        skipped = True
    elif clist_rows and non_trading_day(today, df_today):
        skipped = True

    # ---- 兜底：clist 取不到或残缺时，换 Tushare 同源通道（仅今天该落盘时）----
    # 只在「本来要落盘」时才兜底：周末/停滞日不落盘是数据语义决定的，
    # 与通道好坏无关，兜底去取只会拿到同一份上一交易日的数据。
    channel = "clist"
    fb_info: dict = {}
    if not skipped and incomplete:
        print(f"🔁 clist 不足（{clist_rows} < {max(int(MIN_COMPLETE), int(expected * 0.95))}），"
              f"尝试 Tushare 兜底通道")
        fb_df, fb_info = fetch_tushare_snapshot(today)
        if fb_df is not None:
            df_today = fb_df
            channel = "tushare_dc"
            expected = float(MIN_COMPLETE)   # 兜底没有 clist 那个服务端 total
            print(f"[i] 改用 Tushare 兜底数据（{len(df_today)} 个板块）")
        else:
            print(f"⚠️ Tushare 兜底也不可用：{fb_info.get('error')}；"
                  f"继续按原失败语义处理")

    if df_today.empty or len(df_today) < MIN_VALID:
        fail(f"概念板块快照取数失败（clist rows={clist_rows}, total={total}, "
             f"失败页={failed_pages}；兜底={fb_info.get('error') or '未触发'}），不动历史归档")

    incomplete = len(df_today) < max(float(MIN_COMPLETE), expected * 0.95)

    if not skipped and incomplete:
        # ★ 残缺截面拒收（2026-10-12 改）。旧行为是「照实归档」并打一条 warning，
        # 结果 2026-09-21 起连续 10 个交易日把 100~300 个板块的半份快照写进了
        # history.csv，污染了所有基于它的统计——而且因为每天都有新数据落地，
        # 从新鲜度看一切正常。残缺截面比没有数据更坏：它是一组看起来正常的百分数。
        # 若该日此前已被写过（上一次跑批残留），一并清掉，避免历史里留着半份。
        removed = drop_date_from_history(today)
        print(f"❌ 本日快照残缺：仅 {len(df_today)} 个板块"
              f"（阈值 {max(int(MIN_COMPLETE), int(expected * 0.95))}，失败页 {failed_pages}，"
              f"通道 {channel}），"
              f"拒绝写入 history.csv" + (f"；并清除该日已有 {removed} 行残留" if removed else ""))
    elif not skipped:
        full = append_history(today, df_today)
        full.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
        print(f"✅ 归档完成：{HISTORY_PATH}（{full['date'].nunique()} 个交易日，{len(full)} 行；"
              f"通道 {channel}）")

    # ---- 分析（读回刚落盘的归档，保证 analysis 与 history 一致） ----
    hist = sr.load_history(HISTORY_PATH)
    analysis = sr.compute_analysis(hist)
    analysis["generated_at"] = _cst_now().strftime("%Y-%m-%d %H:%M:%S CST")
    analysis["source"] = "eastmoney_concept_boards"
    # 本次数据走的哪条管子必须如实落盘：两条通道同源同口径，但排查问题时
    # 「今天这份数据是谁给的」是第一个要问的问题。
    analysis["data_channel"] = channel
    if channel == "tushare_dc":
        analysis["channel_note"] = (
            "东财 clist 本次取数失败或残缺（" + str(fb_info.get("error") or "行数不足")
            + "），改用 Tushare dc_index/dc_daily 重建——那是同一份东财概念板块数据")
    if skipped:
        analysis["note"] = "今日非交易日/数据停滞，未新增归档，以下为最近交易日的分析"
    # 兜底通道每天探活一次：它是「clist 挂掉时才上场」的备用管子，
    # 若只在需要它的那天才发现没权限，等于又白缺一天。
    analysis["fallback_probe"] = probe_fallback_channel(today)
    with open(ANALYSIS_PATH, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    print(f"✅ 分析落盘：{ANALYSIS_PATH}（status={analysis.get('status')}，date={analysis.get('date')}）")

    if incomplete:
        print(f"::warning::板块轮动 incomplete：仅取到 {len(df_today)} 个板块"
              f"（阈值 {max(int(MIN_COMPLETE), int(expected * 0.95))}，失败页 {failed_pages}，"
              f"通道 {channel}），"
              f"**未写入 history.csv**（残缺截面入库会静默污染后续所有统计）")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
