"""Offline publication-window checks, separate from legacy freshness helpers."""
import datetime as dt

from data_freshness import norm_date, _fmt
from update_schedule import MAIN_CRON_CST, TARGET_READY_CST, beijing_now


def publication_status(dates: dict, now=None, calendar=None) -> dict:
    """Compare actual artifact dates with the publication window, without network IO.

    A cached exchange calendar distinguishes holidays from a missed batch. Without
    it, weekday expectations are estimates and the message explicitly says so.
    No message claims a workflow is running: dates cannot prove that.
    """
    now = beijing_now(now)
    today = now.strftime("%Y%m%d")
    start = now.replace(hour=MAIN_CRON_CST[0], minute=MAIN_CRON_CST[1], second=0, microsecond=0)
    deadline = now.replace(hour=TARGET_READY_CST[0], minute=TARGET_READY_CST[1], second=0, microsecond=0)
    normalized = {label: norm_date(value) for label, value in dates.items()}
    valid = {label: date for label, date in normalized.items() if date and date <= today}
    unknown = [label for label in dates if label not in valid]
    calendar = calendar if isinstance(calendar, dict) else {}
    cal_start = norm_date(calendar.get("start_date"))
    cal_end = norm_date(calendar.get("end_date"))
    open_dates = calendar.get("open_dates")
    calendar_known = bool(cal_start and cal_end and cal_start <= today <= cal_end
                          and isinstance(open_dates, list) and open_dates
                          and all(norm_date(d) == d for d in open_dates))
    if calendar_known:
        open_dates = sorted(set(open_dates))
        previous = [d for d in open_dates if d < today]
        calendar_known = bool(previous)
    if calendar_known:
        is_open = today in open_dates
        previous_date = previous[-1]
    else:
        is_open = now.weekday() < 5
        previous_day = now.date() - dt.timedelta(days=1)
        while previous_day.weekday() >= 5:
            previous_day -= dt.timedelta(days=1)
        previous_date = previous_day.strftime("%Y%m%d")

    expected = today if is_open and now >= start else previous_date
    # 某模块已经拿到较新交易日时，其他模块必须同步，不能被时间窗掩盖。
    expected = max([expected, *valid.values()])
    stale = [label for label, date in valid.items() if date < expected]
    status = "current"
    message = f"已核验模块的数据日期均为 {_fmt(expected)}。"
    if not valid:
        status, message = "unknown", "尚无可核验的数据日期，请查看各模块日期。"
    elif stale:
        status = "delayed" if now >= deadline or expected < today else "waiting"
        message = f"{'更新延迟' if status == 'delayed' else '等待收盘更新'}：{'、'.join(stale)}尚未更新到 {_fmt(expected)}。"
    elif is_open and now < start and any(d < today for d in valid.values()):
        status, message = "scheduled", f"尚未到今日收盘更新窗口；计划北京时间 {start:%H:%M} 开始更新。"
    elif not is_open:
        message = f"今日休市，展示最近交易日 {_fmt(expected)} 的数据。"
    if unknown:
        message += f" {'、'.join(unknown)}的日期暂无法确认。"
        if status == "current":
            status = "unknown"
    if not calendar_known:
        message += " 交易日历暂不可用，以上按周一至周五估算；法定休市日以交易所安排为准。"
        if status == "current" and expected != today:
            status = "unknown"
    return {"status": status, "message": message, "dates": normalized,
            "expected_date": expected, "calendar_known": calendar_known,
            "stale": stale, "unknown": unknown, "checked_at": now.strftime("%Y-%m-%d %H:%M")}
