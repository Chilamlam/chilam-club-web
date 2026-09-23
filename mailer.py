# -*- coding: utf-8 -*-
"""邮件发送 —— 全站唯一的 SMTP 真源。

为什么单独抽出来（2026-09-23）：
  密码找回需要「把重置码送到用户邮箱」，而站上原本只有 `daily_digest.py`
  内部的 `_smtp_conf()`——它是摘要投递的私有实现。直接把它复制一份到
  密码模块是最省事的写法，但那样 SMTP 主机/端口/账号/发件人的解析规则
  就存在两处实现：等哪天有人把 465 改成 587、或把 `SMTP_SSL` 换成
  `starttls`，只会在**一侧**改到，另一侧继续用旧规则。而两边的失败表现
  完全一样（「邮件没收到」），排查时根本看不出是分叉导致的。
  所以这里收口成唯一实现，`daily_digest.py` 反过来引用本模块。

配置键沿用 `DIGEST_SMTP_*`（不新造一套）：
  DIGEST_SMTP_HOST / DIGEST_SMTP_PORT / DIGEST_SMTP_USER
  DIGEST_SMTP_PASS / DIGEST_SMTP_FROM
  沿用旧名的理由：站长已经在文档里被告知过这组键（摘要投递用），
  密码找回与摘要是同一个发件邮箱、同一套凭据，没有理由配两遍；
  更名则意味着「已配好的那套突然不生效」，是最容易让人踩空的一种改法。

本模块**不 import streamlit**：它要在 GitHub Actions 里跑（摘要投递），
也要在站内运行时被调用（密码找回）。st.secrets → 环境变量的桥接由
调用方负责（页面侧 `_bridge_secrets_to_env()`）。
"""
from __future__ import annotations

import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 配置键：这里的 5 个名字就是全站 SMTP 配置的唯一定义，
# 任何模块都不应再自己拼接环境变量名。
SMTP_HOST = "DIGEST_SMTP_HOST"
SMTP_PORT = "DIGEST_SMTP_PORT"
SMTP_USER = "DIGEST_SMTP_USER"
SMTP_PASS = "DIGEST_SMTP_PASS"
SMTP_FROM = "DIGEST_SMTP_FROM"

DEFAULT_PORT = 465          # SSL；587 走 STARTTLS，由代码自动判端口选择
DEFAULT_TIMEOUT = 25


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def smtp_conf() -> dict | None:
    """读取 SMTP 配置。**三要素缺一即视为未配置**（返回 None，不抛错）。

    为什么「未配置」必须是 None 而不是抛异常：调用方要能区分
    「这台机器没配邮件」与「配了但发失败」——前者是**能力缺失**，
    应该去引导用户走别的通道（微信/人工）；后者是**故障**，应该如实报错。
    把两者混成一种，就会在没配邮件的环境里对着用户喊「发送失败，请重试」，
    让他一直重试一个永远不会成功的东西。
    """
    host, user, pwd = _env(SMTP_HOST), _env(SMTP_USER), _env(SMTP_PASS)
    if not (host and user and pwd):
        return None
    raw_port = _env(SMTP_PORT)
    try:
        port = int(raw_port) if raw_port else DEFAULT_PORT
    except ValueError:
        # 端口填了非数字（如误填成 "465 "带中文空格、或填了域名）：
        # 回落默认端口而不是崩掉——但要说清楚发生了回落，否则
        # 「配置了却连不上」会被当成网络问题查半天。
        print(f"[Mailer] {SMTP_PORT} 不是数字（{raw_port!r}），已回落 {DEFAULT_PORT}")
        port = DEFAULT_PORT
    return {"host": host, "port": port, "user": user, "pwd": pwd,
            "sender": _env(SMTP_FROM) or user}


def is_configured() -> bool:
    """是否具备发信能力。页面据此决定「邮件找回」这条通道是否可选。"""
    return smtp_conf() is not None


def send_mail(to: str, subject: str, text: str,
              html: str | None = None) -> tuple[bool, str]:
    """发一封邮件。返回 (是否送达, 说明)。

    说明文案**永不包含口令/授权码**：SMTP 抛出的异常里可能带上服务端
    返回的认证详情，而这段文案会被展示到页面上、也可能进日志。
    只保留异常类型与简短原因，足以定位（认证失败/连不上/被拒），
    不足以泄露凭据。
    """
    conf = smtp_conf()
    if not conf:
        return False, "未配置 SMTP（DIGEST_SMTP_HOST/USER/PASS）"
    to = (to or "").strip()
    if not to:
        return False, "收件人为空"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = conf["sender"]
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))

    srv = None
    try:
        if conf["port"] == 465:
            srv = smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=DEFAULT_TIMEOUT)
        else:
            srv = smtplib.SMTP(conf["host"], conf["port"], timeout=DEFAULT_TIMEOUT)
            srv.starttls()
        srv.login(conf["user"], conf["pwd"])
        srv.sendmail(conf["sender"], [to], msg.as_string())
        return True, "已发送"
    except smtplib.SMTPAuthenticationError:
        # 最常见且最容易被误判的一种：不是网络问题，是授权码错了/过期。
        # 报「网络错误」会让人去查网络，方向完全错。
        return False, "SMTP 认证失败（授权码错误或已过期，请重新生成）"
    except Exception as e:
        return False, f"SMTP 发送失败：{type(e).__name__}"
    finally:
        if srv is not None:
            try:
                srv.quit()
            except Exception:
                pass
