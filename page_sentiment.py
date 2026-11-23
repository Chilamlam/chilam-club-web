# -*- coding: utf-8 -*-
"""
短线情绪派生指标展示层（读 data/sentiment/derived.json）

只做渲染，不做计算。所有数字来自 sentiment.py 的硬计算产物，
本文件不联网、不推断、不给缺失值补默认——拿不到就显式说明拿不到。

嵌入位置：app.py 的「全市场情绪看板」，紧挨连板情绪天梯之前。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

import sentiment as sm
from stock_search import matches_stock

def _pct(v, digits: int = 1, sign: bool = False) -> str:
    """None/NaN 一律显示 —，禁止用 0 冒充缺失值。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    if f != f:  # NaN
        return "—"
    return f"{f:+.{digits}f}%" if sign else f"{f:.{digits}f}%"


def _render_kpi(derived: dict) -> None:
    promo = derived.get("promotion") or {}
    premium = derived.get("premium") or {}
    gap = derived.get("ladder_gap") or {}
    breadth = derived.get("breadth") or {}

    c1, c2, c3, c4 = st.columns(4)

    rates = promo.get("rates") or {}
    r12 = rates.get("1进2")
    with c1:
        if r12:
            st.metric(
                "1进2 晋级率",
                _pct(r12["rate"] * 100),
                delta=f"{r12['promoted']}/{r12['base']} 只",
                delta_color="off",
                help="昨日首板股中，今日继续涨停的比例；统计截止今日收盘。",
            )
        else:
            st.metric("1进2 晋级率", "—")
            st.caption(promo.get("reason", "缺少昨日归档"))

    with c2:
        if premium.get("status") in ("ok", "insufficient"):
            st.metric(
                "昨日连板股今日中位涨跌幅",
                _pct(premium["median_pct"], 2, sign=True),
                delta=f"上涨占比 {premium['win_rate'] * 100:.0f}% | {premium['n']} 只",
                delta_color="off",
                help="昨日连板股今日收盘涨跌幅的中位数；上涨占比是样本内上涨只数比例。",
            )
            if premium.get("missing"):
                st.caption(f"（{premium['missing']} 只今日行情缺失，未计入）")
        else:
            st.metric("昨日连板股今日中位涨跌幅", "—")
            st.caption(premium.get("reason", "数据缺失"))

    with c3:
        if gap.get("status") == "ok":
            st.metric(
                "梯队最高高度",
                f"{gap['max_height']} 板",
                delta=(f"断层于 {gap['gaps']} 板" if gap.get("gaps") else "梯队连续"),
                delta_color="inverse" if gap.get("gaps") else "normal",
                help="最高连板数与中间空档分别统计；最高板降低不等于出现中间空档。",
            )
            st.caption(f"今日涨停 {gap['total']} 家")
        else:
            st.metric("梯队最高高度", "—")
            st.caption(gap.get("reason", "数据缺失"))

    with c4:
        if breadth.get("status") == "ok":
            d = breadth["divergence"]
            st.metric(
                "均值−中位 背离",
                f"{d:+.2f} pct",
                delta=f"均值 {breadth['mean_pct']:+.2f}% / 中位 {breadth['median_pct']:+.2f}%",
                delta_color="off",
                help="全市场个股涨跌幅的算术均值减中位数，单位为百分点。不是指数收益或权重贡献。",
            )
        else:
            st.metric("均值−中位 背离", "—")
            st.caption(breadth.get("reason", "数据缺失"))

def _render_promotion_table(promo: dict) -> None:
    rates = (promo or {}).get("rates") or {}
    if not rates:
        return
    paths = sorted(rates, key=lambda path: int(path.split("进")[0]), reverse=True)
    with st.expander("🪜 各高度晋级概况", expanded=True):
        rows = []
        for k in paths:
            v = rates[k]
            rows.append({
                "晋级路径": k,
                "昨日基数": v["base"],
                "今日晋级": v["promoted"],
                "未晋级": v["base"] - v["promoted"],
                "晋级率": f"{v['rate'] * 100:.1f}%",
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)
        st.caption("按昨日各高度的股票逐只核对今日涨停归档；未晋级不等于跌停，今日连板数为「—」表示未在当日涨停归档中找到。")
    members = [dict(r, path=path) for path in paths for r in rates[path].get("members", [])]
    if not members:
        return
    scope = f"promotion_{promo.get('prev_date', '')}_{promo.get('date', '')}"
    with st.expander(f"按昨日板数查看个股（共 {len(members)} 只）", expanded=False):
        a, b = st.columns(2)
        path = a.selectbox("昨日板数", paths + ["全部高度"], key=scope+"_height",
            format_func=lambda p: (f"昨日{p.split('进')[0]}板 · {p}（{rates[p]['base']}只）" if p in rates else p))
        result = b.radio("今日结果", ["全部", "晋级", "未晋级"], horizontal=True, key=scope+"_result")
        query = st.text_input("在晋级明细中查找股票", placeholder="名称、代码或拼音首字母，如 sjkj", key=scope+"_query")
        filtered = [r for r in members if (path == "全部高度" or r["path"] == path)
                    and (result == "全部" or r["promoted"] == (result == "晋级"))
                    and matches_stock(query, r["name"], r["code"])]
        filtered.sort(key=lambda r: (-r["previous_height"], not r["promoted"], r["code"]))
        if not filtered:
            st.info("当前条件下没有股票，可切换板数、晋级结果或清空搜索。")
            return
        frame = pd.DataFrame([{"昨日板数": f"{r['previous_height']}板", "名称": r["name"], "代码": r["code"],
                    "今日结果": "晋级" if r["promoted"] else "未晋级",
                    "今日连板数": str(r["current_height"]) if r["current_height"] is not None else "—"}
                    for r in filtered])
        page_size = 20
        pages = (len(frame) + page_size - 1) // page_size
        signature = (path, result, query, len(frame))
        if st.session_state.get(scope+"_filter") != signature:
            st.session_state[scope+"_page"] = 1
            st.session_state[scope+"_filter"] = signature
        page = st.selectbox("明细页", range(1, pages+1), key=scope+"_page",
                            format_func=lambda n: f"第 {n} / {pages} 页", disabled=pages == 1)
        start = (page-1)*page_size
        st.caption(f"匹配 {len(frame)} 只 · 显示第 {start+1}—{min(start+page_size, len(frame))} 只 · 每页最多 {page_size} 只")
        st.dataframe(frame.iloc[start:start+page_size], hide_index=True, width="stretch")
        st.download_button("下载当前筛选的全部明细", frame.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"晋级明细_{promo.get('date', '')}.csv", mime="text/csv", key=scope+"_download")


def _render_caliber(derived: dict) -> None:
    with st.expander("📖 统计口径", expanded=False):
        st.markdown(
            f"""
**数据来源**：涨停池来自交易所公开涨停数据归档，全市场涨幅来自 Tushare 日线，
全部为收盘后硬计算，不经过任何 AI 生成或推测。

**统计日**：`{derived.get('date', '—')}`　**对比基准日**：`{derived.get('prev_date') or '—'}`　
**归档天数**：{derived.get('archive_days', 0)} 天　**产出时间**：{derived.get('generated_at', '—')}

- 晋级率统计昨日首板股在今日继续涨停的比例。
- 连板样本涨跌幅以昨日收盘为基准；上涨占比为样本中涨幅大于零的比例，未模拟实际买卖。
- 中间高度无股票称为缺档，与最高连板数下降分别记录。
"""
        )


def render_sentiment_block() -> None:
    """在全市场看板中渲染情绪派生指标区块。数据缺失时如实说明，绝不编造。"""
    st.subheader("🌡️ 短线接力统计")

    derived = sm.load_derived()
    if not derived:
        st.info(
            "⏳ 情绪派生指标尚未生成。这些指标需要至少两个交易日的涨停归档才能计算，"
            "首次运行 `daily_sentiment.py` 后开始积累。"
        )
        return

    status = derived.get("status")
    if status == "failed":
        st.error("❌ 本次派生指标计算失败，关键数据缺失。以下不展示任何推测值。")
        for key, label in (("promotion", "晋级率"), ("premium", "连板溢价"),
                           ("ladder_gap", "梯队"), ("breadth", "市场宽度")):
            blk = derived.get(key) or {}
            if blk.get("status") != "ok":
                st.caption(f"· {label}：{blk.get('reason', '未知原因')}")
        return

    if status == "incomplete":
        st.warning("⚠️ 部分指标数据不完整，缺失项已在下方标注为「—」，未用旧值或估算填充。")

    st.caption(
        f"统计日 `{derived.get('date', '—')}` | 对比基准日 "
        f"`{derived.get('prev_date') or '—'}` | 归档 {derived.get('archive_days', 0)} 天"
    )

    # 旧派生产物没有逐股结果，用同日期的原始归档补齐；不沿用旧可靠性标签。
    promo = derived.get("promotion") or {}
    if promo.get("rates") and not any("members" in r for r in promo["rates"].values()):
        recalculated = sm.promotion_rates(sm.load_ladder_history(), derived.get("date"), derived.get("prev_date"))
        if recalculated.get("status") == "ok":
            promo = recalculated
    _render_kpi(dict(derived, promotion=promo))
    _render_promotion_table(promo)
    _render_caliber(derived)
