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
  complete   退出码 0：全量取到（≥400 个板块），归档+分析完整落盘
  incomplete 退出码 2：只取到部分（50~399），照实归档，分析标注 partial
  failed     退出码 3：取不到有效数据，不动历史归档，绝不用旧值顶替当日

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}


def fail(msg: str) -> None:
    print(f"❌ [failed] {msg}")
    sys.exit(3)


def _cst_now() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def fetch_page(pn: int, retries: int = len(EM_HOSTS)) -> dict | None:
    """拉一页 clist。每次重试换一个域名，最终失败返回 None。

    为什么换域名：502 是东财对机房 IP 的边缘级拒绝，同一域名重试无意义；
    轮换分片/延迟域名才可能落到不同边缘。"""
    for attempt in range(retries):
        host = EM_HOSTS[attempt % len(EM_HOSTS)]
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


def fetch_snapshot(max_pages: int = 8) -> tuple[list[dict], int | None]:
    """翻页拉全量概念板块快照，返回 (行列表, 服务端 total)。

    单页失败不放弃后续页（TLS 断连往往是瞬时的且只咬某几页），
    连续 2 页失败才停；取到多少算多少，覆盖度由调用方对 total 判定。
    """
    rows: list[dict] = []
    total = None
    consecutive_fail = 0
    for pn in range(1, max_pages + 1):
        data = fetch_page(pn)
        if data is None:
            consecutive_fail += 1
            if consecutive_fail >= 2:
                break
            continue
        consecutive_fail = 0
        total = data.get("total", total)
        diff = data.get("diff") or []
        rows.extend(diff)
        if not diff:
            break
        if total is not None and len(rows) >= int(total):
            break
        time.sleep(0.4)
    return rows, total


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


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    today = _cst_now().strftime("%Y-%m-%d")
    weekday = _cst_now().weekday()

    rows, total = fetch_snapshot()
    if not rows or len(rows) < MIN_VALID:
        fail(f"概念板块快照取数失败（rows={len(rows)}, total={total}），不动历史归档")

    df_today = rows_to_df(rows)
    df_today.insert(0, "date", today)
    print(f"[i] 取到 {len(df_today)} 个概念板块（服务端 total={total}）")

    # 覆盖度判定：优先对服务端 total（可发现「只拿到半份」），total 未知才退回阈值
    if total:
        incomplete = len(df_today) < int(total) * 0.95
    else:
        incomplete = len(df_today) < MIN_COMPLETE

    skipped = False
    if weekday >= 5:
        print(f"⏭️ 今天是周{'六' if weekday == 5 else '日'}，clist 返回的是上一交易日收盘数据，不落盘")
        skipped = True
    elif non_trading_day(today, df_today):
        skipped = True

    if not skipped:
        full = append_history(today, df_today)
        full.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
        print(f"✅ 归档完成：{HISTORY_PATH}（{full['date'].nunique()} 个交易日，{len(full)} 行）")

    # ---- 分析（读回刚落盘的归档，保证 analysis 与 history 一致） ----
    hist = sr.load_history(HISTORY_PATH)
    analysis = sr.compute_analysis(hist)
    analysis["generated_at"] = _cst_now().strftime("%Y-%m-%d %H:%M:%S CST")
    analysis["source"] = "eastmoney_concept_boards"
    if skipped:
        analysis["note"] = "今日非交易日/数据停滞，未新增归档，以下为最近交易日的分析"
    with open(ANALYSIS_PATH, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    print(f"✅ 分析落盘：{ANALYSIS_PATH}（status={analysis.get('status')}，date={analysis.get('date')}）")

    if incomplete:
        print(f"::warning::板块轮动 incomplete：仅取到 {len(df_today)} 个板块（< {MIN_COMPLETE}），照实归档")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
