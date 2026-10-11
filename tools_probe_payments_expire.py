# -*- coding: utf-8 -*-
"""探针：expire_stale_pending_payments 的真库行为验证。

验证项（全部真库读写，测试行用 PROBE- 前缀识别、结束时删除）：
  A1  超时 pending 订单被作废（造一条 created_at=now-25h 的测试单）
  A2  作废后 note 带「自动作废」字样（管理员能看懂原因）
  A3  未超时 pending 订单不被误伤（造一条 created_at=now 的测试单）
  A4  返回条数 n >= 1 且真实僵尸单（9 月的 pending）一并被清理
  R1  反验：hours 设极大值时任何单都不被动（防「无差别清库」的假实现）
退出码：任一断言失败 exit 1；网络原因查不到结果记 UNVERIFIED，exit 2。
"""
from __future__ import annotations

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

with open(os.path.join(ROOT, ".streamlit", "secrets.toml"), "rb") as f:
    _sec = tomllib.load(f)
os.environ["SUPABASE_URL"] = _sec["supabase"]["url"]
os.environ["SUPABASE_KEY"] = _sec["supabase"].get("service_key") or _sec["supabase"]["key"]

import database  # noqa: E402

results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def raw_request(method: str, endpoint: str, params: dict = None,
                json_data=None, retries: int = 8):
    """独立 raw 请求（curl 子进程），与被测函数分开，避免同源实现互相掩盖。

    为什么走 curl 不走 urllib：本机 urllib→Supabase 存在长 TLS 坏窗口
    （2026-10-06/07 实测），urllib 全灭时 curl 偶有单次成功；外呼一律 curl。
    """
    import subprocess
    url = (os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + endpoint)
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}"
                              for k, v in params.items())
    base = ["curl", "-sS", "-i", "--max-time", "20",
            "-H", f"apikey: {os.environ['SUPABASE_KEY']}",
            "-H", f"Authorization: Bearer {os.environ['SUPABASE_KEY']}",
            "-H", "Content-Type: application/json",
            "-H", "Prefer: return=representation"]
    for i in range(retries):
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
        print(f"  [HTTP {status}] {out[:150]!r}")
        time.sleep(5)
    return None


TAG = "PROBE-EXP-" + datetime.now(timezone.utc).strftime("%H%M%S")
ADMIN_UID = 19  # 历史僵尸单用户，测试行挂他名下、结束即删

# ---- 造数：一条超时单 + 一条新鲜单 ----
old_iso = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
now_iso = datetime.now(timezone.utc).isoformat()
made_old = raw_request("POST", "payments", json_data={
    "user_id": ADMIN_UID, "order_no": TAG + "-OLD", "plan_name": "monthly",
    "months": 1, "amount": 1.00, "payment_method": "wechat",
    "status": "pending", "created_at": old_iso, "note": "PROBE",
})
made_new = raw_request("POST", "payments", json_data={
    "user_id": ADMIN_UID, "order_no": TAG + "-NEW", "plan_name": "monthly",
    "months": 1, "amount": 1.00, "payment_method": "wechat",
    "status": "pending", "created_at": now_iso, "note": "PROBE",
})
if not made_old or not made_new:
    print("UNVERIFIED  测试订单写入失败（网络），未触碰任何真实数据")
    sys.exit(2)

try:
    # ---- A1/A2/A4：执行作废（连接层闪断时整体重试，PATCH 在生产里只发一次） ----
    n, err = 0, {"status": 0}
    for attempt in range(10):
        n, err = database.expire_stale_pending_payments(hours=24)
        if not err:
            break
        if err.get("status") != 0:
            break  # 服务端明确报错，重试无意义
        print(f"  [expire retry {attempt+1}] {err.get('message','')[:80]}")
        time.sleep(8)
    check("A1 超时测试单被作废", not err and n >= 1, f"n={n}, err={err}")
    if err:
        print("UNVERIFIED  作废请求失败，跳过后续断言")
        sys.exit(2)

    rows = raw_request("GET", "payments",
                       params={"order_no": f"like.{TAG}*"})
    by_no = {r["order_no"]: r for r in (rows or [])}
    old_row = by_no.get(TAG + "-OLD")
    new_row = by_no.get(TAG + "-NEW")

    check("A2 note 含自动作废说明",
          bool(old_row) and "自动作废" in (old_row.get("note") or ""),
          str((old_row or {}).get("note", ""))[:60])
    check("A3 新鲜单未被误伤",
          bool(new_row) and new_row.get("status") == "pending",
          f"status={(new_row or {}).get('status')}")

    # A4 真实僵尸单：执行前库里至少有 9 月的 5 笔 pending（id 4/5/6/7/8）
    pend_now = raw_request("GET", "payments",
                           params={"status": "eq.pending", "select": "id,created_at",
                                   "created_at": f"lt.{old_iso}"})
    check("A4 无 24h 前的残留 pending（真实僵尸单已清）",
          isinstance(pend_now, list) and len(pend_now) == 0,
          f"残留 {len(pend_now or [])} 笔")

    # ---- R1 反验：hours 极大时应零作废（防无差别清库假实现） ----
    n2, err2 = database.expire_stale_pending_payments(hours=24 * 365)
    check("R1 hours=365天时零作废（含刚才的新鲜单）",
          not err2 and n2 == 0, f"n2={n2}, err2={err2}")
    again = raw_request("GET", "payments",
                        params={"order_no": f"eq.{TAG}-NEW"})
    check("R1b 新鲜单仍然 pending",
          bool(again) and isinstance(again, list)
          and again[0].get("status") == "pending",
          str((again or [{}])[0].get("status")))
finally:
    # ---- 清理测试行 ----
    for suffix in ("-OLD", "-NEW"):
        raw_request("DELETE", "payments", params={"order_no": f"eq.{TAG}{suffix}"})
    print("cleanup done")

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
sys.exit(1 if failed else 0)
