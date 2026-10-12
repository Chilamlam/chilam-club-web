# -*- coding: utf-8 -*-
"""反向验证行业信号链路断言是否为真断言（造错反验）。

用法: <stcheck>/python.exe tools_probe_industry_signal_reverse.py
退出码 0 = 全部断言有效；3 = 存在假断言（改了错却没被抓住）。

一条永不失败的断言等于没有断言。这里用 21 种「造错」方式检验主探针
（tools_probe_industry_signal.py）能否抓住：

  industry_signal.py（计算层）
    1) 分位去掉中位秩（改成 (v<=x) 占比）        → 「全并列→50」必须炸
    2) 空序列返回 0.0 冒充算出来                  → 「空序列→None」必须炸
    3) 命中判据 < 改成 <=                          → 「mv_pct==30.0 不命中」必须炸
    4) 命中判据 > 改成 >=                          → 「严格不等式（源码级）」必须炸
    5) 排序方向反转（热度升序）                    → 「picks 按热度非增」必须炸
    6) head(MAX_PICKS) 改成 head(MAX_PICKS+5)      → 「截断为 3」必须炸
    7) hits_count 报成 picks 长度                  → 「hits_count 报全量」必须炸
    8) MIN_TRADE_DAYS 改成 0（样本不足硬算）        → 「19 日→insufficient」必须炸
    9) 占比分母改成全期总和（每日不再自洽）        → 「每日占比之和=100」必须炸
   10) MIN_SECTION_RATIO 改成 0（残缺截面放行）     → 「残缺日整日剔除」必须炸
   11) 缺市值行业不剔除（填 0 参与占比）            → 「缺市值行业被剔除」必须炸
   12) mv_window_actual 报成声明值（假装满窗）      → 「报实际 40」必须炸
   13) 0 命中时不出段落                            → 「0 命中仍出段落」必须炸
   14) 段落里塞一句「建议买入」                    → 「不出现操作建议词」必须炸
   15) 计算层 import streamlit                     → 「不得 import 展示层」必须炸
  daily_industry_signal.py（跑批层）
   16) 去掉 trade_date 自证校验                    → 「返回日期不符→拒收」必须炸
   17) section_ok 恒真                             → 「111 个行业→False」必须炸
  daily_sector_rotation.py（板块轮动）
   18) incomplete 分支改回照实归档                 → 「只清理不写归档」必须炸
   19) fetch_snapshot 退回单轮                     → 「分轮重试」必须炸
  digest.py / run_daily.py（接线）
   20) digest 不再调用行业信号段                    → 「摘要里出现行业信号段」必须炸
   21) run_daily 步骤 key 被改名                    → 「步骤表含 industry_signal」必须炸

每一项都必须报出 ❌ 且命中期望断言；若某项仍然全绿，说明该断言是假断言。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
PROBE = str(ROOT / "tools_probe_industry_signal.py")


def run_probe() -> str:
    p = subprocess.run([PY, PROBE], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=900)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, label: str, expect: str) -> bool:
    """临时改源码 → 跑探针 → 无条件还原（并校验还原后与备份逐字节一致）。"""
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe()
        fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
        hit = [ln for ln in fails if expect in ln]
        crashed = "Traceback" in out
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}"
              f"{'  [探针崩溃]' if crashed else ''}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:2]:
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
            print(f"!!!! 还原失败：{target} 与备份不一致，请立即人工恢复 !!!!")


CASES = [
    # ---------- 计算层 ----------
    ("industry_signal.py",
     "    less = int((v < value).sum())\n"
     "    equal = int((v == value).sum())\n"
     "    return (less + 0.5 * equal) / n * 100.0",
     "    return float((v <= value).mean() * 100.0)",
     "1) 分位去掉中位秩（改为 (v<=x) 占比）", "全并列"),

    ("industry_signal.py",
     "    if n == 0:\n        return None",
     "    if n == 0:\n        return 0.0",
     "2) 空序列返回 0.0 冒充算出来", "空序列"),

    ("industry_signal.py",
     'universe["mv_pct"] < MV_PCT_MAX',
     'universe["mv_pct"] <= MV_PCT_MAX',
     "3) 命中判据 < 改成 <=", "mv_pct == 30.0 不命中"),

    ("industry_signal.py",
     'universe["amt_pct"] > AMT_PCT_MIN',
     'universe["amt_pct"] >= AMT_PCT_MIN',
     "4) 命中判据 > 改成 >=", "严格不等式"),

    ("industry_signal.py",
     'hits.sort_values(["amt_pct", "mv_pct", "code"], ascending=[False, True, True])',
     'hits.sort_values(["amt_pct", "mv_pct", "code"], ascending=[True, True, True])',
     "5) 排序方向反转（热度升序）", "非增排列"),

    ("industry_signal.py",
     'picks = hits.head(MAX_PICKS).to_dict("records")',
     'picks = hits.head(MAX_PICKS + 5).to_dict("records")',
     "6) 不截断（head 放宽 5 条）", "截断为 MAX_PICKS=3"),

    ("industry_signal.py",
     '"hits_count": int(len(hits)),',
     '"hits_count": int(len(picks)),',
     "7) hits_count 报成 picks 长度", "hits_count 报全量"),

    ("industry_signal.py",
     "MIN_TRADE_DAYS = 20",
     "MIN_TRADE_DAYS = 0",
     "8) MIN_TRADE_DAYS=0（样本不足也硬算）", "insufficient"),

    ("industry_signal.py",
     'd["mv_share"] = d["float_mv_yi"] / d.groupby("date")["float_mv_yi"].transform("sum") * 100.0',
     'd["mv_share"] = d["float_mv_yi"] / d["float_mv_yi"].sum() * 100.0',
     "9) 占比分母改成全期总和（每日不自洽）", "之和仍 = 100"),

    ("industry_signal.py",
     "MIN_SECTION_RATIO = 0.90",
     "MIN_SECTION_RATIO = 0.0",
     "10) 完整性守卫关闭（残缺截面放行）", "整日剔除"),

    ("industry_signal.py",
     '    d = d[pd.to_numeric(d["float_mv_yi"], errors="coerce").fillna(0) > 0]',
     '    d = d.copy()\n'
     '    d["float_mv_yi"] = pd.to_numeric(d["float_mv_yi"], errors="coerce").fillna(0)',
     "11) 缺市值行业不剔除（填 0 参与占比）", "缺市值的行业被剔除"),

    ("industry_signal.py",
     '"mv_window_actual": int(universe["mv_window_len"].max()),',
     '"mv_window_actual": MV_WINDOW,',
     "12) mv_window_actual 报声明值（假装满窗）", "mv_window_actual 报实际"),

    ("industry_signal.py",
     "    if not picks:\n"
     "        lines.append(f\"- 今日无同时满足两条件的行业（全 {sig.get('industry_count', '—')} 个行业已逐一核对）。\")",
     "    if not picks:\n        return \"\", None",
     "13) 0 命中时不出段落", "0 命中仍出段落"),

    ("industry_signal.py",
     '        f"### 🔥 低市值占比 + 高资金热度行业（{date}）",',
     '        f"### 🔥 低市值占比 + 高资金热度行业（{date}） 建议买入",',
     "14) 段落里塞进操作建议词", "不出现操作建议词"),

    ("industry_signal.py",
     "from __future__ import annotations\n\nimport os",
     "from __future__ import annotations\n\nimport os\nimport streamlit  # noqa: F401",
     "15) 计算层 import streamlit", "不 import streamlit"),

    # ---------- 跑批层 ----------
    ("daily_industry_signal.py",
     "    if days != [trade_date]:",
     "    if False:",
     "16) 去掉 trade_date 自证校验", "拒收（防参数被静默忽略）"),

    ("daily_industry_signal.py",
     "    return len(df) >= whitelist_n * SECTION_RATIO",
     "    return True",
     "17) section_ok 恒真（残缺也算达标）", "111 个行业"),

    # ---------- 板块轮动 ----------
    ("daily_sector_rotation.py",
     '        removed = drop_date_from_history(today)\n'
     '        print(f"❌ 本日快照残缺：仅 {len(df_today)} 个板块"',
     '        full = append_history(today, df_today)\n'
     '        full.to_csv(HISTORY_PATH, index=False, encoding="utf-8")\n'
     '        removed = drop_date_from_history(today)\n'
     '        print(f"❌ 本日快照残缺：仅 {len(df_today)} 个板块"',
     "18) 板块轮动 incomplete 退回照实归档", "incomplete 分支只清理"),

    ("daily_sector_rotation.py",
     "    for rnd in range(PAGE_ROUNDS):",
     "    for rnd in range(1):",
     "19) fetch_snapshot 退回单轮（不补齐失败页）", "分轮重试"),

    # ---------- 接线 ----------
    ("digest.py",
     "                     section_industry_signal(),\n",
     "",
     "20) digest 不再调用行业信号段", "摘要里出现行业信号段"),

    ("run_daily.py",
     '    {"key": "industry_signal", "script": "daily_industry_signal.py", "label": "行业信号",',
     '    {"key": "industry_signal_renamed", "script": "daily_industry_signal.py", "label": "行业信号",',
     "21) run_daily 步骤 key 被改名（步骤静默消失）", "步骤表含 industry_signal"),
]


def main() -> int:
    ok = 0
    bad = []
    for target, old, new, label, expect in CASES:
        if mutate(target, old, new, label, expect):
            ok += 1
        else:
            bad.append(label)

    print("=" * 60)
    print(f"造错反验：{ok}/{len(CASES)} 项被断言抓住")
    if bad:
        print("以下造错未被抓住（存在假断言，必须修断言或修代码）：")
        for b in bad:
            print(f"  · {b}")
        return 3
    print("全部通过：这些断言确实能抓 bug。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
