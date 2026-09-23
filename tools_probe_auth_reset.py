# -*- coding: utf-8 -*-
"""密码修改 / 忘记密码 自检探针。

覆盖三层，缺一层就不算验过：
  A. 纯计算层（password_reset）：码空间、字符集、归一化、加盐哈希、掩码、链接
  B. 编排层（auth）：限流三出口、通道降级、单次消费、试错上限、过期 fail-closed、
     **以及每条拒绝路径上「不该发生的副作用必须为 0 次」**
  C. 接线与契约（源码 AST / SQL 文本）：明文码不落库、码不回显进提示、
     SMTP 唯一真源、三个页面的调用点、RLS 策略取舍

关于「副作用计数归零」为什么是本探针的主体：
    这套功能里最危险的失效不是「报错」，而是「拒绝的同时把事情做了一半」——
    比如码校验失败却仍改了密码、限流拦截却仍发出了码。这类 bug 在人工点测里
    几乎不可能被发现（页面提示是对的），只有把副作用函数换成计数器、
    并显式断言**不该触发处为 0 次**才抓得住。所以下面每条拒绝路径都配了一条
    `n(...) == 0` 的断言，而不是只断言「返回了 False」。
"""
from __future__ import annotations

import ast
import contextlib
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_pass = _fail = _unverified = 0
_fail_msgs: list[str] = []


def ck(cond, msg: str) -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"✅ {msg}")
    else:
        _fail += 1
        _fail_msgs.append(msg)
        print(f"❌ {msg}")


def bad(msg: str) -> None:
    """失败的唯一出口——自己 print 一行 ❌ 会漏出统计之外。"""
    global _fail
    _fail += 1
    _fail_msgs.append(msg)
    print(f"❌ {msg}")


def uv(msg: str) -> None:
    """未验证：前提不成立导致某项**根本没验到**。按失败计（不可当通过）。"""
    global _unverified
    _unverified += 1
    _fail_msgs.append("[未验证] " + msg)
    print(f"⚠️ [未验证] {msg}")


def _read(path: str) -> str:
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _fn_ast(path: str, name: str):
    """取某个顶层函数的 AST（按行区间切出后 dedent，避免缩进导致解析失败）。"""
    src = _read(path)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            lines = src.split("\n")[node.lineno - 1:node.end_lineno]
            return ast.parse("\n".join(lines))
    return None


def _calls_in(tree, chain: tuple) -> int:
    """数出形如 a.b.c(...) 的调用次数（chain=('auth','change_password')）。"""
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


def _dict_keys(tree, varname: str) -> set:
    """取 `varname = {...}` 这个字典字面量的字符串键集合。

    为什么要专门定位一份字典而不是扫全函数的字符串常量：函数里还有错误协议
    的键（`{"code": "UNKNOWN"}`），全量扫会把合法的错误码键认成「写了明文码字段」。
    """
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == varname
                and isinstance(node.value, ast.Dict)):
            for k in node.value.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
    return out


def _names_in_returns(tree) -> set:
    """收集所有 return 语句里出现的变量名与 f-string 插值（用于「码不回显」判定）。"""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
    return out


# ============================================================
# A. 纯计算层
# ============================================================
import password_reset as pr  # noqa: E402

print("=" * 60)
print("A. 计算层：码生成 / 归一化 / 哈希 / 掩码 / 链接")
print("=" * 60)

ck(len(set(pr.CODE_ALPHABET)) == 32 and len(pr.CODE_ALPHABET) == 32,
   f"码字符集恰好 32 个且无重复（实测 {len(pr.CODE_ALPHABET)}）")
ck(not (set(pr.CODE_ALPHABET) & set("IO01")),
   "字符集不含易抄错的 I / O / 0 / 1（这四个是抄码失败的主要来源）")
_space = len(pr.CODE_ALPHABET) ** pr.CODE_LENGTH
ck(_space >= 10 ** 12,
   f"码空间 ≥ 1e12（实测 32^{pr.CODE_LENGTH} = {_space:.3e}）——6 位纯数字只有 1e6，"
   "配可离线爆破的哈希等于纸糊")

_codes = [pr.generate_code() for _ in range(2000)]
ck(all(len(c) == pr.CODE_LENGTH for c in _codes),
   f"连续生成 2000 枚，长度恒为 {pr.CODE_LENGTH}")
ck(all(set(c) <= set(pr.CODE_ALPHABET) for c in _codes),
   "连续生成 2000 枚，字符全部落在字符集内")
ck(len(set(_codes)) == 2000,
   "连续生成 2000 枚**互不相同**（若 generate_code 退化成常量或弱随机，这里立刻变红）")

ck(pr.normalize_code("a7k2 m9pq-3x") == "A7K2M9PQ3X",
   "归一化：转大写 + 去空格/连字符")
ck(pr.normalize_code("  ") == "" and pr.normalize_code(None) == "",
   "归一化：空白/None 归一为空串（上层据此判「没输入」）")
ck(pr.normalize_code("A7K2-M9PQ-3X") == pr.normalize_code("a7k2m9pq3x"),
   "归一化对格式差异不敏感（同一枚码的两种写法结果相同）")

_s1, _s2 = pr.new_salt(), pr.new_salt()
ck(_s1 != _s2 and len(_s1) == 32,
   "盐每次不同且为 32 位 hex（16 字节）")
_code_t = "A7K2M9PQ3X"
ck(pr.hash_code(_code_t, _s1) == pr.hash_code(_code_t, _s1),
   "同码同盐 → 哈希稳定")
ck(pr.hash_code(_code_t, _s1) != pr.hash_code(_code_t, _s2),
   "同码不同盐 → 哈希不同（盐确实生效，彩虹表无效）")
ck(_code_t not in pr.hash_code(_code_t, _s1)
   and _code_t.lower() not in pr.hash_code(_code_t, _s1),
   "哈希结果里不含明文码（不是「拼一下就存」）")
ck(pr.verify_code(_code_t, _s1, pr.hash_code(_code_t, _s1)) is True,
   "verify_code：正确码通过")
ck(pr.verify_code("BBBBBBBBBB", _s1, pr.hash_code(_code_t, _s1)) is False,
   "verify_code：错误码拒绝")
ck(pr.verify_code(_code_t, "", "") is False,
   "verify_code：盐/哈希缺失时**拒绝**（fail closed，不因空值放行）")
ck(pr.verify_code("a7k2-m9pq-3x", _s1, pr.hash_code(_code_t, _s1)) is True,
   "verify_code：用户带空格/连字符/小写输入也能通过（归一化在两侧一致）")

ck(pr.masked_email("zhangsan@qq.com").startswith("zh")
   and "zhangsan" not in pr.masked_email("zhangsan@qq.com")
   and pr.masked_email("zhangsan@qq.com").endswith("@qq.com"),
   f"掩码邮箱不泄露完整用户名（实测 {pr.masked_email('zhangsan@qq.com')}）")
ck(pr.masked_email("a@b.com") == "a***@b.com",
   "掩码：单字符用户名也保留域名")
ck(pr.masked_email("") == "" and pr.masked_email("nope") == "n***",
   "掩码：空串/无 @ 输入不抛错")

_old_site = os.environ.pop(pr.SITE_URL_ENV, None)
try:
    ck(pr.site_url() == "" and pr.reset_link("ABCD") == "",
       "未配 SITE_URL 时：不给链接（绝不回落硬编码域名——站点改名后链接会静默指向不存在地址）")
    os.environ[pr.SITE_URL_ENV] = "chilam.club/"
    ck(pr.site_url() == "https://chilam.club",
       "只填域名时自动补 https（否则邮件里会变成相对路径）")
    _link = pr.reset_link("A7K2M9PQ3X")
    ck(_link == "https://chilam.club/auth?reset=A7K2M9PQ3X",
       f"重置链接拼装正确（实测 {_link}）")
    ck("@" not in _link,
       "**链接里刻意不带邮箱**：码 + 邮箱双重匹配才生效，码被旁路看到也不足以夺号")
    ck(_link.split("://", 1)[1].split("?")[0] == "chilam.club/auth",
       "重置链接指向 `/auth` 这一页（pages/auth.py 的公开路径）——"
       "**不是** `/dashboard`：拼错页面时邮件已经发出去了，用户点开看到的是会员中心")
    ck(_link.count("?") == 1 and _link.split("?")[1] == "reset=A7K2M9PQ3X",
       "链接只带一个 reset 参数（多余参数会被 Streamlit 吞掉/露出内部状态）")
finally:
    if _old_site is None:
        os.environ.pop(pr.SITE_URL_ENV, None)
    else:
        os.environ[pr.SITE_URL_ENV] = _old_site

ck(pr.CODE_TTL_MINUTES <= 30,
   f"码有效期 ≤ 30 分钟（实测 {pr.CODE_TTL_MINUTES} 分钟）")
ck(pr.MAX_VERIFY_ATTEMPTS >= 3 and pr.MAX_VERIFY_ATTEMPTS <= 10,
   f"试错上限在合理区间（实测 {pr.MAX_VERIFY_ATTEMPTS} 次）——只有有效期没有次数上限，"
   "等于给暴力猜留整段窗口")


# ============================================================
# B. 编排层（副作用全部换成计数器）
# ============================================================
import auth          # noqa: E402
import mailer        # noqa: E402


class FakeDB:
    """假数据层：只记录「被调了几次、参数是什么」，不触网。"""

    def __init__(self, *, user=None, recent_min=0, recent_hour=0, wx_uid=None,
                 code_row=None, password_ok=True):
        self.user = user
        self.recent_min = recent_min
        self.recent_hour = recent_hour
        self.wx_uid = wx_uid
        self.code_row = code_row
        self.password_ok = password_ok
        self.calls: dict[str, list] = {}

    def _log(self, name, *a, **k):
        self.calls.setdefault(name, []).append((a, k))

    def n(self, name) -> int:
        return len(self.calls.get(name, []))

    def last(self, name):
        c = self.calls.get(name) or []
        return c[-1] if c else None

    def args(self, name) -> tuple:
        """最近一次调用的**位置参数**。

        单独开一个方法而不是在断言里写 `last(...)[0][2]`：那种下标串极难读，
        而且很容易把 kwargs（索引 1）当成 args —— 本探针第一版就是这么写错的，
        报错是 KeyError: 2，看上去像数据问题、其实是自己的取值口写反了。
        """
        rec = self.last(name)
        return rec[0] if rec else ()

    # --- 被 auth 调用的接口 ---
    def get_user_by_email(self, email):
        self._log("get_user_by_email", email)
        return self.user

    def count_reset_requests(self, uid, minutes):
        self._log("count_reset_requests", uid, minutes)
        return self.recent_min if minutes <= 1 else self.recent_hour

    def get_user_wxpusher_uid(self, uid):
        self._log("get_user_wxpusher_uid", uid)
        return self.wx_uid

    def invalidate_reset_codes(self, uid):
        self._log("invalidate_reset_codes", uid)
        return True

    def create_password_reset_code(self, uid, channel, salt, chash, ttl, note=""):
        self._log("create_password_reset_code", uid, channel, salt, chash, ttl, note)
        return {"id": 7, "user_id": uid}, None

    def update_reset_code_delivery(self, cid, delivered, note=""):
        self._log("update_reset_code_delivery", cid, delivered, note)
        return True

    def create_password_reset_request(self, uid, note=""):
        self._log("create_password_reset_request", uid, note)
        return {"id": 1}, None

    def get_active_reset_code(self, uid):
        self._log("get_active_reset_code", uid)
        return self.code_row

    def bump_reset_attempts(self, cid, n):
        self._log("bump_reset_attempts", cid, n)
        return True

    def consume_reset_code(self, cid):
        self._log("consume_reset_code", cid)
        return True

    def update_user_password(self, uid, pw):
        self._log("update_user_password", uid, pw)
        return (True, None) if self.password_ok else (False, {"message": "fake"})

    def verify_password(self, row, pw):
        self._log("verify_password", pw)
        return pw == (row or {}).get("_pw") or pw == "correct-pw"


@contextlib.contextmanager
def harness(db: FakeDB, *, mail_ready=False, wx_ok=True, mail_ok=True, notify_ok=True,
            current_user=None):
    """把 auth 的协作者全部换成受控替身，并记录投递次数。"""
    d = {"wx": [], "mail": []}
    _old_admin = sys.modules.get("admin_notify")

    class _FakeAdmin:
        @staticmethod
        def notify_admins(title, body):
            return (notify_ok, "ok" if notify_ok else "fake-fail")

    _saved = (auth.database, mailer.is_configured, pr.deliver_wxpusher,
              pr.deliver_email, auth.get_current_user)

    def _wx(uid, code, minutes=pr.CODE_TTL_MINUTES):
        d["wx"].append({"uid": uid, "code": code})
        return wx_ok, ("ok" if wx_ok else "fake-push-fail")

    def _mail(email, code, minutes=pr.CODE_TTL_MINUTES):
        d["mail"].append({"email": email, "code": code})
        return mail_ok, ("ok" if mail_ok else "fake-smtp-fail")

    auth.database = db
    mailer.is_configured = lambda: mail_ready
    pr.deliver_wxpusher = _wx
    pr.deliver_email = _mail
    auth.get_current_user = lambda: current_user
    sys.modules["admin_notify"] = _FakeAdmin
    try:
        yield d
    finally:
        (auth.database, mailer.is_configured, pr.deliver_wxpusher,
         pr.deliver_email, auth.get_current_user) = _saved
        if _old_admin is not None:
            sys.modules["admin_notify"] = _old_admin
        else:
            sys.modules.pop("admin_notify", None)


_USER = {"id": 5, "email": "u@example.com", "_pw": "correct-pw"}
print()
print("=" * 60)
print("B. 编排层：限流 / 通道降级 / 单次消费 / 试错上限 / 过期")
print("=" * 60)

# ---- B1. 未注册邮箱：不该发码、不该建申请 ----
with harness(FakeDB(user=None)) as d:
    st_, msg = auth.request_password_reset("nobody@example.com")
ck(st_ == "failed" and "尚未注册" in msg,
   "未注册邮箱 → 明确告知未注册（与登录/注册口径一致，不让打错邮箱的人干等）")
ck(d["wx"] == [] and d["mail"] == [],
   "【副作用归零】未注册邮箱时未投递任何通道（0 次）")

# ---- B2. 限流：近 1 分钟已请求过 ----
_db = FakeDB(user=_USER, recent_min=1, wx_uid="UID_x")
with harness(_db) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "failed" and "频繁" in msg,
   f"冷却期内再次请求被拒（实测文案：{msg[:24]}…）")
ck(_db.n("create_password_reset_code") == 0,
   "【副作用归零】被限流时**没有生成任何码**（0 次）")
ck(d["wx"] == [] and d["mail"] == [],
   "【副作用归零】被限流时未投递（0 次）")

# ---- B3. 小时上限 ----
_db = FakeDB(user=_USER, recent_hour=pr.MAX_REQUESTS_PER_HOUR, wx_uid="UID_x")
with harness(_db) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "failed" and "上限" in msg,
   "小时请求数达上限被拒")
ck(_db.n("create_password_reset_code") == 0 and d["wx"] == [],
   "【副作用归零】小时上限拦截时未生成码、未投递")

# ---- B4. 限流取数失败（-1）：必须放行且如实说明限流未生效 ----
_db = FakeDB(user=_USER, recent_min=-1, recent_hour=-1, wx_uid="UID_x")
with harness(_db) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "sent",
   "限流取数失败时**放行**（云端抖一下不该把所有人的密码找回锁死）")
ck("频率限制" in msg and "未生效" in msg,
   "且如实告知「频率限制本次未生效」（不假装限流正常）")
ck(len(d["wx"]) == 1, "该路径确实投递了 1 次（放行是真的放行）")

# ---- B5. 单通道：已绑微信 ----
_db = FakeDB(user=_USER, wx_uid="UID_abc")
with harness(_db, mail_ready=True) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "sent" and len(d["wx"]) == 1,
   "已绑微信 → 走微信推送（1 次）")
ck(len(d["mail"]) == 0,
   "【副作用归零】微信成功时**不重复发邮件**（0 次）——同一件事推两遍会让人退订")
_sent_code = d["wx"][0]["code"]
_stored = _db.args("create_password_reset_code")
_plain_stored = (str(_stored[2]) + str(_stored[3])).encode()
ck(_stored[1] == "wxpusher",
   f"落库记录 channel 记为 wxpusher（实测 {_stored[1]}）")
ck(_sent_code.encode() not in _plain_stored,
   "【明文不落库】库里存的 salt/hash 中**不含明文码**")
ck(pr.verify_code(_sent_code, _stored[2], _stored[3]) is True,
   "库里存的 salt/hash 与投递出去的码**可互相验证**（不是存了个无关串）")
_note = _db.args("update_reset_code_delivery")[2]
ck(_sent_code not in str(_note),
   "【明文不落库】投递回写的 note 里也不含明文码")

# ---- B6. 微信失败 → 降级走邮件 ----
_db = FakeDB(user=_USER, wx_uid="UID_abc", recent_min=0)
with harness(_db, mail_ready=True, wx_ok=False, mail_ok=True) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "sent" and len(d["wx"]) == 1 and len(d["mail"]) == 1,
   "微信投递失败 → 自动降级到邮件（各尝试 1 次）")
ck("example.com" in msg,
   "成功文案指明送达位置（此处为掩码邮箱）")

# ---- B7. 两条通道都失败 → 转人工，且**不谎报已发送** ----
_db = FakeDB(user=_USER, wx_uid="UID_abc")
with harness(_db, mail_ready=True, wx_ok=False, mail_ok=False, notify_ok=True) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "manual" and "未能送达" in msg,
   "两条通道都失败 → 状态为 manual 且文案明说「未能送达」"
   "（关键字取代码里真实存在的措辞；写「未送达」会因「未能送达」不含该子串而恒假）")
ck(_db.n("create_password_reset_request") == 1,
   "人工申请已落库（1 次）——只推告警不落库，申请会随推送丢失而凭空消失")
ck("sent" not in st_,
   "绝不把「发不出去」记成已发送")

# ---- B8. 无任何自助通道 → 直接转人工，不生成码 ----
_db = FakeDB(user=_USER, wx_uid=None)
with harness(_db, mail_ready=False) as d:
    st_, msg = auth.request_password_reset("u@example.com")
ck(st_ == "manual",
   "无微信绑定且未配 SMTP → 直接转人工")
ck(_db.n("create_password_reset_code") == 0 and d["wx"] == [] and d["mail"] == [],
   "【副作用归零】无可用通道时不生成码、不尝试投递")

# ---- B9. 人工申请：通知失败必须让用户自己去找管理员 ----
_db = FakeDB(user=_USER)
with harness(_db, notify_ok=False) as d:
    st_, msg = auth.request_manual_reset("u@example.com")
ck(st_ == "manual" and "失败" in msg and "主动" in msg,
   "告警未送达时，文案要求用户**主动联系管理员**（不能让他安心等一个没人知道的申请）")
with harness(FakeDB(user=_USER), notify_ok=True) as d2:
    st2, msg2 = auth.request_manual_reset("u@example.com")
ck(st2 == "manual" and "已通知到位" in msg2 and "失败" not in msg2,
   "告警送达时文案说「已通知到位」——两条分支的文案互斥，能真的分辨")

# ---- B10. 用码重置：成功路径 ----
_salt = pr.new_salt()
_good = "A7K2M9PQ3X"
_row = {"id": 7, "code_salt": _salt, "code_hash": pr.hash_code(_good, _salt),
        "expires_at": "2099-01-01T00:00:00+00:00", "attempts": 0}
_db = FakeDB(user=_USER, code_row=dict(_row))
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass1")
ck(ok and "重置成功" in msg, "正确码 + 合规新密码 → 重置成功")
ck(_db.n("update_user_password") == 1 and _db.args("update_user_password")[1] == "newpass1",
   "新密码真的写下去了（1 次，且值正确）")
ck(_db.n("consume_reset_code") == 1,
   "码被消费（单次使用：1 次）")
ck(_db.n("invalidate_reset_codes") >= 1,
   "重置成功后作废该账号其余未消费码（否则「已改密码」与「旧码仍有效」同时成立）")

# ---- B11. 码错：绝不能顺手改密码 ----
_db = FakeDB(user=_USER, code_row=dict(_row))
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", "WRONGWRONG", "newpass1", "newpass1")
ck(not ok and "不正确" in msg and "还可尝试" in msg,
   f"错误码被拒且告知剩余次数（实测：{msg}）")
ck(_db.n("update_user_password") == 0,
   "【副作用归零】码错时**没有改密码**（0 次）——这是整套功能最关键的一条")
ck(_db.n("bump_reset_attempts") == 1 and _db.args("bump_reset_attempts")[1] == 1,
   "试错计数 +1（记为 1）")
ck(_db.n("consume_reset_code") == 0,
   "尚有剩余次数时不作废该码（作废了用户就得重新申请一次）")

# ---- B12. 用掉最后一次机会 → 就地作废 ----
_db = FakeDB(user=_USER, code_row={**_row, "attempts": pr.MAX_VERIFY_ATTEMPTS - 1})
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", "WRONGWRONG", "newpass1", "newpass1")
ck(not ok and "已作废" in msg,
   "最后一次机会用错 → 明确告知已作废")
ck(_db.n("update_user_password") == 0,
   "【副作用归零】超限路径也没有改密码（0 次）")
ck(_db.n("consume_reset_code") == 1,
   "超限时就地作废该码（1 次）")

# ---- B13. 次数已耗尽 ----
_db = FakeDB(user=_USER, code_row={**_row, "attempts": pr.MAX_VERIFY_ATTEMPTS})
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass1")
ck(not ok and "上限" in msg,
   "已达试错上限时，即便**码是对的**也拒绝（先拦后判，避免继续消耗）")
ck(_db.n("update_user_password") == 0 and _db.n("consume_reset_code") == 1,
   "【副作用归零】该路径不改密码（0 次），并作废该码（1 次）")

# ---- B14. 过期：fail closed ----
_db = FakeDB(user=_USER, code_row={**_row, "expires_at": "2000-01-01T00:00:00+00:00"})
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass1")
ck(not ok and "过期" in msg,
   "过期码被拒（即便码内容完全正确）")
ck(_db.n("update_user_password") == 0 and _db.n("consume_reset_code") == 1,
   "【副作用归零】过期路径不改密码、且作废该码")

_db = FakeDB(user=_USER, code_row={**_row, "expires_at": "这不是时间"})
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass1")
ck(not ok and _db.n("update_user_password") == 0,
   "过期字段解析失败按**已过期**处理（fail closed：宁可让人重取，也不放行状态未知的码）")

# ---- B15. 无可用的码 ----
_db = FakeDB(user=_USER, code_row=None)
with harness(_db):
    ok, msg = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass1")
ck(not ok and "没有可用" in msg and _db.n("update_user_password") == 0,
   "无待用码 → 拒绝且不改密码（0 次）")

# ---- B16. 新密码本身就是不合规的：应在碰库前就拦下 ----
_db = FakeDB(user=_USER, code_row=dict(_row))
with harness(_db):
    ok1, _ = auth.reset_password_with_code("u@example.com", _good, "123", "123")
    ok2, _ = auth.reset_password_with_code("u@example.com", _good, "newpass1", "newpass2")
ck(not ok1 and not ok2, "新密码过短 / 两次不一致 → 均拒绝")
ck(_db.n("get_active_reset_code") == 0,
   "【副作用归零】口令不合规时**连码都没去查**（0 次）——校验顺序：先校输入，再碰数据")
ck(_db.n("update_user_password") == 0, "【副作用归零】不改密码（0 次）")

# ---- B17. 修改密码（已登录路径） ----
_db = FakeDB(user={**_USER, "_pw": "correct-pw"})
with harness(_db, current_user={"user_id": 5, "email": "u@example.com"}):
    ok, msg = auth.change_password("correct-pw", "brandnew1", "brandnew1")
ck(ok and _db.n("update_user_password") == 1,
   "当前密码正确 → 改密成功（写库 1 次）")
ck(_db.n("invalidate_reset_codes") >= 1,
   "改密成功后同样作废未消费的重置码")
ck("本机当前会话保持有效" in msg,
   "文案如实说明本机会话保持（不假装把其他设备也踢下线——JWT 无状态做不到）")

_db = FakeDB(user={**_USER, "_pw": "correct-pw"})
with harness(_db, current_user={"user_id": 5, "email": "u@example.com"}):
    ok, msg = auth.change_password("WRONG-pw", "brandnew1", "brandnew1")
ck(not ok and "当前密码不正确" in msg,
   "当前密码错误 → 拒绝")
ck(_db.n("update_user_password") == 0,
   "【副作用归零】当前密码错时**不改密码**（0 次）——token 有效不代表有资格夺号")

_db = FakeDB(user={**_USER, "_pw": "correct-pw"})
with harness(_db, current_user={"user_id": 5, "email": "u@example.com"}):
    ok, msg = auth.change_password("correct-pw", "correct-pw", "correct-pw")
ck(not ok and "不能与当前密码相同" in msg,
   "新密码与当前密码相同 → 拒绝（否则「改了密码」是个假动作）")
ck(_db.n("update_user_password") == 0, "【副作用归零】该路径不改密码（0 次）")

_db = FakeDB(user=_USER)
with harness(_db, current_user=None):
    ok, msg = auth.change_password("correct-pw", "brandnew1", "brandnew1")
ck(not ok and "登录" in msg and _db.n("update_user_password") == 0,
   "未登录 → 拒绝且不写库（0 次）")

# ---- B18. 管理员发码 ----
_db = FakeDB(user=_USER)
with harness(_db):
    ok, code, note = auth.admin_issue_reset_code("u@example.com")
ck(ok and len(code) == pr.CODE_LENGTH and set(code) <= set(pr.CODE_ALPHABET),
   "管理员发码：返回一枚合法码")
_stored = _db.args("create_password_reset_code")
ck(code.encode() not in (str(_stored[2]) + str(_stored[3])).encode(),
   "【明文不落库】管理员发的码同样只存盐+哈希")
ck(pr.verify_code(code, _stored[2], _stored[3]) is True,
   "管理员发的码与落库哈希可互相验证")
ck(_stored[1] == "admin",
   "通道记为 admin（与自助通道可区分，便于事后审计）")

_db = FakeDB(user=None)
with harness(_db):
    ok, code, note = auth.admin_issue_reset_code("nobody@example.com")
ck(not ok and code == "" and _db.n("create_password_reset_code") == 0,
   "【副作用归零】管理员为不存在账号发码 → 拒绝且不写库（0 次）")


# ============================================================
# C. 接线与契约（源码 AST / SQL 文本）
# ============================================================
print()
print("=" * 60)
print("C. 接线与契约：不落库 / 不回显 / 唯一真源 / 页面调用点")
print("=" * 60)

# ---- C1. SQL：表结构不含明文码列 ----
_sql = _read("init_password_reset.sql")
_blk = _sql.split("CREATE TABLE IF NOT EXISTS public.password_reset_codes (")[1].split(");")[0]
_cols = [l.strip().split()[0] for l in _blk.split("\n")
         if l.strip() and not l.strip().startswith("--")]
ck({"code_salt", "code_hash"} <= set(_cols),
   f"SQL：重置码表有 code_salt / code_hash（实测列 {_cols}）")
ck("code" not in _cols and "plain_code" not in _cols,
   "SQL：**没有明文码列**（库里能直接读出可用的重置码 = 拖库即可夺号）")
ck("consumed_at" in _cols and "attempts" in _cols and "expires_at" in _cols,
   "SQL：单次使用(consumed_at) / 试错上限(attempts) / 有效期(expires_at) 三件套齐备")
ck("password_reset_requests" in _sql,
   "SQL：人工申请单独有表（推送可能丢，必须落库可查）")
ck(_sql.count("ENABLE ROW LEVEL SECURITY") == 2 and "CREATE POLICY" not in _sql,
   "SQL：两表启用 RLS 且**不给任何策略**（本站走 sb_secret_*≈service_role 绕过 RLS，"
   "非 service_role 一律读不到重置码；这是凭据表的刻意取舍，改前先读文件里的说明）")

# ---- C2. 写库层不带明文码 ----
_dai = _fn_ast("database.py", "create_password_reset_code")
# 只看真正发给 PostgREST 的那份载荷（`data = {...}`），不要扫函数里所有字符串常量：
# 错误协议里本来就有 {"code": "UNKNOWN"} 这种键，按全量扫会把**合法的错误码键**
# 误判成「写了明文字段」——这是断言范围过宽造成的假阳性。
_keys = _dict_keys(_dai, "data")
ck("code_salt" in _keys and "code_hash" in _keys,
   f"database.create_password_reset_code：写库载荷含 code_salt / code_hash（实测键 {sorted(_keys)}）")
ck("code" not in _keys and "plain_code" not in _keys,
   "database.create_password_reset_code：写库载荷**不含**明文码字段（明文不落库）")
ck(bool(_keys),
   "【元断言】确实取到了写库载荷字典（否则上面两条是在对一个空集合做判断，恒真）")

# ---- C3. auth：码不进任何用户可见文案 ----
for _fn in ("request_password_reset", "reset_password_with_code"):
    _t = _fn_ast("auth.py", _fn)
    _ret_names = _names_in_returns(_t)
    ck("code" not in _ret_names,
       f"auth.{_fn}：返回值里不出现码变量（提示文案不回显码）——"
       "回显一次就等于把凭据写进页面/日志/截图")
_t_admin = _fn_ast("auth.py", "admin_issue_reset_code")
ck("code" in _names_in_returns(_t_admin),
   "auth.admin_issue_reset_code：**唯一**允许回传明文码的函数（设计如此，由管理员线下转达）")

# ---- C4. auth：两条路径都必须作废旧码 ----
for _fn in ("change_password", "reset_password_with_code"):
    _t = _fn_ast("auth.py", _fn)
    ck(_calls_in(_t, ("database", "invalidate_reset_codes")) >= 1,
       f"auth.{_fn}：改密后作废该账号未消费的重置码（AST 锚调用点）")

# ---- C5. SMTP 唯一真源 ----
_t = _fn_ast("daily_digest.py", "_smtp_conf")
ck(_calls_in(_t, ("mailer", "smtp_conf")) >= 1,
   "daily_digest._smtp_conf 委托给 mailer.smtp_conf（一份 SMTP 规则、一处实现）")
_body_txt = ast.unparse(_t)
ck("DIGEST_SMTP_" not in _body_txt,
   "daily_digest._smtp_conf 内不再自己拼 SMTP 环境变量名（否则两处规则必漂移："
   "一边改端口、另一边继续用旧规则，而两边症状都是「邮件没收到」）")
_t = _fn_ast("pages/dashboard.py", "_bridge_secrets_to_env")
ck(_calls_in(_t, ("auth", "bridge_channel_secrets")) >= 1,
   "dashboard._bridge_secrets_to_env 收口到 auth.bridge_channel_secrets（薄壳，不重复实现）")
ck("st.secrets.get" not in ast.unparse(_t),
   "dashboard 不再自己拼 st.secrets 读取（重复实现已删除）")

# ---- C6. 通道凭据清单覆盖 SMTP 五个键 ----
_need = {"DIGEST_SMTP_HOST", "DIGEST_SMTP_PORT", "DIGEST_SMTP_USER",
         "DIGEST_SMTP_PASS", "DIGEST_SMTP_FROM", "WXPUSHER_APP_TOKEN"}
_missing_keys = _need - set(auth._CHANNEL_KEYS)
ck(not _missing_keys,
   f"auth._CHANNEL_KEYS 覆盖全部通道凭据（缺：{sorted(_missing_keys) or '无'}）——"
   "漏一个就会出现「明明配了却提示没配」，而原因是桥没搭上")

# ---- C7. 页面调用点（AST 锚调用点，不看字面量出现） ----
_ap = ast.parse(_read("pages/auth.py"))
for _chain, _desc in ((("auth", "request_password_reset"), "发起找回"),
                      (("auth", "reset_password_with_code"), "用码重置"),
                      (("auth", "request_manual_reset"), "人工兜底"),
                      (("auth", "bridge_channel_secrets"), "凭据桥接")):
    ck(_calls_in(_ap, _chain) >= 1,
       f"pages/auth.py 调用了 {_chain[1]}（{_desc}）")
c_dash = ast.parse(_read("pages/dashboard.py"))
ck(_calls_in(c_dash, ("auth", "change_password")) >= 1,
   "pages/dashboard.py 调用了 auth.change_password（已登录改密入口）")
c_adm = ast.parse(_read("pages/admin.py"))
ck(_calls_in(c_adm, ("auth", "admin_issue_reset_code")) >= 1,
   "pages/admin.py 调用了 auth.admin_issue_reset_code（人工重置入口）")
ck(_calls_in(c_adm, ("database", "mark_reset_request_handled")) >= 1,
   "pages/admin.py 能把申请标记为已处理（否则待办列表永远清不掉）")

# ---- C8. 忘记密码必须可被「以代码切换」 ----
_apsrc = _read("pages/auth.py")
_tabs_calls = _calls_in(_ap, ("st", "tabs"))
ck(_tabs_calls == 0,
   "pages/auth.py 的**代码区**不调用 st.tabs（AST 判调用点）——tabs 没有「选中第 N 个」"
   "的能力，而「登录失败直达忘记密码」「?reset= 链接落到重置表单」都要求可编程切换。"
   "注：模块 docstring 里解释这段设计时会提到 st.tabs 字样，用子串判定会被它骗过")
ck('key="auth_mode"' in _apsrc and "st.radio" in _apsrc,
   "pages/auth.py 用带 key 的 radio 当面板选择器（靠 session_state 编程切换）")
ck(_calls_in(_ap, ("st", "query_params", "get")) >= 1
   and "reset" in ast.unparse(_ap),
   "pages/auth.py 读取 ?reset= 链接参数以预填码（AST 锚 st.query_params.get 调用点；"
   "原先写成 replace 后再查 not in 的同义反复，那条恒假）")
ck('del st.query_params["reset"]' in _apsrc,
   "预填后**立刻从地址栏抹掉码**（截图/转发/共用电脑时不该把码带出去）")
# 必须**从 apply 块的起点往后找**：同样三行在上面的「发送重置码」块里也出现过，
# 用全局 find 会取到首次匹配（偏移更小），顺序断言于是恒假 —— 本探针第一版就是这么错的。
_apply_at = _apsrc.find("if apply_reset:")
if _apply_at < 0:
    uv("pages/auth.py 找不到 apply_reset 分支，顺序断言没验到")
    _i_clear = _i_mode = _i_flash = -1
else:
    _i_clear = _apsrc.find('st.session_state["_clear_reset_code"] = True', _apply_at)
    _i_mode = _apsrc.find('st.session_state["auth_mode"] = MODE_LOGIN', _apply_at)
    _i_flash = _apsrc.find('st.session_state["auth_flash"] = ("ok", msg)', _apply_at)
ck(0 < _apply_at < _i_clear < _i_mode < _i_flash,
   "重置成功后的顺序是「登记清空码 → 切面板 → 写 flash」（widget 创建后再改自己的 "
   "session_state 会抛错，且提示必须在 rerun 前落到 flash 才看得见）")
ck('st.session_state.pop("auth_flash"' in _apsrc,
   "页面有 flash 读取端（写入端才不是白写）")

# ---- C9. 登录失败要给出「下一步」 ----
ck("忘记密码" in _apsrc and "点上方" in _apsrc,
   "登录失败时提示可直接去忘记密码（不是甩一句「请重试」）")

# ---- C10. 文案口径必须与新事实一致（不能一边发邮件一边说没有） ----
# 这两条守的是「站内两处说法互相矛盾」：重置码已经会走注册邮箱，
# 首页/摘要页若还留「目前不提供邮件投递」的绝对说法，用户就会困惑到底有没有。
_ap_app = _read("app.py")
_pg_digest = _read("page_digest.py")
_stale = "目前不提供邮件投递"
ck(_stale not in _ap_app and _stale not in _pg_digest,
   f"首页与摘要页都不再有「{_stale}」这种绝对说法（与「重置码可发注册邮箱」不矛盾）")
ck("邮件" in _ap_app and "邮件" in _pg_digest,
   "两处都**如实提到**邮件通道的实际口径（是改写准，不是删掉不提）")
ck("微信" in _ap_app.split("摘要内容始终免费可看")[1][:400],
   "首页对摘要投递明说主通道是微信（口径与已跑通的通道一致）")

# ---- C12. 凭据桥：平铺键与子表两种写法都必须真能桥到环境变量 ----
# 静默失效的重灾区：_NESTED_KEYS 里写错键名 → 用户配了、页面说没配、零报错。
class _FakeSecrets(dict):
    """贴近真身的替身。

    真 `st.secrets` 是**基于属性、未命中即抛 KeyError** 的容器，不是宽容的 dict：
    查一个不存在的顶层键会 `KeyError`，而不是返回空串。`bridge_channel_secrets()`
    正是在这个 KeyError 被吃掉之后才走「子表」那条回退分支的。
    替身若用普通 dict 的宽容语义，那条分支就**永远走不到**，断言只在测替身本身
    （本探针首版就踩了这个：平铺过了、子表恒红，而生产逻辑两种都支持）。
    """

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            raise KeyError(key) from None

    def get(self, key, default=None):
        # 与真身一致：未命中抛错（上层用 try/except 兜住），而不是静默给默认值
        return self[key]

    def __contains__(self, key):
        return dict.__contains__(self, key)


_PAIRS = [("DIGEST_SMTP_HOST", "smtp.example.com", ("smtp", "host")),
          ("DIGEST_SMTP_PORT", "587", ("smtp", "port")),
          ("DIGEST_SMTP_USER", "bot@example.com", ("smtp", "user")),
          ("DIGEST_SMTP_PASS", "secret-auth-code", ("smtp", "pass")),
          ("DIGEST_SMTP_FROM", "noreply@example.com", ("smtp", "from")),
          ("WXPUSHER_APP_TOKEN", "AT_token_xxx", ("wxpusher", "app_token")),
          ("SITE_URL", "https://chilam.club", None)]

ck(set(auth._NESTED_KEYS) <= {k for k, _, _ in _PAIRS},
   "auth._NESTED_KEYS 的每个键都在本组用例里被真实验过一次"
   "（新增子表键却忘了加用例，这条会红）")

# ★ 注入方式：`auth.st` 是 **streamlit 模块对象**，不能整个替换成 dict ——
#   那样 `st.secrets.get` 会抛 AttributeError，而 auth.py 里「取不到就当空」的
#   try/except 会把它吞掉，断言就变成「什么都没桥到」的假警报（本探针首版即如此）。
#   正确做法是只替换那个模块对象的 `.secrets` 属性。
_st_mod = getattr(auth, "st", None)
_had_secrets = _st_mod is not None and hasattr(_st_mod, "secrets")
_old_secrets = getattr(_st_mod, "secrets", None) if _had_secrets else None


def _use_secrets(fake):
    if _st_mod is None:
        return False
    _st_mod.secrets = fake
    return True


_saved_env = {name: os.environ.pop(name, None) for name, _, _ in _PAIRS}
_orig_env_unset = {name: os.environ.pop(name, None) for name, _, _ in _PAIRS}
ck(_st_mod is not None,
   "本机装好了 streamlit（否则桥接函数直接 return，本组全部断言都测不到东西）")
try:
    # (1) 平铺写法
    _use_secrets(_FakeSecrets({name: val for name, val, _ in _PAIRS}))
    auth.bridge_channel_secrets()
    _flat_miss = [name for name, val, _ in _PAIRS if os.environ.get(name) != val]
    ck(not _flat_miss,
       f"平铺键写法能桥到环境变量（未生效：{_flat_miss}）——"
       "漏一个的表现就是「明明配了却提示未配置」")

    # (2) 子表写法：只在 st.secrets 里放嵌套表
    for name, _, _ in _PAIRS:
        os.environ.pop(name, None)
    _nested = {}
    for name, val, path in _PAIRS:
        if path:
            _nested.setdefault(path[0], {})[path[1]] = val
    _use_secrets(_FakeSecrets(_nested))
    auth.bridge_channel_secrets()
    _nest_miss = [name for name, val, path in _PAIRS if path and os.environ.get(name) != val]
    ck(not _nest_miss,
       f"子表写法（[smtp] host=…）也能桥到环境变量（未生效：{_nest_miss}）——"
       "有人习惯这么写，映射表名字对不上就是静默失效")

    # (3) 已有 env 不被覆盖（Actions 里环境变量才是唯一来源）
    os.environ["DIGEST_SMTP_HOST"] = "from-real-env.example.com"
    _use_secrets(_FakeSecrets({"DIGEST_SMTP_HOST": "from-secrets.example.com"}))
    auth.bridge_channel_secrets()
    ck(os.environ["DIGEST_SMTP_HOST"] == "from-real-env.example.com",
       "已存在的环境变量不被 Secrets 覆盖（否则 Actions 里的注入会被静默顶掉）")

    # (4) 全空时不得凭空造出变量（防「把空串也写进 env」导致 is_configured 误判）
    for name, _, _ in _PAIRS:
        os.environ.pop(name, None)
    _use_secrets(_FakeSecrets({}))
    auth.bridge_channel_secrets()
    _ghost = [name for name, _, _ in _PAIRS if name in os.environ]
    ck(not _ghost, f"空 Secrets 不写入任何环境变量（凭空造出：{_ghost}）")
finally:
    if _st_mod is not None:
        if _had_secrets:
            _st_mod.secrets = _old_secrets
        else:
            try:
                del _st_mod.secrets
            except Exception:
                pass
    for name, old in _saved_env.items():
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old

# ---- C11. pages 的导入引导必须「强制置顶」（2026-09-23 实测踩坑） ----
# Streamlit 每次跑脚本前会执行 modified_sys_path（exec_code.py:63），把
# **本脚本所在目录**插到 sys.path[0]，跑完再摘掉 —— 事后打印 sys.path 是干净的，
# 坑被完全掩盖。于是 pages/auth.py 里 `import auth` 解析回**本页自己**
# （与根目录 auth.py 词干撞名），报 RecursionError（实测 162 层）。
# 病根是「条件式插入」：`if _ROOT not in sys.path: insert` —— 根通常**已经**
# 在 sys.path 里（Streamlit 的 web/bootstrap.py 插过一次），条件不成立就不插，
# pages/ 仍排第一。规则必须是：无条件移除、再插到最前。
def _path_guard_shape(tree):
    """(是否强制置顶, 是否存在条件式插入)。"""
    forced = conditional = False
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            body = ast.unparse(node)
            if "sys.path" in ast.unparse(node.test) and "sys.path.remove" in body:
                forced = True
        elif isinstance(node, ast.If):
            body = ast.unparse(node)
            if "not in sys.path" in ast.unparse(node.test) and "sys.path.insert" in body:
                conditional = True
    return forced, conditional


def _bootstrap_block(src):
    """取「导入引导」注释块到 sys.path.insert(0, _ROOT) 的原文，用于横向比对。"""
    i = src.find("导入引导")
    j = src.find("sys.path.insert(0, _ROOT)", i)
    if i < 0 or j < 0:
        return ""
    return src[i:j + len("sys.path.insert(0, _ROOT)")]


_PAGE_FILES = ("pages/auth.py", "pages/admin.py", "pages/dashboard.py")
_blocks = {}
for _rel in _PAGE_FILES:
    _tree = ast.parse(_read(_rel))
    _forced, _cond = _path_guard_shape(_tree)
    ck(_forced, f"{_rel} 把项目根**强制**顶到 sys.path[0]（while 移除 + insert）")
    ck(not _cond,
       f"{_rel} 没有条件式 sys.path 插入（`if _ROOT not in sys.path` 不成立就不插，"
       "根通常已在 sys.path 里，pages/ 会继续排第一）")
    _blocks[_rel] = _bootstrap_block(_read(_rel))

_uniq = set(_blocks.values()) - {""}
ck(all(_blocks.values()),
   f"三个页面的引导块都能取到（空值说明有人改了措辞，本组断言需要同步更新）")
ck(len(_uniq) == 1 and bool(_uniq),
   f"三个页面的引导块**逐字一致**（防「一份规则三处实现」漂移）"
   f"；实测到了 {len(_uniq)} 种版本")

# 解析结果必须被显式校验：万一引导再被改坏，页面要当场报错，
# 而不是把整张登录页当成 auth 模块拿去用（那正是 RecursionError 的前身）。
ck(_calls_in(_ap, ("os", "path", "realpath")) >= 2
   and "RuntimeError" in ast.unparse(_ap),
   "pages/auth.py 在 import 之后校验 auth.__file__ 确实是根目录的 auth.py，"
   "不符就 raise（fail loud，不留静默错模块的余地）")

print()
print("-" * 60)
print(f"通过 {_pass} 项，失败 {_fail} 项" + (f"，未验证 {_unverified} 项" if _unverified else ""))
if _fail_msgs:
    print("失败明细：")
    for m in _fail_msgs:
        print(f"  · {m}")
sys.exit(1 if (_fail or _unverified) else 0)
