# -*- coding: utf-8 -*-
"""每日清理：把超过 24 小时未确认的 pending 订单自动作废。

本站支付是「用户扫码 → 管理员人工对账 → 后台确认」的半人工流程，
pending 只代表购买意向、不代表到账。不清理的话，僵尸单会永久堆积在
待确认列表，每次都要人工分辨「付了没确认」还是「压根没付」。

作废是可恢复的，且恢复路径必须真能走通：状态写 expired（区别于用户主动
取消的 cancelled），后台「已超时作废的订单」区会列出它们，管理员核对
收款码账单后确认确实收到款，点「恢复为待确认」退回 pending，再走标准
订单确认流程开通 VIP（走库里事务型 RPC，比任何补开旁路都可靠）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402


def main() -> int:
    # 跑批环境凭据由 Actions secrets / cron 环境注入 SUPABASE_URL/KEY；
    # 本地调试时从 secrets.toml 桥接（与探针同口径）。
    if not os.environ.get("SUPABASE_URL"):
        try:
            import tomllib
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   ".streamlit", "secrets.toml"), "rb") as f:
                sec = tomllib.load(f)
            sb = sec.get("supabase", {})
            os.environ["SUPABASE_URL"] = sb.get("url", "")
            os.environ["SUPABASE_KEY"] = sb.get("service_key") or sb.get("key", "")
        except Exception:
            pass

    n, err = database.expire_stale_pending_payments(hours=24)
    if err:
        print(f"[expire-orders] 作废失败：{err}")
        return 1
    print(f"[expire-orders] 本次作废超 24h 未确认订单 {n} 笔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
