"""Private server-side journal storage using the existing verified login.

Never accepts a user ID from widgets/imports. A failed read isn't an empty book;
conditional updates reject concurrent changes and never replace entry premises.
"""
import copy
import uuid

import auth
import database


def _owner():
    user = auth.get_current_user()
    uid = user.get("user_id") if user else None
    if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
        return None
    return uid


def _error(message, code="JOURNAL_ERROR"):
    return {"code": code, "message": message}


def load_records():
    uid = _owner()
    if uid is None:
        return None, _error("登录后才能读取账号记录。", "LOGIN_REQUIRED")
    # Supabase commonly caps a response at 1000 rows: paginate explicitly.
    records, offset = [], 0
    while True:
        data, err = database._supabase_request(
            "GET", "decision_journal", params={"user_id": f"eq.{uid}",
                "select": "id,schema,version,created_at,plan,outcome,revision",
                "order": "created_at.asc,id.asc", "limit": 500, "offset": offset},
            return_error=True, retries=1, timeout=5)
        if err:
            return None, err
        if not isinstance(data, list):
            return None, _error("云端响应异常，未将其当作空记录。")
        records.extend(data)
        if len(data) < 500:
            return records, None
        offset += 500


def save_record(record):
    uid = _owner()
    if uid is None:
        return None, _error("登录后才能同步账号记录。", "LOGIN_REQUIRED")
    try:
        ident = str(uuid.UUID(record["id"]))
        revision = int(record.get("revision", 0))
        if revision < 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        return None, _error("计划 ID 或版本无效。")
    if record.get("_synced"):
        result, err = database._supabase_request(
            "PATCH", "decision_journal", params={"id": f"eq.{ident}", "user_id": f"eq.{uid}",
                "revision": f"eq.{revision}"},
            json_data={"outcome": copy.deepcopy(record["outcome"]), "revision": revision + 1},
            return_error=True)
    else:
        body = {k: copy.deepcopy(record[k]) for k in ("id", "schema", "version", "created_at", "plan", "outcome")}
        body.update(user_id=uid, revision=1)
        result, err = database._supabase_request("POST", "decision_journal", json_data=body, return_error=True)
    if err:
        return None, err
    if not isinstance(result, list) or len(result) != 1:
        return None, _error("记录已在其他会话更新或不可访问。本地内容仍保留，请先导出，再重新读取云端。", "CONFLICT")
    saved = {k: v for k, v in result[0].items() if k != "user_id"}
    saved.update(_synced=True, _dirty=False)
    return saved, None
