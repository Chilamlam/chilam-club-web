# -*- coding: utf-8 -*-
"""反向验证板块轮动「重合度相邻性」断言是否为真断言（造错反验）。

用法: <stcheck>/python.exe tools_probe_sector_rotation_reverse.py [用例编号…]
退出码 0 = 全部断言有效；3 = 存在假断言（改了错却没被抓住）。

一条永不失败的断言等于没有断言。这里用 35 种「造错」方式检验两份探针能否抓住：

  · 前 21 项打 `tools_probe_sector_rotation.py`（计算层 + 页面源码接线）
  · 后 14 项打 `tools_probe_sector_rotation_page.py`（AppTest 真渲染）

为什么必须给页面探针也做一遍：那些断言验的是「页面对用户的承诺」，而它们的
失败方向往往是**看起来完全正常**——例如把「未计算重合度」渲染成 0/10，
或把 Watch=None 渲染成「🔭 明日观察：None」。计算层的探针看不到这些。

背景（2026-10-12）：板块轮动归档自 9/21 起因取数被东财拒绝而写入残缺截面，
清洗后又留下 09-18 → 10-12 的日期空档。此时 dates[-2] 只是「上一份数据」，
照旧算重合度会打出「与上一交易日（09-18）重合 x/10」——数字正常、含义全变。
为此加了相邻性判据（MAX_PREV_GAP_DAYS / NORMAL_GAP_DAYS / _gap_days /
_prev_label / compute_streak 断点 / 「样本不连续」标签），下面的造错逐条检验
这些断言真的能抓住回归。

  sector_rotation.py（计算层）
    1) 相邻阈值抬到 9999（等于取消长空档判断）     → 「长空档：prev_is_adjacent=False」必须炸
    2) 相邻阈值收到 0（正常周末也判成缺口）         → 「相邻：prev_is_adjacent=True」必须炸
    3) _gap_days 解析失败返回 0（当成相邻）         → 「_gap_days 无法解析返回 None」必须炸
    4) _prev_label 在 gap=None 时说「上一交易日」    → 「坏日期：prev_label 不说上一交易日」必须炸
    5) _prev_label 去掉 NORMAL_GAP_DAYS 判断        → 「空档：文案不说上一交易日」必须炸
    6) NORMAL_GAP_DAYS 抬到 9999                    → 「空档：文案不说上一交易日」必须炸
    7) 「样本不连续」分支永不生效                    → 「长空档：标签样本不连续」必须炸
    8) compute_streak 去掉空档断点                  → 「streak：跨空档不累加」必须炸
    9) 重合度不看相邻性，照旧硬算                    → 「长空档：重合度为 None」必须炸
   10) overlap_denominator 恒报 n（缺口也报 10）     → 「长空档：denominator 为 None」必须炸
   11) 缺口分支也给「明日观察」条件                  → 「长空档：不给明日观察」必须炸
   12) 产物里删掉 prev_label 字段                    → 「相邻：prev_label 出现在文案里」必须炸
   13) prev_is_adjacent 恒报 True                    → 「长空档：prev_is_adjacent=False」必须炸
   14) prev_gap_days 恒报 0                          → 「相邻：gap 如实为 1」必须炸
   15) 缺口文案不报相隔天数                          → 「长空档：文案点名相隔天数」必须炸
   16) 非法日期也拼「相隔 N 天」（打出 None）        → 「坏日期：文案不出现 None」必须炸

  page_sector_rotation.py（展示层源码接线）
   17) 删掉「样本不连续」elif 分支                   → 「有样本不连续的 elif 分支」必须炸
   18) 缺口说明把常量换成硬编码 12                   → 「用 sr.MAX_PREV_GAP_DAYS」必须炸
   19) 重合度 KPI 的 help 不再区分「未计算重合度」    → 「help 会区分未计算重合度」必须炸

  review_evidence.py（摘要/复盘素材）
   20) 两处措辞只改一处用 prev_label                 → 「两处都用 prev_label」必须炸
   21) 恢复无条件拼 ov/od（删掉 None 分支）           → 「先判 None，不拼 None/None」必须炸

  —— 以下打 AppTest 页面探针（真渲染）——
   22) 整块删掉缺口说明                              → 「缺口说明出现」必须炸
   23) 缺口说明不再报相隔天数（只留日期）             → 「缺口说明带出相隔天数 24」必须炸
   24) 重合度缺失渲染成 0/10 而不是 —                 → 「重合度 KPI 显示 —」必须炸
   25) watch 去掉真值判断（打出「明日观察：None」）    → 「缺口时不给「明日观察」条件」必须炸
   26) 缺口说明把阈值常量换成硬编码 12                → 「页面判据阈值跟着常量变成 37」必须炸
   27) 旧产物（无 prev_is_adjacent）也被判成不连续     → 「旧产物重合度 help 不误报」必须炸
   28) 缺口说明被绑上「有明细才显示」                 → 「缺口说明没有被提前 return 吞掉」必须炸
   29) 「样本不连续」判读改回 st.info                 → 「判读用 warning 呈现」必须炸
   30) 缺口时干脆不渲染 Top10 明细                    → 「缺口时 Top10 明细表照常渲染」必须炸
   31) 页面自己那句说明在 gap=None 时也拼数字          → 「坏日期不打出相隔 None 天」必须炸
   32) 旧产物被误报成「归档不连续」                    → 「旧产物不误报缺口说明」必须炸
   33) 判读理由整段不上屏                              → 「判读理由原样上屏」必须炸
   34) 属性型板块不再剔除（昨日涨停顶到榜首）          → 「昨日涨停没有出现在明细表里」必须炸
   35) 排名矩阵列序反过来（最早在最左）                → 「矩阵最新一列在最左」必须炸
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
PROBES = {
    "calc": str(ROOT / "tools_probe_sector_rotation.py"),
    "page": str(ROOT / "tools_probe_sector_rotation_page.py"),
}


def run_probe(which: str) -> str:
    p = subprocess.run([PY, PROBES[which]], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=900)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, label: str, expect: str,
           probe: str = "calc") -> bool:
    """临时改源码 → 跑指定探针 → 无条件还原（校验还原后与备份逐字节一致）。

    锚点必须**唯一**：重复锚点会让 replace(…, 1) 改到非目标位置，
    于是「造错没生效」却可能被当成「断言抓住了」——两个方向都会骗人。
    """
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    cnt = backup.count(old)
    if cnt == 0:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    if cnt > 1:
        print(f"--- {label}\n    !! 锚点不唯一（出现 {cnt} 次），造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe(probe)
        fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
        hit = [ln for ln in fails if expect in ln]
        crashed = "Traceback" in out
        print(f"--- [{probe}] {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}"
              f"{'  [探针崩溃]' if crashed else ''}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:2]:
            print(f"      {ln.strip()[:140]}")
        if not hit:
            for ln in fails[:3]:
                print(f"      （其它 FAIL）{ln.strip()[:140]}")
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
    ("sector_rotation.py",
     "MAX_PREV_GAP_DAYS = 12",
     "MAX_PREV_GAP_DAYS = 9999",
     "1) 相邻阈值抬到 9999（取消长空档判断）",
     "长空档：prev_is_adjacent=False"),

    ("sector_rotation.py",
     "MAX_PREV_GAP_DAYS = 12",
     "MAX_PREV_GAP_DAYS = 0",
     "2) 相邻阈值收到 0（正常周末也判成缺口）",
     "相邻：prev_is_adjacent=True"),

    ("sector_rotation.py",
     "    except (ValueError, TypeError):\n        return None",
     "    except (ValueError, TypeError):\n        return 0",
     "3) _gap_days 解析失败返回 0（把非法日期当相邻）",
     "坏日期：_gap_days 对无法解析的输入返回 None"),

    ("sector_rotation.py",
     '        return f"上一份数据（{prev_date}，无法确认相隔天数）"',
     '        return f"上一交易日（{prev_date}）"',
     "4) _prev_label 在 gap=None 时说「上一交易日」",
     "坏日期：_prev_label 在 gap=None 时不说「上一交易日」"),

    ("sector_rotation.py",
     "    if gap > NORMAL_GAP_DAYS:",
     "    if False:",
     "5) _prev_label 去掉 NORMAL_GAP_DAYS 判断（总是说上一交易日）",
     "空档：文案**不**说「上一交易日」"),

    ("sector_rotation.py",
     "NORMAL_GAP_DAYS = 4",
     "NORMAL_GAP_DAYS = 9999",
     "6) NORMAL_GAP_DAYS 抬到 9999",
     "空档：文案**不**说「上一交易日」"),

    ("sector_rotation.py",
     "    if overlap is None and prev_is_adjacent is False:",
     "    if False:",
     "7) 「样本不连续」分支永不生效",
     "长空档：标签「样本不连续」"),

    ("sector_rotation.py",
     "        if i < end_idx:\n"
     "            gap = _gap_days(dates[i + 1], dates[i])\n"
     "            if gap is None or gap > MAX_PREV_GAP_DAYS:\n"
     "                break\n",
     "        if False:\n            pass\n",
     "8) compute_streak 去掉空档断点（跨空档累加）",
     "streak：跨空档不累加"),

    ("sector_rotation.py",
     "        if prev_is_adjacent:\n"
     '            prev_codes = set(top_for_date(history, prev_date, window_col, n)["code"])\n'
     '            overlap = len(prev_codes & set(top_df["code"]))\n',
     '        prev_codes = set(top_for_date(history, prev_date, window_col, n)["code"])\n'
     '        overlap = len(prev_codes & set(top_df["code"]))\n',
     "9) 重合度不看相邻性，照旧硬算",
     "长空档：重合度为 None"),

    ("sector_rotation.py",
     '        "overlap_denominator": n if overlap is not None else None,',
     '        "overlap_denominator": n,',
     "10) overlap_denominator 恒报 n（缺口也报 10）",
     "长空档：overlap_denominator 为 None"),

    ("sector_rotation.py",
     '                f"全市场概念板块中位数 {_fmt(universe_median)}%。",\n'
     "            ],\n"
     '            "watch": None,\n'
     "        }\n"
     "    elif overlap is None:",
     '                f"全市场概念板块中位数 {_fmt(universe_median)}%。",\n'
     "            ],\n"
     '            "watch": "明日继续观察",\n'
     "        }\n"
     "    elif overlap is None:",
     "11) 缺口分支也给「明日观察」条件",
     "长空档：不给「明日观察」条件"),

    ("sector_rotation.py",
     '        "prev_label": (_prev_label(prev_date, prev_gap_days)\n'
     "                       if prev_date else None),\n",
     "",
     "12) 产物里删掉 prev_label 字段",
     "相邻：prev_label 出现在文案里"),

    ("sector_rotation.py",
     '        "prev_is_adjacent": prev_is_adjacent,',
     '        "prev_is_adjacent": True,',
     "13) prev_is_adjacent 恒报 True",
     "长空档：prev_is_adjacent=False"),

    ("sector_rotation.py",
     '        "prev_gap_days": prev_gap_days,',
     '        "prev_gap_days": 0,',
     "14) prev_gap_days 恒报 0",
     "相邻：gap 如实为 1"),

    ("sector_rotation.py",
     '            gap_txt = f"上一份数据是 {prev_date}，相隔 {prev_gap_days} 天"',
     '            gap_txt = f"上一份数据是 {prev_date}"',
     "15) 缺口文案不报相隔天数",
     "长空档：文案点名相隔天数"),

    ("sector_rotation.py",
     "        if prev_gap_days is None:\n"
     '            gap_txt = f"上一份数据是 {prev_date}，但其日期无法解析、无法判断相隔多久"\n'
     "        else:\n"
     '            gap_txt = f"上一份数据是 {prev_date}，相隔 {prev_gap_days} 天"\n',
     '        gap_txt = f"上一份数据是 {prev_date}，相隔 {prev_gap_days} 天"\n',
     "16) 非法日期也拼「相隔 N 天」（打出相隔 None 天）",
     "坏日期：文案不出现 None"),

    # ---------- 展示层 ----------
    ("page_sector_rotation.py",
     '    elif label == "样本不连续":',
     '    elif label == "旧标签":',
     "17) 删掉「样本不连续」elif 分支",
     "页面：有「样本不连续」的 elif 判读分支"),

    ("page_sector_rotation.py",
     "f\"已超过相邻交易日判据 {sr.MAX_PREV_GAP_DAYS} 天），因此**不计算榜单重合度**、\"",
     "f\"已超过相邻交易日判据 12 天），因此**不计算榜单重合度**、\"",
     "18) 缺口说明把常量换成硬编码 12",
     "页面：缺口说明用 sr.MAX_PREV_GAP_DAYS"),

    ("page_sector_rotation.py",
     '              else "归档不连续，未计算重合度（跨空档的比较没有「日间轮动」含义）"),',
     '              else "与上一交易日 Top10 的重合数：≥7 主升，≤3 轮动"),',
     "19) 重合度 KPI 的 help 不再区分「未计算重合度」",
     "页面：重合度 KPI 的 help 会区分"),

    # ---------- 复盘素材 ----------
    ("review_evidence.py",
     '        who = sector.get("prev_label") or "上一交易日"\n'
     "        lines.append(",
     '        who = "上一交易日"\n'
     "        lines.append(",
     "20) 两处措辞只改一处用 prev_label",
     "复盘素材：两处重合度文案都用产物的 prev_label"),

    ("review_evidence.py",
     "        if ov is None:\n"
     "            # ov 为 None：归档不连续（或不足两日）→ 没有重合度可报。\n"
     "            # 绝不能打成「重合 None/None」：那是把「没算」显示成一个怪值。\n"
     '            lines = ["- Top10 无新面孔（上一份数据不连续，未计算重合度）"]\n'
     "        else:\n"
     '            who = sector.get("prev_label") or "上一交易日"\n'
     '            lines = [f"- Top10 无新面孔（与{who}重合 {ov}/{od}）"]\n',
     '        who = sector.get("prev_label") or "上一交易日"\n'
     '        lines = [f"- Top10 无新面孔（与{who}重合 {ov}/{od}）"]\n',
     "21) 恢复无条件拼 ov/od（删掉 None 分支）",
     "复盘素材：算出重合度的分支先判 None"),
]


# ============ 打 AppTest 页面探针：造错后必须**页面上看得见** ============
PAGE_CASES = [
    ("page_sector_rotation.py",
     '    if data.get("prev_is_adjacent") is False:',
     "    if False:",
     "22) 整块删掉缺口说明",
     "缺口说明出现"),

    ("page_sector_rotation.py",
     "f\"📎 归档在此前缺了交易日（上一份数据 {data.get('prev_date')}，{_gap_txt}，\"",
     "f\"📎 归档在此前缺了交易日（上一份数据 {data.get('prev_date')}，\"",
     "23) 缺口说明不再报相隔天数（只留日期）",
     "缺口说明带出相隔天数 24"),

    ("page_sector_rotation.py",
     '        "—" if overlap is None else f"{overlap}/{denom}",',
     '        f"{overlap or 0}/{denom}",',
     "24) 重合度缺失渲染成 0/10 而不是 —",
     "重合度 KPI 显示 —"),

    ("page_sector_rotation.py",
     '    if verdict.get("watch"):\n'
     '        st.caption(f"🔭 明日观察：{verdict[\'watch\']}")',
     '    if True:\n'
     '        st.caption(f"🔭 明日观察：{verdict[\'watch\']}")',
     "25) watch 去掉真值判断（打出「🔭 明日观察：None」）",
     "缺口时不给「明日观察」条件"),

    ("page_sector_rotation.py",
     "f\"已超过相邻交易日判据 {sr.MAX_PREV_GAP_DAYS} 天），因此**不计算榜单重合度**、\"",
     "f\"已超过相邻交易日判据 12 天），因此**不计算榜单重合度**、\"",
     "26) 缺口说明把阈值常量换成硬编码 12",
     "页面判据阈值跟着常量变成 37",
     "page"),

    ("page_sector_rotation.py",
     'if data.get("prev_is_adjacent") is not False',
     'if data.get("prev_is_adjacent") is not None',
     "27) 旧产物（无 prev_is_adjacent）也被判成不连续",
     "旧产物重合度 help 不误报"),
    # 注意：这一项只能打页面探针——它验的是「旧产物不被误报」，计算层探针看不到。

    ("page_sector_rotation.py",
     '    if data.get("prev_is_adjacent") is False:',
     '    if data.get("prev_is_adjacent") is False and data.get("top"):',
     "28) 缺口说明被绑上「有明细才显示」（明细为空时被吞掉）",
     "缺口说明没有被「无上榜板块」的提前 return 吞掉"),

    ("page_sector_rotation.py",
     '        st.warning(f"📌 周期判读：**{label}**")\n    elif label == "样本不足":',
     '        st.info(f"📌 周期判读：**{label}**")\n    elif label == "样本不足":',
     "29) 「样本不连续」判读改回 st.info",
     "判读用 warning 呈现"),

    ("page_sector_rotation.py",
     '    rows = data.get("top") or []\n    if not rows:',
     '    rows = [] if data.get("prev_is_adjacent") is False else (data.get("top") or [])\n'
     "    if not rows:",
     "30) 缺口时干脆不渲染 Top10 明细（过度保守）",
     "缺口时 Top10 明细表照常渲染"),

    ("page_sector_rotation.py",
     '        _gap_txt = (f"相隔 {_gap} 天" if _gap is not None\n'
     '                    else "日期无法解析、无法判断相隔多久")',
     '        _gap_txt = f"相隔 {_gap} 天"',
     "31) 页面自己那句说明在 gap=None 时也拼数字",
     "坏日期**不**打出「相隔 None 天」"),

    ("page_sector_rotation.py",
     '    if data.get("prev_is_adjacent") is False:',
     '    if data.get("prev_is_adjacent") is not True:',
     "32) 旧产物被误报成「归档不连续」（None 走进缺口分支）",
     "旧产物不误报缺口说明"),

    ("page_sector_rotation.py",
     '    for reason in verdict.get("reasons") or []:',
     '    for reason in (verdict.get("reasons") or [])[:0]:',
     "33) 判读理由整段不上屏",
     "计算层的缺口理由（带相隔天数）原样上屏"),

    ("sector_rotation.py",
     '    d = d[~d["name"].map(is_style_board)]',
     "    pass",
     "34) 属性型板块不再剔除（昨日涨停顶到榜首）",
     "属性型板块「昨日涨停」没有出现在明细表里",
     "page"),

    ("sector_rotation.py",
     "    col_dates = [d for d in dates if d <= as_of][-days:][::-1]  # 最新在最左",
     "    col_dates = [d for d in dates if d <= as_of][-days:]",
     "35) 排名矩阵列序反过来（最早在最左）",
     "矩阵最新一列在最左",
     "page"),
]


def main() -> int:
    only = sys.argv[1:]
    plan = ([(c, "calc") for c in CASES]
            + [(c, "page") for c in PAGE_CASES])
    if only:
        plan = [(c, p) for c, p in plan if c[3].split(")", 1)[0] in only]
    ok = 0
    bad = []
    for case, probe in plan:
        # 用例自带第 6 元素时以它为准（同一份源码改动可能只有一种探针看得见）
        which = case[5] if len(case) > 5 else probe
        if mutate(*case[:5], probe=which):
            ok += 1
        else:
            bad.append(case[3])

    print("=" * 60)
    print(f"造错反验：{ok}/{len(plan)} 项被断言抓住")
    if bad:
        print("以下造错未被抓住（存在假断言，必须修断言或修代码）：")
        for b in bad:
            print(f"  · {b}")
        return 3
    print("全部通过：这些断言确实能抓 bug。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
