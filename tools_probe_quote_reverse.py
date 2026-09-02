# -*- coding: utf-8 -*-
"""反向验证 tools_probe_quote_api.py 与 tools_probe_live_quote_page.py 的断言是否为真断言。

用法: /c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_quote_reverse.py
退出码 0 = 全部断言真实有效；3 = 存在假断言（改完消歧/K线/搜索逻辑后必跑）。

一条永不失败的断言等于没有断言。这里用十种「造错」方式分别检验：
  数据层探针（进程内 monkeypatch）
    1) 退回旧的 000xxx→sh 硬编码 + `if not q` 兜底  → A 段撞号断言必须炸
    2) 北交所日K 退回老 fqkline 端点（只返回 1 根）  → B 段根数下限必须炸
    3) 搜索接口打成必然失败                          → C 段搜索断言必须炸
    6) 摘掉东财兜底源                                → C 段北交所/转债用例必须炸
    7) 摘掉 _drop_unquotable 的报价校验              → C 段死按钮断言必须炸
    8) 无结果时把 _SearchEmpty 吞成 []               → C 段空结果契约断言必须炸
  页面层探针（临时改源码再还原 —— AppTest 另起 runtime，monkeypatch 进不去）
    4) 搜索接口整体不可达                            → 搜索结果按钮断言必须炸
    5) 去掉撞号排序、只取先验第一个前缀              → 选择器断言必须炸
    9) 去掉代码框的名称回落                          → 回落按钮断言必须炸
   10) 无结果时 return [] 而非抛异常                 → 空结果不缓存断言必须炸
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
import os
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
FIB_PROBE = ROOT / "tools_probe_fibonacci_page.py"
TARGET = ROOT / "page_live_quote.py"
FIB_TARGET = ROOT / "page_fibonacci.py"


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


def run_with_source_patch(old: str, new: str, label: str, expect: str,
                          target=None, probe=None, only: str = "") -> bool:
    """页面探针用：临时改源码再跑 AppTest，跑完**无条件还原**。

    为什么不能像上面那样 monkeypatch：AppTest 会另起一个 Streamlit runtime、
    在子解释器里重新 import 页面模块，父进程里打的补丁进不去 —— 只能改源码。
    还原写在 finally 里，且结尾会校验文件内容与备份完全一致。

    only: 传给黄金分割页探针的 FIB_PROBE_ONLY —— 那边每个用例都要起一次 runtime
    并真拉数据，全跑一轮几分钟，造错四轮就不可接受。**但只跑相关用例是有代价的**：
    tag 写错会导致零用例执行、fail=0，此时下面按 expect 过滤抓不到东西，会被判成
    「断言没抓住」而报警 —— 失败方向是安全的。
    """
    tgt = target or TARGET
    prb = probe or PAGE_PROBE
    backup = tgt.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    tgt.write_text(backup.replace(old, new), encoding="utf-8")
    env = dict(os.environ)
    if only:
        env["FIB_PROBE_ONLY"] = only
    try:
        p = subprocess.run([PY, str(prb)], capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", cwd=str(ROOT),
                           timeout=1800, env=env)
        out = (p.stdout or "") + (p.stderr or "")
        fails = [ln for ln in out.splitlines() if "[FAIL]" in ln]
        hit = [ln for ln in fails if expect in ln]
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:3]:
            print(f"      {ln.strip()[:150]}")
        if not hit and fails:
            print("      （抓到的是其它 FAIL，非目标断言）")
            for ln in fails[:2]:
                print(f"      {ln.strip()[:150]}")
        print()
        return bool(hit)
    finally:
        tgt.write_text(backup, encoding="utf-8")
        if tgt.read_text(encoding="utf-8") != backup:
            print(f"!!! 源码还原失败，请立即 git checkout {tgt.name} !!!")


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

# 造错 6：摘掉东财兜底源 —— 北交所 / 转债那几条只能靠它命中，必须炸。
# smartbox 对「锦波生物 / jbsw / 北证50 / 立讯转债 / 兴业转债」全返回 0 条，
# 所以这几条用例天然在守「兜底源还在」。
ok.append(run_with_patch('''
q._search_eastmoney = lambda kw, limit: []
''', "造错6: 摘掉东财兜底源", "锦波生物"))

# 造错 7：摘掉 _drop_unquotable 的报价校验 —— 死按钮就会漏进结果里。
# 期望抓到的是 _drop_unquotable 那条独立断言（它直接喂假候选，不依赖上游过滤）。
ok.append(run_with_patch('''
q._drop_unquotable = lambda hits: hits
''', "造错7: 摘掉搜索结果的报价校验", "_drop_unquotable"))

# 造错 8：无结果时退回 return [] —— cache_data 会把空列表缓存整整 10 分钟。
# 数据层探针里 cache_data 被 stub 掉，验的是「内核抛 _SearchEmpty」这个契约。
ok.append(run_with_patch('''
_orig_kernel = q._search_kernel
def _bad_kernel(keyword, limit):
    try:
        return _orig_kernel(keyword, limit)
    except q._SearchEmpty:
        return []
q._search_kernel = _bad_kernel
''', "造错8: 搜索无结果时退回 return []（会被缓存）", "_SearchEmpty"))

# ===== 以下三项检验页面层探针 tools_probe_live_quote_page.py =====

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

# 造错 9：把代码框的名称回落打成空操作 —— 此时「宁德时代」打进代码框只剩一句报错
ok.append(run_with_source_patch(
    "    hits = search_symbols(raw.strip())",
    "    return False\n    hits = search_symbols(raw.strip())",
    "造错9: 去掉代码框的名称回落（页面层）", "没给出回落搜索按钮"))

# 造错 10：搜索无结果时退回 return [] —— 真 runtime 下 cache_data 会把空列表缓存
# 10 分钟。这条必须走页面探针：底层探针的 cache_data 是空 stub，验不出缓存行为。
ok.append(run_with_source_patch(
    "        raise _SearchEmpty(kw)",
    "        return []",
    "造错10: 空搜索结果改回 return[]（页面层真缓存）", "空搜索结果被缓存了"))

# ===== 以下四项检验黄金分割页探针 tools_probe_fibonacci_page.py =====
# 这几项都改 page_live_quote.py 的取数层（黄金分割页复用它），只有造错 13 改
# page_fibonacci.py 本身。每项都用 FIB_PROBE_ONLY 只跑相关用例 —— 全跑一轮
# 要几分钟，四轮不可接受。

# 造错 11：退回老实现的单段 limit=1000 —— 实测 5 年区间只得 640 根（比传 800 还少）
ok.append(run_with_source_patch(
    "    _pull(start, end)\n"
    "    # 补尾：`end` 落在当天时，某些 limit 分片当天还没刷新",
    "    _pull(start, end)\n"
    "    if True:\n"
    "        rows = [merged[k] for k in sorted(merged) if start <= str(k) <= end]\n"
    "        df = _tx_kline_records(rows)\n"
    "        return df.rename(columns={'datetime': 'date'}) if not df.empty else df\n"
    "    # 补尾：`end` 落在当天时，某些 limit 分片当天还没刷新",
    "造错11: 区间取数退回单段拉取（不分段回补、不补尾）",
    "静默残缺", probe=FIB_PROBE, only="bars"))

# 造错 12：只去掉补尾那两行 —— 长区间末根会停在前一个交易日（图上最新一天凭空消失）。
# 期望文案是「最新交易日是」：探针拿**独立分片通道**取 max 当参照，不能拿同一函数的
# 短区间 —— 去掉补尾后短区间也丢当天，两边一起错、对比恒等，断言就成了假断言。
# 这正是本项造错第一次跑时抓不到东西的原因，改掉参照物后才真能抓住。
ok.append(run_with_source_patch(
    "    if merged and max(merged) < end:\n        _pull(max(merged), end)",
    "    if False:\n        _pull(max(merged), end)",
    "造错12: 去掉末根补齐（长区间少最新一天）",
    "最新交易日是", probe=FIB_PROBE, only="tail"))

# 造错 13：黄金分割页退回老的硬前缀规则（6/5/9 开头算沪市，否则算深市）。
# 这是本轮真正修掉的那个 bug：000905 会被拼成 sz000905，而腾讯**对错前缀不容错**
# —— 它返回的是「厦门港务」，图照样能画，只是画的是另一只标的。
ok.append(run_with_source_patch(
    "    cands = lq.resolve_candidates(raw)",
    "    _pfx = 'sh' if raw[:1] in ('6', '5', '9') else 'sz'\n"
    "    cands = lq.resolve_candidates(_pfx + raw) if raw.isdigit() and len(raw) == 6 \\\n"
    "            else lq.resolve_candidates(raw)",
    "造错13: 黄金分割页退回硬前缀规则（静默取错标的）",
    "000905", target=FIB_TARGET, probe=FIB_PROBE, only="collide"))

# 造错 14：北交所退回老 fqkline 端点 —— 静默只返回 1 根，图上就一根 K 线
ok.append(run_with_source_patch(
    '_TX_KLINE_EP = {"A_SHARE": "fqkline", "BJ_SHARE": "newfqkline",',
    '_TX_KLINE_EP = {"A_SHARE": "fqkline", "BJ_SHARE": "fqkline",',
    "造错14: 北交所日K 退回老 fqkline 端点（页面层）",
    "920982", probe=FIB_PROBE, only="bj"))

print("=" * 60)
print("反向验证结论:", "全部断言真实有效" if all(ok) else "存在假断言，必须修!")
sys.exit(0 if all(ok) else 3)
