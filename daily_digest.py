"""
收盘后主动推送跑批

设计要点（为什么这样做，而不是直接写一个发邮件脚本）：

1. **零凭据也能有产出**。任何渠道都没配时，脚本仍然把摘要写到
   `data/digest/latest.md` + `history/YYYYMMDD.md` 并正常退出。
   这样「收盘摘要」这个功能立刻可用（站内可读、可分享），
   推送渠道是叠加增强，不是前置门槛。

2. **多渠道各自独立、互不阻断**。任一渠道失败只记一条 failure，
   不影响其他渠道和归档。渠道全部未配 = skipped，不算失败。

3. **绝不发空推送**。摘要 has_content=False（关键数据全缺）时不发送，
   只归档并打印原因。宁可今天不推，也不推一封"今天没有数据"的骚扰信。

4. **渠道分工必须写清楚，否则会配错**（8/30 修正）：

   · **WxPusher = 用户投递主通道**。一个 appToken + 每人一个 UID，
     用户扫码关注一次即可，之后按 UID 精准推送。这是目前唯一免费、
     能一对多推到微信、且不用把用户拉进企业通讯录的方案。
   · **Server酱 = 管理员运维告警，不是用户通道**。它的 SendKey 绑定单个
     微信账号，官方明确「群发不支持」——用它服务付费用户在架构上就是死路。
     现在它只在「有渠道失败 / 有数据缺失 / 无人可投」时给站长报一声，
     正好落在免费版 5 条/天以内。
   · **邮件 = 可选兜底**。收盘 19:40 发出的邮件，用户次日早上才在促销堆里
     翻到，「主动触达」这一段基本失效，所以不再是主路径；仍保留是因为
     未绑定微信的付费用户总得有个去处。

     **邮件当前处于「代码在、凭据没配」的状态**（DIGEST_SMTP_* 未进 Secrets），
     即恒不发信。所以站内文案一律只承诺微信，不许出现「推送到邮箱」——
     承诺一个不存在的通道，用户会守着邮箱等一封永远不来的信，
     而日志跟「这个人没订阅」完全一样，谁都发现不了。

5. **同一天不重复轰炸**（9/3 新增，实测踩出来的）。GitHub Actions 的 schedule
   被平台延迟 1~9 小时已成常态，加上看门狗补跑，实测 08-30~09-02 每天有
   2~3 条 run 落地，每条都会走到这里 —— 付费用户同一份摘要连收 3 遍，
   最后一遍常在凌晨 01:5x。半夜被推醒是直接退订的理由。
   现在按 `data/digest/push_ledger.json` 逐目标记录「已经收到过哪份内容」，
   **指纹一致则跳过，内容有更新则补推**。为什么不按日期去重：那会让
   「当天第一次推的是坏版本」永远无法补救（2026-09-01 RPS 扑空正是这形状），
   把防轰炸做成了防修复。

   ★ 账本里的目标键必须是 **HMAC 摘要，不是 UID/邮箱原文**。本仓库是
   **公开**的，而账本随 data/ 被 Actions 提交：存原文等于把 WxPusher UID
   （配上同样在跑批环境里的 app_token 就能冒名推送）和付费用户邮箱
   （PII，且"出现在账本里"本身就等于"此人是会员"）挂到公网上。
   裸 sha256 也不行 —— 邮箱可枚举，那等于提供一个"输入邮箱查是否会员"的接口。
   一个 secret 都取不到时**放弃记账**（当天多推一次），绝不退化成硬编码盐：
   盐写在仓库里，"已脱敏"就只是自我安慰。

环境变量（GitHub Secrets）：
  WXPUSHER_APP_TOKEN     WxPusher 应用 token（用户投递主通道，AT_ 开头）
  DIGEST_SERVERCHAN_KEY  Server酱 SendKey —— **仅管理员告警**
  DIGEST_WECOM_WEBHOOK   企业微信群机器人（内部群播报，可选）
  DIGEST_SMTP_HOST / _PORT / _USER / _PASS / _FROM   邮件兜底（当前未配置＝不发）
  SUPABASE_URL / SUPABASE_KEY   取有效订阅名单（**两个都必须配**，缺一即整段跳过）
  DIGEST_TEST_TO         调试收件邮箱，逗号分隔
  WXPUSHER_TEST_UID      调试推送 UID，逗号分隔（自测用，无需真实订阅）
  DIGEST_DRY_RUN=1       只归档不发送
  DIGEST_LEDGER_SALT     账本目标键的 HMAC 密钥（可选；未配则回落到
                         SUPABASE_KEY / WXPUSHER_APP_TOKEN，全无则不记账）

退出码：0 正常（含全渠道未配置、含全员已收到而整体跳过）
        2 部分渠道失败 / 3 摘要无内容或归档失败
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.request
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import admin_notify as an
import digest as dg
import symbol_resolve as sr

DIGEST_DIR = os.path.join("data", "digest")
HIST_DIR = os.path.join(DIGEST_DIR, "history")
# 已投递账本：记录「哪一天、哪份内容、已经推给谁了」。
# 放在 data/ 下随 Actions 一起提交，这样多次 run 之间能互相看见彼此推过什么。
# 不能放 data/digest/history/（那里 page_digest._history_dates() 会按
# 「纯数字 .md」筛选，.json 不会被误收，但同目录混放两类语义的文件迟早出事）。
LEDGER_PATH = os.path.join(DIGEST_DIR, "push_ledger.json")


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


_BJ_PREFIX = ("92", "43", "83", "87")


def _tx_code(raw: str) -> str:
    """纯代码 → 腾讯行情代码。**已废弃，勿新增调用**。

    保留只为不破坏可能存在的外部引用。它按「6/5/9 开头算沪市」猜前缀，对
    000905 / 000016 / 000300 这类沪深撞号代码会静默取到另一只标的。
    正确做法是 `symbol_resolve.resolve_candidates()` —— 探沪深北后按当日是否
    真在交易取舍，并如实上报 ambiguous。
    """
    c = str(raw).strip().upper().split(".")[0]
    if not c.isdigit():
        return ""
    if len(c) == 6 and c[:2] in _BJ_PREFIX:
        return f"bj{c}"
    if c.startswith(("6", "5", "9")):
        return f"sh{c}"
    return f"sz{c}"


def fallback_pct_map(codes: list[str], date_key: str = "") -> dict:
    """
    兜底：只取「实际有人自选」的那几十个代码的涨幅（腾讯行情，纯 stdlib）。

    为什么要这条兜底：个性化段落是付费用户唯一拿到的独占内容，
    让它因为 tushare 一处失效就整段消失，等于当天的付费价值归零。
    全市场取数失败时，需要的其实只是这几十个代码，成本比全市场低两个数量级。

    **兜底不等于放宽真实性要求**：腾讯返回的是「最近一个交易日收盘」，
    如果它的日期与摘要日期不一致，说明拿到的是别的交易日的涨幅，
    此时整段放弃而不是拿错日期的数字冒充今日——宁可没有，不可有错。

    **市场前缀必须走 symbol_resolve 消歧**（2026-09-03 修）：这里原先用
    「6/5/9 开头算沪市，否则算深市」的朴素规则，自选里出现 000905 会静默取到
    深市「厦门港务」的涨幅，然后以「中证500」的名义推进用户微信——
    数字合法、格式正常、日志平静，用户毫无识别可能。这是全站最贵的一处静默取错。
    """
    codes = [c for c in {str(x).strip().upper().split(".")[0] for x in codes if x} if c]
    if not codes:
        return {}
    out: dict[str, float] = {}
    stale = set()
    ambiguous: list[str] = []
    for i in range(0, len(codes), 60):
        chunk = codes[i:i + 60]
        # 撞号代码要把沪深北都探到，再按「当日是否真在交易」取舍。腾讯对不存在
        # 的标的直接不返回该行，所以多探两个前缀几乎不增加成本。
        probe: dict[str, list[str]] = {}
        for c in chunk:
            if re.fullmatch(r"\d{6}", c):
                probe[c] = [f"{p}{c}" for p in sr.prefixes_for(c)]
            elif re.fullmatch(r"(SH|SZ|BJ)\d{6}", c):
                probe[c] = [c.lower()]
        if not probe:
            continue
        flat = [t for lst in probe.values() for t in lst]
        got = sr.fetch_quotes(flat, timeout=15)
        if got is None:
            print(f"⚠️ 兜底涨幅取数失败（{len(flat)} 个代码）")
            continue
        for bare_code, tx_list in probe.items():
            cands = [got[t] for t in tx_list if t in got]
            if not cands:
                continue
            cands.sort(key=sr.rank_key)
            q = cands[0]
            day = str(q.get("update_time") or "")[:8]
            if date_key and day and day != date_key:
                stale.add(day)
                continue
            out[bare_code] = q["pct_chg"]
            if sr.is_ambiguous(cands):
                ambiguous.append(f"{bare_code}→{sr.market_label(q['tx_code'])}{q.get('name')}"
                                 f"（另有 {'/'.join(sr.describe_alternatives(cands))}）")
    if stale:
        print(f"⚠️ 兜底行情日期为 {'/'.join(sorted(stale))}，与摘要日期 {date_key} 不符，已丢弃")
    if ambiguous:
        # 撞号本身不是错误（已按活跃度取到正确的那只），但必须留痕：日志里看得见，
        # 将来若先验判错，能直接从这行定位是哪个代码取歪了。
        print(f"ℹ️ 撞号代码已按活跃度消歧：{'；'.join(ambiguous)}")
    if out:
        print(f"↩️ 个性化段落改用腾讯行情兜底，命中 {len(out)}/{len(codes)} 个代码")
    return out


def recipients() -> tuple[list[dict] | None, str]:
    """
    返回 (名单, 说明)。名单元素 {user_id, email, wxpusher_uid, watchlist}。

    **None 与 [] 必须区分**：None = 取数本身失败（凭据缺失/网络/表结构变了），
    [] = 确实没有有效订阅。两者都返回 [] 的写法会让「配置坏了」长期伪装成
    「暂时没人付费」——日志平静地打印「有效订阅 0 位」，看起来完全正常。

    历史教训：到期列名实际是 `expires_at`，代码里曾写 `end_date or expire_date`，
    两个列都不存在 → `.get()` 恒 None → 有效订阅永远算成 0 位。
    字段名写错的取数逻辑不会抛异常，只会给出一个语法正确、语义为空的答案。
    """
    if not (_env("SUPABASE_URL") and _env("SUPABASE_KEY")):
        return None, "未配置 SUPABASE_URL / SUPABASE_KEY（两个都必须有），无法取订阅名单"
    try:
        import database as db
        rec = db.get_push_recipients()
    except Exception as e:
        return None, f"订阅名单取数异常：{type(e).__name__}: {e}"
    if rec is None:
        return None, "订阅名单取数失败（凭据、网络或 subscriptions 表结构不符）"
    bound = sum(1 for r in rec if r.get("wxpusher_uid"))
    with_wl = sum(1 for r in rec if r.get("watchlist"))
    unbound = len(rec) - bound
    msg = (f"有效订阅 {len(rec)} 位｜已绑微信 {bound} 位｜有自选股 {with_wl} 位")
    print(f"📇 {msg}")
    # 「付了钱但没绑微信」= 权益收了钱没交付。它在日志里长得跟正常一样
    # （渠道全成功、无失败项），只有显式点出来才会被处理。
    if unbound > 0:
        msg += f"｜⚠️ 付费未绑微信 {unbound} 位（这些人今天收不到任何投递）"
        print(f"   ⚠️ {unbound} 位有效订阅未绑定微信，今天不会收到投递——"
              "他们的会员权益有一半没生效")
    return rec, msg


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


# ================= 已投递账本（防重复轰炸） =================
#
# 为什么需要它（2026-09-03 实测）：
#   跑批一天真的会跑好几次 —— schedule 两条 cron（19:37 / 22:20）都在被平台
#   延迟 1~9 小时，实测 08-30 起每天 2~3 条 run 落地，加上看门狗补跑派发，
#   最多一天 4 次。每一次都会走到 send_wxpusher，于是付费用户同一份摘要
#   连收 3 遍，最后一遍常在凌晨 01:5x —— 半夜被推醒，是直接退订的理由。
#   实测 08-30 / 08-31 / 09-01 / 09-02 四天全部重复推送（3、3、2、3 次）。
#
# 幂等键为什么取「内容指纹」而不是「日期」：
#   按日期去重 = 当天第一次推完就永久闭嘴。而第一次很可能是坏的
#   （2026-09-01 RPS 连续三班扑空、摘要缺失项一堆），修好后补跑本该补推一次，
#   按日期去重会让用户手里永远留着坏版本 —— 把「防轰炸」做成了「防修复」。
#   按内容指纹：内容一字不变才跳过，变好了就补推。实测同一 date_key 的
#   2~3 条 run 摘要正文逐字相同，故指纹去重能完全消掉这批重复。
#
# 指纹取 markdown 全文而不是摘要行：摘要行只有几个指标，个性化段落、
# 新股池、战绩段变了它照样不变，会把「内容已更新」误判成「还是那份」。
#
# 账本按 UID / email 逐个记录，而不是只记一个全局标记：
#   同一天里新用户可能刚订阅、或某个 UID 上次失败了。逐个记录才能做到
#   「已经收到这版内容的人跳过，没收到的人照发」。只记全局标记会让
#   新订阅用户当天什么都收不到，而日志显示「已推送，跳过」，无人发现。

def _fingerprint(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


def _ledger_salt() -> str:
    """目标键摘要用的 HMAC 密钥。取任一已配置的 secret，绝不落盘。

    为什么要带密钥而不是裸 sha256：邮箱是**可枚举**的。裸 sha256 的账本等于
    「输入一个邮箱就能查他是不是付费会员」的在线查询接口 —— 公开仓库里放这个
    等于公开会员名单。HMAC 的密钥不在仓库里，这条路直接断掉。

    一个都取不到时返回空串，调用方据此**放弃写账本**（见 main）。
    绝不能退化成硬编码常量：常量就在仓库里，等于把密钥公开，
    「带了密钥」变成纯粹的心理安慰 —— 这种假安全比不做更危险。

    轮换 secret 的后果是所有键变了一次 → 当天最多多推一遍，之后自愈。
    这个代价远小于泄露名单，故不为它引入新的 secret（PAT 无 workflow scope，
    加 secret 就得人工改 yml）。

    残余暴露面（已知并接受）：账本仍会泄露「某天有多少个目标收到了推送」，
    即会员规模的下限。要连这个都藏起来只能把账本移出仓库（存 Supabase），
    代价是建表 + 改取数层，暂不做。
    """
    for k in ("DIGEST_LEDGER_SALT", "SUPABASE_KEY", "WXPUSHER_APP_TOKEN"):
        v = _env(k)
        if v:
            return v
    return ""


def _target_key(target) -> str:
    """账本里的目标键必须是**不可逆摘要**，不能是 UID / 邮箱原文。

    2026-09-03 收口：账本随 data/ 被 Actions 提交，而本仓库是**公开**的。
      · WxPusher UID 是投递凭据 —— 拿到 app_token（也在跑批环境里）+ UID
        就能冒名给这个人推消息；
      · 邮箱是 PII，且「出现在账本里」本身就等于「此人是付费会员」。
    账本只需回答「这个目标今天收过这份内容吗」，查询时目标已知，
    现算一次摘要比对即可，完全不需要存原文。

    群维度标记（__wecom__）不含个人信息，保留原文以便人工看账本时能读懂。
    """
    t = str(target)
    if t.startswith("__") and t.endswith("__"):
        return t
    salt = _ledger_salt()
    if not salt:
        # 没有密钥就没有可用的摘要。此处**绝不**退化成裸 sha256 或硬编码盐：
        # 那会把「已脱敏」的假象写进公开仓库。返回空串让调用方跳过记账。
        return ""
    return "t_" + hmac.new(salt.encode("utf-8"),
                           t.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def ledger_seen(done: dict | None, target) -> str | None:
    """这个目标已收到过的内容指纹（没收到过则 None）。

    渠道函数只认原始 UID / 邮箱，键的摘要化收在这一个函数里 ——
    否则「查的时候忘了摘要」就会全员判成未推过，去重静默失效。

    无密钥时 `_target_key` 返回空串 → 这里恒返 None → 全员按未推过处理。
    即「宁可重复推一次，也不把原文写进公开仓库」。
    """
    k = _target_key(target)
    if not k:
        return None
    return (done or {}).get(k)


def load_ledger() -> dict:
    """读已投递账本。**读不到与「确实没推过」必须区分**：

    读失败（文件损坏、IO 错误）时返回 None 而不是 {}。调用方据此选择
    「宁可重复推一次，也不要因为读不到账本就把所有人都当成已推过而集体漏推」。
    集体漏推是静默失败，用户与日志都看不出异常；重复推一次至少是可见的。
    """
    if not os.path.exists(LEDGER_PATH):
        return {}                                  # 确实没有账本 = 谁都没推过
    try:
        with open(LEDGER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:                         # noqa: BLE001
        print(f"⚠️ 账本读取失败（{type(e).__name__}: {e}）—— 本次按「未推过」处理，"
              f"可能重复推送一次；这比因读不到账本而集体漏推安全")
        return None


def save_ledger(ledger: dict) -> None:
    try:
        os.makedirs(DIGEST_DIR, exist_ok=True)
        tmp = LEDGER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(ledger, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LEDGER_PATH)                # 原子替换，避免半截文件
    except Exception as e:                          # noqa: BLE001
        print(f"⚠️ 账本写入失败（{type(e).__name__}: {e}）—— 下次跑批会重复推送")


def ledger_delivered(ledger: dict | None, date_key: str) -> dict:
    """返回「这一天已经成功投递过的 目标 → 内容指纹」映射。

    记录逐目标的指纹而不是一个全局指纹，是因为个性化段落是一人一份：
    base 正文没变、但某个用户中途改了自选股时，他那份内容其实变了，
    用全局指纹会把他判成「已推过」而永远收不到更新版。

    ledger 为 None（读取失败）时返回空 dict = 全部当作没推过（宁可重复不可漏）。
    """
    if not ledger:
        return {}
    rec = ledger.get(date_key) or {}
    d = rec.get("delivered")
    return dict(d) if isinstance(d, dict) else {}


def ledger_mark(ledger: dict, date_key: str, pairs) -> None:
    """pairs: [(target, fingerprint), ...] —— 只记**真正投递成功**的目标。

    target 在这里统一过 `_target_key()` 摘要，所以调用方传原始 UID / 邮箱即可，
    落盘的永远是不可逆摘要。一处摘要、一处比对，不给「一边摘要一边原文」
    留缝隙 —— 那种分叉的表现是去重静默失效（全员重复推、日志毫无异常）。
    """
    rec = ledger.get(date_key) or {}
    d = rec.get("delivered")
    d = dict(d) if isinstance(d, dict) else {}
    dropped = 0
    for t, fp in pairs:
        k = _target_key(t)
        if not k:                     # 无密钥 → 宁可不记账，也不写原文
            dropped += 1
            continue
        d[k] = fp
    if dropped:
        print(f"⚠️ 账本跳过 {dropped} 个目标：未配置任何可用作摘要密钥的 secret，"
              f"不能把 UID/邮箱原文写进公开仓库。后果是明天同一份内容会再推一次；"
              f"要根治请配 DIGEST_LEDGER_SALT")
    rec["delivered"] = d
    rec["last_push_at"] = _bj_now().strftime("%Y-%m-%d %H:%M:%S")
    ledger[date_key] = rec
    # 只留最近 30 天，避免账本无限增长（history/ 已经承担长期归档）
    for k in sorted(ledger.keys())[:-30]:
        ledger.pop(k, None)


# ================= 渠道 =================

def _post_json(url: str, body: dict, timeout: int = 15) -> tuple[bool, str]:
    """POST JSON，返回 (HTTP 是否 2xx, 响应体文本)。

    响应体截断长度取 800 而非 200：Server酱 的成败判定要靠响应体里的 `code`
    字段，截太短会把 JSON 切断导致解析失败，进而把失败误判成成功。
    """
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, r.read().decode("utf-8", "ignore")[:800]
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:800]}"
    except Exception as e:
        return False, str(e)


def send_wxpusher(rec: list[dict], pct_map: dict, base: dict,
                  done: dict | None = None) -> tuple[str | None, list]:
    """用户投递主通道。返回 (失败说明或 None, [(uid, 内容指纹), ...] 成功清单)。

    分两批发，原因是内容不同而不是为了省请求数：
      · 有自选股的用户 → 每人单独渲染（个性化段是付费的核心价值，必须一人一份）
      · 无自选股的用户 → 共用 base 正文，可以合并成一次请求（最多 2000 UID）

    未绑定微信的用户在这里被跳过而不是报错——他们只是还没扫码，不是故障。

    `done` 是「今天已经收到过这份内容的 uid → 指纹」映射（来自账本）。
    指纹一致的 uid 会被跳过，避免同一天被平台重复触发的跑批反复轰炸；
    指纹不同（内容更新了、或他改了自选股）则照发。
    """
    import wxpusher as wx
    if not wx.app_token():
        return None, []                               # 未配置 = 跳过，不算失败

    done = done or {}
    extra = [u.strip() for u in _env("WXPUSHER_TEST_UID").split(",") if u.strip()]
    targets = [(r["wxpusher_uid"], r.get("watchlist") or [])
               for r in (rec or []) if r.get("wxpusher_uid")]
    targets += [(u, []) for u in extra]
    # 同一个 UID 只发一次（调试 UID 可能与真实订阅重合）
    seen, dedup = set(), []
    for uid, wl in targets:
        if uid in seen:
            continue
        seen.add(uid)
        dedup.append((uid, wl))

    if not dedup:
        print("ℹ️ WxPusher 已配置但没有可投递对象（无人绑定微信，且未设 WXPUSHER_TEST_UID）")
        return None, []

    ok_all, bad_all, marks = [], [], []
    skipped = []
    # 个性化段是付费用户唯一独占的内容。只打「投递成功 N/N」看不出他们收到的是
    # 个性化版还是通用版——个性化渲染成空时，用户照样收到一条「成功」的通用摘要，
    # 日志毫无异常。故两类分别计数，并单独记录「本该个性化却降级」。
    n_person_ok, n_plain_ok, degraded = 0, 0, []

    # 个性化：一人一份
    for uid, wl in [(u, w) for u, w in dedup if w]:
        p = dg.build_markdown(watchlist=wl, pct_map=pct_map)
        fp = _fingerprint(p["markdown"])
        if ledger_seen(done, uid) == fp:
            skipped.append(uid)
            continue
        # 有自选股却没渲染出自选段 = 已降级为通用版，必须显式记录而不是静默投递
        if "你的池子" not in p["markdown"]:
            degraded.append(f"…{uid[-6:]}（自选 {len(wl)} 只）")
        o, b = wx.send_to_uids([uid], p["markdown"], p["plain"])
        ok_all += o
        bad_all += b
        n_person_ok += len(o)
        marks += [(u, fp) for u in o]

    # 通用：合并一次
    base_fp = _fingerprint(base["markdown"])
    plain_uids = [u for u, w in dedup if not w and ledger_seen(done, u) != base_fp]
    skipped += [u for u, w in dedup if not w and ledger_seen(done, u) == base_fp]
    if plain_uids:
        o, b = wx.send_to_uids(plain_uids, base["markdown"], base["plain"])
        ok_all += o
        bad_all += b
        n_plain_ok += len(o)
        marks += [(u, base_fp) for u in o]

    # 分母用「本次实际需要投递的人数」，跳过的单独报。
    # 若把跳过的也算进分母，日志会显示「投递成功 0/3」看起来像全挂了。
    need = len(dedup) - len(skipped)
    print(f"{'✅' if not bad_all else '⚠️'} WxPusher 投递成功 {len(ok_all)}/{need}"
          f"（个性化 {n_person_ok} 位｜通用 {n_plain_ok} 位）")
    if skipped:
        print(f"   ⏭️ 跳过 {len(skipped)} 位：今天已收到过完全相同的内容"
              f"（防重复轰炸，内容有更新会自动补推）")
    if degraded:
        print("   ⚠️ 有自选股但个性化段落为空，已降级为通用版："
              + "、".join(degraded[:8]))
    for b in bad_all[:8]:
        print(f"   ❌ {b}")
    return (None if not bad_all else f"wxpusher: {len(bad_all)} 个 UID 失败"), marks


def send_wecom(payload: dict) -> str | None:
    hook = _env("DIGEST_WECOM_WEBHOOK")
    if not hook:
        return None
    ok, msg = _post_json(hook, {"msgtype": "markdown",
                                "markdown": {"content": payload["markdown"][:4000]}})
    print(("✅ 企业微信推送成功" if ok else f"❌ 企业微信推送失败：{msg}"))
    return None if ok else f"wecom: {msg}"


def _serverchan_url(key: str) -> str:
    """端点推导已收进 admin_notify（站内下单告警要用同一套规则）。

    保留同名薄封装：一份规则两处实现必然漂移，而漂移的表现是
    「域名解析不到」，会被误判成网络问题、往错方向排查。
    """
    return an.serverchan_url(key)


def send_serverchan(title: str, body: str) -> str | None:
    """**管理员运维告警通道**（不是用户投递通道）。

    定位在 8/30 修正：Server酱 的 SendKey 是「一把 key 绑一个微信账号」的身份
    凭据，官方文档明确「群发：不支持」。它推不到第三方付费用户手上，
    只能推给站长自己。指望用它服务用户，架构上就是死路，不是配置问题。
    所以它现在只在「渠道失败 / 数据缺失 / 无人可投」时喊站长一声，
    调用频次天然落在免费版 5 条/天以内。

    两个会让"失败"伪装成"成功"的坑（额度耗尽仍返 HTTP 200 要看响应体
    `code`；title 不允许含换行）都在 admin_notify.send_serverchan 里处理。
    """
    if not _env("DIGEST_SERVERCHAN_KEY"):
        return None                        # 未配置不算失败
    ok, msg = an.send_serverchan(title, body)
    print(("✅ 管理员告警已发出" if ok else f"❌ 管理员告警发送失败：{msg}"))
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


def send_email(rec: list[dict], pct_map: dict, base: dict,
               done: dict | None = None) -> tuple[str | None, list]:
    """邮件兜底通道。返回 (失败说明或 None, [(email, 指纹), ...] 成功清单)。

    只发给「有效订阅但**没绑微信**」的用户 + 调试收件人。已绑微信的走 WxPusher，
    不重复轰炸——同一条摘要收两遍会让人直接退订。

    `done` 同 send_wxpusher：今天已收到过相同内容的收件人跳过。
    """
    conf = _smtp_conf()
    if not conf:
        return None, []
    done = done or {}
    extra = [e.strip() for e in _env("DIGEST_TEST_TO").split(",") if e.strip()]
    targets = [(r["email"], r.get("watchlist") or [])
               for r in (rec or [])
               if r.get("email") and not r.get("wxpusher_uid")]
    targets += [(e, []) for e in extra]
    if not targets:
        print("ℹ️ 邮件渠道已配置但无收件人（订阅用户都已绑微信，且未设 DIGEST_TEST_TO）")
        return None, []

    # 先算指纹再决定要不要连 SMTP：全员都已收到时不该白连一次服务器
    plan = []
    skipped = 0
    for email, wl in targets:
        p = dg.build_markdown(watchlist=wl, pct_map=pct_map) if wl else base
        fp = _fingerprint(p["markdown"])
        if ledger_seen(done, email) == fp:
            skipped += 1
            continue
        plan.append((email, p, fp))
    if not plan:
        print(f"   ⏭️ 邮件跳过 {skipped} 位：今天已收到过完全相同的内容")
        return None, []

    sent, failed, marks = 0, [], []
    try:
        srv = (smtplib.SMTP_SSL(conf["host"], conf["port"], timeout=25)
               if conf["port"] == 465 else
               smtplib.SMTP(conf["host"], conf["port"], timeout=25))
        if conf["port"] != 465:
            srv.starttls()
        srv.login(conf["user"], conf["pwd"])
    except Exception as e:
        print(f"❌ SMTP 登录失败：{e}")
        return f"smtp-login: {e}", []

    for email, p, fp in plan:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(p["title"], "utf-8")
        msg["From"] = conf["sender"]
        msg["To"] = email
        msg.attach(MIMEText(p["markdown"], "plain", "utf-8"))
        msg.attach(MIMEText(_md_to_html(p["markdown"]), "html", "utf-8"))
        try:
            srv.sendmail(conf["sender"], [email], msg.as_string())
            sent += 1
            marks.append((email, fp))
        except Exception as e:
            failed.append(f"{email}: {e}")
    try:
        srv.quit()
    except Exception:
        pass

    print(f"✅ 邮件发送 {sent}/{len(plan)} 成功"
          + (f"（跳过 {skipped} 位已收到相同内容）" if skipped else ""))
    if failed:
        for f in failed[:5]:
            print(f"   ❌ {f}")
        return f"smtp: {len(failed)} 封失败", marks
    return None, marks


# ================= 主流程 =================

def _admin_alert(date_key: str, failures: list[str], rec_msg: str,
                 base: dict) -> None:
    """给站长发运维告警——只在真有问题时发，否则会把 5 条/天配额浪费在报喜上。

    「什么算问题」必须包含「无人可投」：渠道全部返回成功、但一个人都没收到，
    在日志里长得跟正常一样。这是最容易被忽略的失败形态。
    """
    problems = list(failures)
    if base.get("missing"):
        problems.append(f"数据缺失 {len(base['missing'])} 项：{'；'.join(base['missing'][:3])}")
    if "有效订阅 0 位" in rec_msg or "无法取订阅名单" in rec_msg or "取数失败" in rec_msg:
        problems.append(rec_msg)
    # 付费未绑微信也是"问题"：收了钱、渠道全成功、但这个人什么都没收到。
    if "付费未绑微信" in rec_msg:
        problems.append("有付费用户未绑定微信，权益未实际交付（详见名单状态）")
    if not problems:
        return
    send_serverchan(
        f"⚠️ Chilam Club {date_key} 推送异常 {len(problems)} 项",
        "## 需要处理\n" + "\n".join(f"- {p}" for p in problems) +
        f"\n\n---\n\n名单状态：{rec_msg}\n\n摘要摘要行：{base.get('plain', '')}")


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

    rec, rec_msg = recipients()
    if rec is None:
        print(f"⚠️ {rec_msg}")
        rec = []

    # 全市场取数失败时，只补「实际有人自选」的那几十个代码。
    # base 用 watchlist=None 渲染，本来就不消费 pct_map，所以这里补在归档之后无影响。
    if not pct_map:
        wanted = sorted({c for r in rec for c in (r.get("watchlist") or [])})
        if wanted:
            pct_map = fallback_pct_map(wanted, date_key)

    # 已投递账本：同一天被平台重复触发的多次跑批不该把同一份摘要反复推给用户。
    # 读失败返回 None → ledger_delivered 给空 dict → 全员当作未推过（宁可重复不可漏）。
    ledger = load_ledger()
    done = ledger_delivered(ledger, date_key)
    if done:
        print(f"📒 账本：{date_key} 今天已投递 {len(done)} 个目标，"
              f"内容指纹一致者本次跳过")

    failures, marks = [], []
    # WxPusher 是用户投递主通道，放在最前面；企业微信是可选的内部群播报
    f_wx, m_wx = send_wxpusher(rec, pct_map, base, done)
    f_mail, m_mail = send_email(rec, pct_map, base, done)
    marks += m_wx + m_mail
    # 企业微信是「内部群播报」而非逐人投递，账本按目标去重那套不适用；
    # 它自己带一个群维度的键，避免同一天群里被刷 3 遍。
    wecom_fp = _fingerprint(base["markdown"])
    if ledger_seen(done, "__wecom__") == wecom_fp:
        print("   ⏭️ 企业微信跳过：今天已播报过完全相同的内容")
        f_wecom = None
    else:
        f_wecom = send_wecom(base)
        if f_wecom is None and _env("DIGEST_WECOM_WEBHOOK"):
            marks.append(("__wecom__", wecom_fp))
    for f in (f_wx, f_wecom, f_mail):
        if f:
            failures.append(f)

    # 只把**真正投递成功**的目标写进账本。失败的不写 → 下次跑批会重试，
    # 这正是「派发成功 ≠ 数据落盘」那条教训的同型处理：结果导向。
    if marks:
        ledger = ledger if isinstance(ledger, dict) else {}
        ledger_mark(ledger, date_key, marks)
        save_ledger(ledger)
        print(f"📒 账本已更新：本次新增 {len(marks)} 条投递记录 → {LEDGER_PATH}")

    # Server酱 不参与用户投递，只在有问题时告警（放最后，才能把上面的失败带上）
    _admin_alert(date_key, failures, rec_msg, base)

    configured = any(_env(k) for k in ("WXPUSHER_APP_TOKEN", "DIGEST_WECOM_WEBHOOK",
                                       "DIGEST_SMTP_HOST"))
    if not configured:
        print("ℹ️ 未配置任何用户投递渠道，摘要已归档可在站内查看。"
              "配置 WXPUSHER_APP_TOKEN（推荐）或 DIGEST_SMTP_* 后自动启用。")
        return

    if failures:
        print(f"⚠️ {len(failures)} 个渠道失败：{failures}")
        sys.exit(2)
    print("✅ 推送全部完成")


if __name__ == "__main__":
    main()
