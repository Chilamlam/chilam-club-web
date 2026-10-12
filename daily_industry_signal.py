# -*- coding: utf-8 -*-
"""行业「低市值占比 + 高资金热度」信号跑批：取数 → 归档 → 算信号 → 落盘。

数据源与口径
------------
tushare `sw_daily`（申万行业日线，2021 版）**按 trade_date 取当日全量**：
一次请求返回当日约 439 个申万指数（含一/二/三级行业与风格指数），
再用 `index_classify(SW2021, L2)` 的白名单过滤出二级行业（已发布约 124 个）。

为什么是这条路径（而不是东财行业接口）：
  1. `sw_daily` **自带 float_mv（流通市值）与 amount（成交额）**，
     「市值占比」不需要再去找第三个数据源，也不需要靠换手率反推。
  2. **历史可直接回溯**，不必逐板块抓日K回填，也就不跟东财的 IP 级限流搏斗。
  3. 东财 push2 对 GitHub Actions 机房 IP 是系统性拒绝（项目前科），
     而 tushare 在 Actions 上稳定可用——本步骤要在跑批里每天跑，这点是决定性的。

单次请求拿一天全量（而非逐行业拉），因此 600 个交易日的回填只需 600 次请求
（约 5 分钟），这是 `--backfill` 能在一次跑批里做完的前提。

产物（`data/industry_signal/`，一个一个文件，失败原因各不相同，不合并）
----------------------------------------------------------------------
  industries.csv    申万二级行业白名单缓存（30 天刷新一次）
  history.csv       逐日截面归档（占比、分位、涨跌的原料，保留 600 个交易日）
  signal.json       当日结论（选出了谁 + 全量行业两个分位）
  history_hits.json 历史命中回顾（每个满窗日选出了谁、之后 T+N 涨跌如何）

后两个分开的理由：当日结论可能"今天没数据"，历史统计可能是"归档还不够长"，
合成一个文件后任一边出问题都会让另一边在页面上一起消失——而它们本该各自如实显示。

字段单位（tushare 原始 → 本项目归档）
-------------------------------------
  amount   千元 → amount_yi   亿元（÷1e5）
  float_mv 万元 → float_mv_yi 亿元（÷1e4）
  total_mv 万元 → total_mv_yi 亿元（÷1e4）
单位换算只在本文件做一次，计算层与展示层一律按亿元读，避免同一列两处换算漂移。

日期标签怎么定（这里错过一次就会给整个历史打错日期）
----------------------------------------------------
锚定规则：以 tushare 交易日历里 **不晚于「参考时点」的最后一个交易日**为当日目标。
参考时点 = 若当前北京时间 < 19:00 则取昨天，否则取今天——`sw_daily` 官方更新
时间为交易日 18:30，早于它跑批会拿到空表。跑批主时点 19:37，正常落在更新后。

**并且必须校验返回行自带的 trade_date 等于请求日期**。理由：同一批接口中
已实测到「参数被静默忽略、返回默认日期数据」的行为（本机探测 sw_daily 的
start/end 时出现过），若不校验，一次静默忽略就会把上一交易日的数据
盖上今天的日期写进归档，而所有下游都看不出异常。校验不过一律不落盘。

失败语义（与 daily_sentiment / daily_sector_rotation 同款三态）
---------------------------------------------------------------
  complete   退出码 0：当日行业数达标，已归档 + 已算信号
  incomplete 退出码 2：行业数不足/数据未更新/残缺日——**不写入历史归档**
             （宁可缺一天，也不让残缺截面污染分位：占比的分母是当日全行业之和，
              少一半行业会让每个行业的占比凭空翻倍，而结果看着仍是一组正常百分数）
  failed     退出码 3：取数失败（token 缺失、接口异常），不动归档
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

import pandas as pd

import industry_signal as isig

OUT_DIR = os.path.join("data", "industry_signal")
HISTORY_PATH = os.path.join(OUT_DIR, "history.csv")
INDUSTRIES_PATH = os.path.join(OUT_DIR, "industries.csv")
SIGNAL_PATH = os.path.join(OUT_DIR, "signal.json")
HITS_PATH = os.path.join(OUT_DIR, "history_hits.json")

# 归档保留交易日数。600 不是随手取的：历史回顾要求每个交易日都攒满
# MV_WINDOW(250) 日的分位窗口才可回算，所以「可回算天数 = 归档天数 - 249」
# 即 600 → 351 天，样本量够看统计；归档只留 400 天则只剩 151 天，偏薄。
# 再往上加要权衡仓库体积与跑批时长（每 250 天约 5 万行 CSV）。
MAX_HISTORY_DAYS = 600
INDUSTRY_CACHE_DAYS = 30     # 白名单缓存有效期（行业分类很少变）
MIN_WHITELIST = 100          # 白名单条数合理区间，超出说明接口异常
MAX_WHITELIST = 200
SECTION_RATIO = 0.90         # 当日行业数 < 白名单 × 此值 → 视为残缺，拒收
REQUEST_SLEEP = 0.25         # 回填时限速（秒），避免触发接口频率限制
UPDATE_HOUR = 19             # sw_daily 18:30 更新，19:00 前跑批按昨天算参考日
MAX_CAL_LOOKBACK = 400       # 交易日历最多往前取多少自然日


def fail(msg: str) -> None:
    print(f"[failed] {msg}")
    sys.exit(3)


def _bj_now() -> datetime.datetime:
    """北京时间。本机 TZ 环境变量会静默失效，故显式加 8 小时。"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def get_pro():
    token = (os.getenv("TUSHARE_TOKEN") or "").strip()
    if not token:
        fail("缺少 TUSHARE_TOKEN，无法取申万行业日线")
    import tushare as ts  # noqa: PLC0415

    ts.set_token(token)
    return ts.pro_api()


# ---------------------------------------------------------------------------
# 白名单（申万 2021 版二级行业 + 其所属一级行业名）
# ---------------------------------------------------------------------------
def load_industries(pro, refresh: bool = False) -> pd.DataFrame:
    """返回 code/name/l1_name 三列白名单。优先读缓存，过期或条数异常则重取。

    `is_pub == '1'` 才纳入：未发布的行业（如「林业Ⅱ」「医疗美容Ⅱ」）没有
    独立指数行情，收进来会让每日行业数永远差一截，进而触发残缺拒收。
    """
    if not refresh and os.path.exists(INDUSTRIES_PATH):
        age_days = (time.time() - os.path.getmtime(INDUSTRIES_PATH)) / 86400
        if age_days <= INDUSTRY_CACHE_DAYS:
            try:
                df = pd.read_csv(INDUSTRIES_PATH, dtype={"code": str})
                if MIN_WHITELIST <= len(df) <= MAX_WHITELIST:
                    print(f"[i] 行业白名单用缓存：{len(df)} 个"
                          f"（{age_days:.1f} 天前，上限 {INDUSTRY_CACHE_DAYS} 天）")
                    return df
                print(f"[!] 白名单缓存条数 {len(df)} 超出合理区间，重取")
            except Exception as exc:  # noqa: BLE001
                print(f"[!] 白名单缓存读取失败（{type(exc).__name__}），重取")
        else:
            print(f"[i] 白名单缓存已 {age_days:.1f} 天（上限 {INDUSTRY_CACHE_DAYS} 天），重取")

    l2 = pro.index_classify(src="SW2021", level="L2")
    l1 = pro.index_classify(src="SW2021", level="L1")
    if l2 is None or l2.empty:
        fail("index_classify 未返回二级行业列表")
    if l1 is None or l1.empty:
        fail("index_classify 未返回一级行业列表（无法标注所属大类）")

    name_map = dict(zip(l1["industry_code"].astype(str), l1["industry_name"].astype(str)))
    pub = l2[l2["is_pub"].astype(str) == "1"].copy() if "is_pub" in l2.columns else l2.copy()
    out = pd.DataFrame({
        "code": pub["index_code"].astype(str),
        "name": pub["industry_name"].astype(str),
        "l1_name": pub["parent_code"].astype(str).map(name_map),
    }).drop_duplicates(subset=["code"]).sort_values("code").reset_index(drop=True)

    if not (MIN_WHITELIST <= len(out) <= MAX_WHITELIST):
        fail(f"白名单条数 {len(out)} 超出合理区间 [{MIN_WHITELIST}, {MAX_WHITELIST}]，"
             f"不落盘缓存（避免把异常名单固化 30 天）")
    os.makedirs(OUT_DIR, exist_ok=True)
    out.to_csv(INDUSTRIES_PATH, index=False, encoding="utf-8")
    print(f"[i] 行业白名单已更新：{len(out)} 个二级行业（已发布）")
    return out


# ---------------------------------------------------------------------------
# 交易日历与取数
# ---------------------------------------------------------------------------
def _cal(pro, start: str, end: str) -> list[str]:
    df = pro.trade_cal(exchange="", start_date=start, end_date=end, is_open="1")
    if df is None or df.empty:
        return []
    return sorted(df["cal_date"].astype(str).tolist())


def target_trade_date(pro, ref: datetime.datetime | None = None) -> str:
    """当日跑批的目标交易日：不晚于参考时点的最后一个交易日。"""
    ref = ref or _bj_now()
    if ref.hour < UPDATE_HOUR:
        ref = ref - datetime.timedelta(days=1)
    start = (ref - datetime.timedelta(days=MAX_CAL_LOOKBACK)).strftime("%Y%m%d")
    dates = _cal(pro, start, ref.strftime("%Y%m%d"))
    if not dates:
        fail(f"交易日历在 {start}~{ref:%Y%m%d} 内没有任何交易日，日历接口异常")
    return dates[-1]


def fetch_day(pro, trade_date: str, whitelist: pd.DataFrame) -> tuple[pd.DataFrame | None, str]:
    """取某交易日全量截面并按白名单过滤。返回 (df, 原因)。原因非空即失败。"""
    try:
        raw = pro.sw_daily(trade_date=trade_date)
    except Exception as exc:  # noqa: BLE001
        return None, f"sw_daily({trade_date}) 抛异常：{type(exc).__name__}: {exc}"
    if raw is None or raw.empty:
        return None, f"sw_daily({trade_date}) 返回空表（数据未更新或非交易日）"

    # 参数是否被静默忽略？返回行自带的日期必须与请求日期一致。
    days = sorted({str(d) for d in raw["trade_date"].astype(str)})
    if days != [trade_date]:
        return None, (f"sw_daily({trade_date}) 返回的 trade_date 是 {days}，"
                      f"与请求日期不一致——参数可能被忽略，拒收（不落盘）")

    keep = raw[raw["ts_code"].astype(str).isin(set(whitelist["code"]))].copy()
    if keep.empty:
        return None, f"sw_daily({trade_date}) 返回 {len(raw)} 行，但白名单内 0 行（编码口径不符）"

    meta = whitelist.set_index("code")
    df = pd.DataFrame({
        "date": str(trade_date),
        "code": keep["ts_code"].astype(str),
        "name": keep["ts_code"].astype(str).map(meta["name"]),
        "l1_name": keep["ts_code"].astype(str).map(meta["l1_name"]),
        "close": pd.to_numeric(keep.get("close"), errors="coerce"),
        "pct_chg": pd.to_numeric(keep.get("pct_change"), errors="coerce"),
        "amount_yi": pd.to_numeric(keep.get("amount"), errors="coerce") / 1e5,
        "float_mv_yi": pd.to_numeric(keep.get("float_mv"), errors="coerce") / 1e4,
        "total_mv_yi": pd.to_numeric(keep.get("total_mv"), errors="coerce") / 1e4,
        "vol": pd.to_numeric(keep.get("vol"), errors="coerce"),
    })
    return df.reset_index(drop=True), ""


def section_ok(df: pd.DataFrame, whitelist_n: int) -> bool:
    return len(df) >= whitelist_n * SECTION_RATIO


# ---------------------------------------------------------------------------
# 归档
# ---------------------------------------------------------------------------
def read_history() -> pd.DataFrame:
    if not os.path.exists(HISTORY_PATH):
        return pd.DataFrame()
    try:
        return pd.read_csv(HISTORY_PATH, dtype={"code": str})
    except Exception as exc:  # noqa: BLE001
        fail(f"历史归档读取失败（{type(exc).__name__}: {exc}），拒绝覆盖写")


def merge_history(hist: pd.DataFrame, df_day: pd.DataFrame) -> pd.DataFrame:
    """同日替换（幂等）+ 追加 + 按交易日数截断。"""
    if not hist.empty and "date" in hist.columns:
        hist = hist[hist["date"].astype(str) != str(df_day["date"].iloc[0])]
    full = pd.concat([hist, df_day], ignore_index=True) if not hist.empty else df_day.copy()
    full = full.sort_values(["date", "code"]).reset_index(drop=True)
    dates = sorted(full["date"].astype(str).unique())
    if len(dates) > MAX_HISTORY_DAYS:
        keep = set(dates[-MAX_HISTORY_DAYS:])
        full = full[full["date"].astype(str).isin(keep)].reset_index(drop=True)
    return full


def drop_date(hist: pd.DataFrame, date: str) -> pd.DataFrame:
    """从归档里移除某日（用于清掉此前写入的残缺截面）。"""
    if hist.empty or "date" not in hist.columns:
        return hist
    return hist[hist["date"].astype(str) != str(date)].reset_index(drop=True)


def write_signal(hist: pd.DataFrame, as_of: str) -> dict:
    sig = isig.compute_signal(isig.load_history(HISTORY_PATH), as_of=as_of)
    sig["generated_at"] = _bj_now().strftime("%Y-%m-%d %H:%M:%S CST")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(SIGNAL_PATH, "w", encoding="utf-8") as f:
        json.dump(sig, f, ensure_ascii=False, indent=2)
    return sig


def write_history_hits() -> dict:
    """回算历史命中并落盘 history_hits.json。

    与 signal.json 分成两个文件而不是塞进一个：当日结论与历史统计的失败原因
    完全不同（前者可能"今天没数据"，后者是"归档还不够长"）。合成一个文件后，
    任一边出问题都会让另一边在页面上一起消失——而它们本该各自如实显示。

    放在跑批里算（约 45 秒 / 600 天）而不是页面里算：页面每次交互都会重跑脚本，
    放页面等于每次点勾选框都重算一遍全历史分位。
    """
    hits = isig.history_hits(isig.load_history(HISTORY_PATH))
    hits["generated_at"] = _bj_now().strftime("%Y-%m-%d %H:%M:%S CST")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(HITS_PATH, "w", encoding="utf-8") as f:
        json.dump(hits, f, ensure_ascii=False, indent=2)
    return hits


def _report_hits(hits: dict) -> None:
    if hits.get("status") != "ok":
        print(f"[i] 历史回顾：status={hits.get('status')}"
              f"（{hits.get('reason') or '—'}）")
        return
    print(f"[i] 历史回顾落盘：{hits['first_date']}~{hits['last_date']} "
          f"满窗 {hits['trade_days_scanned']} 天"
          f"（跳过窗口不足 {hits['skipped_short_window']} 天）"
          f"| 命中 {hits['total_hits']} 次 / 空命中 {hits['empty_days']} 天")
    for n in hits["forward_days"]:
        s = hits["stats"][str(n)]
        print(f"    T+{n}: 样本 {s['n']} 平均 {s['mean']}% 胜率 {s['win_rate']}%"
              f" | 同期基准 {s['mean_bench']}% 超额 {s['mean_excess']}%")


def _report(sig: dict) -> None:
    print(f"[i] 信号落盘：status={sig.get('status')} date={sig.get('date')} "
          f"行业数={sig.get('industry_count')} 命中={sig.get('hits_count')}")
    for p in sig.get("picks") or []:
        print(f"    · {p['name']}({p.get('l1_name') or '—'}) "
              f"市值占比 {p['mv_share']:.2f}% 分位 {p['mv_pct']:.1f} | "
              f"成交额占比 {p['amt_share']:.2f}% 分位 {p['amt_pct']:.1f} | "
              f"当日 {p['pct_chg'] if p['pct_chg'] is not None else '—'}")


# ---------------------------------------------------------------------------
def run_backfill(pro, whitelist: pd.DataFrame, days: int) -> tuple[int, int]:
    """回填最近 days 个交易日的截面。返回 (新增/更新天数, 失败天数)。

    已存在且截面完整的日期直接跳过：回填被打断后重跑不必从头再来，
    也让「每天跑批顺手带上 --backfill」不会变成每天重复拉 250 次请求。
    """
    end = target_trade_date(pro)
    start = (_bj_now() - datetime.timedelta(days=int(days * 1.6) + 30)).strftime("%Y%m%d")
    all_dates = _cal(pro, start, end)[-days:]
    if not all_dates:
        print("[!] 回填：交易日历为空，跳过")
        return 0, 0

    hist = read_history()
    have: set[str] = set()
    if not hist.empty and "date" in hist.columns:
        cnt = hist.groupby("date")["code"].nunique()
        have = {str(d) for d, n in cnt.items() if n >= len(whitelist) * SECTION_RATIO}
    todo = [d for d in all_dates if d not in have]
    print(f"[i] 回填：目标 {len(all_dates)} 个交易日"
          f"（{all_dates[0]}~{all_dates[-1]}），已有 {len(all_dates) - len(todo)} 天，"
          f"待取 {len(todo)} 天")

    ok = 0
    bad = 0
    for i, ds in enumerate(todo, 1):
        df, why = fetch_day(pro, ds, whitelist)
        if df is None:
            print(f"    [{i}/{len(todo)}] {ds} 跳过：{why}")
            bad += 1
        elif not section_ok(df, len(whitelist)):
            print(f"    [{i}/{len(todo)}] {ds} 残缺少于 "
                  f"{int(len(whitelist) * SECTION_RATIO)} 个行业，拒收")
            bad += 1
        else:
            hist = merge_history(hist, df)
            ok += 1
            if i % 20 == 0 or i == len(todo):
                print(f"    [{i}/{len(todo)}] 已归档 {ok} 天（最新 {ds}）")
        time.sleep(REQUEST_SLEEP)

    if ok:
        os.makedirs(OUT_DIR, exist_ok=True)
        hist.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
        print(f"[i] 回填写盘：{HISTORY_PATH}（{hist['date'].nunique()} 个交易日，{len(hist)} 行）")
    return ok, bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0,
                    help="回填最近 N 个交易日（0=仅当日增量）")
    ap.add_argument("--refresh-industries", action="store_true",
                    help="强制刷新行业白名单缓存")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    pro = get_pro()
    whitelist = load_industries(pro, refresh=args.refresh_industries)

    if args.backfill > 0:
        ok, bad = run_backfill(pro, whitelist, args.backfill)
        if ok == 0:
            print(f"[!] 回填没有新增任何交易日（失败 {bad} 天）")
            return 2
        sig = write_signal(read_history(), target_trade_date(pro))
        _report(sig)
        # 历史回顾是同一份归档的派生统计，刻意不用 try 兜住：它算不出来说明
        # 代码或归档有问题，让整步响亮地失败（页面上的日期一致性也会点名），
        # 而不是打一行容易被忽略的警告后照常返回 0。
        _report_hits(write_history_hits())
        print(f"[i] 回填完成：成功 {ok} 天，失败 {bad} 天")
        return 0

    # ---- 当日增量 ----
    ds = target_trade_date(pro)
    print(f"[i] 目标交易日：{ds}")
    df, why = fetch_day(pro, ds, whitelist)
    if df is None:
        print(f"[incomplete] {why}")
        return 2

    if not section_ok(df, len(whitelist)):
        # 拒收并清掉该日此前的残留：残缺截面绝不能留在历史里。
        hist = read_history()
        cleaned = drop_date(hist, ds)
        removed = len(hist) - len(cleaned)
        if removed > 0:
            cleaned.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
            print(f"[!] 已从归档中移除 {ds} 的 {removed} 行残缺数据")
        print(f"[incomplete] {ds} 仅 {len(df)} 个行业"
              f"（低于 {int(len(whitelist) * SECTION_RATIO)}），未写入归档")
        return 2

    hist = read_history()
    full = merge_history(hist, df)
    full.to_csv(HISTORY_PATH, index=False, encoding="utf-8")
    print(f"[i] 归档完成：{HISTORY_PATH}"
          f"（{full['date'].nunique()} 个交易日，{len(full)} 行，当日 {len(df)} 个行业）")

    sig = write_signal(full, ds)
    _report(sig)
    _report_hits(write_history_hits())
    return 0


if __name__ == "__main__":
    sys.exit(main())
