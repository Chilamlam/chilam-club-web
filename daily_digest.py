"""
收盘后主动推送跑批

设计要点（为什么这样做，而不是直接写一个发邮件脚本）：

1. **零凭据也能有产出**。任何渠道都没配时，脚本仍然把摘要写到
   `data/digest/latest.md` + `history/YYYYMMDD.md` 并正常退出。
   这样「收盘摘要」这个功能立刻可用（站内可读、可分享），
   推送渠道是叠加增强，不是前置门槛。

2. **多渠道各自独立、互不阻断**。邮件、企业微信、Server酱 任一失败都只记一条
   failure，不影响其他渠道和归档。渠道全部未配 = skipped，不算失败。

3. **绝不发空推送**。摘要 has_content=False（关键数据全缺）时不发送，
   只归档并打印原因。宁可今天不推，也不推一封"今天没有数据"的骚扰信。

4. **收件人来自 Supabase 有效订阅**。这是推送该锁在付费墙后的原因——
   它依赖用户的邮箱与我们的持续投入，别处抄不走。

环境变量（GitHub Secrets）：
  DIGEST_SMTP_HOST / _PORT / _USER / _PASS / _FROM   邮件（缺任一即跳过邮件）
  DIGEST_WECOM_WEBHOOK    企业微信群机器人 webhook
  DIGEST_SERVERCHAN_KEY   Server酱 SendKey
  SUPABASE_URL / SUPABASE_KEY   取有效订阅用户邮箱（缺则邮件仅发 DIGEST_TEST_TO）
  DIGEST_TEST_TO          调试收件人，逗号分隔，始终包含
  DIGEST_DRY_RUN=1        只归档不发送

退出码：0 正常（含全渠道未配置）/ 2 部分渠道失败 / 3 摘要无内容或归档失败
"""
from __future__ import annotations

import datetime
import json
import os
import smtplib
import sys
import urllib.error
import urllib.request
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import digest as dg

DIGEST_DIR = os.path.join("data", "digest")
HIST_DIR = os.path.join(DIGEST_DIR, "history")


def _bj_now() -> datetime.datetime:
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


# ================= 数据准备 =================

def gather_pct_map() -> dict:
    """
    取当日全市场涨幅，供个性化段落使用。
    取不到就返回空 dict —— 个性化段落随之整段消失，不会显示错误数字。
    """
    token = _env("TUSHARE_TOKEN")
    if not token:
        print("⚠️ 无 TUSHARE_TOKEN，个性化段落本次跳过")
        return {}
    try:
        import pandas as pd
        import tushare as ts
        ts.set_token(token)
        pro = ts.pro_api()
        derived = dg._load_json(dg.DERIVED_PATH) or {}
        date_key = derived.get("date") or _bj_now().strftime("%Y%m%d")
        df = pro.daily(trade_date=date_key, fields="ts_code,pct_chg")
        if df is None or df.empty:
            print(f"⚠️ {date_key} 全市场行情为空，个性化段落跳过")
            return {}
        return {dg._bare(c): float(v) for c, v in zip(df["ts_code"], df["pct_chg"])
                if pd.notna(v)}
    except Exception as e:
        print(f"⚠️ 全市场涨幅取数失败：{e}")
        return {}


def subscriber_emails() -> list[tuple[str, list[str]]]:
    """
    返回 [(email, watchlist)] —— 有效订阅用户及其自选股。
    Supabase 不可用时返回空列表（邮件仅发调试收件人）。
    """
    if not (_env("SUPABASE_URL") and _env("SUPABASE_KEY")):
        print("⚠️ 未配置 SUPABASE_URL/KEY，跳过订阅用户收集")
        return []
    try:
        import database as db
        subs = db.get_all_subscriptions()
        today = datetime.date.today().isoformat()
        active_ids = set()
        for s in subs or []:
            end = str(s.get("end_date") or s.get("expire_date") or "")[:10]
            if end and end >= today:
                active_ids.add(s.get("user_id"))
        out = []
        for uid in active_ids:
            u = db.get_user_by_id(uid)
            if not u or not u.get("email"):
                continue
            out.append((u["email"], db.get_user_watchlist(uid) or []))
        print(f"📇 有效订阅用户 {len(out)} 位")
        return out
    except Exception as e:
        print(f"⚠️ 订阅用户收集失败：{e}")
        return []


# ================= 归档 =================

def archive(payload: dict, date_key: str) -> None:
    os.makedirs(HIST_DIR, exist_ok=True)
    with open(os.path.join(DIGEST_DIR, "latest.md"), "w", encoding="utf-8") as f:
        f.write(payload["markdown"])
    with open(os.path.join(HIST_DIR, f"{date_key}.md"), "w", encoding="utf-8") as f:
        f.write(payload["markdown"])
    meta = {
        "date": date_key,
        "title": payload["title"],
        "plain": payload["plain"],
        "missing": payload["missing"],
        "has_content": payload["has_content"],
        "generated_at": _bj_now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(DIGEST_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"✅ 摘要已归档 → {DIGEST_DIR}/latest.md 与 history/{date_key}.md")


# ================= 渠道 =================

def _post_json(url: str, body: dict, timeout: int = 15) -> tuple[bool, str]:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "ignore")[:200]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}"
    except Exception as e:
        return False, str(e)


def send_wecom(payload: dict) -> str | None:
    hook = _env("DIGEST_WECOM_WEBHOOK")
    if not hook:
        return None
    ok, msg = _post_json(hook, {"msgtype": "markdown",
                                "markdown": {"content": payload["markdown"][:4000]}})
    print(("✅ 企业微信推送成功" if ok else f"❌ 企业微信推送失败：{msg}"))
    return None if ok else f"wecom: {msg}"


def send_serverchan(payload: dict) -> str | None:
    key = _env("DIGEST_SERVERCHAN_KEY")
    if not key:
        return None
    ok, msg = _post_json(f"https://sctapi.ftqq.com/{key}.send",
                         {"title": payload["title"], "desp": payload["markdown"]})
    print(("✅ Server酱推送成功" if ok else f"❌ Server酱推送失败：{msg}"))
    return None if ok else f"serverchan: {msg}"


def _smtp_conf() -> dict | None:
    host, user, pwd = _env("DIGEST_SMTP_HOST"), _env("DIGEST_SMTP_USER"), _env("DIGEST_SMTP_PASS")
    if not (host and user and pwd):
        return None
    return {"host": host, "port": int(_env("DIGEST_SMTP_PORT") or 465),
            "user": user, "pwd": pwd, "sender": _env("DIGEST_SMTP_FROM") or user}


def _md_to_html(md: str) -> str:
    """极简 markdown → HTML（只处理标题/粗体/列表/分隔线，不引第三方依赖）。"""
    import html as _h
    import re
    out = []
    for line in md.split("\n"):
        s = _h.escape(line.rstrip())
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"`(.+?)`", r"<code style='background:#f3f3f3;padding:1px 4px;"
                              r"border-radius:3px;'>\1</code>", s)
        s = re.sub(r"_(.+?)_", r"<em>\1</em>", s)
        if s.startswith("### "):
            out.append(f"<h3 style='margin:18px 0 8px;'>{s[4:]}</h3>")
        elif s.startswith("# "):
            out.append(f"<h2 style='margin:0 0 12px;'>{s[2:]}</h2>")
        elif s.strip() == "---":
            out.append("<hr style='border:none;border-top:1px solid #e5e5e5;margin:16px 0;'>")
        elif s.startswith("- "):
            out.append(f"<div style='margin:4px 0 4px 10px;'>• {s[2:]}</div>")
        elif s.strip() == "":
            out.append("<div style='height:6px;'></div>")
        else:
            out.append(f"<div style='margin:3px 0;'>{s.replace('　', '&nbsp;&nbsp;')}</div>")
    return ("<div style=\"font-family:-apple-system,'PingFang SC','Microsoft YaHei',"
            "sans-serif;font-size:15px;line-height:1.7;color:#222;max-width:680px;\">"
            + "\n".join(out) + "</div>")


def send_email(recipients: list[tuple[str, list[str]]], pct_map: dict,
               base: dict) -> str | None:
    conf = _smtp_conf()
    if not conf:
        return None
    extra = [e.strip() for e in _env("DIGEST_TEST_TO").split(",") if e.strip()]
    targets = list(recipients) + [(e, []) for e in extra]
    if not targets:
        print("⚠️ 邮件渠道已配置但无收件人（无有效订阅且未设 DIGEST_TEST_TO）")
        return None

    sent, failed = 0, []
    try:
        srv = (smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=25)
               if conf["port"] == 465 else
               smtplib.SMTP(conf["host"], conf["port"], timeout=25))
        if conf["port"] != 465:
            srv.starttls()
        srv.login(conf["user"], conf["pwd"])
    except Exception as e:
        print(f"❌ SMTP 登录失败：{e}")
        return f"smtp-login: {e}"

    for email, wl in targets:
        # 每人单独组装：自选股不同，个性化段落也不同
        p = dg.build_markdown(watchlist=wl, pct_map=pct_map) if wl else base
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(p["title"], "utf-8")
        msg["From"] = conf["sender"]
        msg["To"] = email
        msg.attach(MIMEText(p["markdown"], "plain", "utf-8"))
        msg.attach(MIMEText(_md_to_html(p["markdown"]), "html", "utf-8"))
        try:
            srv.sendmail(conf["sender"], [email], msg.as_string())
            sent += 1
        except Exception as e:
            failed.append(f"{email}: {e}")
    try:
        srv.quit()
    except Exception:
        pass

    print(f"✅ 邮件发送 {sent}/{len(targets)} 成功")
    if failed:
        for f in failed[:5]:
            print(f"   ❌ {f}")
        return f"smtp: {len(failed)} 封失败"
    return None


# ================= 主流程 =================

def main() -> None:
    pct_map = gather_pct_map()
    base = dg.build_markdown(watchlist=None, pct_map=pct_map)
    derived = dg._load_json(dg.DERIVED_PATH) or {}
    date_key = derived.get("date") or _bj_now().strftime("%Y%m%d")

    try:
        archive(base, date_key)
    except Exception as e:
        print(f"❌ 归档失败：{e}")
        sys.exit(3)

    print("-" * 56)
    print(base["plain"])
    print("-" * 56)

    if not base["has_content"]:
        print("⛔ 关键数据全部缺失，本次不发送任何推送（宁可不推，也不推空内容）")
        for m in base["missing"]:
            print(f"   · {m}")
        sys.exit(3)

    if _env("DIGEST_DRY_RUN") == "1":
        print("🧪 DRY_RUN=1，仅归档不发送")
        return

    failures = []
    for f in (send_wecom(base), send_serverchan(base)):
        if f:
            failures.append(f)
    f = send_email(subscriber_emails(), pct_map, base)
    if f:
        failures.append(f)

    configured = any(_env(k) for k in ("DIGEST_WECOM_WEBHOOK", "DIGEST_SERVERCHAN_KEY",
                                       "DIGEST_SMTP_HOST"))
    if not configured:
        print("ℹ️ 未配置任何推送渠道，摘要已归档可在站内查看。"
              "配置 DIGEST_WECOM_WEBHOOK / DIGEST_SERVERCHAN_KEY / DIGEST_SMTP_* 后自动启用。")
        return

    if failures:
        print(f"⚠️ {len(failures)} 个渠道失败：{failures}")
        sys.exit(2)
    print("✅ 推送全部完成")


if __name__ == "__main__":
    main()
