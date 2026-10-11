# -*- coding: utf-8 -*-
"""反向验证情绪周期自适应阈值的断言是否为真断言。

用法: <stcheck>/python.exe tools_probe_sentiment_reverse.py
退出码 0 = 全部断言有效；3 = 存在假断言。

一条永不失败的断言等于没有断言。这里用 11 种「造错」方式检验：

  sentiment.py（计算层，直接改源码后无条件还原）
    1) 阈值口径把「严格早于当日」放宽为「含当日」      → 当日极值不变性必须炸
    2) 干脆去掉 before_date 过滤                        → 序列长度断言必须炸
    3) 分位顺序反转（high 取 p25、low 取 p80）          → 「启用分位阈值」必须炸
    4) 档位边界 >= 改成 >                                → 边界算例必须炸
    5) 去掉阈值单调性校验（逆序阈值照单全收）           → 逆序回退断言必须炸
    6) PHASE_MIN_SAMPLES 改成 0（样本不足也硬算分位）   → 样本不足回退断言必须炸
    7) 不剔除首板 <10 的噪声日                          → 噪声日不进分布断言必须炸
    8) 发酵期不再要求正溢价                             → 负溢价不判发酵必须炸
    9) 验证条件退回固定阈值（忽略下发阈值）             → 刻度同源断言必须炸
  page_sentiment.py（展示层）
   10) 阈值缺失时编造基准行                            → 空串契约断言必须炸
   11) 周期区块不再渲染基准行（函数没人调用）           → 接线断言必须炸

每一项都必须报出 ❌ 且命中期望断言；若某项仍然全绿，说明该断言是假断言。
"""
import os
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
SENT_PROBE = str(ROOT / "tools_probe_sentiment.py")
UI_PROBE = str(ROOT / "tools_probe_sentiment_ui.py")


def run_probe(probe: str) -> str:
    p = subprocess.run([PY, probe], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT),
                       timeout=900)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, label: str, expect: str,
           probe: str) -> bool:
    """临时改源码 → 跑探针 → 无条件还原（并校验还原后与备份逐字节一致）。"""
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe(probe)
        fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
        hit = [ln for ln in fails if expect in ln]
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:3]:
            print(f"      {ln.strip()[:130]}")
        if not hit:
            for ln in fails[:3]:
                print(f"      （其它 FAIL）{ln.strip()[:130]}")
            if not fails:
                print("      （探针全绿，改动完全没被察觉）")
        print()
        return bool(hit)
    finally:
        path.write_text(backup, encoding="utf-8")
        if path.read_text(encoding="utf-8") != backup:
            print(f"!!! 源码还原失败，请立即 git checkout {target} !!!")


ok = []

# ---------- 1：阈值口径放宽为「含当日」 ----------
ok.append(mutate(
    "sentiment.py",
    'h = h[h["date"] < str(before_date)]',
    'h = h[h["date"] <= str(before_date)]',
    "1) 阈值口径含当日（分位自指）",
    "当日结果换成极值", SENT_PROBE))

# ---------- 2：完全不过滤 before_date ----------
ok.append(mutate(
    "sentiment.py",
    '    if before_date is not None:\n        h = h[h["date"] < str(before_date)]\n',
    '    if False:\n        h = h[h["date"] < str(before_date)]\n',
    "2) 去掉 before_date 过滤（阈值吃进全部历史含当日）",
    "序列长度", SENT_PROBE))

# ---------- 3：分位顺序反转 ----------
ok.append(mutate(
    "sentiment.py",
    "PHASE_QUANTILES = (0.80, 0.55, 0.25)",
    "PHASE_QUANTILES = (0.25, 0.55, 0.80)",
    "3) 分位顺序反转（发酵门槛取 p25）",
    "启用分位阈值", SENT_PROBE))

# ---------- 4：档位边界 >= 改 > ----------
ok.append(mutate(
    "sentiment.py",
    '    if r12 >= th["high"] and (prem is None or prem > 0):\n        return "发酵期"\n'
    '    if r12 >= th["mid"]:\n        return "分歧期"\n'
    '    if r12 >= th["low"]:\n        return "退潮期"\n',
    '    if r12 > th["high"] and (prem is None or prem > 0):\n        return "发酵期"\n'
    '    if r12 > th["mid"]:\n        return "分歧期"\n'
    '    if r12 > th["low"]:\n        return "退潮期"\n',
    "4) 档位边界 >= 改为 >（临界值掉档）",
    "边界 r12=30.0%", SENT_PROBE))

# ---------- 5：去掉阈值单调性校验 ----------
ok.append(mutate(
    "sentiment.py",
    "            if h > m > l >= 0:\n",
    "            if True:\n",
    "5) 逆序阈值不再被拒绝（全部日子滑进同一档）",
    "逆序阈值", SENT_PROBE))

# ---------- 6：样本门槛归零 ----------
ok.append(mutate(
    "sentiment.py",
    "PHASE_MIN_SAMPLES = 20   # 分位法最低样本数；不足则回退经验阈值",
    "PHASE_MIN_SAMPLES = 0   # 分位法最低样本数；不足则回退经验阈值",
    "6) 样本门槛归零（7 天样本也硬算分位）",
    "回退经验阈值", SENT_PROBE))

# ---------- 7：不剔除噪声日 ----------
ok.append(mutate(
    "sentiment.py",
    "        if base < PHASE_MIN_BASE:\n            continue\n",
    "        if False:\n            continue\n",
    "7) 首板仅 5 只的日子也进分布（噪声污染分位）",
    "不进分布", SENT_PROBE))

# ---------- 8：发酵期不要求正溢价 ----------
ok.append(mutate(
    "sentiment.py",
    '    if r12 >= th["high"] and (prem is None or prem > 0):',
    '    if r12 >= th["high"]:',
    "8) 发酵期不再要求正溢价（接力顺畅却全员亏钱）",
    "不判发酵期", SENT_PROBE))

# ---------- 9：验证条件退回固定阈值 ----------
ok.append(mutate(
    "sentiment.py",
    "        th = _normalize_thresholds(thresholds)\n"
    "        prem = (premium or {}).get(\"median_pct\")",
    "        th = _normalize_thresholds(None)\n"
    "        prem = (premium or {}).get(\"median_pct\")",
    "9) 验证条件忽略下发阈值（两处各算一套必漂移）",
    "档位刻度来自传入阈值", SENT_PROBE))

# ---------- 10：阈值缺失时编造基准行 ----------
ok.append(mutate(
    "page_sentiment.py",
    '        return f"📐 判定基准：{th.get(\'note\', \'经验阈值\')}　·　{cur}"\n'
    '    return ""',
    '        return f"📐 判定基准：{th.get(\'note\', \'经验阈值\')}　·　{cur}"\n'
    '    return "📐 判定基准：历史分位"',
    "10) 阈值缺失时编造基准行（拿不到就说拿不到）",
    "空串", UI_PROBE))

# ---------- 11：基准行不再被渲染 ----------
ok.append(mutate(
    "page_sentiment.py",
    "    if cap:\n        st.caption(cap)\n",
    "    if False:\n        st.caption(cap)\n",
    "11) 周期区块不渲染基准行（函数写了没人调）",
    "渲染出判定基准行", UI_PROBE))

print("-" * 60)
print(f"反向验证：{sum(ok)}/{len(ok)} 项断言确认真实有效")
if len(ok) != sum(ok) or not all(ok):
    print("❌ 存在假断言或造错未生效")
    sys.exit(3)
print("✅ 全部造错均被对应断言抓住")
