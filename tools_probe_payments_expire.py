# -*- coding: utf-8 -*-
"""探针：订单「超时作废 → 后台可见 → 恢复为待确认」全链路验证。

为什么这条链路需要探针：「已付款但管理员确认晚了」是**钱**的问题。
自动作废一旦把这类订单变成不可恢复的状态，客户付了钱却永远开不出 VIP，
而管理员在后台连看都看不到它。所以三件事必须各有断言：
作废写什么状态、后台能不能看见、能不能退回标准确认入口。

分两组：

  离线组 P（不需要网络，纯载荷/守卫断言，可用 --offline 单独跑）
    P1a 作废走 PATCH payments
    P1b 作废状态写 expired（不是 cancelled，用户主动取消是另一回事）
    P1c 作废只动 status=pending 的行
    P1d 截止时间由 hours 参数决定（24h/48h 两次调用截止点必须不同）
    P1e 返回条数取自响应行数（响应里 3 行就报 3，不写死）
    P2a 恢复必须按 id + status=eq.expired 双条件（原子保护）
    P2b 恢复后状态为 pending
    P2c 零行命中不得谎报成功
    P2d 命中一行才算成功
    P2e 传输/服务端报错时必须返回失败并带原因
    P3a 后台只列 expired（不含 cancelled）
    P3b 只看近 days 天下单的（14 天/30 天截止点必须不同）
    P4a confirm 对 cancelled 订单拒绝且零写请求
    P4b confirm 对 completed 订单拒绝且零写请求（防重复续期）
    P4c confirm 对 expired 订单拒绝，并给出「先恢复为待确认」的可操作指引

  接线组 W（AST 锚调用点，防止「函数写好了但没人调用」）
    W1 后台调用 get_expired_payments（作废单必须列出来）
    W2 后台调用 restore_payment_to_pending
    W3 恢复调用被 st.button 条件包住（不能一加载就自动全部恢复）
    W4 用户订单历史里有 expired 状态文案
    W5 推送文案说明「已收款可在后台恢复」

  真库组 B（真读写 Supabase，测试行 PROBE- 前缀识别、结束时删除）
    B1 超时 pending 单被作废，且状态为 expired
    B2 作废后 note 带「自动作废」字样
    B3 未超时 pending 单不被误伤
    B4 get_expired_payments 能查到它（后台看得见），且**查不到**用户主动取消的单
    B5 restore_payment_to_pending 后库里状态回到 pending
    B6 重复恢复必须失败（不能第二次也报成功）
    B7 恢复后该单出现在 get_pending_payments()（回到标准确认入口）
    B8 用户主动取消的单不能被 restore 捞回来（guard 生效）
    B9 库里无 24h 前的残留 pending（真实僵尸单已清）
    R1 hours 设 365 天时零作废（防「无差别清库」的假实现）

用法：python tools_probe_payments_expire.py [--offline]
退出码：0 全过 ｜ 1 有断言失败 ｜ 2 网络原因 UNVERIFIED（不计通过）
"""
from __future__ import annotations

import ast
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

OFFLINE = "--offline" in sys.argv

with open(os.path.join(ROOT, ".streamlit", "secrets.toml"), "rb") as f:
    _sec = tomllib.load(f)
os.environ["SUPABASE_URL"] = _sec["supabase"]["url"]
os.environ["SUPABASE_KEY"] = _sec["supabase"].get("service_key") or _sec["supabase"]["key"]

import database  # noqa: E402

results = []
unverified = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def _cutoff_gap_hours(value: str) -> float:
    """把 'lt.<iso>' 解析成「距离现在多少小时」。"""
    iso = value.split(".", 1)[1]
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


# ==================== 离线组：载荷与守卫（不需要网络） ====================

def offline_checks() -> None:
    orig_req = database._supabase_request
    orig_by_id = database.get_payment_by_id
    calls = []

    def recorder(returns):
        def fake(method, endpoint, params=None, json_data=None,
                 headers_extra=None, return_error=False):
            calls.append({"method": method, "endpoint": endpoint,
                          "params": params or {}, "json_data": json_data})
            return (returns, None) if return_error else returns
        return fake

    try:
        # ---------- P1 作废载荷 ----------
        calls.clear()
        database._supabase_request = recorder([])
        n, err = database.expire_stale_pending_payments(hours=24)
        c = calls[0] if calls else {}
        check("P1a 作废走 PATCH payments",
              c.get("method") == "PATCH" and c.get("endpoint") == "payments", str(c)[:120])
        check("P1b 作废状态写 expired（与用户主动取消的 cancelled 区分）",
              (c.get("json_data") or {}).get("status") == "expired",
              str((c.get("json_data") or {}).get("status")))
        check("P1c 作废只动 status=pending 的行",
              (c.get("params") or {}).get("status") == "eq.pending",
              str((c.get("params") or {}).get("status")))
        gap24 = _cutoff_gap_hours((c.get("params") or {}).get("created_at", "lt."))
        check("P1d-1 hours=24 的截止点落在 24 小时前",
              23.5 <= gap24 <= 24.5, f"{gap24:.2f}h")
        calls.clear()
        database._supabase_request = recorder([{"id": 1}, {"id": 2}, {"id": 3}])
        n3, err3 = database.expire_stale_pending_payments(hours=48)
        gap48 = _cutoff_gap_hours(calls[0]["params"]["created_at"])
        check("P1d-2 hours=48 的截止点随之变成 48 小时前（没写死 24）",
              47.5 <= gap48 <= 48.5 and abs(gap48 - gap24) > 23,
              f"{gap48:.2f}h")
        check("P1e 作废条数取自响应行数（响应 3 行就报 3，不写死）",
              n3 == 3 and err3 is None, f"n={n3}, err={err3}")

        # ---------- P2 恢复载荷 ----------
        calls.clear()
        database._supabase_request = recorder([])  # 零行命中
        ok0, msg0 = database.restore_payment_to_pending(123)
        c = calls[0] if calls else {}
        check("P2a 恢复按 id + status=eq.expired 双条件（原子保护，防把已完成单退回）",
              (c.get("params") or {}).get("id") == "eq.123"
              and (c.get("params") or {}).get("status") == "eq.expired",
              str(c.get("params")))
        check("P2b 恢复后状态写 pending",
              (c.get("json_data") or {}).get("status") == "pending",
              str((c.get("json_data") or {}).get("status")))
        check("P2c 零行命中不得谎报成功", ok0 is False, f"ok={ok0}, msg={msg0}")
        calls.clear()
        database._supabase_request = recorder([{"id": 123, "status": "pending"}])
        ok1, msg1 = database.restore_payment_to_pending(123)
        check("P2d 命中一行才算成功", ok1 is True, f"ok={ok1}, msg={msg1}")
        calls.clear()

        def err_req(method, endpoint, params=None, json_data=None,
                    headers_extra=None, return_error=False):
            return (None, {"status": 0, "code": "", "message": "TLS 抖动", "detail": ""})

        database._supabase_request = err_req
        ok2, msg2 = database.restore_payment_to_pending(123)
        check("P2e 传输失败时必须返回失败并带原因",
              ok2 is False and "TLS" in str(msg2), f"ok={ok2}, msg={msg2}")

        # ---------- P3 后台清单载荷 ----------
        calls.clear()
        database._supabase_request = recorder([])
        database.get_expired_payments(days=14)
        c = calls[0] if calls else {}
        check("P3a 后台只列 expired（不含用户主动取消的 cancelled）",
              (c.get("params") or {}).get("status") == "eq.expired",
              str((c.get("params") or {}).get("status")))
        g14 = _cutoff_gap_hours((c.get("params") or {}).get("created_at", "gt."))
        calls.clear()
        database.get_expired_payments(days=30)
        g30 = _cutoff_gap_hours(calls[0]["params"]["created_at"])
        check("P3b 时间窗随 days 变化（14 天 → 720h，30 天 → 720h 以上）",
              330 <= g14 <= 342 and 714 <= g30 <= 726 and g30 > g14,
              f"14d={g14:.0f}h, 30d={g30:.0f}h")

        # ---------- P4 确认守卫：非 pending 一律零写请求 ----------
        def boom(*a, **k):
            raise AssertionError("对非 pending 订单不应发起任何请求")

        database._supabase_request = boom
        for st_val, label in (("cancelled", "cancelled"), ("completed", "completed"),
                              ("expired", "expired")):
            database.get_payment_by_id = (
                lambda _id, _s=st_val: {"id": _id, "status": _s, "user_id": 19,
                                        "plan_name": "monthly", "months": 1})
            try:
                r = database.confirm_payment(999)
                if st_val == "expired":
                    check("P4c confirm 拒绝 expired 且指引「先恢复为待确认」",
                          bool(r) and r.get("ok") is False
                          and "恢复为待确认" in str(r.get("error", "")), str(r))
                else:
                    check(f"P4{'a' if st_val == 'cancelled' else 'b'} "
                          f"confirm 对 {label} 订单拒绝且零写请求",
                          bool(r) and r.get("ok") is False, str(r))
            except AssertionError as e:
                check(f"P4 confirm 对 {label} 订单必须零写请求", False, str(e))
    finally:
        database._supabase_request = orig_req
        database.get_payment_by_id = orig_by_id


# ==================== 接线组：页面是否真的把它显示/触发出来 ====================

def _calls_in(tree, chain: tuple) -> int:
    """数出形如 a.b.c(...) 的调用次数（chain=('database','get_expired_payments')）。"""
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f, parts = node.func, []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
            if tuple(reversed(parts)) == chain:
                n += 1
    return n


def _has_dict_key(tree, key: str) -> bool:
    """AST 里是否存在键为 key 的字典字面量（用于状态文案映射）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and k.value == key:
                    return True
    return False


def wiring_checks() -> None:
    """页面接线：算得再对，没显示出来 / 被无条件执行，都等于没做。"""
    admin_src = open(os.path.join(ROOT, "pages", "admin.py"), encoding="utf-8").read()
    dash_src = open(os.path.join(ROOT, "pages", "dashboard.py"), encoding="utf-8").read()
    admin_tree = ast.parse(admin_src)
    dash_tree = ast.parse(dash_src)

    check("W1 后台调用 get_expired_payments（作废单必须列出来，否则看不见）",
          _calls_in(admin_tree, ("database", "get_expired_payments")) >= 1)
    total = _calls_in(admin_tree, ("database", "restore_payment_to_pending"))
    check("W2 后台调用 restore_payment_to_pending（必须有退回标准入口的按钮）",
          total >= 1, f"调用 {total} 次")

    # 恢复若不在 st.button 条件里，页面一加载就会把**所有**作废单自动退回 pending，
    # 作废机制等于被自己撤销，而管理员什么都没点。
    # 判据收紧到「If 的测试表达式**本身**就是 st.button(...) 调用」：写成
    # `if True or st.button(...)` 一样是每次加载都执行，只查「test 里含 st.button」
    # 会被这种写法骗过。
    guarded = 0
    for node in ast.walk(admin_tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
                and _calls_in(node.test, ("st", "button")) >= 1):
            if _calls_in(node, ("database", "restore_payment_to_pending")) >= 1:
                guarded += 1
    check("W3 恢复调用被 st.button 条件包住（不能无条件自动恢复）",
          total >= 1 and guarded == total, f"总 {total}，被按钮守卫 {guarded}")
    check("W4 用户订单历史有 expired 状态文案（客户要看得懂自己的订单怎么了）",
          _has_dict_key(dash_tree, "expired"))
    check("W5 推送文案说明「已收款可在后台恢复」（管理员知道怎么补开）",
          "恢复为待确认" in dash_src)


# ==================== 真库组 ====================

def raw_request(method: str, endpoint: str, params: dict = None,
                json_data=None, retries: int = 8):
    """独立 raw 请求（curl 子进程），与被测函数分开，避免同源实现互相掩盖。

    为什么双通道（curl 主 + urllib 备）：本机到 Supabase 的两条 TLS 栈会
    交替被掐——2026-10-06/07 是 urllib 全灭、curl 勉强能通；2026-10-11 深夜
    反过来，curl 稳定报 `Recv failure: Connection was reset`（rc=35），
    同刻 urllib 秒过 200。只挂一条通道时，「测试工具自己连不上」会被记成
    「功能坏了」——探针谎报比漏报更坏。所以两条都试，两条都失败才返回 None，
    调用方必须记 UNVERIFIED，不得记 FAIL。
    """
    import subprocess
    url = (os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + endpoint)
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}"
                              for k, v in params.items())
    hdrs = {
        "apikey": os.environ["SUPABASE_KEY"],
        "Authorization": f"Bearer {os.environ['SUPABASE_KEY']}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    base = ["curl", "-sS", "-i", "--max-time", "20"]
    for _k, _v in hdrs.items():
        base += ["-H", f"{_k}: {_v}"]
    # curl 一旦被掐是**稳定被掐**（rc=35 连续），让它只试 2 轮就切备通道，
    # 把完整重试留给备用通道；若 curl 本来就通，2 轮内必然成功。
    curl_tries = min(retries, 2)
    for i in range(curl_tries):
        cmd = ["curl", "-X", method] + base
        if json_data is not None:
            cmd += ["-d", json.dumps(json_data)]
        cmd += [url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = r.stdout or ""
        # -i 输出：状态行 + 头 + 空行 + body。状态行形如 "HTTP/2 201"。
        # 本机代理会间歇注入干扰（实测 stdout 混入 "000"），所以从状态行
        # 取真实状态码，而不是用 -w 尾缀——尾缀在代理干扰下位置不可靠。
        status = None
        body = out
        for line in out.splitlines():
            if line.upper().startswith("HTTP/"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    status = parts[1]
                head_end = out.find("\r\n\r\n")
                if head_end == -1:
                    head_end = out.find("\n\n")
                if head_end != -1:
                    body = out[head_end:]
                break
        if status in ("200", "201", "204"):
            try:
                return json.loads(body.strip()) if body.strip() else None
            except json.JSONDecodeError:
                print(f"  [parse] body 异常：{body[:120]!r}")
                return None
        time.sleep(4)
    print(f"  [curl] {curl_tries} 次未成功（{method} {endpoint}）"
          f"rc={r.returncode} {(r.stderr or '').strip()[:90]!r} → 切 urllib 备用通道")

    # ---- 通道 2：urllib（备用） ----
    for i in range(retries):
        try:
            req = urllib.request.Request(url, method=method, headers=hdrs)
            if json_data is not None:
                req.data = json.dumps(json_data).encode("utf-8")
            with urllib.request.urlopen(req, timeout=20) as resp:
                print(f"  [via urllib] {method} {endpoint} -> {resp.status}")
                body_u = resp.read().decode("utf-8")
                return json.loads(body_u) if body_u.strip() else None
        except urllib.error.HTTPError as e:
            print(f"  [HTTP {e.code}] {e.read().decode('utf-8', 'replace')[:150]}")
            return None
        except Exception as e:
            if i == retries - 1:
                print(f"  [urllib last] {type(e).__name__}: {str(e)[:90]}")
            time.sleep(4)

    print(f"  [both channels failed] {method} {endpoint}")
    return None


class NetworkGap(RuntimeError):
    """两条通道都取不到数据：测试环境断网，不是被测功能失败。"""


def _need(payload, label: str):
    """读取类断言的前置检查：取不到就抛 NetworkGap（调用方记 UNVERIFIED）。

    为什么不能把 None 当失败：`raw_request` 返回 None 只意味着「两条通道都
    没把数据拿回来」，与「库里那一行状态不对」是两件完全不同的事。把前者
    记成 FAIL，就是探针在谎报功能坏了。
    """
    if payload is None:
        raise NetworkGap(label)
    return payload


TAG = "PROBE-EXP-" + datetime.now(timezone.utc).strftime("%H%M%S")
ADMIN_UID = 19  # 历史僵尸单用户，测试行挂他名下、结束即删


def online_checks() -> bool:
    """返回 True 表示完成验证；False 表示网络不通、结果 UNVERIFIED。"""
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    def mk(suffix, status, created):
        return raw_request("POST", "payments", json_data={
            "user_id": ADMIN_UID, "order_no": TAG + suffix, "plan_name": "monthly",
            "months": 1, "amount": 1.00, "payment_method": "wechat",
            "status": status, "created_at": created, "note": "PROBE",
        })

    made_old = mk("-OLD", "pending", old_iso)
    made_new = mk("-NEW", "pending", now_iso)
    made_can = mk("-CAN", "cancelled", now_iso)
    if not made_old or not made_new or not made_can:
        print("UNVERIFIED  测试订单写入失败（网络），未触碰任何真实数据")
        return False

    try:
        # ---- B1/B2：执行作废（连接层闪断时整体重试，生产里 PATCH 只发一次） ----
        n, err = 0, {"status": 0}
        for attempt in range(10):
            n, err = database.expire_stale_pending_payments(hours=24)
            if not err:
                break
            if err.get("status") != 0:
                break  # 服务端明确报错，重试无意义
            print(f"  [expire retry {attempt+1}] {str(err.get('message',''))[:80]}")
            time.sleep(8)
        check("B1 超时单被作废（返回条数 >= 1）", not err and n >= 1, f"n={n}, err={err}")
        if err:
            print("UNVERIFIED  作废请求失败，跳过后续断言")
            return False

        rows = _need(
            raw_request("GET", "payments", params={"order_no": f"like.{TAG}*"}),
            "B2-B4 读取测试单")
        by_no = {r["order_no"]: r for r in (rows or [])}
        old_row = by_no.get(TAG + "-OLD")
        new_row = by_no.get(TAG + "-NEW")
        can_row = by_no.get(TAG + "-CAN")

        check("B2 作废后状态为 expired（不是 cancelled）",
              bool(old_row) and old_row.get("status") == "expired",
              f"status={(old_row or {}).get('status')}")
        check("B3 note 含自动作废说明",
              bool(old_row) and "自动作废" in (old_row.get("note") or ""),
              str((old_row or {}).get("note", ""))[:60])
        check("B4 新鲜单未被误伤",
              bool(new_row) and new_row.get("status") == "pending",
              f"status={(new_row or {}).get('status')}")

        # ---- B5：后台可见 ----
        exp_list = database.get_expired_payments(days=14)
        exp_nos = {r.get("order_no") for r in (exp_list or [])}
        check("B5 后台「已超时作废」清单能查到它（看得见才谈得上补开）",
              TAG + "-OLD" in exp_nos, f"清单 {len(exp_nos)} 笔")
        check("B6 用户主动取消的单不在该清单里（两种语义不混）",
              TAG + "-CAN" not in exp_nos, f"cancelled 是否混入：{TAG + '-CAN' in exp_nos}")

        # ---- B7/B8/B9：恢复链路 ----
        if not old_row:
            check("B7pre 恢复前能读到测试单 id", False, "读取成功但该行不存在")
            return False
        ok_r, msg_r = database.restore_payment_to_pending(old_row["id"])
        check("B7 恢复为待确认成功", ok_r is True, f"ok={ok_r}, msg={msg_r}")
        back = _need(
            raw_request("GET", "payments", params={"order_no": f"eq.{TAG}-OLD"}),
            "B8 读取恢复后状态")
        check("B8 库里状态确实回到 pending",
              bool(back) and isinstance(back, list) and back[0].get("status") == "pending",
              f"status={(back or [{}])[0].get('status') if back else 'None'}")
        ok_r2, msg_r2 = database.restore_payment_to_pending(old_row["id"])
        check("B9 重复恢复必须失败（不能第二次也报成功）",
              ok_r2 is False, f"ok={ok_r2}, msg={msg_r2}")

        pend_list = database.get_pending_payments()
        check("B10 恢复后该单回到待确认列表（能走标准确认入口）",
              TAG + "-OLD" in {r.get("order_no") for r in (pend_list or [])},
              f"待确认 {len(pend_list or [])} 笔")

        ok_c, msg_c = database.restore_payment_to_pending(can_row["id"]) if can_row else (None, "")
        check("B11 用户主动取消的单不能被 restore 捞回来（guard 生效）",
              ok_c is False, f"ok={ok_c}, msg={msg_c}")

        # ---- B12/R1：真实僵尸单已清 + 无差别清库反验 ----
        pend_old = _need(
            raw_request("GET", "payments",
                        params={"status": "eq.pending", "select": "id,created_at",
                                "created_at": f"lt.{old_iso}"}),
            "B12 读取残留 pending")
        check("B12 无 24h 前的残留 pending（真实僵尸单已清）",
              isinstance(pend_old, list) and len(pend_old) == 0,
              f"残留 {len(pend_old or [])} 笔")

        n2, err2 = database.expire_stale_pending_payments(hours=24 * 365)
        check("R1 hours=365天时零作废（防无差别清库的假实现）",
              not err2 and n2 == 0, f"n2={n2}, err2={err2}")
        again = _need(
            raw_request("GET", "payments", params={"order_no": f"eq.{TAG}-NEW"}),
            "R2 读取新鲜单")
        check("R2 新鲜单仍然 pending",
              bool(again) and isinstance(again, list)
              and again[0].get("status") == "pending",
              str((again or [{}])[0].get("status")))
        return True
    except NetworkGap as e:
        print(f"UNVERIFIED  两条通道都取不到数据（{e}），本次未完成核验；"
              f"这不是功能失败，重跑即可")
        return False
    finally:
        for suffix in ("-OLD", "-NEW", "-CAN"):
            raw_request("DELETE", "payments", params={"order_no": f"eq.{TAG}{suffix}"})
        print("cleanup done")


def main() -> int:
    print("=== 离线组：作废/恢复/确认的载荷与守卫 ===")
    offline_checks()
    print("\n=== 接线组：页面显示与触发 ===")
    wiring_checks()

    if not OFFLINE:
        print("\n=== 真库组：端到端 ===")
        if not online_checks():
            unverified.append("真库组未完成（网络）")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} 项通过"
          + (f"，{len(unverified)} 项 UNVERIFIED" if unverified else ""))
    if failed:
        return 1
    return 2 if unverified else 0


if __name__ == "__main__":
    sys.exit(main())
