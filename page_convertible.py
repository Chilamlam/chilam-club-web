"""Convertible-bond research board: complete A/B/C split plus pattern stats."""
from __future__ import annotations

import pandas as pd
import streamlit as st
from convertible import balance_yi
from investor_journey import candidate_actions

CLASS_TABS = [("A", "🅰️ 债强于股"), ("B", "🅱️ 股强于债"), ("C", "🔗 股债同向候选"), ("N", "➖ 待进一步判断")]

PLAYBOOK = {
    "A": "收盘时转债涨幅相对正股领先至少 1.5 个百分点；分组记录后续跨日、开盘后与触价情形。",
    "B": "收盘时正股涨幅相对转债领先至少 2 个百分点；分组记录后续价格结果。",
    "C": "收盘时股债同向且达到幅度阈值；后续联动与阶段变化需另行记录。",
    "N": "未满足当前收盘阈值；保留为未分组样本。",
}


def _pct(value, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:  # NaN
        return "—"
    return f"{number:+.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number != number:
        return "—"
    return f"{number:.{digits}f}"


def _xq(code: str) -> str:
    text = str(code or "")
    if "." not in text:
        return ""
    number, market = text.split(".")[0], text.split(".")[1].upper()
    return f"https://xueqiu.com/S/{market}{number}"


def _ensure(frame: pd.DataFrame, columns) -> None:
    """旧版快照可能缺新列；页面要降级显示，而不是抛 KeyError。"""
    for column in columns:
        if column not in frame.columns:
            frame[column] = None


def _table(rows: list[dict], *, empty: str = "今日该类没有标的") -> None:
    if not rows:
        st.caption(empty)
        return
    frame = pd.DataFrame(rows)
    # Keep archived remain_size in its source unit (yuan).  Derive the displayed
    # column here so snapshots published before this fix are corrected too.
    frame["remain_size_yi"] = frame.get("remain_size", pd.Series(index=frame.index, dtype=object)).map(balance_yi)
    frame["xueqiu"] = frame["code"].map(_xq)
    frame["tag"] = frame.apply(
        lambda row: row.get("stage") or row.get("sub_tag") or "", axis=1)
    columns = ["code", "name", "stock_name", "close", "pct_chg", "stock_pct",
               "premium", "double_low", "amount_yi", "remain_size_yi", "tag", "corr20", "xueqiu"]
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    st.dataframe(
        frame[columns],
        column_config={
            "code": st.column_config.TextColumn("代码", width="small"),
            "name": st.column_config.TextColumn("转债"),
            "stock_name": st.column_config.TextColumn("正股"),
            "close": st.column_config.NumberColumn("价格", format="%.3f"),
            "pct_chg": st.column_config.NumberColumn("涨幅%", format="%+.2f"),
            "stock_pct": st.column_config.NumberColumn("正股%", format="%+.2f"),
            "premium": st.column_config.NumberColumn("溢价率%", format="%.2f"),
            "double_low": st.column_config.NumberColumn("双低", format="%.2f"),
            "amount_yi": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
            "remain_size_yi": st.column_config.NumberColumn("余额(亿元)", format="%.2f"),
            "tag": st.column_config.TextColumn("节奏/标签"),
            "corr20": st.column_config.NumberColumn("20日相关", format="%.2f"),
            "xueqiu": st.column_config.LinkColumn("雪球", display_text="❄️"),
        },
        use_container_width=True, hide_index=True,
    )


def _metric_row(items: list[tuple[str, str, str | None]]) -> None:
    columns = st.columns(len(items))
    for column, (label, value, help_text) in zip(columns, items):
        with column:
            st.metric(label, value, help=help_text)


def _render_overview(payload: dict) -> None:
    market = payload.get("market") or {}
    classes = payload.get("classes") or {}
    _metric_row([
        ("A 债强于股", str((classes.get("A") or {}).get("count", 0)), PLAYBOOK["A"]),
        ("B 股强于债", str((classes.get("B") or {}).get("count", 0)), PLAYBOOK["B"]),
        ("C 股债同向候选", str((classes.get("C") or {}).get("count", 0)), PLAYBOOK["C"]),
        ("N 待进一步判断", str((classes.get("N") or {}).get("count", 0)), PLAYBOOK["N"]),
    ])
    _metric_row([
        ("上涨/下跌", f"{market.get('up', 0)}/{market.get('down', 0)}", None),
        ("中位涨幅", _pct(market.get("median_pct")), None),
        ("平均双低", _num(market.get("avg_double_low")), "价格 + 溢价率"),
        ("合计成交额", f"{_num(market.get('amount_yi'), 0)} 亿", None),
    ])

    st.caption("收盘筛选用于缩小 A/B/C 的观察范围；两个收盘涨跌幅不能确认盘中是否联动。"
               "N 收录未满足阈值的标的，真正的联动关系仍需对照股债分时走势。")
    labels = dict(CLASS_TABS)
    choice = st.radio("查看分类", [labels[key] for key, _ in CLASS_TABS],
                      horizontal=True, key="cb_class_choice", label_visibility="collapsed")
    key = next(key for key, label in CLASS_TABS if label == choice)
    part = classes.get(key) or {}
    st.caption(f"{PLAYBOOK[key]}　共 {part.get('count', 0)} 只，按当日涨幅排序。")
    _table(part.get("rows") or [])
    candidate_actions(pd.DataFrame(part.get("rows") or []), key="cb_next", code_col="code")


def _render_patterns(payload: dict) -> None:
    patterns = payload.get("patterns") or {}
    aggregate = patterns.get("aggregate") or {}
    window = patterns.get("window") or {}
    if not aggregate:
        st.caption("模式统计尚未生成：需要至少两个已归档交易日。")
        return
    st.caption(f"样本窗口：{window.get('from', '—')} ~ {window.get('to', '—')}，"
               f"共 {window.get('days', 0)} 个已完成交易日的次日结果。"
               "每个统计都写明样本数与价格比较口径；仅为历史理论价差，未扣费用与滑点。")

    def block(name: str, spec: dict, metric: str = "overnight", *,
              show_win: bool = True) -> None:
        stat = spec.get(metric) or {}
        if metric == "dip":
            note = f"触及 {stat.get('n', 0)}/{stat.get('total', 0)} 个样本"
        else:
            note = f"样本 {stat.get('n', 0)} 个"
        cols = [(f"{name}（{note}）", f"{_num(stat.get('mean'))}%", None),
                ("中位", _pct(stat.get("median")), None)]
        if show_win:
            cols.append(("正收益占比", f"{_num(stat.get('win'), 0)}%",
                         "样本中结果大于 0 的比例"))
        _metric_row(cols)

    rank1 = aggregate.get("rank1") or {}
    rank2 = aggregate.get("rank2") or {}
    st.markdown("**跨日价差（昨收 → 次日开盘）**")
    block("当日最强第一", rank1)
    block("当日最强第二", rank2)
    st.markdown("**开盘至收盘理论价差（次日开盘 → 次日收盘）**")
    block("昨日第一", rank1, "open_to_close")
    block("昨日第二", rank2, "open_to_close")
    st.markdown("**开盘下方 3% 触及情形（次日最低价触及该价位后，按收盘价计算理论价差）**")
    block("昨日第一", rank1, "dip")
    block("昨日第二", rank2, "dip")
    st.markdown("**次日最低 → 收盘（事后区间参考，不作收益统计）**")
    block("昨日第一", rank1, "low_to_close", show_win=False)
    block("昨日第二", rank2, "low_to_close", show_win=False)
    st.caption("「最低→收盘」非负，仅展示事后最低价到收盘的空间；最低点无法提前确定，"
               "因此不统计正收益占比，也不当作策略收益。")

    swap = patterns.get("swap") or {}
    if swap.get("n"):
        st.caption(f"次日排序互换：昨日第二今日反超第一 {swap.get('n', 0)} 次样本中"
                   f"发生 {_num(swap.get('rate'), 0)}%。它只比较次日涨幅；"
                   "盘中名次何时互换及其后续价格，需要分时数据另行记录。")

    st.markdown("**逐日明细**")
    rows = patterns.get("rows") or []
    if rows:
        frame = pd.DataFrame(rows)
        _ensure(frame, ("overnight", "open_to_close", "dip_to_close", "next_high"))
        frame["rank"] = frame["rank"].map({1: "第一", 2: "第二"})
        st.dataframe(
            frame[["date", "next_date", "rank", "code", "pct_chg", "amount_yi",
                   "overnight", "open_to_close", "dip_to_close", "next_high"]],
            column_config={
                "date": st.column_config.TextColumn("交易日"),
                "next_date": st.column_config.TextColumn("次日"),
                "rank": st.column_config.TextColumn("名次"),
                "code": st.column_config.TextColumn("代码"),
                "pct_chg": st.column_config.NumberColumn("当日涨幅%", format="%+.2f"),
                "amount_yi": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
                "overnight": st.column_config.NumberColumn("跨日%", format="%+.2f"),
                "open_to_close": st.column_config.NumberColumn("开盘→收盘%", format="%+.2f"),
                "dip_to_close": st.column_config.NumberColumn("触价→收盘%", format="%+.2f"),
                "next_high": st.column_config.NumberColumn("跨日最高%", format="%+.2f"),
            }, use_container_width=True, hide_index=True,
        )

    st.markdown("**C 类收盘形态分组（规则标签，实际题材阶段需结合盘面）**")
    stages = patterns.get("stages") or {}
    definitions = patterns.get("definitions") or {}
    rules = definitions.get("stage_rules") or {}
    stage_rows = []
    for stage, spec in stages.items():
        total = spec.get("n") or 0
        stage_rows.append({
            "stage": stage,
            "rule": rules.get(stage, ""),
            "n": total,
            "next_pct": (spec.get("next_pct") or {}).get("mean"),
            "overnight": (spec.get("overnight") or {}).get("mean"),
            "open_to_close": (spec.get("open_to_close") or {}).get("mean"),
            "win": (spec.get("next_pct") or {}).get("win"),
        })
    if stage_rows:
        st.dataframe(
            pd.DataFrame(stage_rows),
            column_config={
                "stage": st.column_config.TextColumn("阶段"),
                "rule": st.column_config.TextColumn("判定规则", width="large"),
                "n": st.column_config.NumberColumn("样本", format="%d"),
                "next_pct": st.column_config.NumberColumn("次日涨跌%", format="%+.2f"),
                "overnight": st.column_config.NumberColumn("跨日%", format="%+.2f"),
                "open_to_close": st.column_config.NumberColumn("开盘→收盘%", format="%+.2f"),
                "win": st.column_config.NumberColumn("次日正收益占比%", format="%.0f"),
            }, use_container_width=True, hide_index=True,
        )
        st.caption("表内同时列出各阶段的记录数和结果分布；记录较少的阶段仍保留明细。"
                   "阶段划分由收盘数据得到，盘中可能变化。")

    b_spec = aggregate.get("b_overnight") or {}
    st.markdown("**B 类观察样本：正股收盘强、转债涨幅落后**")
    st.caption("以正股涨幅和收盘位置筛选，不能仅据日线确认正股全天维持强度。")
    if b_spec.get("n"):
        _metric_row([
            ("样本", str(b_spec.get("n")), "正股涨幅≥3%、收盘位于日内上25%、转债落后≥2个百分点"),
            ("跨日均值", _pct((b_spec.get("overnight") or {}).get("mean")), None),
            ("开盘→收盘", _pct((b_spec.get("open_to_close") or {}).get("mean")), None),
            ("跨日正收益占比", f"{_num((b_spec.get('overnight') or {}).get('win'), 0)}%", None),
        ])
        b_rows = patterns.get("b_rows") or []
        if b_rows:
            st.dataframe(
                pd.DataFrame(b_rows)[["date", "next_date", "code", "stock_pct",
                                      "pct_chg", "overnight", "open_to_close"]],
                column_config={
                    "date": st.column_config.TextColumn("交易日"),
                    "next_date": st.column_config.TextColumn("次日"),
                    "code": st.column_config.TextColumn("代码"),
                    "stock_pct": st.column_config.NumberColumn("正股%", format="%+.2f"),
                    "pct_chg": st.column_config.NumberColumn("转债%", format="%+.2f"),
                    "overnight": st.column_config.NumberColumn("跨日%", format="%+.2f"),
                    "open_to_close": st.column_config.NumberColumn("开盘→收盘%", format="%+.2f"),
                }, use_container_width=True, hide_index=True,
            )
    else:
        st.caption("样本窗口内没有符合条件的样本。")


def _render_watch(payload: dict) -> None:
    watch = payload.get("watch") or {}
    patterns = payload.get("patterns") or {}
    aggregate = patterns.get("aggregate") or {}
    st.caption("该清单只记录今天的分组与历史上同类样本的价格结果；"
               "不提供交易动作，理论价差也不代表可以实际成交。")

    st.markdown("**今日涨幅前列（后续跨日与开盘后价格记录）**")
    leaders = watch.get("today_leaders") or []
    if leaders:
        for rank, row in enumerate(leaders, start=1):
            spec = aggregate.get(f"rank{rank}") or {}
            overnight = (spec.get("overnight") or {})
            open_close = (spec.get("open_to_close") or {})
            dip = (spec.get("dip") or {})
            st.markdown(
                f"{rank}. **{row.get('name')}** `{row.get('code')}`　"
                f"今日 {_pct(row.get('pct_chg'))}｜正股 {row.get('stock_name')} "
                f"{_pct(row.get('stock_pct'))}｜溢价 {_num(row.get('premium'))}%｜"
                f"成交 {_num(row.get('amount_yi'))} 亿｜{row.get('sub_tag') or row.get('stage') or '—'}"
                 f"　\n　　↳ 历史同排名样本 {spec.get('n', 0)} 个：跨日均值 "
                f"{_pct(overnight.get('mean'))}（正收益占比 {_num(overnight.get('win'), 0)}%），"
                f"开盘→收盘 {_pct(open_close.get('mean'))}，"
                f"开盘下方3%触及后的理论价差 {_pct(dip.get('mean'))}"
                f"（触及 {dip.get('n', 0)}/{dip.get('total', 0)}）"
            )
    else:
        st.caption("今日没有成交额达标的强势标的。")

    st.markdown("**昨日涨幅前列：次日价格结果**")
    realised = watch.get("yesterday") or []
    if realised:
        frame = pd.DataFrame(realised)
        _ensure(frame, ("overnight", "open_to_close", "dip_to_close"))
        st.dataframe(
            frame[["code", "name", "prev_pct", "today_pct", "overnight",
                   "open_to_close", "dip_to_close"]],
            column_config={
                "code": st.column_config.TextColumn("代码"),
                "name": st.column_config.TextColumn("转债"),
                "prev_pct": st.column_config.NumberColumn("昨日%", format="%+.2f"),
                "today_pct": st.column_config.NumberColumn("今日%", format="%+.2f"),
                "overnight": st.column_config.NumberColumn("跨日%", format="%+.2f"),
                "open_to_close": st.column_config.NumberColumn("开盘→收盘%", format="%+.2f"),
                "dip_to_close": st.column_config.NumberColumn("触价→收盘%", format="%+.2f"),
            }, use_container_width=True, hide_index=True,
        )
        st.caption("触价→收盘为空白，表示次日最低价没有触及开盘价下方 3%；"
                   "该情形仅作理论价差记录，不代表可以实际成交。")
    else:
        st.caption("昨日强势标的今日不在快照中。")

    st.markdown("**B 类分组：正股收盘涨幅高于转债**")
    candidates = watch.get("b_candidates") or []
    if candidates:
        frame = pd.DataFrame(candidates)
        st.dataframe(
            frame[["code", "name", "stock_name", "stock_pct", "pct_chg",
                   "premium", "amount_yi"]],
            column_config={
                "code": st.column_config.TextColumn("代码"),
                "name": st.column_config.TextColumn("转债"),
                "stock_name": st.column_config.TextColumn("正股"),
                "stock_pct": st.column_config.NumberColumn("正股%", format="%+.2f"),
                "pct_chg": st.column_config.NumberColumn("转债%", format="%+.2f"),
                "premium": st.column_config.NumberColumn("溢价率%", format="%.2f"),
                "amount_yi": st.column_config.NumberColumn("成交额(亿)", format="%.2f"),
            }, use_container_width=True, hide_index=True,
        )
        st.caption(f"共 {watch.get('b_total', len(candidates))} 只 B 类转债成交额达标；"
                   "上表按正股涨幅取前 8 只；正股盘中联动和尾盘表现需另行记录。")
    else:
        st.caption("今日没有符合条件的 B 类标的。")


def _render_rules() -> None:
    st.markdown("""
**当前是收盘筛选口径，只用到当日转债与正股涨跌幅。** A/B/C 为待核验候选；其余放入 N：

| 类别 | 判定（按顺序执行） | 含义 |
| --- | --- | --- |
| C 股债同向候选 | 正股与转债同向，\\|正股\\|≥1.5%，且 \\|转债\\|≥max(0.8%, \\|正股\\|×30%) | 收盘同向且达到幅度阈值，盘中联动待核验 |
| A 债强于股 | 转债涨幅 − 正股涨幅 ≥ 1.5 个百分点 | 转债相对更强，原因需结合题材和分时走势 |
| B 股强于债 | 正股涨幅 − 转债涨幅 ≥ 2 个百分点，且正股 ≥ 2% | 正股收盘涨幅更高 |
| N 待进一步判断 | 上述都不满足 | 当前日线阈值未归类 |

**A 类的子标签**只描述可观测特征：次新（上市 60 日内）、高溢价（≥30%）、放量（成交额≥5 日均量 1.5 倍）。

**C 类收盘形态标签**（收盘后回看；逐阶段保留记录数与结果）：

- 退潮：今日跌幅≥5%，前 5 日累计涨幅≥8%
- 分歧：振幅≥8%，收盘位于当日振幅下 40%，前 5 日累计≥5%
- 高潮：今日涨幅≥15%
- 加速：今日涨幅≥5%，前 5 日累计≥8%，收盘距 20 日高点≤1%
- 回流：今日涨幅≥3%，昨日跌幅≥3%，前 5 日累计≥5%
- 启动：今日涨幅≥5%，前 5 日累计≤3%，成交额≥5 日均量 1.5 倍

**统计口径**：强势榜只取当日成交额≥3000 万元（0.3 亿）的转债；第一/第二名按当日涨幅排序。
跨日价差 = 昨收→次日开盘；开盘至收盘理论价差 = 次日开盘→次日收盘；
开盘下方 3% 触及情形 = 次日最低价触及该价位后，按次日收盘价计算理论价差；跨日最高 = 昨收→次日最高。
「次日最低→次日收盘」非负，只作事后空间参考，不统计正收益占比。
榜单以收盘数据形成，所有比例都是事后价格比较，不代表可以在对应价格取得仓位。
全部为价格理论涨幅，未扣费用与滑点；触及某价位也不表示当时可以实际成交。

**已知局限**：溢价率用最新转股价计算，下修后若基础表未同步会短暂失真；
分类不含成交额门槛，冷门债也会被分类，但不会进入强势榜与观察清单。
""")


def render_convertible_block(payload: dict | None) -> None:
    st.subheader("🛡️ 可转债收盘分组")
    if not payload or not payload.get("date"):
        st.warning("可转债快照尚未生成：等待收盘跑批产出 data/convertible.json。")
        return
    status = str(payload.get("status") or "")
    date = str(payload.get("date"))
    generated = str(payload.get("generated_at") or "")
    market = payload.get("market") or {}
    threshold = market.get("liquid_threshold_yi")
    threshold_text = f"（≥{threshold:g} 亿）" if isinstance(threshold, (int, float)) else ""
    st.caption(f"数据日期 **{date}**｜快照生成 {generated or '—'}｜"
               f"全市场 {market.get('total', 0)} 只，成交额达标 {market.get('liquid', 0)} 只"
               f"{threshold_text}")
    if status != "ok":
        st.warning(f"本次更新失败（{payload.get('status_message') or status}），"
                   f"下面仍是 {date} 的上一份快照。")
    if payload.get("notes"):
        with st.expander("口径与数据说明"):
            for note in payload["notes"]:
                st.markdown(f"- {note}")

    tabs = st.tabs(["🧭 分类总览", "📊 十日模式", "🎯 候选与结果记录", "📖 判定规则"])
    with tabs[0]:
        _render_overview(payload)
    with tabs[1]:
        _render_patterns(payload)
    with tabs[2]:
        _render_watch(payload)
    with tabs[3]:
        _render_rules()
