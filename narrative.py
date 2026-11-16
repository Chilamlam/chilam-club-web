# -*- coding: utf-8 -*-
"""把收盘后的客观产物翻译成散户可读的市场叙事。

这里只做解释，不生成个股买卖建议。输入由跑批产物提供，缺少关键数据时
宁可明确说无法判断，也不使用猜测补全。
"""
from __future__ import annotations

import json
import os
import math

from data_freshness import norm_date


DERIVED_PATH = os.path.join("data", "sentiment", "derived.json")
SECTOR_PATH = os.path.join("data", "sector_rotation", "analysis.json")
LADDER_PATH = os.path.join("data", "limit_ladder.json")


def _load(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _date_display(value: str) -> str:
    value = str(value or "")
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value or "最新交易日"


def _pct(value, digits: int = 1) -> str:
    return f"{float(value):+.{digits}f}%"


def build_narrative(derived: dict | None, sectors: dict | None,
                    ladder: dict | None) -> dict:
    """返回可直接展示的叙事段落及其数据状态。"""
    if not derived or derived.get("status") not in ("ok", "complete"):
        return {"status": "unavailable", "date": "", "markdown": "", "missing": ["情绪数据未生成"]}

    date = norm_date(derived.get("date"))
    if not date:
        return {"status": "unavailable", "date": "", "markdown": "", "missing": ["情绪数据缺少有效统计日"]}
    phase = derived.get("phase") or {}
    breadth = derived.get("breadth") or {}
    promotion = ((derived.get("promotion") or {}).get("rates") or {}).get("1进2") or {}
    premium = derived.get("premium") or {}
    gap = derived.get("ladder_gap") or {}
    missing = []
    paragraphs = []
    if not sectors or sectors.get("status") != "ok" or norm_date(sectors.get("date")) != date:
        sectors = None
        missing.append("板块数据缺失、状态异常或与情绪统计日不同，暂不合并解释")

    def number(value):
        try:
            return value is not None and not isinstance(value, bool) and math.isfinite(float(value))
        except (ValueError, TypeError):
            return False

    # 第一段先给结论，读者不需要先理解指标名称。
    if (breadth.get("status") == "ok"
            and all(number(breadth.get(k)) for k in ("up", "down", "median_pct"))):
        up = breadth.get("up")
        down = breadth.get("down")
        mood = "下跌的股票更多" if down > up else "上涨的股票更多" if up > down else "涨跌家数相当"
        paragraphs.append(
            f"今天的市场，**{mood}**。全市场上涨 {up} 家、"
            f"下跌 {down} 家，中位涨跌幅 {_pct(breadth.get('median_pct', 0), 2)}；"
        )
        if phase.get("status") == "ok":
            paragraphs[-1] += f"站内短线接力模型标记为“{phase.get('phase')}”。这一标签描述连板接力，不代表所有板块都处在同一状态。"
    else:
        missing.append("市场宽度或情绪周期数据缺失")

    # “今天炒什么”使用板块的中期强度和当日变化，避免把排名直接说成主线。
    top = [x for x in ((sectors or {}).get("top") or [])
           if x.get("name") and number(x.get("pct_chg")) and number(x.get("pct_10d"))]
    if (sectors or {}).get("window_col") != "pct_10d":
        top = []
    if top:
        shown = top[:3]
        names = "、".join(
            f"{x.get('name', '未命名')}（10日 {_pct(x.get('pct_10d', 0), 1)}，今日 {_pct(x.get('pct_chg', 0), 1)}）"
            for x in shown
        )
        today_leaders = sorted(top, key=lambda x: float(x.get("pct_chg", -999)), reverse=True)[:2]
        leader_names = "、".join(x.get("name", "未命名") for x in today_leaders)
        paragraphs.append(
            f"最近强的方向，今天还强吗？近10日强度榜靠前的是 **{names}**。"
            f"在这份强度榜内，今天表现靠前的是{leader_names}。"
            "这只能说明哪些板块强、哪些板块正在被交易，不能单凭板块排名断言它就是确定主线。"
        )
    else:
        missing.append("板块轮动数据缺失，无法回答今天炒什么")

    # 第二段解释“边际变化”：强度仍在，但当日涨跌混杂即定义为分化。
    if top:
        positive = sum(float(x.get("pct_chg", 0)) > 0 for x in top[:5])
        if positive >= 4:
            trend = "这几个近期强势板块多数当天上涨，表现仍有延续"
        elif positive <= 1:
            trend = "这几个近期强势板块当天上涨的不多，近期强势没有普遍延续"
        else:
            trend = "这几个近期强势板块当天涨跌不一，表现存在分化"
        paragraphs.append(
            f"哪些信号值得留意？{trend}。这里对比的是近期累计强度和当天表现，"
            "尚不能据此确认相对昨天的转强或转弱，也不能解释上涨的消息原因。"
        )

    if (number(promotion.get("rate")) and number(promotion.get("base"))
            and number(promotion.get("promoted")) and float(promotion["base"]) > 0):
        paragraphs.append(
            f"短线接力的结果是：上一交易日首次涨停的 {promotion['base']} 只股票中，"
            f"本日有 {promotion['promoted']} 只再次涨停，占 {float(promotion['rate']) * 100:.1f}%。"
            + ("样本少于10只，暂不据此概括市场。" if float(promotion['base']) < 10 else "这说明连着涨停的机会有多少，并不等于买入后的盈利概率。")
        )
    else:
        missing.append("涨停延续数据缺失")
    if premium.get("status") in ("ok", "insufficient") and number(premium.get("median_pct")) and number(premium.get("n")):
        paragraphs.append(f"上一交易日已连续涨停的 {premium['n']} 只股票，本日涨跌幅中位数为 {_pct(premium['median_pct'], 2)}。这只代表这一小组股票，不代表普通持仓的表现。")

    if gap.get("status") == "ok":
        paragraphs.append(
            f"高位情绪目前最高到 {gap.get('max_height', '—')} 板，今日涨停 {gap.get('total', '—')} 家，"
            f"{gap.get('verdict', '梯队结构需结合明日确认')}。"
        )
    else:
        missing.append("连板梯队数据缺失")

    plan = derived.get("verification_plan") or []
    if plan:
        checks = []
        for item in plan[:3]:
            checks.append(f"- **{item.get('指标', '指标')}**：今天是 {item.get('今日基准', '—')}；明天看{item.get('验证条件', '—')}。")
        paragraphs.append("下一交易日先观察这些信号，届时再核对变化：\n" + "\n".join(checks))
    else:
        missing.append("明日验证条件缺失")

    return {
        "status": "ok" if paragraphs else "unavailable",
        "date": str(date or ""),
        "markdown": "\n\n".join(paragraphs),
        "missing": missing,
    }


def load_narrative() -> dict:
    return build_narrative(_load(DERIVED_PATH), _load(SECTOR_PATH), _load(LADDER_PATH))
