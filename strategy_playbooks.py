"""Versioned research templates distilled from the owner's local reading notes.

Templates are hypotheses, not certified strategies or the authors' exact rules.
No dependency on the owner's Windows folders at runtime.
"""
VERSION = "2026-11-20.2"

SOURCES = {
    "goose": {
        "title": "鹅城交易系统 · 2026-11-19.1 提炼稿",
        "location": "鹅城交易系统.md §0、§1.1、§3.4–3.5",
        "lesson": "先核对资金与承接、策略前提、成本和执行能力；记录反证，不用总分抵消关键约束。",
        "boundary": "采用用户提炼稿中的研究流程；未移植旧仓位比例、三因子阈值或衍生品权限。",
    },
    "ice138": {
        "title": "爱在冰川 · #138 主线是什么 / #140 定期复盘需要干的事",
        "location": "181技巧分类版/F-龙头、题材与预期差/138；J-交易心理、体系与复盘心法/140",
        "lesson": "同时研究市场持续赚钱的方向与自己能重复执行的模式。",
        "boundary": "一日领涨或行业名称不能证明持续主线；需要连续观察与题材归因。",
    },
    "ice165": {
        "title": "爱在冰川 · #165 两个数据",
        "location": "181技巧分类版/J-交易心理、体系与复盘心法/165-两个数据.md",
        "lesson": "分模式复盘胜率与平均盈利/平均亏损，找出主要盈利和主要亏损来源。",
        "boundary": "网站按扣费后已结束记录统计；小样本、遗漏记录和模拟成交均不能证明实盘优势。",
    },
    "ice_models": {
        "title": "爱在冰川 · #081、#118、#125、#128、#139、#172",
        "location": "站内 data/methodology_181.json 对应篇目及原始分类存档",
        "lesson": "区分接力、修复、趋势回踩的观察条件，为每一种写出失效条件。",
        "boundary": "既有战法池仅是形态候选；冰点、时间规律和形态不等于买点或已验证胜率。",
    },
}

PLAYBOOKS = {
    "trend": {
        "name": "趋势跟随", "horizon": "数日至数周", "attention": "收盘研究 + 预设检查点",
        "question": "强度能否持续，突破后是否仍有承接？",
        "sources": ["goose", "ice138", "ice_models"],
        "pools": ["pool_rubbing_breakout", "pool_trend_catchup"],
        "automatic": "RPS 强度、阶段新高与既有回踩池，同日去重并列出入选依据。",
        "manual": "资金与行业驱动是否持续；公告风险；突破是否过度拥挤；自己的历史执行记录。",
        "trigger": "写明关键价位、量价确认方式及检查时间；只有全部成立才重新评估。",
        "invalid": "关键结构失守、所属方向持续转弱，或在约定期限内未确认。",
        "avoid": "不把亏损的趋势单改名为价值投资或抄底单。",
        "pages": [("强度与新高", "🔥 强势股"), ("行业持续性", "🔄 板块轮动")],
    },
    "reversal": {
        "name": "分歧修复", "horizon": "数个交易日", "attention": "需要盘中核对承接",
        "question": "只是跌深了，还是已经有可核验的承接与修复？",
        "sources": ["goose", "ice_models"],
        "pools": ["pool_ice_rebound", "pool_ma6_pullback"],
        "automatic": "既有冰点观察池与人气股均线回踩池，保留风险标签。",
        "manual": "修复前后的证据日期、承接强弱、锚点是否完整；跌停流动性与成交可达性。",
        "trigger": "写明停止下跌后的承接证据、重新站回的价位及验证时间。",
        "invalid": "承接消失、参考低点失守、修复超时或流动性继续恶化。",
        "avoid": "双冰、低价或接近均线都不足以证明反转。",
        "pages": [("修复候选详情", "📋 晚间复盘与次日计划"), ("市场广度", "🛸 全市场看板")],
    },
    "relay": {
        "name": "题材接力", "horizon": "日内观察 / 次日验证", "attention": "需要持续盘中观察",
        "question": "题材有持续赚钱效应，还是只剩涨停数量？",
        "sources": ["ice138", "ice_models", "goose"],
        "pools": ["pool_10am_first", "pool_2b_pk"],
        "automatic": "十点前首板与换手二板观察名单；结合晋级样本和昨日连板股表现。",
        "manual": "题材身份与地位、竞价/换手承接、可成交性；自己的盯盘能力。",
        "trigger": "写明题材、锚点及个股同时满足的条件，约定盘中核对时点。",
        "invalid": "题材核心失去承接、亏钱效应扩散，或原有强弱排序被反证。",
        "avoid": "早封板不是买入保证；封板价也不是可成交价格。",
        "pages": [("战法池与时间轴", "📋 晚间复盘与次日计划"), ("龙头约束与承接候选", "🧭 题材接力预案")],
    },
    "fundamental": {
        "name": "基本面研究", "horizon": "数周至数季", "attention": "定期跟踪财报与经营证据",
        "question": "增长由谁付钱，现金能否收回，融资和承接条件有无变化？",
        "sources": ["goose", "ice138"], "pools": [],
        "automatic": "提供行业与宏观研究入口；现有数据不足以自动生成价值或成长选股名单。",
        "manual": "营收/订单、现金转化、融资续接、客户依赖、估值假设和可反驳证据。",
        "trigger": "写明财报/公告来源、估值假设与待确认事件，以及价格条件。",
        "invalid": "收入假设失效、现金转化恶化、融资受限或估值安全边际消失。",
        "avoid": "低 PE、行业低市值占比和股价上涨不能替代现金流研究。",
        "pages": [("行业线索", "🏭 行业信号"), ("宏观与估值", "🌐 宏观与股债性价比"), ("资讯核验", "📰 资讯雷达")],
    },
    "etf": {
        "name": "ETF 轮动", "horizon": "数周及以上", "attention": "收盘或周度检查",
        "question": "这只基金代表什么风险暴露，与现有持仓是否重复？",
        "sources": ["goose", "ice138"], "pools": [],
        "automatic": "展示强势 ETF 的 RPS 读数与日期，保留基金的独立交易所代码。",
        "manual": "指数成分、重复持仓、费率、流动性、溢价及境内外交易时间差。",
        "trigger": "写明轮动周期、强度保持条件、可接受溢价与再平衡时间。",
        "invalid": "相对强度不再满足规则、溢价超限或组合集中度超出个人预算。",
        "avoid": "ETF 数量多不代表分散；行业基金可能押在同一个变量上。",
        "pages": [("ETF 强度榜", "🔥 强势股"), ("宏观风险", "🌐 宏观与股债性价比")],
    },
}
