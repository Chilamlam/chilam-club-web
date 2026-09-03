# -*- coding: utf-8 -*-
"""依赖清单自检：两份 requirements 必须覆盖各自入口的「顶层裸 import」闭包。

为什么需要这条自检
------------------
2026-09-03 侦察发现：`requirements.txt` 与 `requirements-batch.txt` **都缺 numpy**，
而 `tech_analysis.py` / `scorecard.py` / `sentiment.py` / `page_macro_erp.py` 四个
模块顶层就是 `import numpy as np`。目前没崩，纯粹因为 pandas 与 streamlit 把 numpy
当自己的依赖顺带装上了 —— 这是**借来的运气**：pip 哪天解出一个不带 numpy 的组合
（或 pandas 换数组后端），线上就是模块加载即 ImportError。白屏这条路 2026-09-02
已经走过一次，代价是全天不可用，不能靠「传递依赖大概会装上」续命。

判据为什么只盯「顶层裸 import」
------------------------------
`import bcrypt` 写在 try 里、`import tech_analysis as ta` 写在函数里 —— 缺包只走
降级分支，不会让模块加载失败。把这些也算硬依赖会逼清单越写越长，反而淹没真问题。
规则：**顶层裸 import 缺声明 = FAIL；软 import 缺声明 = 只提示**。

两条独立通道（刻意冗余）
------------------------
  A. 闭包通道：AST 遍历入口 → 本地模块闭包 → 收集顶层第三方 import，与清单比对。
  B. 点名通道：numpy 这个**已实测的具体缺口**单独硬断言。
若哪天 AST 解析静默失效（语法变更 / 入口列表写空），A 会退化成「什么都没查出来」
而 fail 0；B 与元断言负责让这种情况判成失败，而不是假通过。

用法
----
    python tools_probe_requirements.py              # 正向
    python tools_probe_requirements.py --negative   # 反向造错（必须全被抓住）
退出码：0 通过 / 1 有失败项 / 2 反向验证不通过
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

FAILS: list = []
CHECKED = 0
HINTS: list = []

def ok(msg: str) -> None:
    global CHECKED
    CHECKED += 1
    print(f"  [OK]   {msg}")


def bad(msg: str) -> None:
    """失败唯一出口（自己 print FAIL 会让主流程漏抓）。"""
    global CHECKED
    CHECKED += 1
    FAILS.append(msg)
    print(f"  [FAIL] {msg}")


# ---- pip 包名 → import 名的差异表。只列本仓库真的用到的，不做通用映射。 ----
DIST_TO_IMPORT = {
    "pyjwt": "jwt",
    "beautifulsoup4": "bs4",
    "google-generativeai": "google",
    "python-dateutil": "dateutil",
}


def norm_dist(line: str) -> str:
    """requirements 一行 → 规范化的 pip 包名（小写、去版本约束/注释/extras）。"""
    s = line.split("#")[0].strip()
    if not s or s.startswith("-"):
        return ""
    s = re.split(r"[<>=!~\[;]", s)[0].strip().lower()
    return s.replace("_", "-")


def read_req(path: Path) -> set:
    if not path.is_file():
        return set()
    return {d for d in (norm_dist(l) for l in path.read_text(encoding="utf-8").splitlines()) if d}


def declared_imports(dists: set) -> set:
    """清单声明的包 → 它们提供的 import 名集合。"""
    return {DIST_TO_IMPORT.get(d, d.replace("-", "_")) for d in dists}


def scan_file(path: Path):
    """返回 (顶层裸 import 的顶层模块名, 软 import 的顶层模块名)。

    顶层裸 = 直接躺在 module.body 里的 Import/ImportFrom。写在 try 里的会被
    ast.Try 包住，写在函数里的会被 FunctionDef 包住，两者都算软。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def names(n) -> set:
        if isinstance(n, ast.Import):
            return {a.name.split(".")[0] for a in n.names}
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            return {n.module.split(".")[0]}
        return set()

    hard: set = set()
    for n in tree.body:
        hard |= names(n)
    soft: set = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            soft |= names(n)
    return hard, soft - hard


def closure(root: Path, entries: list):
    """从入口沿本地模块传递闭包，返回 (硬第三方, 软第三方, 走到的本地模块数)。"""
    pages = root / "pages"
    local = {f.stem for f in root.glob("*.py")}
    local_pages = {f.stem for f in pages.glob("*.py")} if pages.is_dir() else set()
    std = set(sys.stdlib_module_names)

    seen: set = set()
    hard_third: set = set()
    soft_third: set = set()
    stack = list(entries)
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        p = root / f"{mod}.py"
        if not p.is_file():
            p = pages / f"{mod}.py"
        if not p.is_file():
            continue
        h, s = scan_file(p)
        for grp, dest in ((h, hard_third), (s, soft_third)):
            for m in grp:
                if m in local or m in local_pages:
                    stack.append(m)
                elif m not in std:
                    dest.add(m)
    return hard_third, soft_third - hard_third, len(seen & (local | local_pages))


def entries_for(root: Path):
    """两套入口。前端 = app.py + pages/*（Streamlit 多页路由只认这两处）；
    跑批 = run_daily.py + 所有 daily_*.py（编排器逐个 subprocess 起）。"""
    pages = root / "pages"
    fe = ["app"] + sorted(f.stem for f in pages.glob("*.py")) if pages.is_dir() else ["app"]
    batch = ["run_daily"] + sorted(f.stem for f in root.glob("daily_*.py"))
    return fe, batch


SPECS = [
    # (标签, 清单文件, 取入口的下标 0=前端 1=跑批)
    ("前端（Streamlit Cloud）", "requirements.txt", 0),
    ("跑批（GitHub Actions）", "requirements-batch.txt", 1),
]

# 已实测的具体缺口，独立于 AST 通道硬断言一次。
# 通道 A 若静默失效会「什么都查不出来」，这条负责让它无法假通过。
MUST_DECLARE = [
    ("requirements.txt", "numpy",
     "tech_analysis / page_macro_erp 顶层就 import numpy，"
     "现在没崩只是 pandas·streamlit 顺带装上了 —— 借来的运气"),
    ("requirements-batch.txt", "numpy",
     "scorecard / sentiment 顶层 import numpy，跑批装不上就整步失败"),
]


def check(root: Path) -> None:
    fe, batch = entries_for(root)
    ents = [fe, batch]

    # 元断言：入口列表不能为空，否则闭包为空 → 下面一条 FAIL 都不会有（假通过）
    if len(fe) >= 2 and len(batch) >= 5:
        ok(f"元断言: 入口已找到（前端 {len(fe)} 个 / 跑批 {len(batch)} 个）")
    else:
        bad(f"入口列表异常（前端 {fe} / 跑批 {batch}）—— 闭包为空会让本探针什么都验不到")

    for label, req_name, which in SPECS:
        print(f"[{label}] {req_name}")
        dists = read_req(root / req_name)
        if not dists:
            bad(f"{req_name} 读不到或为空 —— 依赖清单必须存在且非空")
            continue
        provided = declared_imports(dists)
        hard, soft, n_local = closure(root, ents[which])

        # 元断言：闭包必须真的走进了本地模块，且抓到了第三方包
        if n_local >= 5 and hard:
            ok(f"元断言: 闭包走到 {n_local} 个本地模块，收集到 {len(hard)} 个顶层第三方 import")
        else:
            bad(f"{req_name}: 闭包只走到 {n_local} 个模块 / 硬依赖 {sorted(hard)} —— "
                f"AST 通道疑似失效，本次没有验到任何东西")
            continue

        missing = sorted(m for m in hard if m not in provided)
        if not missing:
            ok(f"{req_name} 覆盖全部顶层裸 import：{sorted(hard)}")
        else:
            bad(f"{req_name} 缺声明（顶层裸 import，缺包即模块加载失败）：{missing}")

        soft_missing = sorted(m for m in soft if m not in provided)
        if soft_missing:
            HINTS.append(f"{req_name}: 软 import 未声明（缺了走降级，不判失败）：{soft_missing}")

        # 反向：清单里声明了但没人 import，属于白装（拖慢 CI），只提示不判失败
        used = {m.lower() for m in hard | soft}
        idle = sorted(d for d in dists
                      if DIST_TO_IMPORT.get(d, d.replace("-", "_")).lower() not in used)
        if idle:
            HINTS.append(f"{req_name}: 声明了但代码里没人 import（白装拖慢部署）：{idle}")

    print("[点名] 已实测缺口逐条硬断言（独立于 AST 通道）")
    for req_name, dist, why in MUST_DECLARE:
        if dist in read_req(root / req_name):
            ok(f"{req_name} 已显式声明 {dist} —— {why}")
        else:
            bad(f"{req_name} 必须显式声明 {dist}：{why}")


# 反向造错：每条都写明期望哪条断言 FAIL。锚点必须唯一，失配即报「造错未生效」。
# 每条可以同时改多个文件（有些坑要「清单少一行 + 探针判据退化」两件事同时发生才复现）。
#
# ★ 自指陷阱：造错目标是**本文件自己**时，锚点若是单行字面量，它在下面这张表里
#   也算一次出现 → count=2 → 判「锚点失配」（2026-09-03 实测踩到）。
#   解法是锚点**跨行**：表里写的是转义的 `\n`（两个字符），目标代码里是真换行，
#   两者文本不同，count 才回到 1。这不是花招 —— 计数唯一性是「造错真的生效」的
#   唯一凭据，宁可锚点写长一点。
MUTATIONS = [
    ("前端清单删掉 numpy（还原今天发现的原状）",
     [("requirements.txt", "numpy>=1.26\n", "")],
     "numpy"),
    ("跑批清单删掉 numpy",
     [("requirements-batch.txt", "numpy>=1.26\n", "")],
     "numpy"),
    ("前端清单删掉 pandas（验闭包通道自己有效，不是只靠点名表）",
     [("requirements.txt", "pandas\n", "")],
     "缺声明"),
    ("闭包不沿本地模块递归（只扫入口文件，间接依赖全漏）",
     [("tools_probe_requirements.py",
       "                if m in local or m in local_pages:\n"
       "                    stack.append(m)",
       "                if m in local or m in local_pages:\n"
       "                    pass")],
     "没有沿本地模块递归"),
    ("顶层裸 import 判据退化成「全算软」（清单缺什么都不报）",
     [("tools_probe_requirements.py", "    for n in tree.body:\n        hard |= names(n)",
       "    for n in []:\n        hard |= names(n)")],
     "AST 通道疑似失效"),
    ("跑批入口列表取空（闭包为空 → 一条断言都跑不到）",
     [("tools_probe_requirements.py",
       '    batch = ["run_daily"] + sorted(f.stem for f in root.glob("daily_*.py"))\n'
       "    return fe, batch",
       "    batch = []\n"
       "    return fe, batch")],
     "入口列表异常"),
]


def run_negative() -> int:
    src_root = HERE
    files = sorted(src_root.glob("*.py"))
    reqs = [p for p in (src_root / "requirements.txt", src_root / "requirements-batch.txt")
            if p.is_file()]
    pages = sorted((src_root / "pages").glob("*.py")) if (src_root / "pages").is_dir() else []
    # 反验目录用系统 temp：Git Bash 下 /tmp 会落到真实的 C:\tmp（不存在）
    root = Path(tempfile.mkdtemp(prefix="probe_req_"))
    print(f"反向验证工作目录: {root}")

    broken: list = []
    for name, edits, want_kw in MUTATIONS:
        d = root / re.sub(r"\W+", "_", name)[:60]
        (d / "pages").mkdir(parents=True, exist_ok=True)
        for p in files + reqs:
            shutil.copy2(p, d / p.name)
        for p in pages:
            shutil.copy2(p, d / "pages" / p.name)

        applied = True
        for fname, anchor, repl in edits:
            tgt = d / fname
            txt = tgt.read_text(encoding="utf-8")
            n = txt.count(anchor)
            if n != 1:
                print(f"\n[NEG] {name}: 造错未生效 —— {fname} 里锚点出现 {n} 次（需恰好 1 次）")
                broken.append(f"{name}（{fname} 锚点失配 {n} 次，本条什么都没验到）")
                applied = False
                break
            tgt.write_text(txt.replace(anchor, repl), encoding="utf-8")
        if not applied:
            continue

        r = subprocess.run([sys.executable, str(d / "tools_probe_requirements.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="ignore")
        out = (r.stdout or "") + (r.stderr or "")
        hit = want_kw in out and "[FAIL]" in out
        print(f"\n[NEG] {name}: exit={r.returncode} 命中期望断言={hit}")
        if r.returncode == 0:
            broken.append(f"{name} 造错后依然全绿 —— 相关断言是假的")
        elif not hit:
            broken.append(f"{name} 虽然 FAIL 了，但没命中期望断言「{want_kw}」，可能是别的原因挂的")
        else:
            first = next((l for l in out.splitlines() if "[FAIL]" in l), "")
            print(f"        {first.strip()[:120]}")

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
    print("依赖清单自检（顶层裸 import 必须被声明）")
    print("=" * 62)
    check(HERE)

    # 元断言：闭包必须穿透到间接依赖。numpy 只出现在 tech_analysis / page_macro_erp /
    # scorecard / sentiment 里，app.py 自己一行都没写 —— 若只扫入口文件，
    # 漏掉的恰好就是今天要补的那个包。
    fe, _ = entries_for(HERE)
    hard_fe, _, _ = closure(HERE, fe)
    if "numpy" in hard_fe:
        ok("元断言: 闭包穿透到了间接依赖（numpy 只在 tech_analysis 等下游模块里 import）")
    else:
        bad("闭包没有沿本地模块递归 —— 只扫入口文件会漏掉 numpy 这类间接硬依赖")

    for h in HINTS:
        print(f"  [提示] {h}")
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
