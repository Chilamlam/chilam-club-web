"""Keep index membership, price screens and ownership baskets out of theme indices."""
import re


ATTRIBUTE_NAMES = {
    "AB股", "AH股", "B股", "ST股", "ST板块", "次新股", "创业成份", "低价股", "百元股",
    "科创板做市股", "科创板做市商", "微盘股", "红利破净股", "周期股", "微利股", "小盘股",
    "微盘精选", "权重股", "大盘股", "中盘股", "超跌股", "破增发价股", "基金减仓", "基金增仓",
    "股权集中", "股权分散", "趋势股", "反转股", "题材股", "高成长股", "券商金股", "近期新高",
    "历史新高", "百日新高", "转债标的", "行业龙头", "HS300", "深成500", "上证50", "上证180",
    "中证500", "上证380", "创业板综", "深证100R", "标准普尔", "宁组合", "密集调研", "外资背景",
    "连续亏损", "高市净率", "高市盈率", "增持计划", "近期摘帽", "东方财富热股", "最近多板",
    "价值股", "红利股", "高股息精选", "茅指数", "央视50", "低市盈率", "低市净率", "超级品牌",
}
ATTRIBUTE_PARTS = ("昨日", "含一字", "股通", "重仓", "MSCI", "富时罗素", "标普", "道琼斯",
    "持股", "汇金", "融资融券", "转融券", "破净", "破发", "举牌", "回购增持", "股权激励",
    "员工持股", "GDR", "央企改革100", "中字头", "风格", "新进", "季报", "中报", "年报")


def is_attribute_board(name):
    if not isinstance(name, str) or not name.strip():
        return True
    name = name.strip().rstrip("_")
    return (name in ATTRIBUTE_NAMES or any(part.casefold() in name.casefold() for part in ATTRIBUTE_PARTS)
            or bool(re.fullmatch(r"[大中小]盘(价值|成长|平衡)", name)))
