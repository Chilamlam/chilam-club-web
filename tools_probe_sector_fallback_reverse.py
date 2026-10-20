# -*- coding: utf-8 -*-
"""造错反验：兜底通道的自检断言到底能不能抓住 bug。

做法：逐条把 daily_sector_rotation.py / sector_dc_source.py 里的关键判断改坏，
然后跑 tools_probe_sector_fallback.py，**必须**变成失败；跑完逐字节还原并核对 sha256。

一个断言如果在「代码被改坏」之后仍然绿，它就不是断言，只是一行注释。
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8")

PY = sys.executable
PROBE = ROOT / "tools_probe_sector_fallback.py"
SRC = ROOT / "daily_sector_rotation.py"
DCSRC = ROOT / "sector_dc_source.py"

# (用例名, 目标文件, 原文, 改坏后, 期望被抓住的断言关键字)
MUTATIONS = [
    ("兜底永不触发", SRC,
     "    if not skipped and incomplete:",
     "    if False and not skipped and incomplete:",
     "clist 全败：退出码 0"),

    ("clist 完整也去兜底（无条件触发）", SRC,
     "    if not skipped and incomplete:",
     "    if not skipped:",
     "clist 完整：**未**触发兜底通道"),

    # 这条不改成「无条件 df_today = fb_df」：那会在 fb_df 为 None 时直接崩，
    # 探针是崩了而不是断言红了 —— 崩溃当然也是信号，但它证明不了哪条断言有效。
    # 改成「兜底没成功也把 channel 写成 tushare_dc」，这是更贴近真实的错法：
    # 数据没错、只是事后排查的线索错了，且不会崩。
    ("兜底失败却冒充兜底通道", SRC,
     "        fb_df, fb_info = fetch_tushare_snapshot(today)",
     "        fb_df, fb_info = fetch_tushare_snapshot(today)\n        channel = \"tushare_dc\"",
     "两通道皆败：data_channel 仍是 clist"),

    ("data_channel 不落盘", SRC,
     '    analysis["data_channel"] = channel',
     '    analysis["data_channel"] = "clist"',
     "clist 全败：data_channel=tushare_dc"),

    ("两条通道都败也不 fail（拿空数据往下走）", SRC,
     "    if df_today.empty or len(df_today) < MIN_VALID:",
     "    if False:",
     "clist 全败且兜底败：退出码 3"),

    ("周末也触发兜底", SRC,
     "    if weekday >= 5:",
     "    if False:",
     "周末(clist 完整)：当日没有新增归档"),

    ("兜底行数不足也放行", SRC,
     "    if df is None or len(df) < MIN_COMPLETE:",
     "    if df is None:",
     "兜底：行数 < MIN_COMPLETE"),

    ("残缺拒收不再清除当日残留", SRC,
     "        removed = drop_date_from_history(today)",
     "        removed = 0",
     "两通道皆败：该日残留被清掉"),

    ("探活结果不落盘", SRC,
     '    analysis["fallback_probe"] = probe_fallback_channel(today)',
     '    pass  # 探活结果不落盘',
     "clist 完整：探活记录落了盘"),

    ("探活失败也报成功", SRC,
     '        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}',
     '        return {"ok": True, "error": f"{type(exc).__name__}: {exc}"[:300]}',
     "探活：调用失败时 ok=False 并带出上游原话"),
]


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def run_probe() -> tuple[int, str]:
    r = subprocess.run([PY, str(PROBE)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    originals = {p: p.read_text(encoding="utf-8") for p in (SRC, DCSRC)}
    shas = {p: sha(p) for p in (SRC, DCSRC)}
    print(f"基线：{SRC.name}={shas[SRC]}  {DCSRC.name}={shas[DCSRC]}")

    base_code, base_out = run_probe()
    print(f"基线跑探针：退出码 {base_code}")
    if base_code != 0:
        print("❌ 基线就不通过，先修好再谈造错")
        print(base_out[-2000:])
        return 1

    caught = 0
    try:
        for i, (name, target, old, new, expect) in enumerate(MUTATIONS, 1):
            text = originals[target]
            if old not in text:
                print(f"❌ [{i}] {name}：待改坏的原文没找到，造错脚本本身失效（{old[:50]!r}）")
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            try:
                code, out = run_probe()
            finally:
                target.write_text(originals[target], encoding="utf-8")

            hit = expect in out
            # 必须「退出码非 0」且「点名的那条断言确实红了」——
            # 只看退出码不够：可能是别的用例崩了蒙对的。
            ok = code != 0 and hit
            print(f"{'✅' if ok else '❌'} [{i}] {name} → 退出码 {code}，"
                  f"抓住「{expect}」={hit}")
            if not ok:
                tail = [ln for ln in out.splitlines() if ln.startswith("  ❌")][:6]
                print(f"      实际失败项：{tail}")
            caught += bool(ok)
    finally:
        for p, text in originals.items():
            p.write_text(text, encoding="utf-8")

    print("\n" + "=" * 60)
    for p in (SRC, DCSRC):
        same = sha(p) == shas[p]
        print(f"{'✅' if same else '❌'} 源码已逐字节还原 {p.name} = {sha(p)}")
        if not same:
            return 1
    code, _ = run_probe()
    print(f"{'✅' if code == 0 else '❌'} 还原后探针重新全绿（退出码 {code}）")
    print(f"\n造错反验：{caught}/{len(MUTATIONS)} 项被断言抓住")
    return 0 if caught == len(MUTATIONS) and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
