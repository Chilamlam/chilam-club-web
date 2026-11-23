"""Market research, comparable candidates and a continuous personal workspace."""
from __future__ import annotations

import streamlit as st

import candidate_ui
import page_personal_review as personal
import research_store
import decision_workbench as engine
import review_language
import workbench_insights_ui as insights
from data_freshness import norm_date
from publication_status import publication_status
from site_data import load_csv, load_json
from strategy_playbooks import PLAYBOOKS
from update_schedule import beijing_now

def _context():
    frames = {k: load_csv(p) for k, p in (("strong", "data/strong_stocks.csv"),
              ("breakout", "data/breakout_stocks.csv"), ("etf", "data/strong_etfs.csv"))}
    sentiment = load_json("data/sentiment/derived.json")
    evening = review_language.current_wording(load_json("data/evening_plan/latest.json"))
    market = load_csv("data/market_sentiment.csv")
    labels = {"strong": "个股强度榜", "breakout": "新高突破池", "etf": "ETF强度榜"}
    dates = {"短线情绪": sentiment.get("date"), "晚间战法池": evening.get("date"),
             **{labels[k]: engine.snapshot_date(v) for k, v in frames.items()}}
    if not market.empty and "date" in market:
        dates["市场成交"] = max(market["date"].map(norm_date), default="")
    now = beijing_now()
    today = now.strftime("%Y%m%d")
    asof = max((norm_date(d) for d in dates.values() if norm_date(d) and norm_date(d) <= today), default="")
    publication = publication_status(dates, now=now, calendar=load_json("data/trading_calendar.json"))
    evidence = engine.market_evidence(sentiment, market, evening, asof=asof) if asof else {"facts": [], "warnings": [], "unavailable": "暂无证据"}
    return frames, evening, asof, publication, evidence



def _store_key():
    return research_store.key()


def render_decision_workbench_page(section="candidates"):
    frames, evening, asof, publication, evidence = _context()
    analysis = insights.load_insights(asof)
    if section == "home":
        personal.render_home(frames, evening, asof, publication, evidence, analysis)
        return
    titles = {"candidates": "筛选与比较", "themes": "题材跟踪", "outcomes": "名单表现", "tracking": "研究档案"}
    st.title(titles[section])
    st.caption(f"数据日期 {asof or '未知'} · {publication.get('message', '')}")
    if section == "themes":
        insights.render_themes(analysis, asof)
        return
    if section == "outcomes":
        insights.render_outcomes(analysis)
        return
    if section == "tracking":
        personal.render_tracking(frames, evening, asof, analysis)
        return
    current = bool(asof and asof == publication["expected_date"] and publication["status"] in ("current", "scheduled"))
    records = research_store.load()
    owner_key = research_store.key()
    style_keys = list(PLAYBOOKS)
    preview = {key: engine.candidate_rows(key, frames, evening, asof=asof)[0] if asof else [] for key in style_keys}
    style = st.segmented_control("候选类型", style_keys, selection_mode="single", default=style_keys[0],
        format_func=lambda key: "基本面研究 · 手动" if key == "fundamental" else f"{PLAYBOOKS[key]['name']} · {len(preview[key])}只",
        key=f"{owner_key}_style") or style_keys[0]
    rows, excluded = engine.candidate_rows(style, frames, evening, asof=asof) if asof else ([], ["缺少可用日期。"])
    candidate_ui.render(rows, excluded, PLAYBOOKS[style], asof, f"{owner_key}_{style}", style,
                        analysis, evidence, current, records, frames)


if __name__ == "__main__":
    st.set_page_config(page_title="Chilam Club · 我的复盘", layout="wide")
    render_decision_workbench_page("home")
