# -*- coding: utf-8 -*-
"""反向验证行业信号链路断言是否为真断言（造错反验）。

用法: <stcheck>/python.exe tools_probe_industry_signal_reverse.py
退出码 0 = 全部断言有效；3 = 存在假断言（改了错却没被抓住）。

一条永不失败的断言等于没有断言。这里用 33 种「造错」方式检验两份主探针
（tools_probe_industry_signal.py 计算层 / tools_probe_industry_signal_page.py
展示层真渲染）能否抓住。每项都必须报出 ❌ 且命中期望断言，否则说明该断言是假的。

  industry_signal.py（计算层）
    1) 分位去掉中位秩（改成 (v<=x) 占比）        → 「全并列→50」必须炸
    2) 空序列返回 0.0 冒充算出来                  → 「空序列→None」必须炸
    3) 命中判据 < / > 改成 <= / >=                 → 「严格不等式（AST 级）」必须炸
    4) 排序丢掉 code 兜底键（两键并列）            → 「sort_by_priority 与输入行序无关」必须炸
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
   15) 计算层 import streamlit                     → 「不 import streamlit」必须炸
   22) compute_signal 把命中不等式抄回来            → 「只有 hit_mask 一处实现」必须炸
   23) 走势窗口长度报成 MV_WINDOW（假装满窗）       → 「窗口长度如实上报」必须炸
   24) 走势分位改用全期序列算（前视偏差）           → 「无前视偏差」必须炸

  daily_industry_signal.py（跑批层）
   16) 去掉 trade_date 自证校验                    → 「返回日期不符→拒收」必须炸
   17) section_ok 恒真                             → 「111 个行业→False」必须炸

  daily_sector_rotation.py（板块轮动）
   18) incomplete 分支改回照实归档                 → 「只清理不写归档」必须炸
   19) fetch_snapshot 退回单轮                     → 「分轮重试」必须炸

  digest.py / run_daily.py（接线）
   20) digest 不再调用行业信号段                    → 「摘要里出现行业信号段」必须炸
   21) run_daily 步骤 key 被改名                    → 「步骤表含 industry_signal」必须炸

  page_industry_signal.py（展示层，走 AppTest 真渲染探针）
   25) 散点图命中区改用写死的 30/80 判定            → 「0 命中时散点图仍渲染」必须炸
   26) no_data 分支也打印「今日无命中」文本          → 「两种「没有」不能混」必须炸
   27) 缺值显示成 0 而不是 —                        → 「缺失格显示 —」必须炸
   28) 走势默认不优先选命中行业                     → 「默认选中第一个命中行业」必须炸
   29) 缺明细时不渲染命中名单                       → 「命中名单仍然可用」必须炸
   30) 路由分支消失                                 → 「传了站内参考日期」必须炸
   31) widget key 去掉本页前缀                      → 「前缀 isig_」必须炸
   32) 展示页 import requests（偷偷联网）            → 「展示页不联网」必须炸
   33) universe_frame 只把 picks 标成命中            → 「命中标记数与 hits_count 相等」必须炸
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
PROBES = {
    "calc": str(ROOT / "tools_probe_industry_signal.py"),
    "page": str(ROOT / "tools_probe_industry_signal_page.py"),
}


def run_probe(which: str) -> str:
    p = subprocess.run([PY, PROBES[which]], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=1200)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, label: str, expect: str,
           which: str = "calc") -> bool:
    """临时改源码 → 跑对应探针 → 无条件还原（并校验还原后与备份逐字节一致）。"""
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe(which)
        fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
        hit = [ln for ln in fails if expect in ln]
        crashed = "Traceback" in out
        print(f"--- {label}  [{which}]")
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

    # 锚点随 2026-10-12 的「唯一真源」重构而更新：命中判据已收口进 hit_mask()。
    # 造错改成一次性把两个比较符都放宽，AST 断言必须炸。
    ("industry_signal.py",
     "    return (mv < float(mv_max)) & (amt > float(amt_min))",
     "    return (mv <= float(mv_max)) & (amt >= float(amt_min))",
     "3) 命中判据 < / > 改成 <= / >=", "严格不等式"),

    ("industry_signal.py",
     '    cols = [c for c in ("amt_pct", "mv_pct", "code") if c in frame.columns]',
     '    cols = [c for c in ("amt_pct", "mv_pct") if c in frame.columns]',
     "4) 排序丢掉 code 兜底键（两键并列时顺序不确定）", "code 兜底"),

    ("industry_signal.py",
     '    ascending = [c != "amt_pct" for c in cols]',
     "    ascending = [True for c in cols]",
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

    # ---------- 新增：唯一真源 / 走势口径 ----------
    ("industry_signal.py",
     "    hits = sort_by_priority(universe[hit_mask(universe, thresholds)])",
     '    hits = sort_by_priority(universe[(universe["mv_pct"] < MV_PCT_MAX)'
     ' & (universe["amt_pct"] > AMT_PCT_MIN)])',
     "22) compute_signal 把命中不等式又抄了一遍", "只有 hit_mask 一处实现"),

    ("industry_signal.py",
     "        mv_lens.append(int(len(mw)))",
     "        mv_lens.append(MV_WINDOW)",
     "23) 走势窗口长度报声明值（假装满窗）", "窗口长度如实上报"),

    ("industry_signal.py",
     "        mw = mv_s.iloc[max(0, i - MV_WINDOW + 1): i + 1].dropna()",
     "        mw = mv_s.dropna()",
     "24) 走势分位改用全期序列（前视偏差）", "无前视偏差"),

    # ---------- 展示层（AppTest 真渲染探针） ----------
    ("page_industry_signal.py",
     '    hit = d[d["is_hit"]]\n    rest = d[~d["is_hit"]]',
     '    hit = d[(d["mv_pct"] < 30.0) & (d["amt_pct"] > 80.0)]\n'
     '    rest = d[~((d["mv_pct"] < 30.0) & (d["amt_pct"] > 80.0))]',
     "25) 散点图命中区改用写死的 30/80 判定", "0 命中时散点图仍渲染", "page"),

    ("page_industry_signal.py",
     '    if status == "no_data":\n        st.error(',
     '    if status == "no_data":\n'
     '        st.info("今日无同时满足两条件的行业")\n        st.error(',
     "26) no_data 分支也打印「今日无命中」", "两种「没有」不能混", "page"),

    ("page_industry_signal.py",
     '    try:\n        if v is None or pd.isna(v):\n            return "—"',
     '    try:\n        if v is None or pd.isna(v):\n            return f"{0.0:.{nd}f}{suffix}"',
     "27) 缺值显示成 0 而不是 —", "缺失格显示 — 而不是 0", "page"),

    ("page_industry_signal.py",
     "        codes = hit_codes + [c for c in all_codes if c not in hit_codes]",
     "        codes = all_codes",
     "28) 走势默认不优先选命中行业", "默认选中第一个命中行业", "page"),

    ("page_industry_signal.py",
     '    render_picks(picks, th, mv_w, amt_w, sig.get("industry_count"))',
     '    if frame is not None:\n'
     '        render_picks(picks, th, mv_w, amt_w, sig.get("industry_count"))',
     "29) 缺明细时不渲染命中名单", "命中名单仍然可用", "page"),

    ("app.py",
     "        render_industry_signal_page(site_reference_date())",
     "        render_sector_rotation_page()",
     "30) 路由分支消失（菜单点了没反应）", "路由分支调用该页面", "page"),

    ("page_industry_signal.py",
     'TREND_KEY = "isig_trend_code"',
     'TREND_KEY = "trend_code"',
     "31) widget key 去掉本页前缀", "前缀 isig_", "page"),

    ("page_industry_signal.py",
     "import pandas as pd\nimport plotly.graph_objects as go",
     "import pandas as pd\nimport requests  # noqa: F401\n"
     "import plotly.graph_objects as go",
     "32) 展示页 import requests（偷偷联网）", "展示页不联网", "page"),

    ("industry_signal.py",
     '    df["is_hit"] = hit_mask(df, sig.get("thresholds"))',
     '    df["is_hit"] = hit_mask(df, sig.get("thresholds"))'
     ' & df["code"].isin({p["code"] for p in (sig.get("picks") or [])})',
     "33) universe_frame 只把 picks 标成命中（图上漏掉其余命中行业）",
     "命中标记数与 hits_count", "calc"),
]


def main() -> int:
    only = sys.argv[1:]
    cases = [c for c in CASES if not only or c[3].split(")", 1)[0] in only]
    ok = 0
    bad = []
    for case in cases:
        if mutate(*case):
            ok += 1
        else:
            bad.append(case[3])

    print("=" * 60)
    print(f"造错反验：{ok}/{len(cases)} 项被断言抓住")
    if bad:
        print("以下造错未被抓住（存在假断言，必须修断言或修代码）：")
        for b in bad:
            print(f"  · {b}")
        return 3
    print("全部通过：这些断言确实能抓 bug。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
