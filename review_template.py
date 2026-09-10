# -*- coding: utf-8 -*-
"""
引导式复盘答卷 - 默认模板定义

来源：用户多年的短线全覆盖复盘流程（D:\\stock\\复盘流程.txt），转成结构化答卷。
模板演进原则：
  1. 题目 id 稳定不变（增删题不破坏旧答卷对账）——id 是对账主键
  2. 每题标注 type：single（单选）/ multi（多选）/ yn（是/否）/ choice_from_data
     （选项来自站内当日数据，如最强题材）/ text（自由文本，不判分）
  3. 每题标注 ai_checkable：True = 次日可用实际盘面回验命中；False = 只对比不判分
     （如「市场心理与感受」是主观题，判分没有意义）
  4. 可判分题的 options 与实际盘面的映射写在 daily_review_score.py 的 VERDICT_MAP

分层约束：本文件是纯数据定义，不 import streamlit、不联网、不依赖任何跑批产物，
可脱离 runtime 单测（tools_probe_review.py 直接 import 本文件校验 schema）。
"""

# ---- 题目类型 ----
# single: 单选；multi: 多选；yn: 是/否；choice_from_data: 选项注入自当日数据；text: 自由文本

TEMPLATE_VERSION = "v1-2026-09-10"

DEFAULT_TEMPLATE: list[dict] = [
    {
        "id": "q01_sentiment",
        "section": "开篇",
        "prompt": "一句话概括今天的赚钱效应",
        "type": "single",
        "options": ["普涨酣畅", "局部火热", "温吞分化", "亏钱明显", "冰点绝望"],
        "ai_checkable": False,   # 主观概括，只对比不判分
    },
    {
        "id": "q02_mood",
        "section": "开篇",
        "prompt": "今天你自己的市场心理与感受",
        "type": "single",
        "options": ["亢奋", "平稳", "犹豫", "恐惧", "麻木"],
        "ai_checkable": False,   # 主观感受，AI 不答此题
    },
    {
        "id": "q03_emotion_cycle",
        "section": "情绪周期与指数周期",
        "prompt": "现在处于情绪周期哪个位置？",
        "type": "single",
        "options": ["启动", "发酵", "高潮", "退潮", "冰点"],
        "ai_checkable": True,   # 次日以实际晋级率/溢价回验
    },
    {
        "id": "q04_index_cycle",
        "section": "情绪周期与指数周期",
        "prompt": "现在处于指数周期哪个位置？",
        "type": "single",
        "options": ["主升", "震荡上行", "横盘", "震荡下行", "下行"],
        "ai_checkable": True,   # 次日以指数实际走势回验
    },
    {
        "id": "q05_lead_index",
        "section": "赚钱效应在哪里 · 市场风格",
        "prompt": "当前领先指数是哪个？",
        "type": "single",
        "options": ["上证50", "沪深300", "中证500", "中证1000", "创业板", "科创50"],
        "ai_checkable": True,   # 次日以各指数当日涨幅回验
    },
    {
        "id": "q06_hot_money",
        "section": "赚钱效应在哪里 · 市场风格",
        "prompt": "当前人气股（平台低吸）行情有没有？",
        "type": "yn",
        "options": ["有", "无"],
        "ai_checkable": True,
    },
    {
        "id": "q07_ladder",
        "section": "赚钱效应在哪里 · 市场风格",
        "prompt": "最高板与连板梯队（接力行情）成立吗？",
        "type": "yn",
        "options": ["成立", "不成立"],
        "ai_checkable": True,
    },
    {
        "id": "q08_trend_new_high",
        "section": "赚钱效应在哪里 · 市场风格",
        "prompt": "趋势新高（机构行情）有没有？",
        "type": "yn",
        "options": ["有", "无"],
        "ai_checkable": True,
    },
    {
        "id": "q09_convertible",
        "section": "赚钱效应在哪里 · 市场风格",
        "prompt": "转债投机（投机行情末期/主线套利）有高度吗？",
        "type": "yn",
        "options": ["有", "无"],
        "ai_checkable": True,
    },
    {
        "id": "q10_strongest_theme",
        "section": "主线题材",
        "prompt": "今天最强题材（用于接力）是哪个？",
        "type": "choice_from_data",   # 选项注入自当日板块涨幅榜 Top10
        "options": [],                # 跑批时动态填充
        "ai_checkable": True,
    },
    {
        "id": "q11_new_theme",
        "section": "主线题材",
        "prompt": "今天有没有值得关注的新题材（低迷期破局/篡位/分流轮动）？",
        "type": "yn",
        "options": ["有", "无"],
        "ai_checkable": True,
    },
    {
        "id": "q12_main_pattern",
        "section": "主流模式",
        "prompt": "今天盘面的主流赚钱模式是？（可多选）",
        "type": "multi",
        "options": ["连板接力", "抱团", "趋势低吸", "人气低吸",
                    "转债投机", "注册制次新", "开板次新", "后排补涨"],
        "ai_checkable": True,   # 多选题：命中一个记一半分？——见 scoring 的 multi 规则
    },
    {
        "id": "q13_recognition",
        "section": "辨识度",
        "prompt": "今天市场给到的辨识度标签是？（可多选）",
        "type": "multi",
        "options": ["低位涨停", "最强换手", "一字板", "破发次新",
                    "最大成交金额", "开板次新", "全年涨幅排名", "breaking news"],
        "ai_checkable": False,  # 标签组合主观性强，只对比不判分
    },
    {
        "id": "q14_tomorrow_expect",
        "section": "明天的计划",
        "prompt": "预期明天盘面情况",
        "type": "single",
        "options": ["情绪修复", "延续今日", "高位分歧", "退潮加剧", "说不清"],
        "ai_checkable": True,   # 次日以实际情绪指标回验
    },
    {
        "id": "q15_tomorrow_plan",
        "section": "明天的计划",
        "prompt": "你的明日操作计划（自由填写，不判分，仅存档对比）",
        "type": "text",
        "options": [],
        "ai_checkable": False,
    },
]


def get_default_template() -> list[dict]:
    """返回默认模板（深拷贝由调用方按需处理；本层保持无状态）。"""
    return DEFAULT_TEMPLATE


def get_checkable_ids() -> list[str]:
    """可判分题的 id 列表（回验脚本按此对账）。"""
    return [q["id"] for q in DEFAULT_TEMPLATE if q.get("ai_checkable")]


def get_question_ids() -> list[str]:
    """全部题目 id（答卷完整性校验用）。"""
    return [q["id"] for q in DEFAULT_TEMPLATE]
