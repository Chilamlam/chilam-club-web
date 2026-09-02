# -*- coding: utf-8 -*-
"""反向验证 tools_probe_quote_api.py 与 tools_probe_live_quote_page.py 的断言是否为真断言。

用法: /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_quote_reverse.py
退出码 0 = 全部断言真实有效；3 = 存在假断言（改完消歧/K线/搜索逻辑后必跑）。

一条永不失败的断言等于没有断言。这里用五种「造错」方式分别检验：
  数据层探针（进程内 monkeypatch）
    1) 退回旧的 000xxx→sh 硬编码 + `if not q` 兜底  → A 段撞号断言必须炸
    2) 北交所日K 退回老 fqkline 端点（只返回 1 根）  → B 段根数下限必须炸
    3) 搜索接口打成必然失败                          → C 段搜索断言必须炸
  页面层探针（临时改源码再还原 —— AppTest 另起 runtime，monkeypatch 进不去）
    4) 搜索接口整体不可达                            → 搜索结果按钮断言必须炸
    5) 去掉撞号排序、只取先验第一个前缀              → 选择器断言必须炸
每一项都必须报出 [FAIL]；若某项仍然全绿，说明该断言是假断言。

两条历史教训（都是真实踩过的）：
  · B 段最初自己 print("FAIL ...") 而没走 bad()，[FAIL] 前缀缺失，本脚本按
    [FAIL] 过滤时完全抓不到「北交所日K 只有 1 根」——断言在外部看是隐形的。
    所以探针里所有失败都必须走 bad() 这个唯一出口。
  · 页面探针最初只判「按钮标签含中国稀土」，而 PRESETS 里本来就有一个
    「中国稀土 sz000831」快速直达按钮 —— 搜索接口挂掉时断言照样通过。
    现已改判「换行 + 反引号」这个搜索结果专属的标签结构来区分两类按钮。
"""
import subprocess
import sys
import types
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

_st = types.ModuleType("streamlit")


def _cd(*a, **k):
    if a and callable(a[0]):
        return a[0]

    def d(f):
        return f
    return d


_st.cache_data = _cd
sys.modules.setdefault("streamlit", _st)

PY = sys.executable
PROBE = ROOT / "tools_probe_quote_api.py"
PAGE_PROBE = ROOT / "tools_probe_live_quote_page.py"
TARGET = ROOT / "page_live_quote.py"


def run_with_patch(patch_src: str, label: str, expect_section: str):
    """在探针进程里注入 patch，再跑自检，看是否出现 FAIL。"""
    driver = f'''
import sys, types
sys.path.insert(0, r"{ROOT}")
_st = types.ModuleType("streamlit")
def _cd(*a, **k):
    if a and callable(a[0]): return a[0]
    def d(f): return f
    return d
_st.cache_data = _cd
sys.modules.setdefault("streamlit", _st)
import page_live_quote as q
{patch_src}
exec(open(r"{PROBE}", encoding="utf-8").read().split('import page_live_quote as q')[1])
'''
    p = subprocess.run([PY, "-c", driver], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT))
    out = (p.stdout or "") + (p.stderr or "")
    fails = [ln for ln in out.splitlines() if "[FAIL]" in ln]
    hit = [ln for ln in fails if expect_section in ln]
    status = "反向验证通过" if hit else "!! 断言没抓住 —— 这是假断言 !!"
    print(f"--- {label}")
    print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}  → {status}")
    for ln in hit[:4]:
        print(f"      {ln.strip()}")
    if not hit and fails:
        print("      （抓到的是其它 FAIL，非目标断言）")
        for ln in fails[:3]:
            print(f"      {ln.strip()}")
    print()
    return bool(hit)


def run_with_source_patch(old: str, new: str, label: str, expect: str) -> bool:
    """页面探针用：临时改 page_live_quote.py 源码再跑 AppTest，跑完**无条件还原**。

    为什么不能像上面那样 monkeypatch：AppTest 会另起一个 Streamlit runtime、
    在子解释器里重新 import 页面模块，父进程里打的补丁进不去 —— 只能改源码。
    还原写在 finally 里，且结尾会校验文件内容与备份完全一致。
    """
    backup = TARGET.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    TARGET.write_text(backup.replace(old, new), encoding="utf-8")
    try:
        p = subprocess.run([PY, str(PAGE_PROBE)], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=600)
        out = (p.stdout or "") + (p.stderr or "")
        fails = [ln for ln in out.splitlines() if "[FAIL]" in ln]
        hit = [ln for ln in fails if expect in ln]
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:3]:
            print(f"      {ln.strip()[:150]}")
        print()
        return bool(hit)
    finally:
        TARGET.write_text(backup, encoding="utf-8")
        if TARGET.read_text(encoding="utf-8") != backup:
            print("!!! 源码还原失败，请立即 git checkout page_live_quote.py !!!")


ok = []

# 造错 1：退回旧的「000xxx 一律沪市 + 有返回就不再兜底」
ok.append(run_with_patch('''
def _old_resolve_candidates(raw):
    import re
    s = (raw or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"\\d{6}", s):
        sh = s.startswith(("60", "68", "51", "58", "56", "50", "11", "000", "999"))
        code = ("sh" if sh else "sz") + s
        sym = {"market": "A_SHARE", "tx_code": code, "sina_sym": None, "display": s}
        quote = q._fetch_tx_quote(code, "A_SHARE")
        if not quote:
            alt = ("sz" + s) if code.startswith("sh") else ("sh" + s)
            quote = q._fetch_tx_quote(alt, "A_SHARE")
            if quote:
                sym["tx_code"] = alt
        return [{"sym": sym, "quote": quote}] if quote else []
    return _orig(raw)
_orig = q.resolve_candidates
q.resolve_candidates = _old_resolve_candidates
''', "造错1: 退回旧的 000xxx→沪市 硬编码 + `if not q` 兜底", "000831"))

# 造错 2：北交所日K 退回老 fqkline 端点（静默只返回 1 根）
ok.append(run_with_patch('''
_orig_kline = q._tx_kline
def _bad_kline(code, period, market, limit=240):
    if market == "BJ_SHARE":
        return _orig_kline(code, period, "A_SHARE", limit)   # 用 fqkline，北交所只回 1 根
    return _orig_kline(code, period, market, limit)
q._tx_kline = _bad_kline
''', "造错2: 北交所日K 退回老 fqkline 端点", "北交所"))

# 造错 3：搜索接口打成必然失败
ok.append(run_with_patch('''
q.search_symbols = lambda kw, limit=10: []
''', "造错3: 搜索接口返回空", "搜索"))

# ===== 以下两项检验页面层探针 tools_probe_live_quote_page.py =====

# 造错 4：搜索接口整体不可达 —— 页面上搜索结果按钮必须消失
ok.append(run_with_source_patch(
    "https://smartbox.gtimg.cn/s3/",
    "https://smartbox-nonexistent-xyz.gtimg.cn/s3/",
    "造错4: 搜索接口不可达（页面层）", "未生成结果按钮"))

# 造错 5：去掉撞号排序、只保留先验第一个候选 —— 选择器必须消失
ok.append(run_with_source_patch(
    "for q in _rank_candidates(list(got.values())):",
    "for q in [got[k] for k in sorted(got)][:1]:",
    "造错5: 撞号消歧被去掉（页面层）", "没有出现候选选择器"))

print("=" * 60)
print("反向验证结论:", "全部断言真实有效" if all(ok) else "存在假断言，必须修!")
sys.exit(0 if all(ok) else 3)
