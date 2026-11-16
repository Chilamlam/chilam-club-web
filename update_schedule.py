"""Shared Beijing-time publication window for the UI and batch watchdog."""
import datetime as dt

CST = dt.timezone(dt.timedelta(hours=8))
MAIN_CRON_CST = (18, 37)  # daily_update.yml: 37 10 * * * UTC
TARGET_READY_CST = (19, 0)
RUNTIME_MIN = 18
JUDGE_MARGIN_MIN = 35


def beijing_now(now=None):
    if now is None:
        return dt.datetime.now(CST)
    return now.replace(tzinfo=CST) if now.tzinfo is None else now.astimezone(CST)
