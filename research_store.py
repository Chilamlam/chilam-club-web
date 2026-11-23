"""Account-scoped workspace state. Reads once; user changes save automatically."""
from __future__ import annotations

import copy

import streamlit as st

import decision_workbench as engine
import journal_store
import research_workspace as workspace
from update_schedule import beijing_now


def owner():
    # Identity always comes from the verified server session, never a widget or file.
    return journal_store._owner()


def key():
    uid = owner()
    return f"decision_journal_{uid if uid is not None else 'guest'}"


def error_text(err):
    if not err:
        return ""
    if err.get("code") == "CONFLICT":
        return "另一个窗口已修改这条记录。本窗口的修改仍保留，请下载备份后选择云端版本或另存新记录。"
    return "账号暂时无法保存或读取；本窗口的内容仍保留，请重试或下载备份。"


def load(*, force=False):
    scope = key()
    records = st.session_state.setdefault(scope, {})
    if owner() is None:
        return records
    if force or not st.session_state.get(scope + "_loaded"):
        remote, err = journal_store.load_records()
        st.session_state[scope + "_loaded"] = True
        st.session_state[scope + "_load_error"] = err
        if not err:
            for record in remote:
                local = records.get(record["id"])
                if local and local.get("_dirty"):
                    continue
                record.update(_synced=True, _dirty=False)
                records[record["id"]] = record
    return records


def save(record):
    """Keep local data on every failure. A successful save retains revision guards."""
    scope = key()
    records = st.session_state.setdefault(scope, {})
    pending = copy.deepcopy(record)
    pending["_dirty"] = True
    records[pending["id"]] = pending
    if owner() is None:
        return pending, None
    saved, err = journal_store.save_record(pending)
    if err:
        pending["_save_error"] = err
        return pending, err
    records[saved["id"]] = saved
    return saved, None


def follow(candidate, style="trend", evidence=None, current=False):
    """One active research entry per symbol/style; re-opening never resets its premise."""
    records = load()
    code = engine.symbol_id(candidate.get("symbol"))
    if not code:
        raise ValueError("请输入含交易所的证券代码。")
    active = [r for r in records.values() if r["plan"]["symbol"] == code
              and r["plan"]["style"] == style
              and r["outcome"]["status"] not in ("已结束", "已放弃")]
    if active:
        record = max(active, key=lambda r: (r["created_at"], r["id"]))
        if record.get("_dirty") and owner() is not None:
            saved, err = save(record)
            return saved, False, err
        return record, False, None
    from workbench_insights_ui import quick_follow
    ident, _ = quick_follow(candidate, style, evidence or {}, current, records)
    saved, err = save(records[ident])
    return saved, True, err


def save_message(record, err=None):
    if err:
        return error_text(err)
    if record.get("_synced") and not record.get("_dirty"):
        return "已保存到账号，下次打开会自动读取。"
    if owner() is not None:
        return "修改仍在本窗口，尚未同步到账号，请重试或下载备份。"
    return "已保存在本次会话；游客请下载备份，关闭后可能丢失。"


def new_real_record(record):
    records = load()
    existing = [r for r in records.values() if r["plan"]["symbol"] == record["plan"]["symbol"]
                and r["plan"]["mode"] == "实盘记录" and r["outcome"]["status"] not in ("已结束", "已放弃")]
    if existing:
        return existing[0], None
    fields = copy.deepcopy(record["plan"])
    values = workspace.research(record)
    fields.update({k: values[k] for k in workspace.TEXT_FIELDS})
    for field in ("thesis", "trigger", "invalid", "exit_rule", "cost_risk"):
        fields[field] = fields.get(field) or "尚未填写个人条件"
    fields.update(mode="实盘记录", parent_id=record["id"])
    new = engine.new_plan(fields, now=beijing_now().isoformat())
    # Carry only research conditions, never simulated results or pretend holdings.
    values.update(shares=None, cost_price=None, position_date="")
    new = workspace.revise(new, values, now=beijing_now().isoformat(), reason="从观察建立实盘记录")
    return save(new)


def adopt_legacy_drafts():
    """Migrate only the current session's drafts; no automatic external writes."""
    uid = owner()
    legacy_key = f"observation_plans_{uid if uid is not None else 'guest'}"
    scope = key()
    records = st.session_state.setdefault(scope, {})
    migrated = st.session_state.setdefault(scope + "_migrated_drafts", set())
    for raw, draft in (st.session_state.get(legacy_key) or {}).items():
        if raw in migrated:
            continue
        code = engine.symbol_id(raw)
        if not code:
            continue
        fields = dict(symbol=code, name=draft.get("name") or code, style="trend", mode="模拟观察",
                      asof=beijing_now().strftime("%Y%m%d"), data_current=False,
                      thesis=draft.get("basis") or "旧会话草稿", trigger=draft.get("trigger") or "待补充",
                      invalid=draft.get("invalid") or "待补充", exit_rule=draft.get("horizon") or "待补充",
                      cost_risk="待补充", manual_evidence="从技术页旧会话草稿迁入，未核验成交。")
        record = engine.new_plan(fields, now=beijing_now().isoformat())
        record["outcome"]["notes"] = draft.get("result") or ""
        record["_dirty"] = True
        records[record["id"]] = record
        migrated.add(raw)


def copy_guest_records():
    """Explicit user action after login; guest research never moves silently."""
    import uuid
    if owner() is None:
        return 0, 0
    records = load()
    copied = {r["plan"].get("guest_source_id") for r in records.values()}
    count, failed = 0, 0
    for source in list((st.session_state.get("decision_journal_guest") or {}).values()):
        if source["id"] in copied:
            continue
        record = {k:copy.deepcopy(v) for k,v in source.items() if not k.startswith("_")}
        record.update(id=str(uuid.uuid4()), revision=0)
        record["plan"]["guest_source_id"] = source["id"]
        _, err = save(record)
        count += 1
        failed += bool(err)
    return count, failed
