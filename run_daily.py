"""每日跑批编排器（工作流薄壳化的核心）。

存在的理由
----------
GitHub 的 PAT 若不带 `workflow` scope，就**改不了** `.github/workflows/*.yml`
（git push 被明确拒绝，Contents API 与 Git Data API 均返回 404，四条路全封）。
于是每次新增跑批脚本、调整步骤顺序、加一个 secret，都要人工去网页端粘贴 yml。

解法不是去要更大的权限，而是让 yml 不再承载任何会变的东西：
  - 步骤清单、顺序、依赖、入参、超时、失败语义 → 全部下沉到本文件（普通 .py，可自由推送）
  - secrets → 由 yml 一次性以 `toJSON(secrets)` 整体透传，新增 secret 无需改 yml
  - 数据新鲜度自检 → 下沉到本文件，新增产物无需改 yml
这样 yml 只剩「装环境 → 跑本文件 → 提交 data/」三件永不变的事。

失败语义
--------
原 yml 给每个跑批步骤都加了 `continue-on-error: true`——任一脚本异常都不能中断
后续步骤，否则当日全量数据都不落地。本文件必须完整复刻这一点：
  - 每个步骤独立捕获异常与非零退出码，记录后继续
  - 编排器自身**永不抛出、永远 exit 0**，保证 yml 里的提交步骤一定会执行
  - 非零退出码以 `::warning::` 注解透出，在 Actions 页面可见但不标红失败
  - 每个步骤带独立超时，避免单个脚本挂死吃掉整个 job 的时间预算

用法
----
    python run_daily.py                  # 常规增量跑批
    BACKFILL_DAYS=40 python run_daily.py # 带历史回溯（各步骤按自身能力上限截顶）
    python run_daily.py --only sentiment,digest   # 只跑指定步骤（调试用）
    python run_daily.py --list           # 只打印步骤清单，不执行

退出码：恒为 0（失败语义通过日志注解表达，不通过退出码，理由见上）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# 回溯模式：
#   none       —— 不支持回溯，忽略 BACKFILL_DAYS
#   inline     —— 带 --backfill N 跑一次即可（脚本内部会把当日增量一并做掉）
#   extra_pass —— 先带 --backfill N 跑一次补历史，再不带参数跑一次做当日增量
BACKFILL_NONE = "none"
BACKFILL_INLINE = "inline"
BACKFILL_EXTRA_PASS = "extra_pass"

# ---------------------------------------------------------------------------
# 步骤清单：顺序即依赖顺序，改这里就等于改工作流，不必再碰 yml
#
# 顺序约束（打乱会静默产出残缺数据，务必保留注释）：
#   sentiment  必须在 market_monitor 之后 —— 依赖它产出的 limit_ladder.json
#   scorecard  必须在所有榜单之后         —— 读当日 strong_stocks / breakout / strong_etfs
#   digest     必须在 sentiment+scorecard 之后 —— 读它们的产物组装文本
#
# backfill_cap 是「接口能力上限」而非偏好值：
#   sentiment 走东财涨停池按 date 取历史，实测只能回溯约 15 个交易日，更早返回 tc=0，
#   填再大也拿不到数据，故在此截顶，避免白跑几十次请求。
#   scorecard 用 tushare 重建 RPS 榜，无此限制。
# ---------------------------------------------------------------------------
STEPS: list[dict] = [
    {"key": "rps", "script": "daily_rps_pro.py", "label": "RPS 强势股",
     "backfill": BACKFILL_NONE, "timeout": 1800},
    {"key": "etf", "script": "daily_etf_pro.py", "label": "ETF 榜单",
     "backfill": BACKFILL_NONE, "timeout": 1200},
    {"key": "market_monitor", "script": "daily_market_monitor.py", "label": "全市场监控",
     "backfill": BACKFILL_NONE, "timeout": 1800},
    {"key": "speculation", "script": "daily_speculation.py", "label": "投机与套利",
     "backfill": BACKFILL_NONE, "timeout": 1200},
    {"key": "guru", "script": "daily_guru_loader.py", "label": "核心龙头",
     "backfill": BACKFILL_NONE, "timeout": 900},
    {"key": "radar", "script": "daily_radar.py", "label": "异动雷达",
     "backfill": BACKFILL_NONE, "timeout": 1200},
    {"key": "snapshot", "script": "daily_snapshot.py", "label": "行情快照",
     "backfill": BACKFILL_NONE, "timeout": 1200},
    {"key": "breakout", "script": "daily_breakout.py", "label": "阶段新高突破池",
     "backfill": BACKFILL_NONE, "timeout": 1200},
    {"key": "sentiment", "script": "daily_sentiment.py", "label": "情绪派生指标",
     "backfill": BACKFILL_INLINE, "backfill_cap": 15, "timeout": 1800},
    {"key": "scorecard", "script": "daily_scorecard.py", "label": "战绩归档与绩效",
     "backfill": BACKFILL_EXTRA_PASS, "timeout": 2400},
    {"key": "digest", "script": "daily_digest.py", "label": "收盘摘要与推送",
     "backfill": BACKFILL_NONE, "timeout": 900},
]

# 数据新鲜度自检：(标签, 单行 python 表达式脚本)。
# 下沉到此处的理由与步骤表相同——新增产物只改 .py，不必再动 yml。
FRESHNESS: list[tuple[str, str]] = [
    ("市场情绪", "import pandas as pd;d=pd.read_csv('data/market_sentiment.csv');"
                 "print(d.iloc[-1].to_dict())"),
    # 日期列名两张表不一致（RPS 是中文「更新日期」，突破池是 update_date），
    # 写死单一列名会静默显示「—」，让人误以为数据没日期戳，故各自取真实列名。
    ("RPS 强势股", "import pandas as pd;d=pd.read_csv('data/strong_stocks.csv');"
                   "print('行数', len(d), '| 更新日期', d.iloc[-1]['更新日期'])"),
    ("突破池", "import pandas as pd;d=pd.read_csv('data/breakout_stocks.csv');"
               "print('行数', len(d), '| 更新日期', d.iloc[-1]['update_date'])"),
    ("连板天梯", "import json;d=json.load(open('data/limit_ladder.json',encoding='utf-8'));"
                 "print(d['date'],'涨停',d['total_count'],'家 最高',d['max_height'],'板')"),
    ("情绪派生", "import json;d=json.load(open('data/sentiment/derived.json',encoding='utf-8'));"
                 "p=d.get('phase') or {};print(d['status'],d['date'],'归档',d['archive_days'],"
                 "'天 | 周期',p.get('phase','—'),'| 验证条件',len(d.get('verification_plan') or []),'条')"),
    ("战绩绩效", "import json;d=json.load(open('data/scorecard/performance.json',encoding='utf-8'));"
                 "a=d.get('archive') or {};print(d['status'],'| 区间',a.get('date_from','—'),'~',"
                 "a.get('date_to','—'),a.get('trade_days','—'),'个交易日 | 策略',"
                 "len(d.get('strategies') or {}),'个')"),
    ("收盘摘要", "import json;d=json.load(open('data/digest/latest.json',encoding='utf-8'));"
                 "print(d['date'],'有内容' if d['has_content'] else '无内容','| 缺失',"
                 "len(d.get('missing') or []),'项 |',d['plain'][:80])"),
]


# ---------------------------------------------------------------------------
# secrets 整体注入
# ---------------------------------------------------------------------------
def inject_secrets() -> list[str]:
    """把 yml 用 `toJSON(secrets)` 整体透传进来的 JSON 展开成环境变量。

    这是「新增 secret 不必改 yml」的关键：原先每加一个 DIGEST_xxx 都要在 yml 的
    env 块里手写一行，而 PAT 改不了 yml，等于每次都要人工介入。改为整体透传后，
    用户只需在 GitHub Secrets 页面新增条目，代码侧自动可见。

    安全：只回显键名，绝不回显值。GitHub 本身也会对日志里的 secret 值做打码，
    但不能依赖它——本函数从不把值写进任何输出。
    已存在的环境变量不覆盖（便于本地调试时用真实环境变量顶替）。
    """
    raw = os.environ.pop("ALL_SECRETS", "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::ALL_SECRETS 解析失败（{type(exc).__name__}），"
              f"请检查 yml 是否写成 toJSON(secrets)")
        return []
    if not isinstance(data, dict):
        return []
    injected = []
    for k, v in data.items():
        if k in ("github_token", "GITHUB_TOKEN"):
            continue  # 由 actions/checkout 自行处理，不必也不该二次注入
        if v is None or v == "" or os.environ.get(k):
            continue
        os.environ[k] = str(v)
        injected.append(k)
    return sorted(injected)


# ---------------------------------------------------------------------------
# 步骤执行
# ---------------------------------------------------------------------------
def run_one(script: str, args: list[str], timeout: int) -> tuple[int, float]:
    """跑一个脚本，实时透传输出，返回 (退出码, 耗时秒)。

    超时不视为致命：杀掉进程、记 124（沿用 GNU timeout 惯例）后让编排继续，
    否则一个挂死的接口就能让当日全部数据不落地。
    """
    cmd = [sys.executable, "-u", script, *args]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, timeout=timeout, check=False)
        return proc.returncode, time.time() - t0
    except subprocess.TimeoutExpired:
        print(f"::warning::{script} 超时 {timeout}s，已终止（后续步骤继续）")
        return 124, time.time() - t0
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::{script} 启动失败：{type(exc).__name__}: {exc}")
        return 125, time.time() - t0


def plan_passes(step: dict, backfill: int) -> list[list[str]]:
    """根据回溯模式决定这个步骤要跑几趟、每趟带什么参数。"""
    mode = step.get("backfill", BACKFILL_NONE)
    if backfill <= 0 or mode == BACKFILL_NONE:
        return [[]]
    cap = step.get("backfill_cap")
    n = min(backfill, cap) if cap else backfill
    if cap and backfill > cap:
        print(f"[i] {step['label']}：回溯天数 {backfill} 超出接口能力上限，截顶为 {n}")
    if mode == BACKFILL_INLINE:
        return [["--backfill", str(n)]]
    # extra_pass：先补历史，再做当日增量
    return [["--backfill", str(n)], []]


def check_freshness() -> None:
    """逐项打印产物新鲜度。任何一项读失败只警告，不影响整体。"""
    print("\n" + "=" * 60)
    print("数据新鲜度自检")
    print("=" * 60)
    for label, snippet in FRESHNESS:
        try:
            proc = subprocess.run([sys.executable, "-c", snippet], cwd=REPO_ROOT,
                                  capture_output=True, text=True, timeout=120,
                                  encoding="utf-8", errors="replace")
            out = (proc.stdout or "").strip() or (proc.stderr or "").strip().splitlines()[-1:]
            out = out if isinstance(out, str) else (out[0] if out else "无输出")
            print(f"-- {label}: {out}")
        except Exception as exc:  # noqa: BLE001
            print(f"-- {label}: 读取失败 {type(exc).__name__}")


def main() -> int:
    argv = sys.argv[1:]
    only = None
    if "--only" in argv:
        i = argv.index("--only")
        if len(argv) > i + 1:
            only = {s.strip() for s in argv[i + 1].split(",") if s.strip()}

    if "--list" in argv:
        for s in STEPS:
            cap = s.get("backfill_cap")
            print(f"{s['key']:16} {s['script']:26} {s['label']:12} "
                  f"回溯={s['backfill']}{f'(≤{cap})' if cap else ''} 超时={s['timeout']}s")
        return 0

    injected = inject_secrets()
    if injected:
        print(f"[secrets] 已注入 {len(injected)} 项：{', '.join(injected)}")
    else:
        print("[secrets] 未收到 ALL_SECRETS（本地运行或 yml 未透传），使用现有环境变量")

    raw_bf = (os.environ.get("BACKFILL_DAYS") or "0").strip()
    try:
        backfill = max(0, int(raw_bf))
    except ValueError:
        print(f"::warning::BACKFILL_DAYS='{raw_bf}' 不是整数，按 0 处理")
        backfill = 0
    print(f"[config] BACKFILL_DAYS={backfill}"
          f"{'（回溯模式）' if backfill else '（仅当日增量）'}"
          f"{f' | 仅跑 {sorted(only)}' if only else ''}")

    results: list[tuple[str, int, float]] = []
    for step in STEPS:
        if only and step["key"] not in only:
            continue
        if not os.path.isfile(os.path.join(REPO_ROOT, step["script"])):
            print(f"::warning::{step['script']} 不存在，跳过（步骤表与仓库不一致）")
            results.append((step["label"], 127, 0.0))
            continue
        for args in plan_passes(step, backfill):
            tag = f"{step['label']}{' [回溯]' if args else ''}"
            print("\n" + "=" * 60)
            print(f">>> {tag}  ({step['script']} {' '.join(args)})")
            print("=" * 60, flush=True)
            code, secs = run_one(step["script"], args, step["timeout"])
            results.append((tag, code, secs))
            if code != 0:
                # 非零不中断：这是原 yml 里 continue-on-error 的等价语义。
                # 用 ::warning:: 而非 ::error::，避免把「已知的部分缺失」标成 job 失败。
                print(f"::warning::{tag} 退出码 {code}（不阻断后续步骤）")
    return finish(results)


def finish(results: list[tuple[str, int, float]]) -> int:
    check_freshness()
    print("\n" + "=" * 60)
    print("跑批汇总")
    print("=" * 60)
    for label, code, secs in results:
        mark = "OK  " if code == 0 else f"E{code:<3}"
        print(f"[{mark}] {label:20} {secs:6.1f}s")
    bad = [r for r in results if r[1] != 0]
    print(f"\n共 {len(results)} 趟，{len(results) - len(bad)} 成功，{len(bad)} 异常")
    if bad:
        print("::warning::异常步骤：" + "、".join(f"{l}({c})" for l, c, _ in bad))
    # 恒返回 0：yml 后面还要提交 data/，编排器绝不能因部分失败而阻断落盘。
    return 0


if __name__ == "__main__":
    sys.exit(main())


