"""tools_gh_watchdog.py 的自检探针。

被测对象是**决策逻辑**，不是网络：看门狗最危险的两个失效不是「取不到数据」，
而是①拿通道故障当数据故障、去派发一次没必要的全量补跑；
②在跑批时点之前就判「数据缺失」，把白天每一次检查都变成一次空派。
两者都不会报错、不会崩，只会安静地烧 Actions 额度并让人误判「跑批坏了」。

跑法
----
    python tools_probe_gh_watchdog.py              # 正向（含一次真联网复核）
    python tools_probe_gh_watchdog.py --offline    # 只跑纯函数层，不联网
    python tools_probe_gh_watchdog.py --negative   # 反向造错：每条改动都必须被抓住

安全约束（本探针自己绝不能造成副作用）
------------------------------------
`dispatch()` 一旦被真调用就会触发一次 12~18 分钟的真跑批。所有走到派发分支的
用例都必须先把 dispatch 替换成计数器，并显式断言「不该派发的分支里它是 0 次」。
"""
from __future__ import annotations

import datetime as dt
import io
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tools_gh_watchdog as wd  # noqa: E402

CST = wd.CST
FAILED: list[str] = []
PASSED = 0


def ck(cond: bool, msg: str) -> None:
    global PASSED
    if cond:
        PASSED += 1
        print(f"  [PASS] {msg}")
    else:
        FAILED.append(msg)
        print(f"  [FAIL] {msg}")


def bad(msg: str) -> None:
    """唯一失败出口。自己 print FAIL 会被外层漏抓。"""
    FAILED.append(msg)
    print(f"  [FAIL] {msg}")


def at(y: int, m: int, d: int, hh: int, mm: int) -> dt.datetime:
    return dt.datetime(y, m, d, hh, mm, tzinfo=CST)


# ---------------------------------------------------------------- 桩与夹具
class Cap:
    """替身 dispatch：只计数，绝不真派发。"""

    def __init__(self, ret: bool = True) -> None:
        self.n = 0
        self.ret = ret

    def __call__(self, tok: str) -> bool:  # noqa: ARG002
        self.n += 1
        return self.ret


def run_main(argv, now, remote, dispatch_ret=True):
    """在受控远端下跑一次 main()，返回 (exit_code, 输出文本, dispatch 次数)。

    remote: {path: (ok, body)}；缺 key 视为取数失败（模拟通道挂）。
    """
    orig_fetch, orig_disp, orig_tok = wd.fetch_remote, wd.dispatch, wd._token
    cap = Cap(dispatch_ret)
    buf = io.StringIO()
    orig_stdout = sys.stdout
    wd._CHANNEL.update({"urllib": 0, "curl": 0, "fail": 0})
    try:
        wd.fetch_remote = lambda p: (
            (True, remote[p], "curl") if p in remote else (False, "", "stub 通道挂")
        )
        wd.dispatch = cap
        wd._token = lambda: "STUB"
        sys.stdout = buf
        code = wd.main(argv, now)
    finally:
        sys.stdout = orig_stdout
        wd.fetch_remote, wd.dispatch, wd._token = orig_fetch, orig_disp, orig_tok
    return code, buf.getvalue(), cap.n


def payload(date: str) -> dict:
    """四个产物全为同一天的正常远端。列名刻意用真实的（RPS 中文列 / 突破池英文列）。"""
    return {
        "data/limit_ladder.json": '{"date": "%s", "rows": []}' % date,
        "data/digest/latest.json": '{"date": "%s-%s-%s 22:05:08"}'
        % (date[:4], date[4:6], date[6:]),
        "data/strong_stocks.csv": "code,\u66f4\u65b0\u65e5\u671f\n600519,%s\n" % date,
        "data/breakout_stocks.csv": "code,update_date\n000001,%s\n" % date,
    }


# ---------------------------------------------------------------- 1. 纯函数层
def sec_pure() -> None:
    print("\n[1] 判据与日期归一（纯函数，不联网）")

    # 日期归一：三种真实写法必须压成同一个 YYYYMMDD，否则「格式不同」会被误判成「数据过期」。
    ck(wd._norm_date("20260903") == "20260903", "_norm_date 纯数字")
    ck(wd._norm_date("2026-09-03") == "20260903", "_norm_date 带横线")
    ck(wd._norm_date("2026-09-03 22:05:08") == "20260903", "_norm_date 带时间（只取前 8 位）")
    ck(wd._norm_date(None) == "", "_norm_date(None) 为空串而非 'None'")

    # 交易日：只判周末。周六周日必须为假，否则周末会空派补跑。
    ck(wd.is_trading_day(dt.date(2026, 9, 3)) is True, "周四是交易日")
    ck(wd.is_trading_day(dt.date(2026, 9, 5)) is False, "周六不是交易日")
    ck(wd.is_trading_day(dt.date(2026, 9, 6)) is False, "周日不是交易日")

    # 判定线必须晚于主 cron，且与 cron 常量联动（改 cron 忘改判定线要被抓住）。
    line = wd.judge_after(at(2026, 9, 3, 10, 0))
    cron = at(2026, 9, 3, *wd.MAIN_CRON_CST)
    ck(line > cron, "判定线晚于主 cron 时点")
    ck(line == cron + dt.timedelta(minutes=wd.RUNTIME_MIN + wd.JUDGE_MARGIN_MIN),
       "判定线 = 主 cron + 运行时长 + 余量（三者联动，不是硬编码）")
    ck(line.date() == dt.date(2026, 9, 3), "判定线落在当天（不跨日）")

    # 取值方式：CSV 必须取最后一行，与 run_daily 的 FRESHNESS 口径一致。
    csv_body = "code,update_date\nA,20260901\nB,20260903\n"
    ck(wd._extract_date(csv_body, "csv:update_date") == "20260903", "CSV 取最后一行")
    # 列名写错必须抛错，且必须带上「实际列名」——裸 KeyError 只告诉你键不存在，
    # 排查时分不清是「列改名了」还是「表整个空了」。
    try:
        wd._extract_date(csv_body, "csv:\u4e0d\u5b58\u5728\u7684\u5217")
        bad("CSV 缺列必须抛错")
    except KeyError as exc:
        ck("\u5b9e\u9645\u5217" in str(exc), "CSV 缺列抛错且列出实际列名（不是裸 KeyError）")
    # 中文列名（RPS 那张表）必须能取到 —— 两张表列名不同，写死单列名会静默漏判。
    ck(wd._extract_date("a,\u66f4\u65b0\u65e5\u671f\nx,20260903\n",
                        "csv:\u66f4\u65b0\u65e5\u671f") == "20260903",
       "CSV 中文列名可取（RPS 表）")

    # 判据必须逐产物覆盖：四个产物一个都不能少（2026-09-01 漏判 RPS 的教训）。
    ck(len(wd.PROBES) == 4, "PROBES 覆盖 4 个产物")
    ck({p[1] for p in wd.PROBES} == {
        "data/limit_ladder.json", "data/digest/latest.json",
        "data/strong_stocks.csv", "data/breakout_stocks.csv"}, "PROBES 路径与跑批产物一致")


# ------------------------------------------------- 2. verdict 三态（核心判据）
def sec_verdict() -> None:
    print("\n[2] check_freshness 必须给出三态，而不是 True/False")
    orig = wd.fetch_remote
    try:
        # fresh：四个全今日
        rem = payload("20260903")
        wd.fetch_remote = lambda p: (True, rem[p], "curl")
        v, lines = wd.check_freshness("20260903")
        ck(v == "fresh", "四产物全今日 -> fresh")
        ck(len(lines) == 4, "元断言：明细逐产物 4 行（确实每个都查了）")

        # stale：三个今日、一个落后（2026-09-01 真实事故形状）
        rem2 = payload("20260903")
        rem2["data/strong_stocks.csv"] = "code,\u66f4\u65b0\u65e5\u671f\nx,20260902\n"
        wd.fetch_remote = lambda p: (True, rem2[p], "curl")
        v, lines = wd.check_freshness("20260903")
        ck(v == "stale", "单个产物落后 -> stale（不能被相邻新产物掩盖）")
        ck(any("20260902" in ln for ln in lines), "明细里点名了落后的日期")

        # blind：一个都取不到 —— 这是本次改动的核心，绝不能变成 stale
        wd.fetch_remote = lambda p: (False, "", "通道挂")
        v, lines = wd.check_freshness("20260903")
        ck(v == "blind", "四产物全取不到 -> blind（不是 stale）")
        ck(len(lines) == 4, "元断言：blind 时也逐产物留痕")

        # 部分取到 + 取到的那个是旧的 -> stale（有真实读数支撑）
        def half(p):
            if p == "data/limit_ladder.json":
                return True, '{"date":"20260902"}', "curl"
            return False, "", "通道挂"

        wd.fetch_remote = half
        v, _ = wd.check_freshness("20260903")
        ck(v == "stale", "部分取到且读数落后 -> stale（有真实读数才敢说缺失）")

        # 部分取到 + 取到的那个是今天的 -> 仍是 stale（其余没验到不能算 fresh）
        def half2(p):
            if p == "data/limit_ladder.json":
                return True, '{"date":"20260903"}', "curl"
            return False, "", "通道挂"

        wd.fetch_remote = half2
        v, _ = wd.check_freshness("20260903")
        ck(v == "stale", "只验到一个且为今日也不能报 fresh（其余三个未验证）")

        # 取到了但内容解析失败 -> stale（文件在、内容不对，是真问题）
        rem3 = payload("20260903")
        rem3["data/strong_stocks.csv"] = "code,wrong_col\nx,20260903\n"
        wd.fetch_remote = lambda p: (True, rem3[p], "curl")
        v, lines = wd.check_freshness("20260903")
        ck(v == "stale", "列名变更（解析失败）-> stale 而非 blind")
        ck(any("解析失败" in ln for ln in lines), "解析失败在明细中明说")
    finally:
        wd.fetch_remote = orig


# ------------------------------------------- 3. 决策表：什么情况才允许真派发
def sec_decide() -> None:
    print("\n[3] main() 决策表：退出码 + 是否派发（派发一次 = 12~18 分钟真跑批）")
    day = payload("20260903")
    old = payload("20260902")

    # 收盘后（21:00 > 判定线 20:30）
    code, out, n = run_main([], at(2026, 9, 3, 21, 0), day)
    ck((code, n) == (0, 0), "21:00 数据已就位 -> exit 0 且不派发")
    ck("已就位" in out, "输出说明「已就位」")

    code, out, n = run_main([], at(2026, 9, 3, 21, 0), old)
    ck((code, n) == (0, 1), "21:00 数据是昨天的 -> 派发 1 次、派发成功 exit 0")

    code, out, n = run_main(["--check"], at(2026, 9, 3, 21, 0), old)
    ck((code, n) == (2, 0), "--check 数据缺失 -> exit 2 且绝不派发")

    # 通道全灭：这是旧版会误派发的场景
    code, out, n = run_main([], at(2026, 9, 3, 21, 0), {})
    ck((code, n) == (9, 0), "通道全灭 -> exit 9 且绝不派发（不拿通道故障当数据故障）")
    ck("什么都没验到" in out, "输出明说「什么都没验到」，而非「数据缺失」")

    # 时间窗守卫：早于判定线，缺数据是正常态
    code, out, n = run_main([], at(2026, 9, 3, 10, 7), old)
    ck((code, n) == (1, 0), "10:07（早于判定线）数据是昨天的 -> exit 1 且不派发")
    ck("还早" in out, "输出说明「现在还早」")
    code, out, n = run_main(["--check"], at(2026, 9, 3, 10, 7), old)
    ck((code, n) == (1, 0), "--check 在判定线前也返回 1（不误报缺失）")
    # 元断言：早退分支必须真的没查远端，否则 4 次网络请求白花
    ck("远端数据新鲜度" not in out, "元断言：判定线前直接早退，未发起远端检查")

    # 边界：判定线当刻算「已过」
    line = wd.judge_after(at(2026, 9, 3, 12, 0))
    code, _, n = run_main(["--check"], line, old)
    ck((code, n) == (2, 0), "判定线当刻即开始判定（>= 而非 >）")
    code, _, n = run_main(["--check"], line - dt.timedelta(minutes=1), old)
    ck((code, n) == (1, 0), "判定线前一分钟仍算「还早」")

    # 周末：不判数据、不派发
    code, out, n = run_main([], at(2026, 9, 5, 21, 0), old)
    ck((code, n) == (1, 0), "周六 -> exit 1 且不派发")
    ck("非交易日" in out, "输出说明非交易日")

    # --force：跳过交易日与时间窗守卫，无条件派发
    code, out, n = run_main(["--force"], at(2026, 9, 5, 10, 0), day)
    ck((code, n) == (0, 1), "--force 在周六早上、数据已就位时也派发")

    # 派发失败必须如实报 2（恒 return 0 = 监控失明）
    code, out, n = run_main([], at(2026, 9, 3, 21, 0), old, dispatch_ret=False)
    ck((code, n) == (2, 1), "派发失败 -> exit 2（退出码如实）")
    ck("需人工介入" in out, "派发失败提示需人工介入")


# ------------------------------------------------------ 4. 真联网：通道可用性
def sec_live() -> None:
    print("\n[4] 真联网：主通道必须能取到远端产物（不联网就无法证明 blind 是真 blind）")
    path = wd.PROBES[0][1]
    ok, body, via = wd.fetch_remote(path)
    if not ok:
        bad(f"两条通道都取不到 {path}（{via}）—— 本次无法证明取数可用")
        return
    ck(len(body) > 20, f"取到 {path}（通道={via}，{len(body)} 字节）")
    d = wd._extract_date(body, wd.PROBES[0][2])
    ck(re.fullmatch(r"\d{8}", d) is not None, f"远端 date 可解析 = {d}")

    # 带外参照：curl 单独再取一次，两次读数必须一致。
    # 参照物必须走独立通道，否则两边一起错、对比恒等 = 假断言。
    st, body2 = wd._get_via_curl(f"{wd.RAW}/{path}")
    if st != 200:
        bad("带外 curl 复核失败 —— 本次无法交叉验证读数")
        return
    ck(wd._extract_date(body2, wd.PROBES[0][2]) == d, "带外 curl 复核读数一致")

    # 通道计数器必须真的动了，否则 fetch_remote 被谁 stub 掉了都不知道。
    ck(wd._CHANNEL["curl"] + wd._CHANNEL["urllib"] > 0, "元断言：通道计数器已累加（确实走了网络）")

    # 不存在的路径必须判失败，而不是把 404 页面当数据。
    ok3, _, _ = wd.fetch_remote("data/__definitely_not_exist__.json")
    ck(ok3 is False, "不存在的远端路径判失败（404 不当数据）")


# -------------------------------------------------- 5. 反向验证：造错必须被抓住
# 每条 = (目标文件, 唯一锚点, 替换成什么, 期望在探针输出里出现的关键字)
# 锚点必须跨行写真换行：若单行字面量与本文件内容重合，会在本表里也算一次
# （自指陷阱 -> count=2 -> 判「锚点不唯一」而不是造错未生效）。
MUTATIONS: list[tuple[str, str, str, str]] = [
    # ① 把 blind 退化成 stale —— 旧版的真实缺陷，通道一抖就派发
    ("tools_gh_watchdog.py",
     'if not got_any:\n        return "blind", lines',
     'if not got_any:\n        return "stale", lines',
     "blind"),
    # ② 去掉时间窗守卫 —— 白天每次检查都会空派补跑
    ("tools_gh_watchdog.py",
     "if not force and now < line:",
     "if False:",
     "还早"),
    # ③ 判定线不再联动 cron（硬编码写死）。
    #    刻意用 90 而不是 53：53 恰好等于 RUNTIME_MIN+JUDGE_MARGIN_MIN，
    #    造出来的错与原值等价 -> 任何断言都抓不住 -> 这条反向验证等于没验。
    ("tools_gh_watchdog.py",
     "return base + dt.timedelta(minutes=RUNTIME_MIN + JUDGE_MARGIN_MIN)",
     "return base + dt.timedelta(minutes=90)",
     "联动"),
    # ④ 只要有一个产物是新的就算 fresh —— 2026-09-01 漏判 RPS 的形状
    ("tools_gh_watchdog.py",
     "all_fresh = all_fresh and fresh",
     "all_fresh = all_fresh or fresh",
     "落后"),
    # ⑤ CSV 取第一行而非最后一行
    ("tools_gh_watchdog.py",
     "return _norm_date(rows[-1][key])",
     "return _norm_date(rows[0][key])",
     "最后一行"),
    # ⑥ --check 也允许派发（探针必须抓住「不该派发却派发了」）
    ("tools_gh_watchdog.py",
     'if check_only:\n        print("[结论] 今日数据确实缺失（--check 模式，不补跑）")\n        return 2',
     'if check_only and False:\n        return 2',
     "绝不派发"),
    # ⑦ 缺列时静默返回空串（让「列改名」伪装成「数据过期」）。
    #    注意期望关键字取 bad() 那条消息，而不是 PASS 文案里的「实际列」——
    #    造错后走的是 bad 分支，PASS 文案根本不会打印，写它就变成假断言。
    ("tools_gh_watchdog.py",
     'raise KeyError(f"CSV 缺列 {key}（实际列：',
     'return ""  # noqa\n            raise KeyError(f"CSV 缺列 {key}（实际列：',
     "CSV 缺列必须抛错"),
    # ⑧ 周末守卫失效
    ("tools_gh_watchdog.py",
     "return d.weekday() < 5",
     "return True",
     "非交易日"),
]


def run_negative() -> int:
    """逐条造错跑子进程。断言：探针必须 exit≠0 且输出命中期望关键字。

    造错未生效（锚点 0 次或多次）本身就是失败——那意味着这条反向验证什么都没验。
    """
    root = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable
    print("=" * 68)
    print(f"反向验证：{len(MUTATIONS)} 条造错，每条都必须被正向探针抓住")
    print("=" * 68)
    caught = 0
    missed: list[str] = []

    for i, (fname, anchor, repl, kw) in enumerate(MUTATIONS, 1):
        target = os.path.join(root, fname)
        with open(target, "r", encoding="utf-8") as f:
            src = f.read()
        cnt = src.count(anchor)
        if cnt != 1:
            missed.append(f"#{i} 造错未生效：锚点出现 {cnt} 次（需恰好 1 次）| {anchor[:40]!r}")
            print(f"  [MUT-FAIL] #{i} 锚点 {cnt} 次，跳过 -> {kw}")
            continue
        bak = src
        try:
            with open(target, "w", encoding="utf-8") as f:
                f.write(src.replace(anchor, repl, 1))
            # 造错后跑正向探针（--offline：反向验证只验判据，不受网络抖动干扰）
            p = subprocess.run(
                [py, os.path.join(root, "tools_probe_gh_watchdog.py"), "--offline"],
                capture_output=True, text=True, cwd=root, timeout=300,
            )
            out = (p.stdout or "") + (p.stderr or "")
            # 关键字必须出现在**失败行**里。只判 `kw in out` 会被 PASS 文案喂饱：
            # ck() 无论通过与否都打印同一句 msg，于是「另一条无关断言挂了」+
            # 「关键字出现在某条 PASS 行」也会被算成 caught —— 那是假断言。
            fail_lines = "\n".join(
                ln for ln in out.splitlines()
                if "[FAIL]" in ln or ln.strip().startswith("- ")
            )
            hit = p.returncode != 0 and kw in fail_lines
            if hit:
                caught += 1
                print(f"  [CAUGHT] #{i} {kw}（exit={p.returncode}）")
            else:
                missed.append(
                    f"#{i} 造错未被抓住：exit={p.returncode} "
                    f"关键字命中失败行={kw in fail_lines} | {kw}")
                print(f"  [MISSED] #{i} {kw}（exit={p.returncode}，"
                      f"命中失败行={kw in fail_lines}）")
        finally:
            with open(target, "w", encoding="utf-8") as f:
                f.write(bak)

    print("\n" + "=" * 68)
    # 元断言：一条都没跑 = 什么都没验到，必须判失败
    if caught + len(missed) != len(MUTATIONS):
        print("FAIL 元断言：执行条数与用例数不符，本次反向验证不可信")
        return 1
    if missed:
        print(f"FAIL 抓住 {caught}/{len(MUTATIONS)}，以下未被抓住：")
        for m in missed:
            print(f"  - {m}")
        return 1
    print(f"PASS 全部 {caught}/{len(MUTATIONS)} 条造错均被抓住")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--negative" in argv:
        return run_negative()

    print("=" * 68)
    print("tools_gh_watchdog.py 自检")
    print("=" * 68)
    sec_pure()
    sec_verdict()
    sec_decide()
    if "--offline" in argv:
        print("\n[4] 跳过真联网（--offline）")
    else:
        sec_live()

    print("\n" + "=" * 68)
    if FAILED:
        print(f"FAIL {len(FAILED)} / PASS {PASSED}")
        for m in FAILED:
            print(f"  - {m}")
        return 1
    print(f"PASS {PASSED}/{PASSED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
