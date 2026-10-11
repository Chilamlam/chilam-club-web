# -*- coding: utf-8 -*-
"""反向验证「订单超时作废 / 恢复 / 后台可见」链路断言是否为真断言。

用法: <stcheck>/python.exe tools_probe_payments_expire_reverse.py
退出码 0 = 全部断言有效；3 = 存在假断言或造错未生效。

一条永不失败的断言等于没有断言。这里用 15 种「造错」方式检验
（全部跑离线+接线组，不碰真库，因此可反复执行）：

  database.py（载荷与守卫）
    1) 作废状态写 cancelled 而不是 expired            → P1b 必须炸
    2) 作废时不带 status=pending 过滤（连已完成的也改）→ P1c 必须炸
    3) 作废的截止时间写死 24 小时（忽略 hours 参数）   → P1d-2 必须炸
    4) 作废条数写死 1（不取响应行数）                  → P1e 必须炸
    5) 恢复不带 status=eq.expired 原子保护             → P2a 必须炸
    6) 恢复零行命中当成功                              → P2c 必须炸
    7) 恢复传输失败也报成功                            → P2e 必须炸
    8) 后台清单改成列 cancelled（用户主动取消的单）    → P3a 必须炸
    9) 后台清单时间窗写死 14 天                        → P3b 必须炸
   10) confirm 的 pending 守卫直接放行（零写请求断言） → P4a 必须炸
   11) expired 的错误提示不再指向恢复路径              → P4c 必须炸

  pages/admin.py、pages/dashboard.py（接线）
   12) 后台不再调用 get_expired_payments（看不见）    → W1 必须炸
   13) 恢复改成 `True or st.button(...)`（每次加载都恢复）→ W3 必须炸
   14) 用户订单历史去掉 expired 文案                   → W4 必须炸
   15) 推送文案不再说明可恢复                          → W5 必须炸
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
PROBE = str(ROOT / "tools_probe_payments_expire.py")


def run_probe() -> str:
    p = subprocess.run([PY, PROBE, "--offline"], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=600)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, label: str, expect: str) -> bool:
    """临时改源码 → 跑探针 → 无条件还原（并校验还原后与备份逐字节一致）。"""
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    if old not in backup:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe()
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
        hit = [ln for ln in fails if expect in ln]
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:2]:
            print(f"      {ln.strip()[:130]}")
        if not hit:
            for ln in fails[:3]:
                print(f"      （其它 FAIL）{ln.strip()[:130]}")
            if not fails:
                print("      （探针全绿，改动完全没被察觉）")
        print()
        return bool(hit)
    finally:
        path.write_text(backup, encoding="utf-8")
        if path.read_text(encoding="utf-8") != backup:
            print(f"!!! 源码还原失败，请立即修复 {target} !!!")


# ---------- 基线：未改动前必须全绿 ----------
base = run_probe()
if "FAIL" in base or "21/21 项通过" not in base:
    print("基线不成立：未造错时探针就没全过，无法进行反验。\n")
    print(base[-1500:])
    sys.exit(3)
print("基线正常：未造错时 21/21 全过\n" + "-" * 60)

ok = []

# ---------- 1：作废状态写成 cancelled ----------
ok.append(mutate(
    "database.py",
    '            "status": "expired",\n',
    '            "status": "cancelled",\n',
    "1) 作废写 cancelled 而非 expired（作废单再也进不了恢复清单）",
    "P1b"))

# ---------- 2：作废不带 pending 过滤 ----------
ok.append(mutate(
    "database.py",
    '        params={"status": "eq.pending", "created_at": f"lt.{cutoff}"},',
    '        params={"created_at": f"lt.{cutoff}"},',
    "2) 作废不带 status=pending 过滤（会连已完成订单一起改）",
    "P1c"))

# ---------- 3：截止时间写死 24 小时 ----------
ok.append(mutate(
    "database.py",
    "    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()",
    "    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()",
    "3) 截止时间写死 24 小时（hours 参数形同虚设）",
    "P1d-2"))

# ---------- 4：作废条数写死 ----------
ok.append(mutate(
    "database.py",
    "    return (len(res) if isinstance(res, list) else 0), None",
    "    return (1 if res is not None else 0), None",
    "4) 作废条数写死 1（不取响应行数，跑批日志会骗人）",
    "P1e"))

# ---------- 5：恢复去掉原子保护 ----------
ok.append(mutate(
    "database.py",
    '        params={"id": f"eq.{payment_id}", "status": "eq.expired"},',
    '        params={"id": f"eq.{payment_id}"},',
    "5) 恢复去掉 status=eq.expired 原子保护（可把已完成单退回 pending）",
    "P2a"))

# ---------- 6：恢复零行命中当成功 ----------
ok.append(mutate(
    "database.py",
    "    if not isinstance(data, list) or len(data) == 0:",
    "    if False:",
    "6) 恢复零行命中当成功（管理员以为已退回待确认，其实没动）",
    "P2c"))

# ---------- 7：恢复传输失败也报成功 ----------
ok.append(mutate(
    "database.py",
    '    if err:\n        return False, f"恢复失败：{str(err.get(\'message\', \'\'))[:80]}"',
    '    if False:\n        return False, f"恢复失败：{str(err.get(\'message\', \'\'))[:80]}"',
    "7) 恢复传输失败也当成功（连不上还说已恢复）",
    "P2e"))

# ---------- 8：后台清单列 cancelled ----------
ok.append(mutate(
    "database.py",
    '        "status": "eq.expired",\n        "created_at": f"gt.{cutoff}",',
    '        "status": "eq.cancelled",\n        "created_at": f"gt.{cutoff}",',
    "8) 后台清单改列 cancelled（把用户主动取消的单混进待核对清单）",
    "P3a"))

# ---------- 9：后台清单时间窗写死 ----------
ok.append(mutate(
    "database.py",
    "    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()",
    "    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()",
    "9) 后台清单时间窗写死 14 天（days 参数形同虚设）",
    "P3b"))

# ---------- 10：confirm 守卫放行 ----------
ok.append(mutate(
    "database.py",
    '    if payment.get("status") != "pending":',
    "    if False:",
    "10) confirm 不再拦非 pending（跳过守卫直接写库）",
    "confirm 对 cancelled"))

# ---------- 11：expired 提示不再指向恢复路径 ----------
ok.append(mutate(
    "database.py",
    '        if st_now == "expired":',
    "        if False:",
    "11) expired 的错误提示不再给出「先恢复为待确认」的指引",
    "P4c"))

# ---------- 12：后台不列作废单 ----------
ok.append(mutate(
    "pages/admin.py",
    "    expired_list = database.get_expired_payments(days=14)",
    "    expired_list = []",
    "12) 后台不再调用 get_expired_payments（作废单彻底看不见）",
    "W1"))

# ---------- 13：恢复被改成每次加载都执行 ----------
ok.append(mutate(
    "pages/admin.py",
    'if st.button("↩️ 恢复为待确认"',
    'if True or st.button("↩️ 恢复为待确认"',
    "13) 恢复被写成 `True or st.button(...)`（页面一加载就自动全部恢复）",
    "W3"))

# ---------- 14：用户订单历史去掉 expired 文案 ----------
ok.append(mutate(
    "pages/dashboard.py",
    '"expired": "⌛ 超时未确认（已作废）"',
    '"expired_typo": "⌛ 超时未确认（已作废）"',
    "14) 用户订单历史去掉 expired 文案（客户只看到裸的 expired 英文状态）",
    "W4"))

# ---------- 15：推送文案不再说明可恢复 ----------
ok.append(mutate(
    "pages/dashboard.py",
    "；如果你事后核对发现其实收到了款，在同一页底部的「已超时作废的订单」里点「恢复为待确认」即可补开。",
    "。",
    "15) 推送文案不再说明「已收款可在后台恢复」（管理员不知道能补开）",
    "W5"))

print("-" * 60)
print(f"反向验证：{sum(ok)}/{len(ok)} 项断言确认真实有效")
if len(ok) != sum(ok) or not all(ok):
    print("❌ 存在假断言或造错未生效")
    sys.exit(3)
print("✅ 全部造错均被对应断言抓住")
