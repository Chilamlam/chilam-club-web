"""Archive convertible-bond daily bars and rebuild the speculation snapshot.

The page needs open/high/low/close for both the bond and its underlying stock.
The archive is a pair of CSV files under data/convertible/; only the last few
weeks are kept because the rhythm stages need a 20-day window and the pattern
table needs ten finished days.

Nothing here sends messages and nothing writes AI text.  A failure keeps the
previous snapshot in place and records a status instead of publishing an empty
table.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd

import convertible
from data_freshness import norm_date
from update_schedule import beijing_now

ROOT = Path(__file__).resolve().parent
ARCHIVE_DIR = ROOT / "data" / "convertible"
BOND_CSV = ARCHIVE_DIR / "bond_daily.csv"
STOCK_CSV = ARCHIVE_DIR / "stock_daily.csv"
BASIC_JSON = ARCHIVE_DIR / "basic.json"
OUTPUT = ROOT / "data" / "convertible.json"
CALENDAR = ROOT / "data" / "trading_calendar.json"

ARCHIVE_DAYS = 45          # kept bars: 20-day stage window + 10-day pattern table
BOOTSTRAP_DAYS = 35        # first run: how many published days to pull back
PROBE_DAYS = 5             # how far back to look for the newest published day
BOND_COLUMNS = ["date", "ts_code", "open", "high", "low", "close", "pre_close", "pct_chg", "amount"]
STOCK_COLUMNS = ["date", "ts_code", "open", "high", "low", "close", "pre_close", "pct_chg"]
BASIC_FIELDS = ("ts_code,bond_short_name,stk_code,stk_short_name,list_date,"
                "delist_date,conv_price,remain_size")
BASIC_FIELDS_MIN = "ts_code,bond_short_name,stk_code,conv_price,remain_size"


def open_dates(limit: int = 60) -> list[str]:
    """Trading dates from the offline exchange calendar, newest last."""
    try:
        payload = json.loads(CALENDAR.read_text(encoding="utf-8"))
        dates = [norm_date(item) for item in payload.get("open_dates") or []]
    except (OSError, ValueError, AttributeError, TypeError):
        return []
    clean = sorted({date for date in dates if date})
    return clean[-limit:]


def _read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=columns)
    try:
        frame = pd.read_csv(path, dtype={"date": str, "ts_code": str})
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    return frame[columns]


def _merge_archive(frame: pd.DataFrame, rows: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if rows.empty:
        return frame
    combined = pd.concat([frame, rows[columns]], ignore_index=True)
    combined["date"] = combined["date"].astype(str)
    combined = combined.drop_duplicates(subset=["date", "ts_code"], keep="last")
    keep = sorted(combined["date"].unique())[-ARCHIVE_DAYS:]
    return combined[combined["date"].isin(keep)].reset_index(drop=True)


def _save_csv(frame: pd.DataFrame, path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    frame[columns].to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(path)


def _bond_rows(frame: pd.DataFrame, date: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BOND_COLUMNS)
    out = pd.DataFrame({
        "date": date,
        "ts_code": frame.get("ts_code"),
        "open": frame.get("open"),
        "high": frame.get("high"),
        "low": frame.get("low"),
        "close": frame.get("close"),
        "pre_close": frame.get("pre_close"),
        "pct_chg": frame.get("pct_chg"),
        "amount": frame.get("amount"),
    })
    return out.dropna(subset=["ts_code"]).astype({"ts_code": str})


def _stock_rows(frame: pd.DataFrame, date: str, wanted: set[str]) -> pd.DataFrame:
    if frame is None or frame.empty or not wanted:
        return pd.DataFrame(columns=STOCK_COLUMNS)
    keep = frame[frame["ts_code"].astype(str).isin(wanted)]
    if keep.empty:
        return pd.DataFrame(columns=STOCK_COLUMNS)
    out = pd.DataFrame({
        "date": date,
        "ts_code": keep["ts_code"].astype(str),
        "open": keep.get("open"),
        "high": keep.get("high"),
        "low": keep.get("low"),
        "close": keep.get("close"),
        "pre_close": keep.get("pre_close"),
        "pct_chg": keep.get("pct_chg"),
    })
    return out.reset_index(drop=True)


def _basic_map(frame: pd.DataFrame, trade_date: str) -> dict:
    result = {}
    for row in frame.to_dict("records") if frame is not None else []:
        code = str(row.get("ts_code") or "").strip()
        stk = str(row.get("stk_code") or "").strip()
        if not code or not stk:
            continue
        delist = norm_date(row.get("delist_date"))
        if delist and delist <= trade_date:
            continue
        size = convertible.num(row.get("remain_size"))
        if size is not None and size <= 0:
            continue
        result[code] = {
            "bond_short_name": row.get("bond_short_name") or code,
            "stk_code": stk,
            "stk_short_name": row.get("stk_short_name") or stk,
            "conv_price": convertible.num(row.get("conv_price")),
            "remain_size": size,
            "list_date": norm_date(row.get("list_date")),
            "delist_date": delist,
        }
    return result


def _ensure_dirs() -> None:
    """A fresh checkout has no data/convertible/ yet; the first write must not fail."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _load_basic(pro, trade_date: str, errors: list[str]) -> tuple[dict, pd.DataFrame]:
    for fields in (BASIC_FIELDS, BASIC_FIELDS_MIN):
        try:
            frame = pro.cb_basic(fields=fields)
        except Exception:  # noqa: BLE001 - category only, never the message (it can carry the token)
            errors.append("cb_basic_unavailable")
            continue
        basic = _basic_map(frame, trade_date) if isinstance(frame, pd.DataFrame) else {}
        if basic:
            payload = {"generated_at": beijing_now().isoformat(),
                       "rows": [{"ts_code": code, **info} for code, info in basic.items()]}
            try:
                BASIC_JSON.parent.mkdir(parents=True, exist_ok=True)
                temporary = BASIC_JSON.with_suffix(".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
                temporary.replace(BASIC_JSON)
            except OSError:
                errors.append("basic_write_failed")
            return basic, frame
    try:
        payload = json.loads(BASIC_JSON.read_text(encoding="utf-8"))
        basic = {row["ts_code"]: {k: v for k, v in row.items() if k != "ts_code"}
                 for row in payload.get("rows") or [] if row.get("ts_code")}
        return basic, pd.DataFrame()
    except (OSError, ValueError, KeyError, TypeError):
        return {}, pd.DataFrame()


def _write_status(status: str, message: str) -> None:
    """Keep the last good snapshot; only annotate why this run produced nothing."""
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    payload["status"] = status
    payload["status_message"] = message
    payload["checked_at"] = beijing_now().isoformat()
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)


def _run() -> int:
    import tushare as ts

    _ensure_dirs()
    token = (os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        print("TUSHARE_TOKEN 未配置：保留上一份可转债快照")
        _write_status("error", "TUSHARE_TOKEN 未配置")
        return 0
    pro = ts.pro_api(token, timeout=20)

    today = beijing_now().strftime("%Y%m%d")
    dates = [d for d in open_dates() if d <= today]
    if not dates:
        print("交易日历为空：保留上一份可转债快照")
        _write_status("error", "交易日历为空")
        return 0

    errors: list[str] = []
    bond_archive = _read_csv(BOND_CSV, BOND_COLUMNS)
    stock_archive = _read_csv(STOCK_CSV, STOCK_COLUMNS)

    # Find the newest published session rather than trusting the clock.
    target, target_frame = None, None
    for date in reversed(dates[-PROBE_DAYS:]):
        try:
            frame = pro.cb_daily(trade_date=date)
        except Exception:  # noqa: BLE001
            errors.append("cb_daily_unavailable")
            break
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            target, target_frame = date, frame
            break
    if target is None:
        print("未取到可转债行情：保留上一份快照")
        _write_status("error", "未取到可转债行情（接口未返回数据）")
        return 0

    basic, _ = _load_basic(pro, target, errors)
    if not basic:
        print("缺少转债基础信息：保留上一份快照")
        _write_status("error", "缺少转债基础信息")
        return 0

    wanted = {info["stk_code"] for info in basic.values() if info.get("stk_code")}

    def fetch(api, date: str):
        try:
            frame = api(trade_date=date)
        except Exception:  # noqa: BLE001 - category only, never the message
            errors.append("api_unavailable")
            return None
        time.sleep(0.2)
        return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()

    # Fill the archive newest-first; a partially filled older date still helps.
    bond_dates = set(bond_archive["date"].astype(str)) if not bond_archive.empty else set()
    stock_dates = set(stock_archive["date"].astype(str)) if not stock_archive.empty else set()
    plan = [d for d in dates[-BOOTSTRAP_DAYS:] if d not in bond_dates or d not in stock_dates]
    for date in sorted(plan, reverse=True)[:BOOTSTRAP_DAYS]:
        if date == target:
            bond_frame = target_frame
        elif date in bond_dates:
            bond_frame = None
        else:
            bond_frame = fetch(pro.cb_daily, date)
        if bond_frame is not None and not bond_frame.empty:
            rows = _bond_rows(bond_frame, date)
            bond_archive = _merge_archive(bond_archive, rows, BOND_COLUMNS)
        if date not in stock_dates:
            stock_frame = fetch(pro.daily, date)
            if stock_frame is not None and not stock_frame.empty:
                rows = _stock_rows(stock_frame, date, wanted)
                stock_archive = _merge_archive(stock_archive, rows, STOCK_COLUMNS)

    _save_csv(bond_archive, BOND_CSV, BOND_COLUMNS)
    _save_csv(stock_archive, STOCK_CSV, STOCK_COLUMNS)

    bond_archive = _read_csv(BOND_CSV, BOND_COLUMNS)
    stock_archive = _read_csv(STOCK_CSV, STOCK_COLUMNS)
    if target not in set(bond_archive["date"].astype(str)):
        print("归档写入失败：保留上一份快照")
        _write_status("error", "归档写入失败")
        return 0

    payload = convertible.build_snapshot(
        bond_archive, stock_archive, basic, target,
        now=beijing_now(), archive_days=int(bond_archive["date"].nunique()),
    )
    if errors:
        payload["notes"].append("本次运行有接口异常：" + "、".join(sorted(set(errors))))
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    market = payload["market"]
    print(f"可转债快照已保存：{target}，{market['total']} 只，"
          f"A{payload['classes']['A']['count']} B{payload['classes']['B']['count']} "
          f"C{payload['classes']['C']['count']} N{payload['classes']['N']['count']}，"
          f"归档 {payload['archive_days']} 天")
    return 0


def main() -> int:
    """Wrapper: an unexpected exception must still leave a readable status, not a blank page."""
    try:
        return _run()
    except Exception as exc:  # noqa: BLE001 - never echo the message (it can carry the token)
        name = type(exc).__name__
        print(f"可转债跑批异常：{name}")
        try:
            _write_status("error", f"跑批异常（{name}）")
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
