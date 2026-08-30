"""run_daily.py 编排器自检（纯离线，不联网、不跑真实跑批脚本）。

自检的重点不是「代码能跑」，而是**编排语义不能悄悄退化**：
  1. 步骤表必须与仓库里真实存在的脚本一一对应（防止改名后静默跳过）
  2. 顺序依赖必须成立（sentiment 在 market_monitor 后、scorecard 在榜单后、digest 最后）
  3. 必须完整覆盖原 yml 的 11 个跑批步骤（防止薄壳化时漏搬）
  4. 回溯参数必须按接口能力截顶（sentiment ≤15），extra_pass 必须跑两趟
  5. secrets 整体注入必须只回显键名、绝不回显值
  6. 编排器必须永远 exit 0（否则 yml 的提交步骤不会执行，当日数据不落盘）

用法：python tools_probe_run_daily.py
退出码：0 全通过 / 1 有失败项
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FAILS: list[str] = []
PASSES = 0


def ck(cond: bool, desc: str) -> None:
    global PASSES
    if cond:
        PASSES += 1
    else:
        FAILS.append(desc)
        print(f"[FAIL] {desc}")


def main() -> int:
    sys.path.insert(0, ROOT)
    import run_daily as rd

    # ---- 1. 步骤表与真实文件对齐 ----
    keys = [s["key"] for s in rd.STEPS]
    ck(len(keys) == len(set(keys)), "步骤 key 不得重复")
    for s in rd.STEPS:
        ck(os.path.isfile(os.path.join(ROOT, s["script"])),
           f"步骤 {s['key']} 指向的脚本必须存在：{s['script']}")
        ck(isinstance(s.get("timeout"), int) and s["timeout"] > 0,
           f"步骤 {s['key']} 必须有正整数超时（防单脚本挂死吃掉整个 job）")
        ck(s.get("backfill") in (rd.BACKFILL_NONE, rd.BACKFILL_INLINE, rd.BACKFILL_EXTRA_PASS),
           f"步骤 {s['key']} 的 backfill 模式必须是三种合法值之一")

    # 反向：仓库里所有 daily_*.py 都应被编排，否则等于新增了跑批却忘了挂上去
    on_disk = {f for f in os.listdir(ROOT) if f.startswith("daily_") and f.endswith(".py")}
    in_table = {s["script"] for s in rd.STEPS}
    orphans = on_disk - in_table
    ck(not orphans, f"仓库里的 daily_*.py 必须全部进入步骤表，未挂载：{sorted(orphans)}")

    # ---- 2. 顺序依赖 ----
    idx = {s["key"]: i for i, s in enumerate(rd.STEPS)}
    ck(idx["sentiment"] > idx["market_monitor"],
       "sentiment 必须在 market_monitor 之后（依赖 limit_ladder.json）")
    for k in ("rps", "etf", "breakout"):
        ck(idx["scorecard"] > idx[k], f"scorecard 必须在 {k} 之后（读当日榜单）")
    ck(idx["digest"] > idx["sentiment"] and idx["digest"] > idx["scorecard"],
       "digest 必须在 sentiment 与 scorecard 之后（读它们的产物）")
    ck(idx["digest"] == len(rd.STEPS) - 1, "digest 应是最后一步")

    # ---- 3. 覆盖原 yml 的全部跑批 ----
    for k in ("rps", "etf", "market_monitor", "speculation", "guru", "radar",
              "snapshot", "breakout", "sentiment", "scorecard", "digest"):
        ck(k in idx, f"原 yml 的步骤 {k} 必须在薄壳化后仍被编排（防漏搬）")

    # ---- 4. 回溯参数规划 ----
    sent = next(s for s in rd.STEPS if s["key"] == "sentiment")
    score = next(s for s in rd.STEPS if s["key"] == "scorecard")
    rps = next(s for s in rd.STEPS if s["key"] == "rps")

    ck(rd.plan_passes(sent, 0) == [[]], "backfill=0 时 inline 步骤只跑一趟且不带参数")
    ck(rd.plan_passes(sent, 40) == [["--backfill", "15"]],
       "sentiment 回溯必须按接口能力截顶到 15（东财按 date 只能回溯约 15 个交易日）")
    ck(rd.plan_passes(sent, 7) == [["--backfill", "7"]], "未超上限时原样传递")
    ck(rd.plan_passes(score, 40) == [["--backfill", "40"], []],
       "scorecard 是 extra_pass：先回溯一趟再做当日增量，共两趟")
    ck(rd.plan_passes(score, 0) == [[]], "scorecard 无回溯时只跑当日增量")
    ck(rd.plan_passes(rps, 40) == [[]], "不支持回溯的步骤必须忽略 BACKFILL_DAYS")

    # ---- 5. secrets 注入 ----
    os.environ["ALL_SECRETS"] = json.dumps({
        "TUSHARE_TOKEN": "tok-abc", "DIGEST_SERVERCHAN_KEY": "SCT-xyz",
        "EMPTY_ONE": "", "GITHUB_TOKEN": "ghs-should-skip",
    })
    injected = rd.inject_secrets()
    ck("TUSHARE_TOKEN" in injected and os.environ["TUSHARE_TOKEN"] == "tok-abc",
       "ALL_SECRETS 里的条目必须展开成环境变量")
    ck("DIGEST_SERVERCHAN_KEY" in injected,
       "新增 secret 无需改 yml 即可被注入（这正是薄壳化的目的）")
    ck("EMPTY_ONE" not in injected, "空值 secret 不注入（未配置的渠道不应被误认为已配置）")
    ck("GITHUB_TOKEN" not in injected, "GITHUB_TOKEN 由 checkout 处理，不二次注入")
    ck("ALL_SECRETS" not in os.environ,
       "ALL_SECRETS 必须从环境里 pop 掉，避免子进程再拿到明文全集")
    for k in ("TUSHARE_TOKEN", "DIGEST_SERVERCHAN_KEY"):
        os.environ.pop(k, None)

    ck(rd.inject_secrets() == [], "未提供 ALL_SECRETS 时应安静返回空（本地调试路径）")
    os.environ["ALL_SECRETS"] = "{not-json"
    ck(rd.inject_secrets() == [], "ALL_SECRETS 非法 JSON 时不得抛出，只警告")
    os.environ.pop("ALL_SECRETS", None)

    # ---- 6. 绝不回显 secret 值 ----
    src = io.open(os.path.join(ROOT, "run_daily.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "inject_secrets")
    # 扫 inject_secrets 内所有 print 的实参，凡出现值变量（v / data[...]）即视为泄露。
    # 只扫这个函数是因为它是唯一持有明文值的地方。
    leaked = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            dump = ast.dump(node)
            for bad in ("id='v'", "attr='values'", "id='data'"):
                if bad in dump:
                    leaked.append(bad)
    ck(not leaked, f"inject_secrets 的 print 不得引用 secret 值变量，命中：{leaked}")

    # ---- 7. 恒返回 0 ----
    ck(rd.finish([("A", 0, 1.0), ("B", 3, 2.0), ("C", 124, 3.0)]) == 0,
       "编排器即使有步骤失败也必须返回 0（否则 yml 的 data/ 提交步骤不会执行）")

    # ---- 8. --list 与 --only 不触发真实跑批 ----
    p = subprocess.run([sys.executable, "run_daily.py", "--list"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    ck(p.returncode == 0 and "daily_digest.py" in (p.stdout or ""),
       "--list 应打印完整步骤表并正常退出")

    print(f"\n通过 {PASSES} 项，失败 {len(FAILS)} 项")
    if FAILS:
        for f in FAILS:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
