# -*- coding: utf-8 -*-
"""
「我的池子」每日复盘页（原自选股雷达升级）

存在意义：站内其余页面回答的都是「全市场今天发生了什么」，
这一页是唯一回答「这跟我有什么关系」的地方——
把全市场指标折算成个人视角，用户才有每天回来的理由。

对标 vibe-astock 的「昨日强势股反馈矩阵」：它有黏性不是因为数据多，
而是因为它回答「我昨天追的那个板今天怎么样了」。

数据来源分两层：
  实时层  腾讯 qt.gtimg.cn 批量报价（盘中可用，8 秒缓存）
  跑批层  data/*.csv|json（RPS 榜 / 突破池 / 龙头雷达 / 连板天梯 / 板块热度）
关键原则：拿不到就显示「暂无数据」，绝不用硬编码或旧值填空。
"""
from __future__ import annotations

import json
import os
import urllib.request

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import database
import push_binding as pb
import symbol_resolve as sr

# 中国市场配色：涨红跌绿
C_UP = "#e74c3c"
C_DOWN = "#2ecc71"
C_FLAT = "#95a5a6"

# 沪深300 必须写全前缀：裸 000300 会被当成深市股票代码，腾讯返回空。
BENCH_TX = "sh000300"
BENCH_BARE = "000300"

# 北交所号段（2026-08-29 实测：腾讯必须用 bj 前缀，用 sh/sz 一律返回空）
_BJ_PREFIX = ("92", "43", "83", "87")


# ================= 实时报价 =================

def _tx_code(raw: str) -> str:
    """纯代码 → 腾讯行情代码（**只做语法猜测，撞号会猜错**）。

    保留它只为「取不到报价时还能拼个 K 线地址」这一条退路。它按号段猜前缀，
    对 000905 / 000016 / 000300 这类沪深同号代码会猜成深市 —— 000905 会变成
    深市「厦门港务」。**正常路径请用 fetch_quotes 回填的 tx_code**，那是探过
    沪深北、按当日活跃度取舍后的真值。
    """
    c = str(raw).strip().upper().split(".")[0]
    if not c.isdigit():
        return ""
    if len(c) == 6 and c[:2] in _BJ_PREFIX:
        return f"bj{c}"
    if c.startswith(("6", "5", "9")):
        return f"sh{c}"
    return f"sz{c}"


def _num(v, default=None):
    """None 保持 None，绝不折成 0 —— 0 会被当成「今天真的没换手」。"""
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


@st.cache_data(ttl=15, show_spinner=False)
def fetch_quotes(codes: tuple) -> dict:
    """
    批量取实时报价。返回 {纯代码: {...}}，取不到的代码直接不出现在结果里
    （由调用方显示「暂无数据」，不做任何补值）。
    沪深300 基准始终随批请求，键为 '000300'。

    **市场前缀走 symbol_resolve 消歧，不再按号段猜**（2026-09-03 修）：
    原先「6/5/9 开头算沪市，否则算深市」会让 000905 静默取到深市「厦门港务」
    8.90 元 —— 页面照样画表、涨跌幅照样有数字，只是全是另一只标的的。
    现在沪深北都探，再按「当日是否真在交易 + 宽基指数白名单」取舍。

    每条报价额外带 `tx_code`（消歧后的真值，供 K 线与雪球链接复用）与
    `ambiguous`（是否还有同号活跃标的，供页面标注）。
    """
    probe: dict[str, list[str]] = {}
    for c in codes:
        b = _bare(c)
        if not b:
            continue
        if b.isdigit() and len(b) == 6:
            probe[b] = [f"{p}{b}" for p in sr.prefixes_for(b)]
        else:
            t = _tx_code(b)
            if t:
                probe[b] = [t]
    probe.setdefault(BENCH_BARE, [BENCH_TX])      # 基准始终随批，且写死沪市

    flat = [t for lst in probe.values() for t in lst]
    got = sr.fetch_quotes(flat)
    if not got:                                   # None(失败) 与 {}(全不存在) 都无可展示
        return {}

    out = {}
    for b, tx_list in probe.items():
        cands = [got[t] for t in tx_list if t in got]
        if not cands:
            continue
        cands.sort(key=sr.rank_key)
        q = cands[0]
        out[b] = {
            "name": q["name"],
            "price": q["price"],
            "prev": q["last_close"],
            "pct": q["pct_chg"],
            "high": q["high"],
            "low": q["low"],
            "turnover": _num(q.get("turnover")),
            "amplitude": _num(q.get("amplitude")),
            "mv": q.get("float_mv_yi") or 0.0,
            "time": q["update_time"],
            "tx_code": q["tx_code"],
            "kind": q.get("kind", ""),
            # 撞号判据与文案都在 symbol_resolve 单点定义，页面不再自己写一份
            # ——「候选>1 才算撞号，且不掺 alive」这条规则一旦两处实现必漂移。
            "ambiguous": sr.is_ambiguous(cands),
            "alternatives": sr.describe_alternatives(cands),
        }
    return out


# ================= 跑批数据加载 =================

@st.cache_data(ttl=600, show_spinner=False)
def _load_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _bare(code) -> str:
    return str(code).strip().upper().split(".")[0]


def _index_by_bare(df: pd.DataFrame, col: str = "ts_code") -> dict:
    """把 DataFrame 按纯数字代码建索引，便于与用户输入的裸代码匹配。"""
    if df.empty or col not in df.columns:
        return {}
    out = {}
    for r in df.to_dict("records"):
        out[_bare(r.get(col))] = r
    return out


# ================= 各区块 =================

def _render_overview(rows: list[dict], market_pct: float | None) -> None:
    """我的池子整体表现 + 与全市场对比。"""
    live = [r for r in rows if r["_has_quote"]]
    if not live:
        st.warning("⚠️ 未能取到任何自选标的的实时报价，行情源可能暂时不可用。此处不显示估算值。")
        return

    pcts = pd.Series([r["涨跌幅"] for r in live], dtype="float64")
    up = int((pcts > 0).sum())
    down = int((pcts < 0).sum())
    flat = int(len(pcts) - up - down)
    med = float(pcts.median())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("我的池子中位涨幅", f"{med:+.2f}%",
              help="用中位数而非平均，避免个别大涨股拉飞整体观感")
    c2.metric("红/绿/平", f"{up} / {down} / {flat}")
    if market_pct is not None:
        diff = med - market_pct
        c3.metric("沪深300 今日", f"{market_pct:+.2f}%")
        c4.metric("我的池子 vs 大盘", f"{diff:+.2f}%",
                  delta=f"{'跑赢' if diff > 0 else '跑输'}",
                  delta_color="normal" if diff > 0 else "inverse",
                  help="池子中位涨幅 − 沪深300 涨幅")
    else:
        c3.metric("沪深300 今日", "—")
        c4.metric("我的池子 vs 大盘", "—")
        st.caption("⚠️ 沪深300 报价缺失，无法计算相对强弱。")

    # 涨跌分布条形图
    fig = go.Figure()
    srt = sorted(live, key=lambda r: r["涨跌幅"], reverse=True)
    fig.add_trace(go.Bar(
        x=[r["名称"] or r["代码"] for r in srt],
        y=[r["涨跌幅"] for r in srt],
        marker_color=[C_UP if r["涨跌幅"] > 0 else (C_DOWN if r["涨跌幅"] < 0 else C_FLAT) for r in srt],
        hovertemplate="%{x}<br>涨跌 %{y:.2f}%<extra></extra>",
    ))
    if market_pct is not None:
        fig.add_hline(y=market_pct, line_dash="dash", line_color="#f39c12",
                      annotation_text=f"沪深300 {market_pct:+.2f}%", annotation_position="right")
    fig.add_hline(y=0, line_color="#888", line_width=1)
    fig.update_layout(height=300, margin=dict(l=10, r=10, t=30, b=10),
                      template="plotly_white", showlegend=False,
                      title="我的池子今日涨跌分布（虚线为大盘基准）",
                      yaxis_title="涨跌幅 %")
    st.plotly_chart(fig, use_container_width=True)


def _render_signals(rows: list[dict]) -> None:
    """我的池子里谁被站内策略命中——把全市场榜单折算成个人视角。"""
    hits = [r for r in rows if r["_signals"]]
    st.markdown("#### 🎯 站内策略命中")
    if not hits:
        st.info("今日我的池子里没有标的进入 RPS 强势榜、突破池、龙头雷达或涨停池。")
        return
    st.caption(f"{len(hits)} 只自选被站内策略命中：")
    for r in hits:
        tags = "　".join(r["_signals"])
        color = C_UP if r["涨跌幅"] > 0 else (C_DOWN if r["涨跌幅"] < 0 else C_FLAT)
        st.markdown(
            f"**{r['名称'] or r['代码']}** `{r['代码']}` "
            f"<span style='color:{color}'>{r['涨跌幅']:+.2f}%</span> — {tags}",
            unsafe_allow_html=True,
        )


def _render_sector(rows: list[dict], df_sector: pd.DataFrame) -> None:
    """我的持仓所在板块今天是领涨还是垫底。"""
    st.markdown("#### 🏭 我的池子所在板块今日强弱")
    if df_sector.empty or "industry" not in df_sector.columns:
        st.info("板块热度数据暂无（data/sector_hot.csv 未生成）。")
        return

    sec_map = {}
    for r in df_sector.to_dict("records"):
        sec_map[str(r.get("industry"))] = r

    # 板块在全市场的排名分位
    ranked = df_sector.sort_values("pct_chg", ascending=False).reset_index(drop=True)
    rank_map = {str(r["industry"]): i + 1 for i, r in ranked.iterrows()}
    total = len(ranked)

    mine = {}
    for r in rows:
        ind = r.get("行业")
        if not ind or ind == "-":
            continue
        mine.setdefault(ind, []).append(r["名称"] or r["代码"])

    if not mine:
        st.info("自选标的暂无行业归属信息（需 data/market_snapshot.csv 或 RPS 榜覆盖）。")
        return

    recs = []
    for ind, names in mine.items():
        s = sec_map.get(ind)
        rk = rank_map.get(ind)
        recs.append({
            "板块": ind,
            "我的标的": "、".join(names),
            "板块涨幅": float(s["pct_chg"]) if s else None,
            "全市场排名": f"{rk} / {total}" if rk else "未入热度榜",
        })
    df = pd.DataFrame(recs).sort_values("板块涨幅", ascending=False, na_position="last")
    st.dataframe(
        df, hide_index=True, use_container_width=True,
        column_config={
            "板块涨幅": st.column_config.NumberColumn("板块涨幅", format="%.2f%%"),
        },
    )
    st.caption(
        f"板块热度榜只收录当日涨幅居前的 {total} 个行业，"
        "「未入热度榜」表示该板块今日不在领涨行列，不代表数据缺失。"
    )


# ================= 关键位预警 =================

@st.cache_data(ttl=900, show_spinner=False)
def _fetch_daily_kline(tx_code: str, limit: int = 260) -> pd.DataFrame:
    """取前复权日K（腾讯）。失败返回空表，由调用方标注失败原因。

    URL 由 symbol_resolve.kline_url 生成：北交所必须走 newfqkline（老 fqkline 对
    bj 代码只返回当天 1 根，既不报错也不为空，会被误判成「K线不足」），
    limit 也在那里夹到 800（>=801 静默退回 640 根）。
    """
    url = sr.kline_url(tx_code, limit=limit)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, timeout=12).read().decode("utf-8", "ignore"))
    except Exception:
        return pd.DataFrame()
    node = (d.get("data") or {}).get(tx_code) or {}
    arr = node.get("qfqday") or node.get("day") or []
    if not arr:
        for v in node.values():
            if isinstance(v, list) and v:
                arr = v
                break
    rows = []
    for it in arr:
        if len(it) < 6:
            continue
        try:
            rows.append({
                "datetime": str(it[0]),
                "open": float(it[1]), "close": float(it[2]),
                "high": float(it[3]), "low": float(it[4]), "vol": float(it[5]),
            })
        except (ValueError, TypeError):
            continue
    return pd.DataFrame(rows)


def _render_key_levels(rows: list[dict]) -> None:
    """
    调技术分析层，给出自选标的当前离关键位（中枢边界、Fibnode、汇聚区）多远。
    逐个标的拉日K较慢，因此默认只算用户勾选的标的。
    """
    st.markdown("#### 📏 关键位距离扫描（缠论中枢 + 帝纳波利 Fibnode）")
    st.caption(
        "复用「实时行情 + 技术分析」页的同一套计算层。逐只标的需拉取日K，"
        "为控制加载时间默认不自动全量计算，请勾选要扫描的标的。"
    )

    options = [f"{r['名称'] or r['代码']} ({r['代码']})" for r in rows]
    code_of = {opt: r["代码"] for opt, r in zip(options, rows)}
    # 报价阶段已经探过沪深北并按活跃度定了市场，这里直接复用那个真值。
    # 若再按号段猜一遍，000905 的日K会拉成深市厦门港务的，缠论结论跟着一起错。
    tx_of = {opt: r.get("_tx_code") or "" for opt, r in zip(options, rows)}
    picked = st.multiselect(
        "选择要扫描的自选标的（建议一次不超过 8 只）",
        options, default=options[:min(3, len(options))],
        key="wl_level_pick",
    )
    if not picked:
        st.info("未选择标的。")
        return
    if len(picked) > 12:
        st.warning("一次最多扫描 12 只，已截断。")
        picked = picked[:12]

    try:
        import tech_analysis as ta
    except Exception as e:
        st.error(f"❌ 技术分析计算层加载失败：{type(e).__name__}")
        return

    recs = []
    prog = st.progress(0.0, text="正在扫描…")
    for i, opt in enumerate(picked, 1):
        code = code_of[opt]
        tx = tx_of.get(opt) or _tx_code(code)
        prog.progress(i / len(picked), text=f"正在扫描 {opt}…")
        if not tx:
            recs.append({"标的": opt, "结论": "代码无法识别", "最近关键位": "—", "距离": None})
            continue
        df = _fetch_daily_kline(tx)
        if df.empty:
            recs.append({"标的": opt, "结论": "❌ 日K数据获取失败", "最近关键位": "—", "距离": None})
            continue
        res = ta.analyze(df)
        if not res.get("ok"):
            recs.append({"标的": opt, "结论": f"⚠️ {res.get('reason', '结构分析不可用')}",
                         "最近关键位": "—", "距离": None})
            continue

        close = float(res["close"])
        levels = res.get("levels") or []
        # tech_analysis._key_levels 返回 [{"price","label","side","gap_pct"}]，
        # 已按 |gap_pct| 升序排好，取第一条即离现价最近的关键位。
        best = levels[0] if levels else None
        state = res.get("state") or {}
        recs.append({
            "标的": opt,
            "结论": str(state.get("chan", "—"))[:60],
            "最近关键位": (f"{best['label']} {best['price']:.2f}（{best['side']}）"
                           if best else "—"),
            "距离": (abs(float(best["gap_pct"])) if best else None),
        })
    prog.empty()

    df_out = pd.DataFrame(recs)
    st.dataframe(
        df_out, hide_index=True, use_container_width=True,
        column_config={"距离": st.column_config.NumberColumn("距关键位", format="%.2f%%")},
    )
    st.caption("「距关键位」是现价与最近关键价位的百分比距离，越小代表越接近变盘参考位。仅为结构测算，不构成买卖建议。")


# ================= 微信推送绑定 =================
# 实现已抽到 push_binding.py（同一份 UI 在会员中心/摘要页/本页三处复用）。
# 这里保留两个同名薄封装：既是本页的调用入口，也让既有自检断言继续成立。


def _ensure_wxpusher_token() -> bool:
    return pb.ensure_app_token()


def _render_push_binding(user_id: int) -> None:
    st.markdown("#### 📲 微信推送绑定")
    if not _ensure_wxpusher_token():
        st.info("站点尚未配置微信推送通道，暂不可绑定。")
        return
    # st.warning 的等待态分支在 push_binding.render 里（未扫码 ≠ 绑定失败）
    pb.render(user_id, key_prefix="wl")


# ================= 主入口 =================


def _build_rows(watchlist: list[str]) -> tuple[list[dict], float | None, dict]:
    """
    把自选代码拼成带实时报价 + 站内策略命中标记的记录列表。
    返回 (rows, 沪深300涨幅, 数据源状态)
    """
    quotes = fetch_quotes(tuple(watchlist))
    bench = quotes.get(BENCH_BARE)
    market_pct = float(bench["pct"]) if bench else None

    df_snap = _load_csv("data/market_snapshot.csv")
    df_rps = _load_csv("data/strong_stocks.csv")
    df_break = _load_csv("data/breakout_stocks.csv")
    df_etf = _load_csv("data/strong_etfs.csv")
    ladder = _load_json("data/limit_ladder.json")
    radar = _load_json("data/radar_data.json")

    i_snap = _index_by_bare(df_snap)
    i_rps = _index_by_bare(df_rps)
    i_break = _index_by_bare(df_break)
    i_etf = _index_by_bare(df_etf)
    i_ladder = {_bare(s.get("code")): s for s in (ladder.get("stocks") or [])}
    i_radar = {}
    for win in ("10d", "30d"):
        for it in ((radar.get(win) or {}).get("data") or []):
            i_radar.setdefault(_bare(it.get("ts_code")), []).append(win)

    rows = []
    for code in watchlist:
        b = _bare(code)
        q = quotes.get(b)
        snap = i_snap.get(b, {})
        rps = i_rps.get(b, {})
        brk = i_break.get(b, {})
        etf = i_etf.get(b, {})
        lad = i_ladder.get(b, {})

        name = (q or {}).get("name") or snap.get("name") or rps.get("name") or brk.get("name") or etf.get("name") or "-"
        industry = snap.get("industry") or rps.get("细分行业") or brk.get("industry") or lad.get("industry") or "-"

        signals = []
        if rps:
            signals.append(f"🔥 RPS榜 {float(rps.get('RPS_50', 0)):.1f}（在榜{rps.get('连续天数', 1)}天）")
        if etf:
            signals.append(f"💰 强势ETF榜 {float(etf.get('RPS_50', 0)):.1f}")
        if brk:
            signals.append(f"🚀 {brk.get('level', '新高突破')}")
        if lad:
            signals.append(f"🔴 涨停 {lad.get('limit_times', 1)}连板")
        for win in i_radar.get(b, []):
            signals.append(f"🚨 龙头雷达({win})")

        rows.append({
            "代码": b,
            "名称": name,
            "行业": industry,
            "现价": float(q["price"]) if q else None,
            "涨跌幅": float(q["pct"]) if q else 0.0,
            "换手率": _num((q or {}).get("turnover")),
            "振幅": _num((q or {}).get("amplitude")),
            # 雪球前缀取自消歧后的 tx_code，不再按 6/5 开头猜 —— 猜错会跳到
            # 另一只同号标的的页面（000905 会跳到深市厦门港务）。
            "雪球": sr.xueqiu_url((q or {}).get("tx_code") or _tx_code(b)),
            "_has_quote": q is not None,
            "_tx_code": (q or {}).get("tx_code") or "",
            "_ambiguous": bool((q or {}).get("ambiguous")),
            "_alternatives": (q or {}).get("alternatives") or [],
            "_signals": signals,
        })

    src = {
        "报价时间": bench["time"] if bench else None,
        "跑批日期": (df_rps["更新日期"].iloc[0] if not df_rps.empty and "更新日期" in df_rps.columns else None),
        "天梯日期": ladder.get("date"),
    }
    return rows, market_pct, src


def render_watchlist_page() -> None:
    st.header("⭐ 我的池子 · 每日复盘")
    st.caption("站内唯一回答「这跟我有什么关系」的页面——把全市场指标折算成你自己的池子视角。")

    if not auth.is_logged_in():
        st.warning("🔒 我的池子需要登录后使用，以便云端同步你的自选清单。")
        if st.button("去登录 / 注册 🔐"):
            st.switch_page("pages/auth.py")
        return

    user_id = auth.get_user_id()
    if "user_watchlist" not in st.session_state:
        st.session_state.user_watchlist = database.get_user_watchlist(user_id)
    cur = st.session_state.user_watchlist

    # 写库结果用 flash 传递：st.rerun() 会立刻重跑脚本，
    # 紧跟其前的 st.success/st.error 根本来不及被用户看到
    _flash = st.session_state.pop("wl_flash", None)
    if _flash:
        (st.success if _flash[0] == "ok" else st.error)(_flash[1])

    with st.expander("➕ 添加 / 管理自选代码", expanded=(len(cur) == 0)):
        with st.form("add_stock_form"):
            new_input = st.text_input(
                "输入股票/ETF 代码（多个用逗号隔开）",
                placeholder="000001, 600519, 159915",
            )
            submitted = st.form_submit_button("添加到自选 💾")
            if submitted and new_input:
                tokens = [x.strip() for x in new_input.replace("，", ",").split(",") if x.strip()]
                updated = list(dict.fromkeys(cur + tokens))
                ok = database.update_user_watchlist(user_id, updated)
                st.session_state.user_watchlist = updated
                # 写库失败必须说清楚：只存 session_state 的话刷新就丢，
                # 而个性化摘要推送靠云端自选股取数，静默"成功"会让人以为已生效
                st.session_state.wl_flash = (
                    ("ok", f"已同步至云端，当前自选 {len(updated)} 只。") if ok else
                    ("err", "⚠️ 云端保存失败，本次修改仅在当前会话有效，刷新后会丢失，"
                            "个性化摘要推送也取不到这份清单。请联系管理员检查数据库。"))
                st.rerun()

        if cur:
            del_code = st.selectbox("🗑️ 移出自选：", ["请选择..."] + cur, key="wl_del")
            if st.button("确认移出 ❌") and del_code != "请选择...":
                updated = [c for c in cur if c != del_code]
                ok = database.update_user_watchlist(user_id, updated)
                st.session_state.user_watchlist = updated
                st.session_state.wl_flash = (
                    ("ok", f"已移出 {del_code}") if ok else
                    ("err", f"⚠️ 已在本次会话移出 {del_code}，但云端保存失败，刷新后会恢复。"))
                st.rerun()

    if not cur:
        st.info("💡 自选清单为空。添加几只你实际在跟的标的，这一页才有意义。")
        return

    # 已是会员却没绑定 → 权益有一半没生效，必须显式提示，不能藏在折叠块里。
    # 非会员保持折叠：他们还没付钱，这时候弹红色警告是骚扰。
    _is_member = None
    try:
        _is_member = bool(auth.is_vip())
    except Exception:
        _is_member = None

    if _is_member:
        pb.render_gate(user_id, key_prefix="wl",
                       context="绑定后，摘要顶部会带上用上面这份自选股算出的「你的池子今日」。")
    else:
        with st.expander("📲 微信推送绑定", expanded=False):
            _render_push_binding(user_id)
            st.caption("提示：绑定随时可做，但**收盘摘要推送是会员权益**，"
                       "订阅生效后才会实际投递。摘要正文本身在站内始终免费可读。")

    rows, market_pct, src = _build_rows(cur)

    stamp = []
    if src.get("报价时间"):
        t = str(src["报价时间"])
        stamp.append(f"报价 {t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}" if len(t) >= 12 else f"报价 {t}")
    if src.get("跑批日期"):
        stamp.append(f"榜单数据 {src['跑批日期']}")
    if src.get("天梯日期"):
        stamp.append(f"涨停池 {src['天梯日期']}")
    st.caption("　·　".join(stamp) if stamp else "⚠️ 未取到任何数据时间戳，下方内容可能不完整。")

    st.markdown("---")
    _render_overview(rows, market_pct)

    st.markdown("---")
    c_left, c_right = st.columns([1, 1])
    with c_left:
        _render_signals(rows)
    with c_right:
        _render_sector(rows, _load_csv("data/sector_hot.csv"))

    st.markdown("---")
    st.markdown("#### 📋 我的池子明细")
    df_show = pd.DataFrame([{
        "代码": r["代码"], "名称": r["名称"], "行业": r["行业"],
        # 撞号代码把实际取到的市场写进表里。不标出来的话，用户看到「000905
        # 中证500」和「000905 厦门港务」都只会以为是自己那只。
        "市场": (sr.market_label(r["_tx_code"]) or "—"),
        "现价": r["现价"], "涨跌幅": r["涨跌幅"] if r["_has_quote"] else None,
        "换手率": r["换手率"], "振幅": r["振幅"],
        "策略命中": "　".join(s.split("（")[0] for s in r["_signals"]) or "—",
        "雪球": r["雪球"],
    } for r in rows])
    st.dataframe(
        df_show, hide_index=True, use_container_width=True,
        column_config={
            "现价": st.column_config.NumberColumn("现价", format="%.3f"),
            "涨跌幅": st.column_config.NumberColumn("涨跌幅", format="%.2f%%"),
            "换手率": st.column_config.NumberColumn("换手率", format="%.2f%%"),
            "振幅": st.column_config.NumberColumn("振幅", format="%.2f%%"),
            "雪球": st.column_config.LinkColumn("雪球", display_text="❄️"),
        },
    )
    missing = [r["代码"] for r in rows if not r["_has_quote"]]
    if missing:
        st.caption(f"⚠️ 以下代码未取到实时报价，相关数值留空而非补 0：{', '.join(missing)}")
    # 撞号必须说出口：沪深北同号标的都在交易时，本页按「宽基指数优先 + 当日活跃度」
    # 替用户选了一个。选得对不对只有用户知道，所以要把另一个选项也列出来。
    amb = [r for r in rows if r.get("_ambiguous")]
    if amb:
        st.caption("ℹ️ 以下代码沪深两市同时存在标的，已按「宽基指数优先 + 当日活跃度」取值：　"
                   + "　".join(f"{r['代码']}→{sr.market_label(r['_tx_code'])}{r['名称']}"
                               f"（另有 {'/'.join(r['_alternatives'])}）" for r in amb))

    st.markdown("---")
    _render_key_levels(rows)

    st.markdown("---")
    st.caption("本页仅为数据整理与结构测算，不构成投资建议。")
