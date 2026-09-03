# -*- coding: utf-8 -*-
"""已投递账本自检（daily_digest.py 的防重复轰炸逻辑）

为什么单独一个探针：这段逻辑的失败形态**全部是静默的**。
  · 去重写错 → 用户当天什么都收不到，日志显示「跳过 N 位」看着完全正常
  · 指纹写错 → 内容修好后不补推，用户手里永远留着坏版本
  · 账本读失败当成「全员已推」→ 集体漏推，日志同样毫无异常
  · 只记全局标记 → 当天新订阅的用户被误判已推
这四种都不会报错，也不会让退出码变红。所以断言必须落在
「谁真正收到了、收到的是哪一版」，而不是「函数跑没跑通」。

铁律遵循：
  · 唯一失败出口 bad()/ck()，探针自己不 print FAIL
  · 副作用型被测对象 → wx.send_to_uids / smtplib 全部换成计数器替身，
    并断言「不该投递处调用 0 次」
  · 每条断言旁有元断言守卫（确实进入了被测路径）
  · MUTATIONS 逐条造错反验，且先验「造错本身生效」（锚点唯一）
"""
from __future__ import annotations

import ast
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

PASS: list[str] = []
FAIL: list[str] = []


def ck(cond: bool, msg: str) -> None:
    (PASS if cond else FAIL).append(msg)
    print(("✅ " if cond else "❌ ") + msg)


def bad(msg: str) -> None:
    """唯一失败出口。探针内部任何异常都要走这里，不允许自己 print FAIL。"""
    ck(False, msg)


SRC = open(os.path.join(ROOT, "daily_digest.py"), encoding="utf-8").read()


# ============================================================ 测试替身
class FakeWx:
    """接管 wxpusher.send_to_uids，记录每次调用的收件人与内容。

    必须是计数器而不是 no-op：本探针的核心断言是「不该投递处调用 0 次」，
    换成 no-op 就无法区分「跳过了」和「发了但没记下来」。
    """

    def __init__(self, token="AT_fake"):
        self.token = token
        self.calls = []          # [(uids, markdown)]

    def app_token(self):
        return self.token

    def send_to_uids(self, uids, content, summary, **kw):
        self.calls.append((list(uids), content))
        return list(uids), []    # 全部成功

    # ---- 便于断言 ----
    @property
    def all_uids(self):
        out = []
        for u, _ in self.calls:
            out += u
        return out


class FailWx(FakeWx):
    def send_to_uids(self, uids, content, summary, **kw):
        self.calls.append((list(uids), content))
        return [], [f"{u}: 模拟失败" for u in uids]   # 全部失败


def load_dd(work: str, wx, env: dict | None = None):
    """在临时工作目录里导入 daily_digest，并把副作用换成替身。

    切 cwd 是必需的：DIGEST_DIR / LEDGER_PATH 都是相对路径，
    不切就会写进真实 data/ 目录。
    """
    import importlib
    for k in ("daily_digest", "digest", "wxpusher"):
        sys.modules.pop(k, None)
    fake_wx_mod = type(sys)("wxpusher")
    fake_wx_mod.app_token = wx.app_token
    fake_wx_mod.send_to_uids = wx.send_to_uids
    fake_wx_mod.CT_MARKDOWN = 3
    sys.modules["wxpusher"] = fake_wx_mod

    old = os.environ.copy()
    for k in list(os.environ):
        if k.startswith(("DIGEST_", "WXPUSHER_", "SUPABASE_", "TUSHARE_")):
            os.environ.pop(k, None)
    os.environ.update(env or {})
    cwd = os.getcwd()
    os.chdir(work)
    try:
        dd = importlib.import_module("daily_digest")
        importlib.reload(dd)
        return dd, old, cwd
    except Exception:
        os.chdir(cwd)
        os.environ.clear()
        os.environ.update(old)
        raise


def restore(old, cwd):
    os.chdir(cwd)
    os.environ.clear()
    os.environ.update(old)


BASE_MD = "# 摘要\n\n情绪冰点期 | 1进2 11%\n"
BASE = {"title": "T", "markdown": BASE_MD, "plain": "P",
        "missing": [], "has_content": True}


def mkbase(md: str) -> dict:
    d = dict(BASE)
    d["markdown"] = md
    return d


# ============================================================ 段一：纯函数
# 目标键要过 HMAC，没有任何 secret 时按设计**不记账**，所以纯函数段必须
# 显式给一个盐；「无盐」是本段末尾专门验的另一条路径。
SALT_KEYS = ("DIGEST_LEDGER_SALT", "SUPABASE_KEY", "WXPUSHER_APP_TOKEN")


def sec_pure():
    work = tempfile.mkdtemp(prefix="ledger_pure_")
    wx = FakeWx()
    try:
        dd, old, cwd = load_dd(work, wx, {"DIGEST_LEDGER_SALT": "probe-salt"})
    except Exception as e:                                   # noqa: BLE001
        bad(f"daily_digest 导入失败（{type(e).__name__}: {e}）")
        return
    try:
        # 指纹：同内容同指纹、异内容异指纹（后者才是它存在的理由）
        a = dd._fingerprint("hello")
        b = dd._fingerprint("hello")
        c = dd._fingerprint("hello ")
        ck(a == b and a != c, f"内容指纹：同文同码、异文异码（{a} vs {c}）")
        ck(len(a) == 16, f"指纹长度 16（实际 {len(a)}）")

        # 账本不存在 → {} 而不是 None（「确实没推过」与「读不到」必须分开）
        ck(dd.load_ledger() == {},
           "账本文件不存在 → 返回 {} 表示『谁都没推过』（不是 None）")

        # 账本损坏 → None（读不到）。这两个出口混一起就会集体漏推
        os.makedirs(dd.DIGEST_DIR, exist_ok=True)
        with open(dd.LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        ck(dd.load_ledger() is None,
           "账本损坏 → 返回 None 表示『读不到』（与『没推过』区分）")
        ck(dd.ledger_delivered(None, "20260903") == {},
           "读不到账本时 delivered 为空 → 全员按未推过处理（宁可重复不可漏）")

        # 账本是个 list（类型不对）也要判成读不到
        with open(dd.LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump([1, 2], f)
        ck(dd.load_ledger() is None, "账本顶层不是 dict → 判为读不到")

        # 写读往返 + 原子替换
        led = {}
        dd.ledger_mark(led, "20260903", [("U1", "fp1"), ("U2", "fp2")])
        dd.save_ledger(led)
        back = dd.load_ledger()
        k1, k2 = dd._target_key("U1"), dd._target_key("U2")
        ck(back is not None and back["20260903"]["delivered"] ==
           {k1: "fp1", k2: "fp2"},
           "账本写读往返一致（逐目标记录 目标→指纹）")
        ck("last_push_at" in back["20260903"], "账本记录最后投递时间（便于人工核对）")
        ck(not os.path.exists(dd.LEDGER_PATH + ".tmp"), "写入后不留 .tmp 残file")

        # ---- 目标键必须不可逆：账本随 data/ 提交到**公开**仓库 ----
        raw = json.dumps(back, ensure_ascii=False)
        ck("U1" not in raw and "U2" not in raw,
           f"账本落盘不含目标原文（公开仓库里 UID 是投递凭据、邮箱是 PII）")
        ck(dd._target_key("a@b.com") != "a@b.com" and
           "a@b.com" not in dd._target_key("a@b.com"),
           "邮箱经过摘要（否则账本 = 公开的付费会员名单）")
        ck(dd._target_key("U1") == dd._target_key("U1") and
           dd._target_key("U1") != dd._target_key("U2"),
           "目标键稳定且不同目标不碰撞（否则 A 收到过就把 B 也判成收过）")
        ck(dd._target_key("__wecom__") == "__wecom__",
           "群维度标记保留原文（不含个人信息，人工看账本要能读懂）")
        # 带密钥：换 salt 键必须变，否则「输入邮箱即可查是否会员」这条路仍然通
        os.environ["DIGEST_LEDGER_SALT"] = "salt-A"
        ka = dd._target_key("U1")
        os.environ["DIGEST_LEDGER_SALT"] = "salt-B"
        kb = dd._target_key("U1")
        os.environ["DIGEST_LEDGER_SALT"] = "probe-salt"
        ck(ka != kb, "目标键带密钥（裸 sha256 可被枚举邮箱反查会员身份）")

        # ---- 一个 secret 都没有时：绝不退化成硬编码盐（那是假脱敏）----
        saved = {k: os.environ.pop(k, None) for k in SALT_KEYS}
        ck(dd._ledger_salt() == "",
           "【元断言】确实清空了所有候选密钥（否则下面几条什么都没验到）")
        led_ns = {}
        dd.ledger_mark(led_ns, "20260903", [("U1", "fp1")])
        got = (led_ns.get("20260903") or {}).get("delivered") or {}
        ck(got == {},
           f"无密钥时不记账（宁可明天多推一次，也不把原文写进公开仓库）实际 {got}")
        ck(dd._target_key("U1") == "",
           "无密钥时目标键为空串（而不是硬编码盐 —— 盐在仓库里等于没盐）")
        ck(dd.ledger_seen({"U1": "fp1"}, "U1") is None,
           "无密钥时一律判『没推过』（去重失效但不泄露，方向选对了）")
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

        # ---- ledger_seen：用原文查，内部自己摘要 ----
        done = dd.ledger_delivered(back, "20260903")
        ck(dd.ledger_seen(done, "U1") == "fp1",
           "ledger_seen 用原始目标查询即可命中（摘要收在函数内部，只有一处实现）")
        ck(dd.ledger_seen(done, "U3") is None, "没投递过的目标查出 None")
        ck(dd.ledger_seen(None, "U1") is None, "done 为 None 时不炸且返回 None")

        # 换版：同一目标指纹更新，而不是被拒绝
        dd.ledger_mark(led, "20260903", [("U1", "fp1_new")])
        ck(led["20260903"]["delivered"][k1] == "fp1_new",
           "同一目标内容换版 → 指纹被更新（这样下次才会补推）")
        ck(led["20260903"]["delivered"][k2] == "fp2",
           "换版只影响该目标，其他人的记录不被清掉")

        # 30 天上限
        led2 = {}
        for i in range(1, 41):
            dd.ledger_mark(led2, f"202608{i:02d}", [("U", "f")])
        ck(len(led2) == 30, f"账本只留最近 30 天（实际 {len(led2)}）")
        ck("20260840" in led2 and "20260801" not in led2,
           "保留的是较新的日期，丢弃最旧的")

        # delivered 取数：只认对应日期
        led3 = {"20260902": {"delivered": {dd._target_key("U1"): "fp1"}}}
        ck(dd.ledger_delivered(led3, "20260903") == {},
           "查今天不会读到昨天的记录（日期隔离）")
        ck(dd.ledger_seen(dd.ledger_delivered(led3, "20260902"), "U1") == "fp1",
           "查对应日期能读到记录")
    finally:
        restore(old, cwd)
        shutil.rmtree(work, ignore_errors=True)


# ============================================================ 段二：真投递行为
def sec_wxpusher():
    """核心：同内容第二次必须 0 次调用；内容变了必须真发。

    元断言：每个「跳过」用例前面都要先有一次「真发」，否则「0 次调用」
    可能只是因为压根没进投递路径（没收件人、token 为空等），
    那种情况下断言恒真、什么都验不到。
    """
    work = tempfile.mkdtemp(prefix="ledger_wx_")
    wx = FakeWx()
    try:
        dd, old, cwd = load_dd(work, wx, {"WXPUSHER_APP_TOKEN": "AT_fake"})
    except Exception as e:                                   # noqa: BLE001
        bad(f"daily_digest 导入失败（{type(e).__name__}: {e}）")
        return

    def mkdone(marks):
        """用**生产函数**把成功清单转成 done 映射。

        绝不手搓 `{u: fp for u, fp in marks}` —— 目标键已改成 HMAC 摘要，
        手搓等于在探针里另写一份键规则：真实链路里键对不上（全员重复推）时，
        探针因为自己那份规则自洽而照样全绿。一份规则只能有一处实现。
        """
        led = {}
        dd.ledger_mark(led, "K", marks)
        return dd.ledger_delivered(led, "K")

    try:
        rec = [{"wxpusher_uid": "UID_A", "watchlist": []},
               {"wxpusher_uid": "UID_B", "watchlist": []}]

        # ---- 第一次：账本为空，两人都该收到 ----
        f, marks = dd.send_wxpusher(rec, {}, BASE, {})
        ck(f is None, f"首次投递无失败（实际 {f}）")
        ck(sorted(wx.all_uids) == ["UID_A", "UID_B"],
           f"【元断言】首次真的调用了投递，收件人 {sorted(wx.all_uids)}")
        ck(len(marks) == 2 and all(len(fp) == 16 for _, fp in marks),
           f"首次返回 2 条成功记录且带指纹（实际 {marks}）")
        first_calls = len(wx.calls)
        ck(first_calls >= 1, "【元断言】确实产生了对外请求（否则下面的 0 次无意义）")

        # ---- 第二次：内容一字不变，必须一次都不发 ----
        done = mkdone(marks)
        wx.calls.clear()
        f2, marks2 = dd.send_wxpusher(rec, {}, BASE, done)
        ck(len(wx.calls) == 0,
           f"同内容第二次：对外请求 0 次（实际 {len(wx.calls)} 次）← 防重复轰炸核心")
        ck(marks2 == [], f"跳过时不产生新的账本记录（实际 {marks2}）")
        ck(f2 is None, "全员跳过不算失败")

        # ---- 第三次：内容变了，必须真发 ----
        wx.calls.clear()
        newbase = mkbase(BASE_MD + "\n新增一段\n")
        f3, marks3 = dd.send_wxpusher(rec, {}, newbase, done)
        ck(sorted(wx.all_uids) == ["UID_A", "UID_B"],
           f"内容更新后补推给所有人（实际 {sorted(wx.all_uids)}）← 防的是『防修复』")
        ck(len(marks3) == 2 and marks3[0][1] != marks[0][1],
           "补推后写入的是新指纹（否则下次又会重发）")

        # ---- 第四次：只有一个人推过，另一个必须照发 ----
        wx.calls.clear()
        partial = mkdone([("UID_A", dd._fingerprint(BASE_MD))])
        f4, marks4 = dd.send_wxpusher(rec, {}, BASE, partial)
        ck(wx.all_uids == ["UID_B"],
           f"部分已推：只发没收到的那位（实际 {wx.all_uids}）← 防的是新订阅用户漏推")

        # ---- 投递失败不得写账本（否则下次不会重试）----
        fwx = FailWx()
        sys.modules["wxpusher"].send_to_uids = fwx.send_to_uids
        f5, marks5 = dd.send_wxpusher(rec, {}, BASE, {})
        ck(len(fwx.calls) >= 1, "【元断言】失败用例确实发起了投递")
        ck(marks5 == [],
           f"投递失败时不写账本 → 下次跑批会重试（实际 {marks5}）")
        ck(f5 and "wxpusher" in f5, f"投递失败要返回失败说明（实际 {f5}）")
        sys.modules["wxpusher"].send_to_uids = wx.send_to_uids

        # ---- 个性化用户：指纹按各自内容算 ----
        wx.calls.clear()
        rec_p = [{"wxpusher_uid": "UID_P", "watchlist": ["600519"]}]
        f6, marks6 = dd.send_wxpusher(rec_p, {}, BASE, {})
        ck(len(marks6) == 1, "【元断言】个性化用户确实投递了 1 次")
        pfp = marks6[0][1]
        base_fp = dd._fingerprint(BASE["markdown"])
        ck(pfp != base_fp,
           "个性化用户的指纹按他自己那份内容算（用 base 指纹会导致自选变更后不补推）")
        wx.calls.clear()
        f7, marks7 = dd.send_wxpusher(rec_p, {}, BASE, mkdone([("UID_P", pfp)]))
        ck(len(wx.calls) == 0, "个性化用户同内容第二次也跳过")

        # ---- 未配置 token：返回二元组而不是抛错（契约变更处最易漏改）----
        sys.modules["wxpusher"].app_token = lambda: ""
        r = dd.send_wxpusher(rec, {}, BASE, {})
        ck(isinstance(r, tuple) and len(r) == 2 and r == (None, []),
           f"未配置 token 时返回 (None, []) 而非单值（实际 {r!r}）")
        sys.modules["wxpusher"].app_token = wx.app_token
    except Exception as e:                                   # noqa: BLE001
        bad(f"投递行为段异常（{type(e).__name__}: {e}）")
    finally:
        restore(old, cwd)
        shutil.rmtree(work, ignore_errors=True)


# ============================================================ 段三：源码结构
def sec_source():
    """防止逻辑被「改回去」的结构断言。

    这些不是重复段二 —— 段二验行为，这里验「主流程是否真的把账本接上了」。
    行为断言直接调 send_wxpusher，即使 main() 里忘了传 done 也照样全绿。
    """
    ck("LEDGER_PATH" in SRC and "push_ledger.json" in SRC,
       "账本落在 data/digest/push_ledger.json（随 Actions 提交，多次 run 可见）")

    # ★ 上面那句只证明「代码想把账本写在哪」，证不到它**真能跨 run 活下来**。
    #   账本的全部价值就在跨 run 可见：一旦被 .gitignore 挡掉、或提交步骤没覆盖
    #   它所在的目录，每次 run 都读到空账本 → 去重整体空转、天天重复推，
    #   而日志一切正常（「账本已更新」照样打印，只是写完就丢）。这是最典型的
    #   静默失败，必须用外部事实（git 自己的判定 + yml 的提交步骤）来验。
    _rel = "data/digest/push_ledger.json"
    _p = subprocess.run(["git", "check-ignore", "-v", _rel],
                        capture_output=True, text=True, cwd=ROOT)
    # check-ignore: 退出 0 = 被忽略（有匹配规则），1 = 没被忽略（我们要的）
    ck(_p.returncode == 1,
       f"账本未被 .gitignore 排除（否则跨 run 不可见，去重全程空转）"
       f"{'：命中规则 ' + _p.stdout.strip() if _p.returncode == 0 else ''}")
    # 元断言：check-ignore 这个探法本身得真的会报「被忽略」，否则上一条恒过
    _p2 = subprocess.run(["git", "check-ignore", "-v", "data/chilam.db"],
                         capture_output=True, text=True, cwd=ROOT)
    ck(_p2.returncode == 0,
       "【元断言】check-ignore 确实能识别被忽略的路径（拿已知被忽略的 data/chilam.db 校准）")

    _wf = open(os.path.join(ROOT, ".github", "workflows", "daily_update.yml"),
               encoding="utf-8").read()
    _wf_code = "\n".join(ln for ln in _wf.splitlines()
                         if not ln.lstrip().startswith("#"))
    ck(re.search(r"^\s*git add\s+(data/|\.)\s*$", _wf_code, re.M) is not None,
       "yml 提交步骤覆盖 data/（账本所在目录必须被 git add，否则永远提交不上去）")

    # main() 必须：读账本 → 传给两个渠道 → 只记成功 → 保存
    i_load = SRC.find("ledger = load_ledger()")
    i_wx = SRC.find("send_wxpusher(rec, pct_map, base, done)")
    i_mail = SRC.find("send_email(rec, pct_map, base, done)")
    i_save = SRC.find("save_ledger(ledger)")
    ck(i_load > 0, "main() 调用 load_ledger()")
    ck(i_wx > i_load > 0, "读账本排在 WxPusher 投递之前（否则去重不生效）")
    ck(i_mail > i_load > 0, "读账本排在邮件投递之前")
    ck(i_save > i_wx > 0 and i_save > i_mail > 0,
       "写账本排在所有投递之后（先投递成功再记账）")

    # 账本必须在 archive 之后：归档不能被投递逻辑挡住
    i_arch = SRC.find("archive(base, date_key)")
    ck(0 < i_arch < i_load, "归档仍排在账本与投递之前（推送失败也留产物）")

    # has_content 守卫仍在所有投递之前
    i_hc = SRC.find("if not base[\"has_content\"]")
    ck(0 < i_hc < i_wx, "has_content 空内容守卫仍排在投递之前（防空推送）")

    # DRY_RUN 仍在投递之前，且不写账本（否则干跑一次真跑就永远不发了）
    #
    # ★ 这里必须锚定**代码里的守卫行**，不能 SRC.find("DIGEST_DRY_RUN")：
    #   文件头的环境变量说明里也写着 DIGEST_DRY_RUN=1，find 会命中那句文档，
    #   于是切出来的 seg 横跨了整个 ledger 函数定义区、必然含 ledger_mark，
    #   断言恒假。断言必须认对人 —— 第一次写就踩了这个坑。
    i_dry = SRC.find('if _env("DIGEST_DRY_RUN") == "1":')
    ck(0 < i_dry < i_wx, "DRY_RUN 守卫（代码行而非文档）排在投递之前")
    seg_dry = SRC[i_dry:SRC.find("rec, rec_msg = recipients()")]
    ck(0 < len(seg_dry) < 400,
       f"【元断言】DRY_RUN 段切得对（{len(seg_dry)} 字符，过长说明锚点又抓到文档）")
    ck("ledger_mark" not in seg_dry and "save_ledger" not in seg_dry,
       "DRY_RUN 分支不写账本（干跑一次就让真跑闭嘴是最坏的静默失败）")
    ck("return" in seg_dry, "【元断言】DRY_RUN 段里确实有 return（是守卫分支本体）")

    # 企业微信按群维度去重
    ck("__wecom__" in SRC, "企业微信按群维度单独记账（不与逐人投递混用）")

    # 指纹取全文而不是摘要行
    seg_fp = SRC[SRC.find("def _fingerprint"):SRC.find("def load_ledger")]
    ck("markdown" in seg_fp or "hashlib.sha256" in seg_fp,
       "指纹基于 markdown 全文（只取摘要行会漏掉个性化/战绩段的变化）")
    ck("sha256" in seg_fp, "指纹用 sha256（不是易碰撞的短哈希）")

    # 读失败必须返回 None 而不是 {}
    seg_load = SRC[SRC.find("def load_ledger"):SRC.find("def save_ledger")]
    ck("return None" in seg_load,
       "load_ledger 读失败返回 None（与『文件不存在』的 {} 区分）")
    ck("return {}" in seg_load,
       "load_ledger 文件不存在返回 {}（表示确实谁都没推过）")

    # save 用原子替换
    seg_save = SRC[SRC.find("def save_ledger"):SRC.find("def ledger_delivered")]
    ck("os.replace" in seg_save,
       "账本写入用 os.replace 原子替换（中途中断不会留半截 json）")

    # 账本文件不能落进 history/（page_digest 会扫那个目录按日期列归档）
    ck('os.path.join(DIGEST_DIR, "push_ledger.json")' in SRC,
       "账本不放 history/（那里被 page_digest 按日期扫描，混放两类语义迟早出事）")

    # 两个渠道函数签名都必须返回二元组
    for fn in ("send_wxpusher", "send_email"):
        seg = SRC[SRC.find(f"def {fn}"):]
        seg = seg[:seg.find("\ndef ", 10)]
        ck("tuple[str | None, list]" in seg,
           f"{fn} 返回 (失败说明, 成功清单) 二元组，签名已声明")
        ck("done" in seg.split("\n")[0] or "done:" in seg[:300],
           f"{fn} 接收 done 参数（已投递映射）")


# ============================================================ 段四：前端不受影响
def sec_frontend():
    """账本文件是新增到 data/digest/ 的，必须确认它不会被前端当成归档摘要列出来。"""
    src_page = open(os.path.join(ROOT, "page_digest.py"), encoding="utf-8").read()
    seg = src_page[src_page.find("def _history_dates"):]
    seg = seg[:seg.find("\ndef ", 10)]
    ck('endswith(".md")' in seg,
       "page_digest 只收 .md（push_ledger.json 不会出现在历史列表里）")
    ck("isdigit()" in seg, "且文件名须为纯数字日期（双重过滤）")
    # 真跑一遍：把账本放进 digest 目录，确认列表不含它
    work = tempfile.mkdtemp(prefix="ledger_fe_")
    try:
        hist = os.path.join(work, "data", "digest", "history")
        os.makedirs(hist)
        open(os.path.join(hist, "20260902.md"), "w", encoding="utf-8").write("x")
        open(os.path.join(work, "data", "digest", "push_ledger.json"),
             "w", encoding="utf-8").write("{}")
        # 复刻 _history_dates 的逻辑（不 import streamlit）
        got = sorted([fn[:-3] for fn in os.listdir(hist)
                      if fn.endswith(".md") and fn[:-3].isdigit()], reverse=True)
        ck(got == ["20260902"],
           f"实跑过滤：历史列表只有 20260902，不含账本（实际 {got}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ============================================================ 段五：端到端跑 main()
DERIVED_TPL = {
    "date": "20260902",
    "status": "ok",
    "phase": {"status": "ok", "phase": "冰点期", "basis": "连板高度回落至 5 板",
              "note": "仅为结构描述，不构成投资建议"},
    "promotion": {"rates": {"1进2": {"rate": 0.11, "promoted": 1,
                                     "base": 9, "reliable": False}}},
    "premium": {"status": "ok", "median_pct": -1.2, "win_rate": 0.3, "n": 10},
    "ladder_gap": {"status": "ok", "max_height": 5, "gaps": [], "total": 30},
    "verification_plan": [{"指标": "1进2 晋级率", "今日基准": "11%",
                           "验证条件": "明日 >20% 视为回暖"}],
}


def mkwork(basis: str = "连板高度回落至 5 板") -> str:
    """造一个最小可跑的 data/ 目录：只要 derived.json 齐了，has_content 就为真。"""
    work = tempfile.mkdtemp(prefix="ledger_e2e_")
    d = os.path.join(work, "data", "sentiment")
    os.makedirs(d)
    os.makedirs(os.path.join(work, "data", "digest", "history"))
    payload = copy.deepcopy(DERIVED_TPL)
    payload["phase"]["basis"] = basis
    with open(os.path.join(d, "derived.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return work


def set_basis(work: str, basis: str) -> None:
    p = os.path.join(work, "data", "sentiment", "derived.json")
    with open(p, encoding="utf-8") as f:
        payload = json.load(f)
    payload["phase"]["basis"] = basis
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def run_main(dd) -> tuple[int, str]:
    """跑一次 main()，返回 (退出码, 输出)。main() 里 sys.exit 要按值收住。"""
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            dd.main()
    except SystemExit as e:                                  # noqa: PERF203
        code = int(e.code or 0)
    return code, buf.getvalue()


def sec_main_e2e():
    """**这一段才是线上真正发生的事**：同一天跑批被平台触发 2~3 次。

    为什么非要端到端：段二直接调 send_wxpusher(rec, ..., done)，done 是探针
    自己喂进去的。哪怕 main() 里压根不读账本、不写账本，段二照样全绿。
    2026-09-03 反向验证第 10 条（把 `if marks:` 改成 `if False:`）就是这样
    没被抓住的 —— 投递成功却不记账，下一班照样重复推，而探针全绿。

    所以这里不喂任何 done：让 main() 自己读盘、自己写盘，
    断言落在「第二次跑批时对外请求 0 次」和「账本文件真的存在」。
    """
    work = mkwork()
    wx = FakeWx()
    env = {"WXPUSHER_APP_TOKEN": "AT_fake", "WXPUSHER_TEST_UID": "UID_E2E"}
    try:
        dd, old, cwd = load_dd(work, wx, env)
    except Exception as e:                                   # noqa: BLE001
        bad(f"端到端段导入失败（{type(e).__name__}: {e}）")
        shutil.rmtree(work, ignore_errors=True)
        return
    try:
        ledger_file = os.path.join(work, "data", "digest", "push_ledger.json")

        # ---- 第一跑：账本为空，必须真发，且必须落盘 ----
        code1, out1 = run_main(dd)
        ck(code1 == 0, f"首跑退出码 0（实际 {code1}）")
        ck(wx.all_uids == ["UID_E2E"],
           f"【元断言】首跑真的投递了（收件人 {wx.all_uids}）—— 否则下面全是空转")
        ck("推送全部完成" in out1, "【元断言】首跑走完了完整主流程")
        ck(os.path.exists(ledger_file),
           "首跑后账本文件已落盘（多次 run 之间就靠它互相看见推过什么）")
        led = json.load(open(ledger_file, encoding="utf-8")) if \
            os.path.exists(ledger_file) else {}
        rec1 = (led.get("20260902") or {}).get("delivered") or {}
        key = dd._target_key("UID_E2E")
        ck(list(rec1) == [key] and len(rec1.get(key, "")) == 16,
           f"【元断言】账本里记的是本次收件人（摘要）+ 16 位指纹（实际 {rec1}）")
        ck("UID_E2E" not in json.dumps(led),
           "端到端落盘的账本不含 UID 原文（公开仓库 + UID 是投递凭据）")
        fp1 = rec1.get(key)

        # ---- 第二跑：同一天、内容一字不变 → 一次都不许发 ----
        wx.calls.clear()
        code2, out2 = run_main(dd)
        ck(len(wx.calls) == 0,
           f"端到端第二次跑批：对外请求 0 次（实际 {len(wx.calls)} 次）"
           f"← 线上真正要防的那次重复轰炸")
        ck("跳过" in out2, "第二次跑批日志显式说明「已跳过」（不能静悄悄）")
        led2 = json.load(open(ledger_file, encoding="utf-8"))
        ck((led2.get("20260902") or {}).get("delivered") == rec1,
           "第二次跑批不改账本（内容没变就不该产生新记录）")
        ck(code2 == 0, f"全员已收到时整体算成功（退出码 {code2}）")

        # ---- 第三跑：摘要内容变了（跑批把缺失项补上了）→ 必须补推 ----
        wx.calls.clear()
        set_basis(work, "连板高度回落至 3 板，且赚钱效应转负")
        code3, out3 = run_main(dd)
        ck(wx.all_uids == ["UID_E2E"],
           f"内容变了：端到端第三次跑批会补推（实际 {wx.all_uids}）"
           f"← 防的是把「防轰炸」做成「防修复」")
        led3 = json.load(open(ledger_file, encoding="utf-8"))
        fp3 = ((led3.get("20260902") or {}).get("delivered") or {}).get(key)
        ck(fp3 and fp3 != fp1,
           f"补推后账本指纹已更新（{fp1} → {fp3}），否则下一班又会重发一次")

        # ---- 第四跑：投递全失败 → 不许记账，退出码要如实 ----
        work2 = mkwork()
        fwx = FailWx()
        sys.modules["wxpusher"].send_to_uids = fwx.send_to_uids
        os.chdir(work2)
        code4, out4 = run_main(dd)
        ck(len(fwx.calls) >= 1, "【元断言】失败用例确实发起了投递")
        ck(code4 == 2, f"端到端投递失败：退出码 2 如实反映（实际 {code4}）")
        ck(not os.path.exists(os.path.join(work2, "data", "digest",
                                           "push_ledger.json")),
           "端到端投递失败不落账本 → 下一班会重试（而不是永久漏推这个人）")
        os.chdir(work)
        sys.modules["wxpusher"].send_to_uids = wx.send_to_uids
        shutil.rmtree(work2, ignore_errors=True)
    except Exception as e:                                   # noqa: BLE001
        bad(f"端到端段异常（{type(e).__name__}: {e}）")
    finally:
        restore(old, cwd)
        shutil.rmtree(work, ignore_errors=True)


# ============================================================ 反向造错
# 每条：(锚点原文, 替换文, 期望在 [FAIL] 行里命中的关键字, 说明)
# 锚点必须在源码中唯一，且替换后语义**真的**变化（等价改动 = 没造错）。
MUTATIONS = [
    ("if ledger_seen(done, uid) == fp:\n            skipped.append(uid)\n            continue",
     "if False:\n            skipped.append(uid)\n            continue",
     "个性化用户同内容第二次也跳过",
     "去掉个性化去重 → 付费用户同一份内容仍被重复轰炸"),

    ('plain_uids = [u for u, w in dedup if not w and ledger_seen(done, u) != base_fp]',
     'plain_uids = [u for u, w in dedup if not w]',
     "对外请求 0 次",
     "去掉通用去重 → 同内容第二次还会重复发"),

    ("        marks += [(u, base_fp) for u in o]",
     "        marks += [(u, base_fp) for u in (dedup and plain_uids)]",
     "投递失败时不写账本",
     "把「计划投递」当成「投递成功」记账 → 失败后不再重试，用户永久漏推"),

    ('return data if isinstance(data, dict) else None',
     'return data if isinstance(data, dict) else {}',
     "账本顶层不是 dict",
     "类型不对却返回 {} → 与真·未推过混淆"),

    ('        print(f"⚠️ 账本读取失败（{type(e).__name__}: {e}）—— 本次按「未推过」处理，"\n'
     '              f"可能重复推送一次；这比因读不到账本而集体漏推安全")\n'
     '        return None',
     '        return {}',
     "账本损坏",
     "账本损坏时静默当成 {} → 丢失告警，且与 None 语义混淆"),

    ('    for k in sorted(ledger.keys())[:-30]:\n        ledger.pop(k, None)',
     '    pass',
     "只留最近 30 天",
     "账本无限增长"),

    ('        os.replace(tmp, LEDGER_PATH)',
     '        _placeholder = tmp',
     "账本写读往返一致",
     "非原子写入 → 文件压根没落地，写读往返立刻断（行为断言先抓住，结构断言只是防回退）"),

    ('    f_wx, m_wx = send_wxpusher(rec, pct_map, base, done)',
     '    f_wx, m_wx = send_wxpusher(rec, pct_map, base, {})',
     "读账本排在 WxPusher 投递之前",
     "main 里不传 done → 行为断言全绿但线上照样重复轰炸"),

    ('        fp = _fingerprint(p["markdown"])\n        if ledger_seen(done, uid) == fp:',
     '        fp = _fingerprint(base["markdown"])\n        if ledger_seen(done, uid) == fp:',
     "个性化用户的指纹按他自己那份内容算",
     "个性化用户用 base 指纹 → 他改了自选股也收不到更新版"),

    ('    if marks:\n        ledger = ledger if isinstance(ledger, dict) else {}',
     '    if False:\n        ledger = ledger if isinstance(ledger, dict) else {}',
     "端到端第二次跑批：对外请求 0 次",
     "投递成功却不写账本 → 下一班照样重复推"),

    ('    ledger = load_ledger()\n    done = ledger_delivered(ledger, date_key)',
     '    ledger = load_ledger()\n    done = {}',
     "端到端第二次跑批：对外请求 0 次",
     "main 读了账本却不用 → 去重整体失效（只有端到端能抓）"),

    ('    return "t_" + hmac.new(salt.encode("utf-8"),\n'
     '                           t.encode("utf-8"), hashlib.sha256).hexdigest()[:16]',
     '    return t',
     "账本落盘不含目标原文",
     "目标键存原文 → 公开仓库里 UID（投递凭据）与邮箱（PII）直接暴露"),

    ('    return "t_" + hmac.new(salt.encode("utf-8"),\n'
     '                           t.encode("utf-8"), hashlib.sha256).hexdigest()[:16]',
     '    return "t_" + hashlib.sha256(t.encode("utf-8")).hexdigest()[:16]',
     "目标键带密钥",
     "裸 sha256 → 输入邮箱就能反查此人是否付费会员（账本 = 公开名单查询接口）"),

    ('    k = _target_key(target)\n'
     '    if not k:\n'
     '        return None\n'
     '    return (done or {}).get(k)',
     '    return (done or {}).get(str(target))',
     "ledger_seen 用原始目标查询即可命中",
     "查询忘了摘要 → 全员判成未推过，去重静默失效（日志毫无异常）"),

    ('        k = _target_key(t)\n'
     '        if not k:                     # 无密钥 → 宁可不记账，也不写原文\n'
     '            dropped += 1\n'
     '            continue\n'
     '        d[k] = fp',
     '        d[str(t)] = fp',
     "账本落盘不含目标原文",
     "记账忘了摘要 → 原文照样落进公开仓库，且与查询侧键规则分叉"),

    ('    if not salt:\n'
     '        # 没有密钥就没有可用的摘要。此处**绝不**退化成裸 sha256 或硬编码盐：\n'
     '        # 那会把「已脱敏」的假象写进公开仓库。返回空串让调用方跳过记账。\n'
     '        return ""',
     '    if not salt:\n        salt = "chilam-club-fallback-salt"',
     "无密钥时目标键为空串",
     "无 secret 时退化成硬编码盐 → 盐就在公开仓库里，脱敏是假的"),

    ('        k = _target_key(t)\n'
     '        if not k:                     # 无密钥 → 宁可不记账，也不写原文\n'
     '            dropped += 1\n'
     '            continue',
     '        k = _target_key(t) or str(t)',
     "无密钥时不记账",
     "无密钥时回落到原文当键 → 正是要防的那件事"),
]


def run_negative() -> tuple[int, int]:
    """逐条造错 → 跑子进程 → 断言 exit≠0 且期望关键字出现在 [FAIL] 行里。

    为什么必须限定在 [FAIL] 行：ck() 无论成败都会打印同一句 msg，
    在全量输出里搜关键字则恒命中 → 反向验证假绿。
    """
    src_path = os.path.join(ROOT, "daily_digest.py")
    orig = open(src_path, encoding="utf-8").read()
    ok = 0
    for i, (anchor, repl, expect, why) in enumerate(MUTATIONS, 1):
        n = orig.count(anchor)
        if n != 1:
            print(f"❌ [造错 {i}] 锚点在源码中出现 {n} 次（须唯一）—— 造错未生效，"
                  f"这条反向验证什么都没验到：{why}")
            continue
        mutated = orig.replace(anchor, repl, 1)
        if mutated == orig:
            print(f"❌ [造错 {i}] 替换后源码未变（等价改动 = 没造错）：{why}")
            continue
        try:
            with open(src_path, "w", encoding="utf-8") as f:
                f.write(mutated)
            p = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools_probe_push_ledger.py"),
                 "--positive-only"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=ROOT, timeout=300)
            out = (p.stdout or "") + (p.stderr or "")
            fail_lines = [ln for ln in out.split("\n") if ln.startswith("❌ ")]
            fail_lines += [ln for ln in out.split("\n") if ln.strip().startswith("❌ ")]
            hit = any(expect in ln for ln in fail_lines)
            if p.returncode != 0 and hit:
                print(f"✅ [造错 {i}] 被抓住（exit={p.returncode}）：{why}")
                ok += 1
            elif p.returncode == 0:
                print(f"❌ [造错 {i}] 没抓住！探针仍然全绿 —— 断言是假的：{why}")
            else:
                print(f"❌ [造错 {i}] 失败了但不是因为目标断言（exit={p.returncode}），"
                      f"期望 [FAIL] 行含「{expect}」，实际失败项：{fail_lines[:4]}")
        finally:
            with open(src_path, "w", encoding="utf-8") as f:
                f.write(orig)
    return ok, len(MUTATIONS)


def main() -> int:
    positive_only = "--positive-only" in sys.argv
    sec_pure()
    sec_wxpusher()
    sec_source()
    sec_frontend()
    sec_main_e2e()
    print("-" * 64)
    print(f"正向：通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("\n失败项：")
        for m in FAIL:
            print("  ❌ " + m)
        return 1
    if positive_only:
        print("✅ 正向全部通过（--positive-only，跳过反向造错）")
        return 0

    print("\n" + "=" * 64)
    print("反向验证：逐条故意造错，确认上面的断言真能抓住")
    print("=" * 64)
    ok, total = run_negative()
    print("-" * 64)
    print(f"反向：{ok}/{total} 条造错被抓住")
    if ok != total:
        print("❌ 有断言抓不住对应的 bug —— 这些断言不可信")
        return 1
    print("✅ 正向 + 反向全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())





