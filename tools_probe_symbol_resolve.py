# -*- coding: utf-8 -*-
"""symbol_resolve.py 自检：沪深北撞号消歧 + 反向验证（造错必须被抓住）。

为什么必须有这个探针：撞号是**静默取错**——图能画、涨跌幅有数字，只是全是
另一只标的的。线上实测过的三个错例（2026-09-03）：
    000905 → sz000905「厦门港务」8.90   （想看的是中证500 7729）
    000016 → sz000016「*ST康佳A」2.46   （想看的是上证50）
    000300 → sz000300 腾讯根本不返回该行 → 静默无数据

判据分两层，缺一不可：
  纯函数层  rank_key/prefixes_for/guess_kline —— 不联网，任何时点可复现
  联网层    resolve/resolve_candidates —— 真打 qt.gtimg.cn，验「此刻真的对」

反向验证（`--negative`）：改源码字符串造错，断言必须 FAIL。锚点先验唯一，
失配即报「造错未生效」而不是当成通过。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

FAILS: list = []
CHECKED = 0


def ok(msg: str) -> None:
    global CHECKED
    CHECKED += 1
    print(f"  [OK]   {msg}")


def bad(msg: str) -> None:
    """失败唯一出口。自己 print FAIL 会让主流程漏抓。"""
    global CHECKED
    CHECKED += 1
    FAILS.append(msg)
    print(f"  [FAIL] {msg}")


# ================= 第 1 层：纯函数（不联网） =================

def check_pure() -> None:
    import symbol_resolve as S
    print("[1] 纯函数层：前缀先验 / 排序键 / 类型判定")

    # 撞号代码必须**同时**探沪深，只探一个市场就是漏检的根源
    for code6 in ("000905", "000300", "000016", "000001"):
        order = S.prefixes_for(code6)
        if "sh" in order and "sz" in order and order[0] == "sh":
            ok(f"prefixes_for({code6}) = {order}，沪市优先且不漏深市")
        else:
            bad(f"prefixes_for({code6}) = {order}，撞号代码必须沪深都探且沪市在前")

    if S.prefixes_for("920982")[0] == "bj":
        ok("prefixes_for(920982) 首选 bj")
    else:
        bad(f"prefixes_for(920982) = {S.prefixes_for('920982')}，北交所号段必须首选 bj")

    # rank_key 判据顺序。两个方向都要验，否则「顺序反了」验不出来。
    # ① 白名单宽基指数优先级最高：即使接口那一刻对指数返回 amount=0/high=0
    #    （指数与个股是不同后台源，盘前重置时刻可能不同步），也不能滑向同号个股。
    idx_pre = {"tx_code": "sh000905", "kind": "指数", "alive": True, "amount": 0.0,
               "high": 7700.0, "low": 7680.0}
    stk_live = {"tx_code": "sz000905", "kind": "个股", "alive": True, "amount": 8172.0}
    if S.rank_key(idx_pre) < S.rank_key(stk_live):
        ok("rank_key: 白名单宽基指数优先于同号深市个股")
    else:
        bad("rank_key: 沪市宽基指数没能排在同号深市个股之前")

    idx_dead = dict(idx_pre, alive=False, high=0.0, low=0.0)
    if S.rank_key(idx_dead) < S.rank_key(stk_live):
        ok("rank_key: 白名单宽基即使当刻无成交，也不让位给同号个股")
    else:
        bad("rank_key: 指数源 amount 归零的瞬间会静默滑向同号个股（000905→厦门港务）")

    # 白名单身份不能依赖 kind：PE 段偶发为空会让 sh000905 被判成「债券 / 其他」，
    # 若身份跟着 kind 一起丢，那一刻就退回原坑。
    idx_flaky = dict(idx_pre, kind="债券 / 其他")
    if S.rank_key(idx_flaky) < S.rank_key(stk_live):
        ok("rank_key: 宽基身份认代码白名单，不被抖动的 kind 推断否决")
    else:
        bad("rank_key: kind 判定抖动时宽基指数会掉出优先位")

    # ② 非白名单撞号仍以活跃度为第一判据。
    #    **两只的 amount 必须都为 0**：否则末位的「成交额倒序」自己就把顺序排对了，
    #    活跃度判据形同虚设 —— 2026-09-03 反验实测，给 live 一个大成交额后，把
    #    alive 项固定成 0 造错，断言照样通过（被无关数据喂饱的假断言）。
    #    amount=0 但在交易是真实存在的：部分指数不填成交额段，只有当日高低价。
    no_amt_live = {"tx_code": "sh600980", "kind": "个股", "alive": True,
                   "amount": 0.0, "high": 9.9, "low": 9.5}
    no_amt_dead = {"tx_code": "sz600980", "kind": "个股", "alive": False,
                   "amount": 0.0, "high": 0.0, "low": 0.0}
    if S.rank_key(no_amt_live) < S.rank_key(no_amt_dead):
        ok("rank_key: 非白名单撞号时，活跃度是第一判据（成交额打平也能分出胜负）")
    else:
        bad("rank_key: 非白名单撞号没有按活跃度取舍（活跃度是第一判据这条失效）")

    # 非宽基的沪市指数不能压过个股（否则 sh000998 之类会盖掉真正想看的股票）
    minor_idx = {"tx_code": "sh000998", "kind": "指数", "alive": True, "amount": 100.0}
    if S.rank_key(stk_live) < S.rank_key(minor_idx):
        ok("rank_key: 非宽基沪市指数不压过同号个股")
    else:
        bad("rank_key: 非宽基指数被错误地优先了")

    # 撞号判据只看候选个数，不掺 alive。**这条必须用纯函数验**：盘中两只都活跃，
    # 掺了 alive 也照样返回 True，只在盘前才暴露 —— 靠联网用例验它，等于让断言
    # 的有效性随一天的时间漂移（2026-09-03 09:15 实测 alive 全 False）。
    pre = [{"tx_code": "sh000905", "name": "中证500", "kind": "指数", "alive": False},
           {"tx_code": "sz000905", "name": "厦门港务", "kind": "个股", "alive": False}]
    if S.is_ambiguous(pre) and S.describe_alternatives(pre) == ["深市个股·厦门港务"]:
        ok("is_ambiguous: 盘前(alive 全 False)仍如实上报撞号，并列出另一只")
    else:
        bad("is_ambiguous: 盘前不报撞号 —— 判据掺了 alive，"
            "而撞号是结构事实、与当刻有没有成交无关")

    if not S.is_ambiguous(pre[:1]):
        ok("is_ambiguous: 只有一个候选时不报撞号（避免给每只都加提示）")
    else:
        bad("is_ambiguous: 无歧义代码被误报成撞号")

    # 北交所 K 线端点：老 fqkline 对 bj 只返回 1 根，静默少数据
    u_bj, u_sh = S.kline_url("bj920982"), S.kline_url("sh600519")
    if "newfqkline" in u_bj and "newfqkline" not in u_sh and "fqkline" in u_sh:
        ok("kline_url: 北交所走 newfqkline，沪深走 fqkline")
    else:
        bad(f"kline_url 端点选错: bj={u_bj} sh={u_sh}")

    # limit 上限 800：801 静默退回 640 根
    if ",800,qfq" in S.kline_url("sh600519", limit=1000):
        ok("kline_url: limit 被夹到 800（>=801 会静默退回 640 根）")
    else:
        bad(f"kline_url limit 未夹到 800: {S.kline_url('sh600519', limit=1000)}")

    # 雪球链接必须用解析后的前缀，不能再按 6/5/9 猜
    if S.xueqiu_url("sh000905").endswith("/SH000905"):
        ok("xueqiu_url 用解析后的市场前缀")
    else:
        bad(f"xueqiu_url 前缀错: {S.xueqiu_url('sh000905')}")

    # 港美股不给类型（段位语义不同，硬判会把 hkHSI 误判成基金）。
    # 注意指数的 PE 段是有值的（实测 sh000905 pe≈15），pe=0 且 52周高=-1 走的是
    # 「债券 / 其他」分支 —— 断言必须用真实段位组合，否则验的是不存在的场景。
    cases = [("15.2", "-1", "hkHSI", ""),          # 港股：宁可不标类型
             ("15.2", "-1", "sh000905", "指数"),    # 有 PE + 52周高为 -1
             ("0", "-1", "sh019547", "债券 / 其他"),  # 无 PE + 52周高为 -1
             ("28.5", "1380.0", "sh600519", "个股"),
             ("0", "3.21", "sh510300", "基金 / ETF")]
    wrong = [(t, S.guess_kind(p, y, t), w) for p, y, t, w in cases
             if S.guess_kind(p, y, t) != w]
    if not wrong:
        ok("guess_kind: 港美股返回空串，A股按段位分指数/债券/个股/ETF")
    else:
        bad(f"guess_kind 判定错: {wrong}")


# ================= 第 2 层：联网真值（撞号三错例） =================

# 期望值取自 2026-09-03 实测。价格用量级区间而不是写死数字：写死明天必假红，
# 区间足以区分「中证500 7729」与「厦门港务 8.90」——这正是要抓的错。
LIVE_CASES = [
    # raw,      期望 tx_code, 期望名称含, 价格下界, 价格上界, 是否应报撞号
    ("000905", "sh000905", "中证500",  1000.0, 20000.0, True),
    ("000016", "sh000016", "上证50",    500.0, 10000.0, True),
    ("000001", "sh000001", "上证指数",  1000.0, 20000.0, True),
    ("000300", "sh000300", "沪深300",  1000.0, 20000.0, False),
    ("920982", "bj920982", "锦波生物",     1.0, 100000.0, False),
    ("600519", "sh600519", "贵州茅台",   100.0, 100000.0, False),
]


def check_live() -> None:
    import symbol_resolve as S
    print("[2] 联网层：真打 qt.gtimg.cn 验此刻真的没取错")

    got_any = False
    for raw, want_code, want_name, lo, hi, want_amb in LIVE_CASES:
        best = S.resolve(raw)
        if best is None:
            print(f"  [SKIP] {raw}: 取数失败（通道抖动，本例什么都没验到）")
            continue
        if not best:
            bad(f"{raw}: 沪深北都没返回 —— 撞号代码用错前缀时腾讯会直接不返回该行")
            continue
        got_any = True
        if best["tx_code"] != want_code:
            bad(f"{raw}: 取成了 {best['tx_code']}「{best.get('name')}」"
                f"，应为 {want_code}「{want_name}」—— 静默取错重现")
            continue
        if want_name not in best.get("name", ""):
            bad(f"{raw}: 代码对但名称是「{best.get('name')}」，期望含「{want_name}」")
            continue
        px = best.get("price", 0.0)
        if not (lo <= px <= hi):
            bad(f"{raw}: {best['name']} 价格 {px} 不在量级区间 [{lo}, {hi}] —— "
                f"很可能取成了同号的另一只标的")
            continue
        if best.get("ambiguous") is not want_amb:
            bad(f"{raw}: ambiguous={best.get('ambiguous')} 期望 {want_amb}"
                f"（alt={best.get('alternatives')}）—— 撞号必须如实上报，"
                f"否则调用方会静默替用户决定")
            continue
        amb = f" [撞号,另有 {'/'.join(best['alternatives'])}]" if best["ambiguous"] else ""
        ok(f"{raw} → {best['tx_code']} {best['name']} {px}{amb}")

    # 元断言：确实进入了联网路径。全 SKIP 时上面一条 FAIL 都不会有，
    # 「fail 0」在这种情况下等于什么都没验到，必须判成失败。
    if not got_any:
        bad("联网层一个用例都没验到（全部取数失败）—— 本次不能给出通道结论")
    else:
        ok(f"元断言: 联网路径确实执行（{sum(1 for _ in LIVE_CASES)} 例中有命中）")

    # 三出口语义：None(失败) / []( 确实没有) / {...}(有值)，不能用同一个值兼表
    empty = S.resolve("999999")
    if empty is None:
        print("  [SKIP] 999999: 取数失败，空值语义本次未验")
    elif empty == {}:
        ok("空值语义: 不存在的代码返回 {} 而非 None（与取数失败区分开）")
    else:
        bad(f"999999 竟返回了 {empty.get('tx_code')}「{empty.get('name')}」")


# ================= 第 3 层：反向验证（造错必须被抓住） =================
# 每条造错都写明「期望哪条断言 FAIL」。若造错后仍然全绿，说明那条断言是假的。
# 锚点必须在源码里唯一出现，失配即报「造错未生效」——绝不当成通过。

MUTATIONS = [
    ("只探深市（还原线上原坑）",
     '(("000",), ("sh", "sz")),',
     '(("000",), ("sz",)),',
     "prefixes_for"),
    ("忽略活跃度判据",
     'return (0 if is_major_sh_index(q.get("tx_code")) else 1,\n'
     '            0 if q.get("alive") else 1,',
     'return (0 if is_major_sh_index(q.get("tx_code")) else 1,\n'
     '            0,',
     "活跃度是第一判据"),
    ("宽基身份改看 kind（PE 段抖动即退回原坑）",
     'return (0 if is_major_sh_index(q.get("tx_code")) else 1,\n'
     '            0 if q.get("alive") else 1,\n'
     '            _prior_rank(q),',
     'return (0 if (q.get("kind") == "指数"\n'
     '                  and is_major_sh_index(q.get("tx_code"))) else 1,\n'
     '            0 if q.get("alive") else 1,\n'
     '            _prior_rank(q),',
     "kind 判定抖动"),
    ("白名单让位给活跃度（指数 amount 归零瞬间滑向个股）",
     'return (0 if is_major_sh_index(q.get("tx_code")) else 1,\n'
     '            0 if q.get("alive") else 1,\n'
     '            _prior_rank(q),',
     'return (0 if q.get("alive") else 1,\n'
     '            0 if is_major_sh_index(q.get("tx_code")) else 1,\n'
     '            _prior_rank(q),',
     "静默滑向同号个股"),
    ("北交所错用老 fqkline 端点",
     'ep = KLINE_EP.get(market_of(tx_code), "fqkline")',
     'ep = "fqkline"',
     "端点选错"),
    ("撞号不上报（ambiguous 恒 False）",
     "    return len(cands) > 1",
     "    return False",
     "ambiguous"),
    ("撞号判据掺进 alive（盘前一条都标不出来）",
     "    return len(cands) > 1",
     '    return any(q.get("alive") for q in cands[1:])',
     "ambiguous"),
    ("limit 不夹到 800（触发静默退回 640 根）",
     "{min(int(limit), 800)}",
     "{int(limit)}",
     "limit 未夹到 800"),
]


def run_negative() -> int:
    src_mod = (HERE / "symbol_resolve.py").read_text(encoding="utf-8")
    src_probe = (HERE / "tools_probe_symbol_resolve.py").read_text(encoding="utf-8")
    # 反验目录用系统 temp，不能用 /tmp（Git Bash 下会落到真实的 C:\tmp）
    root = Path(tempfile.mkdtemp(prefix="probe_symres_"))
    print(f"反向验证工作目录: {root}")

    broken = []
    for name, anchor, repl, want_kw in MUTATIONS:
        n = src_mod.count(anchor)
        if n != 1:
            print(f"\n[NEG] {name}: 造错未生效 —— 锚点在源码中出现 {n} 次（需恰好 1 次）")
            broken.append(f"{name}（锚点失配 {n} 次，本条什么都没验到）")
            continue
        d = root / re.sub(r"\W+", "_", name)
        d.mkdir(exist_ok=True)
        (d / "symbol_resolve.py").write_text(src_mod.replace(anchor, repl), encoding="utf-8")
        (d / "tools_probe_symbol_resolve.py").write_text(src_probe, encoding="utf-8")
        r = subprocess.run([sys.executable, str(d / "tools_probe_symbol_resolve.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        out = (r.stdout or "") + (r.stderr or "")
        hit = want_kw in out and "[FAIL]" in out
        print(f"\n[NEG] {name}: exit={r.returncode} 命中期望断言={hit}")
        if r.returncode == 0:
            broken.append(f"{name} 造错后依然全绿 —— 相关断言是假的")
        elif not hit:
            broken.append(f"{name} 虽然 FAIL 了，但没命中期望断言「{want_kw}」，"
                          f"可能是别的原因挂的")
        else:
            first = next((l for l in out.splitlines() if "[FAIL]" in l), "")
            print(f"        {first.strip()[:110]}")

    print("\n" + "=" * 62)
    if broken:
        print(f"反向验证不通过（{len(broken)} 条）：")
        for b in broken:
            print(f"  · {b}")
        return 2
    print(f"反向验证通过：{len(MUTATIONS)} 条造错全部被断言抓住")
    return 0


def main() -> int:
    if "--negative" in sys.argv:
        return run_negative()
    print("=" * 62)
    print("symbol_resolve 自检（撞号消歧）")
    print("=" * 62)
    check_pure()
    if "--offline" not in sys.argv:
        check_live()
    print("=" * 62)
    if FAILS:
        print(f"FAIL {len(FAILS)}/{CHECKED}")
        for f in FAILS:
            print(f"  · {f}")
        return 1
    print(f"PASS {CHECKED}/{CHECKED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())



