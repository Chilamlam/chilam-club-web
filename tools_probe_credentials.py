# -*- coding: utf-8 -*-
"""凭据卫生自检（public 仓库不许夹带可用密钥 / 明文口令 / 后门）

为什么必须有这个探针：这类缺陷**没有任何运行期症状**。
  · JWT 签名密钥硬编码在源码里 → 站点功能完全正常、日志毫无异常，
    但任何人都能离线自签 `{"is_admin":true}` 的 token 拿到后台管理权
  · 「特定邮箱 + 特定明文口令直接 return True」的兜底分支 → 平时永不执行
    （线上账号都是 pbkdf2），代码评审时看着像「依赖缺失容错」
  · secrets.toml 哪天被误 `git add` → 提交成功、CI 全绿、Supabase 全库可写
上面三条 2026-09-03 在本仓库**全部真实存在过**，且都是靠人肉翻代码发现的。
人肉不可复现，所以把判据写死在这里。

铁律遵循：
  · 唯一失败出口 ck()/bad()，探针自己不 print FAIL
  · 每条扫描类断言都有**元断言校准**：先拿「已知必然命中」的样本喂进扫描器，
    证明它真的会报，否则「零命中」永远为真 = 没有断言
  · MUTATIONS 逐条造错反验（含对本探针自身的判据造错）
  · 只输出掩码与指纹，**任何情况下不回显密钥明文 / 口令 / 完整邮箱**
"""
from __future__ import annotations

import ast
import hashlib
import json as _json0
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 子进程里屏蔽 streamlit 的代码片段。**用 sys.modules[...] = None**，
# 这是 import 系统的显式短路，版本无关。
# 不要用旧式 finder（find_module / load_module）：那套协议 3.12 已移除，
# 3.13 上 stub 静默失效，streamlit 照样能 import、st.secrets 照样能读，
# 于是"未配置密钥"的分支根本走不到 —— 屏蔽手段失效 = 断言在测别的东西。
_STUB_ST = (
    "import sys\n"
    "sys.modules['streamlit'] = None\n"
)

PASS: list[str] = []
FAIL: list[str] = []
# "没查"必须与"通过"分开记账。把未验证项混进 PASS 是假绿的典型做法。
UNVERIFIED: list[str] = []


_ASSERT_SEEN: set[str] = set()   # 重复 ID 会在下面当场炸掉，不让漂移悄悄发生


def ck(aid: str, cond: bool, msg: str) -> None:
    """记一条断言。

    `aid` 是**稳定 ID**（A01、A02…），反验脚本按它比对翻转。

    为什么不拿断言消息当键：消息里常嵌动态值（长度、来源、命中列表），
    造错后文案一变，归一化键就漂移，反验会读成「旧断言消失 + 新断言出现」，
    于是「造错没抓住」被误判成「断言不稳定」。ID 由人工写在调用点，
    不受分支是否执行、消息怎么拼的影响。
    """
    if aid in _ASSERT_SEEN:
        raise SystemExit(f"断言 ID 重复：{aid} —— 每个 ck() 必须用唯一 ID")
    _ASSERT_SEEN.add(aid)
    (PASS if cond else FAIL).append(f"{aid} {msg}")
    print(f"{'PASS' if cond else 'FAIL'} {aid} | {msg}")


def bad(msg: str) -> None:
    """唯一失败出口。探针内部任何异常都要走这里。

    异常路径无法预分配 ID（不知道会在哪一步炸），用调用点行号兜底。
    """
    import sys as _s
    ck(f"E{_s._getframe(1).f_lineno:04d}", False, msg)


def fp(s: str) -> str:
    """指纹：可用于比对，不可逆推。"""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def mask(s: str) -> str:
    if len(s) <= 8:
        return s[:2] + "*" * max(len(s) - 2, 0)
    return s[:4] + "*" * 8 + s[-2:]


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="ignore")


# ============================================================ 扫描器（被测判据）
# 这些函数就是"判据"本身。它们既用于扫真实代码，也用于被元断言校准 ——
# 校准是必须的：如果扫描器写错了（比如正则打错一个字符），
# "零命中"会永远成立，断言看着全绿但什么都没验。
#
# **一律走 AST，不用正则扫文本**。第一版用正则，立刻误报了 auth.py 注释里
# 那句"原实现写的是 environ.get(NAME, '<固定常量>')"的说明文字 ——
# 注释和文档字符串里正当地写满了反面示例，按文本扫必然把它们当成真代码。
# 这与 tools_probe_digest.py 踩过的"注释里提到 secret 名就算已点名"是同一个病：
# **判据的作用域必须是"会被执行的代码"，注释永远不是。**
# AST 里注释根本不存在，docstring 是孤立的 Constant 而非 Call/Compare，天然免疫。

_SECRETISH = re.compile(
    r"(SECRET|TOKEN|KEY|PASSWORD|PASSWD|PWD|SALT|CREDENTIAL)", re.I)
_PW_NAMES = ("password", "passwd", "pwd", "raw_password", "plain_password")


def _callee(node: ast.Call) -> str:
    """取被调用者的可读名：os.environ.get -> 'environ.get'，getenv -> 'getenv'。"""
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value
        if isinstance(base, ast.Attribute):
            return f"{base.attr}.{f.attr}"
        if isinstance(base, ast.Name):
            return f"{base.id}.{f.attr}"
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def scan_hardcoded_defaults(src: str) -> list[tuple[str, str]]:
    """返回 [(密钥名, 掩码后的默认值)]。

    只认「取密钥类环境变量时给了一个非空字符串默认值」。空字符串默认值是
    "未配置"的正常写法，不算。
    """
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if _callee(node) not in ("environ.get", "getenv", "os.getenv"):
            continue
        name, default = node.args[0], node.args[1]
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            continue
        if not _SECRETISH.search(name.value):
            continue
        if not (isinstance(default, ast.Constant) and isinstance(default.value, str)):
            continue          # 默认值是变量/表达式 → 不是写死的字面量
        if len(default.value) < 6:
            continue          # 空串或极短占位不算可用密钥
        out.append((name.value, mask(default.value)))
    return out


def scan_plaintext_pw(src: str) -> list[str]:
    """返回 ["行号: 描述"]。命中「口令变量 == 字符串字面量」（含反向写法）。"""
    hits = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(o, (ast.Eq, ast.NotEq)) for o in node.ops):
            continue
        sides = [node.left, *node.comparators]
        has_pw = any(isinstance(s, ast.Name) and s.id.lower() in _PW_NAMES
                     for s in sides)
        has_lit = any(isinstance(s, ast.Constant) and isinstance(s.value, str)
                      and len(s.value) >= 3 for s in sides)
        if has_pw and has_lit:
            hits.append(f"{node.lineno}: 口令变量与字符串字面量直接比较")
    return hits


# ---- 元断言：先证明扫描器真的会命中 ----
_FIX_DEFAULT = 'X = os.environ.get("APP_SECRET_KEY", "some-real-default")\n'
_FIX_DEFAULT_EMPTY = 'X = os.environ.get("APP_SECRET_KEY", "")\n'
_FIX_DEFAULT_VAR = 'X = os.environ.get("APP_SECRET_KEY", fallback)\n'
_FIX_DEFAULT_COMMENT = '# X = os.environ.get("APP_SECRET_KEY", "some-real-default")\n'
_FIX_DEFAULT_DOC = '"""说明：原来写的是 os.environ.get("APP_SECRET_KEY", "old-const")。"""\n'
_FIX_PW = 'def f():\n    if email == "a@b.c" and password == "hunter2":\n        return True\n'
_FIX_PW_REV = 'def f():\n    if "hunter2" == password:\n        return True\n'
_FIX_PW_COMMENT = '# if password == "hunter2": return True\n'
_FIX_PW_OK = 'def f():\n    return hmac.compare_digest(dk, target)\n'

ck("A01", scan_hardcoded_defaults(_FIX_DEFAULT) == [("APP_SECRET_KEY", mask("some-real-default"))],
   "【元断言】硬编码默认值扫描器确实能抓住 environ.get(NAME, '默认值')")
ck("A02", scan_hardcoded_defaults(_FIX_DEFAULT_EMPTY) == [],
   "【元断言】空字符串默认值不算硬编码密钥（那是'未配置'的正常写法）")
ck("A03", scan_hardcoded_defaults(_FIX_DEFAULT_VAR) == [],
   "【元断言】默认值是变量而非字面量时不算（真值来自别处，不在仓库里）")
ck("A04", scan_hardcoded_defaults(_FIX_DEFAULT_COMMENT) == [],
   "【元断言】注释里的反面示例不算 —— 注释不会被执行（正则扫文本必踩此坑）")
ck("A05", scan_hardcoded_defaults(_FIX_DEFAULT_DOC) == [],
   "【元断言】文档字符串里描述旧写法不算（本仓库的 auth.py 正是这么写注释的）")
ck("A06", len(scan_plaintext_pw(_FIX_PW)) == 1,
   "【元断言】明文口令扫描器确实能抓住 password == '字面量'")
ck("A07", len(scan_plaintext_pw(_FIX_PW_REV)) == 1,
   "【元断言】明文口令扫描器也抓反向写法 '字面量' == password")
ck("A08", scan_plaintext_pw(_FIX_PW_COMMENT) == [],
   "【元断言】注释里的明文口令不算（误报会训练人忽略自检，和漏报一样有害）")
ck("A09", scan_plaintext_pw(_FIX_PW_OK) == [],
   "【元断言】不把正确写法 hmac.compare_digest(...) 误判成明文比对")



# ============================================================ 1. 源码里不许有可用密钥
print()
print("-" * 72)
print("1. 源码：硬编码密钥默认值 / 明文口令比对")

# 扫全部业务代码（探针自己除外 —— 探针里必然有 fixture 字面量）。
_SCAN_FILES = sorted(
    f for f in os.listdir(ROOT)
    if f.endswith(".py") and not f.startswith("tools_probe_")
) + [os.path.join("pages", f) for f in sorted(os.listdir(os.path.join(ROOT, "pages")))
     if f.endswith(".py")]

_hard: list[str] = []
_plain: list[str] = []
for _rel in _SCAN_FILES:
    try:
        _src = read(_rel)
    except OSError as e:
        bad(f"读取 {_rel} 失败：{type(e).__name__}")
        continue
    for _name, _val in scan_hardcoded_defaults(_src):
        _hard.append(f"{_rel}: {_name} 默认值={_val}")
    for _h in scan_plaintext_pw(_src):
        _plain.append(f"{_rel}:{_h}")

ck("A10", len(_SCAN_FILES) >= 30,
   f"【元断言】确实扫到了业务代码（{len(_SCAN_FILES)} 个 .py，含 pages/），"
   "文件集为空时下面两条会恒过")
ck("A11", not _hard,
   "源码里不得有『密钥类环境变量的可用默认值』—— 公开仓库里的默认值等于公开的钥匙。"
   f"命中：{_hard}")
ck("A12", not _plain,
   "源码里不得有『口令 == 字面量』的明文比对 —— 它同时意味着口令被写进了仓库。"
   f"命中：{_plain}")


# ============================================================ 2. JWT 密钥的三条出口
print()
print("-" * 72)
print("2. auth.py：JWT 密钥来源必须是 env / secrets / 随机，绝不回落公开常量")

_auth = read("auth.py")
ck("A13", "_LEAKED_SECRET_FP" in _auth and re.search(r'_LEAKED_SECRET_FP\s*=\s*["\'][0-9a-f]{8,}["\']', _auth) is not None,
   "auth.py 保留已泄露旧密钥的**指纹**用于启动告警（指纹不可逆，写在公开仓库无损失）")
ck("A14", re.search(r"token_urlsafe|token_hex|urandom", _auth) is not None,
   "没配密钥时生成随机密钥（而不是回落到某个固定字符串）")

# 真正跑一遍三种来源，验行为而不是验文本。
def _fresh_auth(env_val: str | None) -> tuple[dict | None, str]:
    """在子进程里以指定 env 载入 auth，回收 (信息字典, 全部输出)。

    必须用子进程：`JWT_SECRET` 在 import 时求值一次，同进程内改 env 再 reload
    会被 sys.modules 缓存干扰，验不出真实启动行为。

    子进程里**必须真正屏蔽 streamlit**，否则 auth 会读到本机
    `.streamlit/secrets.toml`，"未配 env"这一分支根本走不到。
    第一版用的是旧式 finder（find_module / load_module）—— 那套协议
    Python 3.12 已移除，3.13 上 stub 完全不生效：实测 streamlit 1.62.0
    照样 import 成功、`st.secrets` 照样可读，于是"未配时用随机密钥"
    被测成了"读到 secrets 里的值"。**屏蔽手段自己失效了，断言就在测别的东西。**
    改用 `sys.modules["streamlit"] = None` —— 这是 import 系统的显式短路，
    版本无关，且下面 `_STUB_ST` 的自证会当场验它是否真的挡住了。
    """
    code = _STUB_ST + (
        "import auth, hashlib, json\n"
        "print('@@' + json.dumps({'src': auth.JWT_SECRET_SOURCE,\n"
        "      'fp': hashlib.sha256(auth.JWT_SECRET.encode()).hexdigest()[:12],\n"
        "      'n': len(auth.JWT_SECRET)}))\n"
    )
    env = dict(os.environ)
    env.pop("JWT_SECRET", None)
    if env_val is not None:
        env["JWT_SECRET"] = env_val
    try:
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=120)
    except subprocess.TimeoutExpired:
        return None, "子进程超时"
    import json as _json
    out = (p.stdout or "") + (p.stderr or "")
    line = [l for l in (p.stdout or "").splitlines() if l.startswith("@@")]
    if not line:
        return None, out
    return _json.loads(line[0][2:]), out


# ---- 元断言 0：先证明"屏蔽 streamlit"这件事本身有效 ----
# 这是本段所有断言的**前提**。屏蔽失效 → 子进程读到本机 secrets →
# "未配置"分支永远走不到，后面几条断言测的全是另一回事（且会假绿）。
_stub_probe = subprocess.run(
    [sys.executable, "-c", _STUB_ST +
     "import json\n"
     "try:\n"
     "    import streamlit\n"
     "    print('@@' + json.dumps({'blocked': False}))\n"
     "except ImportError:\n"
     "    print('@@' + json.dumps({'blocked': True}))\n"],
    cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    errors="ignore", timeout=120)
_stub_ln = [l for l in (_stub_probe.stdout or "").splitlines() if l.startswith("@@")]
ck("A15", bool(_stub_ln) and _json0.loads(_stub_ln[0][2:])["blocked"] is True,
   "【元断言】子进程里 streamlit 确实被屏蔽住了 —— 屏蔽失效时 auth 会读到本机 "
   "secrets.toml，'未配密钥'的分支根本走不到，下面几条断言会集体测错对象")

_LEAKED = "chilam_club_secret_key_2026_invest_secure"   # 已在 git 历史公开，无新增泄露
_leak_fp = fp(_LEAKED)

_a_none, _out_none = _fresh_auth(None)
_a_leak, _out_leak = _fresh_auth(_LEAKED)
_a_good, _out_good = _fresh_auth("Z" * 60)

if _a_none is None or _a_leak is None or _a_good is None:
    bad(f"载入 auth.py 失败，无法验证密钥来源。输出：{(_out_none + _out_leak + _out_good)[:400]}")
else:
    ck("A16", _a_none["src"] == "ephemeral-random" and _a_none["n"] >= 32,
       f"未配 JWT_SECRET 时用进程级随机密钥（来源={_a_none['src']} 长度={_a_none['n']}）")
    ck("A17", _a_none["fp"] != _leak_fp,
       "未配时**不会**回落到已泄露的旧常量（这正是原来的洞）")
    # 用集合判"三者互不相同"。原来写的是链式 `a != b != c`，它只比 (a,b) 与
    # (b,c)，**漏掉 (a,c)** —— 恰好 a、c 相等时（stub 失效时的真实情形）
    # 整个表达式仍为 True，元断言看着绿却什么都没校准。
    ck("A18", len({_a_none["fp"], _a_good["fp"], _leak_fp}) == 3,
       "【元断言】三种来源拿到的确实是三个不同密钥（若有重合则上面两条无意义）")
    ck("A19", "重新登录" in _out_none or "随机密钥" in _out_none,
       "未配密钥时**显式告警**（静默降级=运维永远不知道自己没配）")
    # 已泄露的旧常量：不只要告警，必须**拒绝使用**。
    # 只告警的话，日志里多一行、洞照样开着 —— 而本仓库 Secrets 里填的就是它。
    ck("A20", _a_leak["src"].startswith("ephemeral-random(rejected-leaked"),
       "**配了已泄露旧常量时必须拒用并降级为随机密钥**（只打印告警等于不修）")
    ck("A21", _a_leak["fp"] != _leak_fp,
       "拒用后实际生效的密钥确实不是那个公开常量（元断言：证明拒用不是空话）")
    ck("A22", "拒绝使用" in _out_leak or "旧常量" in _out_leak,
       "拒用时必须**明确告知运维**发生了什么、下一步要做什么")
    ck("A23", "旧常量" not in _out_good and "随机密钥" not in _out_good,
       "配了强随机密钥时不产生噪音告警（狼来了会让人忽略真告警）")

# 伪造 token：拿已公开的旧常量自签一个 is_admin=true，必须验不过。
# 这条是整个探针的核心 —— 它直接回答「外人能不能拿到后台权限」。
#
# 注意实验条件：这里**故意不给 env**，让 auth 走"配置里是旧常量 → 拒用 → 随机"
# 或"完全未配 → 随机"两条路之一；两种情况下伪造签名都必须验不过。
_forge_code = _STUB_ST + (
    "import base64, hmac, hashlib, json\n"
    "import auth\n"
    "def b64(x):\n"
    "    return base64.urlsafe_b64encode(x).decode().rstrip('=')\n"
    f"LEAK = {_LEAKED!r}\n"
    "h = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())\n"
    "pl = b64(json.dumps({'user_id': 1, 'email': 'x@y.z', 'is_admin': True,\n"
    "                     'exp': 9999999999}).encode())\n"
    "sig = b64(hmac.new(LEAK.encode(), f'{h}.{pl}'.encode(), hashlib.sha256).digest())\n"
    "ok_self = auth.decode_jwt_token(auth.create_jwt_token(1, 'a@b.c', True))\n"
    "print('@@' + json.dumps({'forged': auth.decode_jwt_token(f'{h}.{pl}.{sig}') is not None,\n"
    "                         'self_ok': bool(ok_self and ok_self.get('is_admin'))}))\n"
)
_env2 = dict(os.environ)
_env2.pop("JWT_SECRET", None)
try:
    _fp2 = subprocess.run([sys.executable, "-c", _forge_code], cwd=ROOT, env=_env2,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="ignore", timeout=120)
    _ln = [l for l in (_fp2.stdout or "").splitlines() if l.startswith("@@")]
    _r = _json0.loads(_ln[0][2:]) if _ln else None
    if _r is None:
        bad("伪造 token 实验没拿到结果（子进程无输出），"
            f"未能验证'外人能否自签管理员'。输出：{((_fp2.stdout or '') + (_fp2.stderr or ''))[:300]}")
except Exception as e:                                  # noqa: BLE001
    _r = None
    bad(f"伪造 token 实验执行失败：{type(e).__name__}: {e}")

if _r is not None:
    ck("A24", _r["self_ok"] is True,
       "【元断言】自签自验通路是通的（否则下一条'伪造被拒'可能只是因为解码整体坏了）")
    ck("A25", _r["forged"] is False,
       "用**已公开的旧常量**伪造的 is_admin token 必须被拒 —— "
       "这条挂了等于后台管理权对全网开放")

# 补一条**真实部署形态**下的伪造实验：不屏蔽 streamlit，让 auth 真的去读
# 本机 `.streamlit/secrets.toml`（这就是线上 Streamlit Cloud 的形态）。
# 上面那条在"屏蔽 streamlit"的环境里验，只覆盖了跑批/探针场景；
# 前端进程读的是 secrets，而 secrets 里填的恰恰是那个已公开的旧常量 ——
# **必须在带 secrets 的环境里再验一次，否则线上那条路完全没被测到。**
_forge_live = (
    "import base64, hmac, hashlib, json\n"
    "import auth\n"
    "def b64(x):\n"
    "    return base64.urlsafe_b64encode(x).decode().rstrip('=')\n"
    f"LEAK = {_LEAKED!r}\n"
    "h = b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())\n"
    "pl = b64(json.dumps({'user_id': 1, 'email': 'x@y.z', 'is_admin': True,\n"
    "                     'exp': 9999999999}).encode())\n"
    "sig = b64(hmac.new(LEAK.encode(), f'{h}.{pl}'.encode(), hashlib.sha256).digest())\n"
    "ok_self = auth.decode_jwt_token(auth.create_jwt_token(1, 'a@b.c', True))\n"
    "print('@@' + json.dumps({'forged': auth.decode_jwt_token(f'{h}.{pl}.{sig}') is not None,\n"
    "                         'self_ok': bool(ok_self and ok_self.get('is_admin')),\n"
    "                         'src': auth.JWT_SECRET_SOURCE,\n"
    "                         'has_st': True}))\n"
)
if not os.path.exists(os.path.join(ROOT, ".streamlit", "secrets.toml")):
    UNVERIFIED.append("真实部署形态（读 st.secrets）下伪造 token 是否被拒"
                      "（本机无 secrets.toml，未验）")
    print("SKIP 本机无 .streamlit/secrets.toml → 无法验证'读 secrets 的进程'，"
          "计入**未验证**")
else:
    try:
        _fl = subprocess.run([sys.executable, "-c", _forge_live], cwd=ROOT, env=_env2,
                             capture_output=True, text=True, encoding="utf-8",
                             errors="ignore", timeout=180)
        _lnl = [l for l in (_fl.stdout or "").splitlines() if l.startswith("@@")]
        _rl = _json0.loads(_lnl[0][2:]) if _lnl else None
    except Exception as e:                              # noqa: BLE001
        _rl = None
        bad(f"真实形态伪造实验执行失败：{type(e).__name__}: {e}")
    if _rl is None:
        UNVERIFIED.append("真实部署形态下伪造 token 是否被拒（子进程无输出）")
        print("SKIP 带 streamlit 的子进程没有输出（可能是 st.secrets 读取报错），"
              "计入**未验证**")
    else:
        ck("A26", _rl["self_ok"] is True,
           "【元断言】读 st.secrets 的进程里，自签自验通路也是通的")
        ck("A27", _rl["forged"] is False,
           "**真实部署形态（读 st.secrets）下，用已公开旧常量伪造的 is_admin token "
           f"也必须被拒**（实际密钥来源={_rl['src']}）—— "
           "线上前端走的就是这条路")


# ============================================================ 3. 被跟踪的文件不许夹带凭据
print()
print("-" * 72)
print("3. git 跟踪范围：凭据 / PII 形态字面量")

# 只扫**被 git 跟踪**的文件 —— 判据要对齐"会被公开发布的东西"，
# 而不是工作区（工作区里有 .streamlit/secrets.toml，那是正常的、被 ignore 的）。
_CRED_RX = [
    ("GitHub PAT", re.compile(r"\bghp_[A-Za-z0-9]{30,}")),
    ("GitHub PAT(new)", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}")),
    ("WxPusher app_token", re.compile(r"\bAT_[A-Za-z0-9]{16,}")),
    ("WxPusher UID", re.compile(r"\bUID_[A-Za-z0-9]{10,}")),
    ("Server酱 SendKey", re.compile(r"\bSCT\d{4,}T[A-Za-z0-9]{16,}")),
    ("Google API Key", re.compile(r"\bAIza[A-Za-z0-9_\-]{30,}")),
    ("JWT/Supabase anon key", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("Supabase 项目域名", re.compile(r"https://[a-z0-9]{15,}\.supabase\.co")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

_tracked = [f for f in (git("ls-files").stdout or "").splitlines() if f.strip()]
ck("A28", len(_tracked) >= 50,
   f"【元断言】确实取到了 git 跟踪清单（{len(_tracked)} 个文件），"
   "清单为空时下面的扫描会恒过")


def scan_creds(text: str) -> list[str]:
    return [name for name, rx in _CRED_RX if rx.search(text)]


# 元断言：拿"必然命中"的合成样本校准每一条正则。
# 这些是**编造的**假样本（形态对、值是假的），只用于证明扫描器活着。
#
# 所有样本一律用**拼接**构造，不写完整字面量。原因：本文件自己也进了 git 跟踪
# 清单，会被下面 A31 的扫描扫到 —— 若把 `eyJhbGci...` / `-----BEGIN RSA PRIVATE
# KEY-----` 这类完整串写在这里，扫描器会**把自己的样本当成真凭据报出来**。
# 拼接后源文本里不存在能触发正则的完整形态，A31 才能既扫自己又不自伤：
# 真有人把凭据粘进本文件，照样会被抓到（那不是样本形态）。
_FAKE = {
    "GitHub PAT": "ghp_" + "A" * 36,
    "WxPusher app_token": "AT_" + "b" * 20,
    "WxPusher UID": "UID_" + "c" * 16,
    "Server酱 SendKey": "SCT12345T" + "d" * 20,
    "Google API Key": "AIza" + "e" * 35,
    "JWT/Supabase anon key": "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiJ4In0",
    "Supabase 项目域名": "https://" + "abcdefghijklmnop" + ".supabase.co",
    "私钥块": "-----BEGIN " + "RSA " + "PRIVATE KEY-----",
}
_miss = [k for k, v in _FAKE.items() if k not in scan_creds(v)]
ck("A29", not _miss, f"【元断言】每条凭据正则都能命中对应形态的样本（失效的：{_miss}）")
ck("A30", scan_creds("这是一段普通中文说明，含 example.com 与 AT_ 前缀但不成形") == [],
   "【元断言】普通文本不误报（否则真命中会被噪音淹没）")

_cred_hits: list[str] = []
for _rel in _tracked:
    _p = os.path.join(ROOT, _rel.replace("/", os.sep))
    try:
        with open(_p, encoding="utf-8", errors="ignore") as fh:
            _t = fh.read()
    except OSError:
        continue
    for _name in scan_creds(_t):
        _cred_hits.append(f"{_rel}: {_name}")

ck("A31", not _cred_hits,
   f"被 git 跟踪的文件里不得出现凭据形态字面量（仓库是 public）。命中：{_cred_hits}")


# ============================================================ 4. secrets.toml 必须在圈外
print()
print("-" * 72)
print("4. 本地 Secrets 文件：必须被忽略，且从未进入历史")

_sec_rel = ".streamlit/secrets.toml"
_ci = git("check-ignore", "-v", _sec_rel)
ck("A32", _ci.returncode == 0,
   f"{_sec_rel} 必须被 .gitignore 排除（它含 Supabase service_key，"
   f"泄露=全库可读写）{'；当前未被忽略' if _ci.returncode != 0 else '：' + (_ci.stdout or '').strip()}")

# 元断言：拿一个**必然不被忽略**的路径校准，证明 check-ignore 不是恒返回 0
_ci_ctrl = git("check-ignore", "-v", "app.py")
ck("A33", _ci_ctrl.returncode == 1,
   "【元断言】check-ignore 对未被忽略的路径确实返回 1（拿 app.py 校准），"
   "否则上一条恒过")

_hist = git("log", "--all", "--oneline", "--", _sec_rel)
ck("A34", not (_hist.stdout or "").strip(),
   f"{_sec_rel} 从未被提交过（一旦进过历史，删掉也追不回 —— 只能轮换密钥）。"
   f"历史命中：{(_hist.stdout or '').strip()[:200]}")

# 元断言：同一个查法对**确实在历史里**的文件必须有输出
_hist_ctrl = git("log", "--all", "--oneline", "--", "app.py")
ck("A35", bool((_hist_ctrl.stdout or "").strip()),
   "【元断言】git log --all -- <path> 对确实存在的文件有输出（拿 app.py 校准），"
   "否则上一条恒过")

_tracked_set = set(_tracked)
ck("A36", _sec_rel not in _tracked_set,
   f"{_sec_rel} 当前不在 git 跟踪清单里")


# ============================================================ 5. 带外复核：已泄露口令是否已轮换
print()
print("-" * 72)
print("5. 带外复核：git 历史里公开过的口令，线上是否还在用")

# 为什么要连线：前四段都只能证明"代码干净了"，**证不到"线上安全了"**。
# 口令一旦进过 public 仓库的 git 历史就追不回来，删代码只是止血；
# 真正的风险敞口是"那个已公开的口令现在还能不能登进后台"。
# 这条只能靠带外查询回答 —— 这也是"下游保护生效证不到上游没出事"的同一条教训。
#
# 无凭据时**必须报"未验证"而不是 PASS**：把"没查"记成"通过"正是假绿的典型。
_LEAKED_PW = "chilam666"                 # 已公开在 git 历史，此处仅用于比对
_LEAKED_ADMIN = "chilam@admin.com"       # 同上


def _local_supabase() -> tuple[str, str]:
    """从本地 secrets.toml 读 Supabase 配置。CI 里没有这个文件属正常。"""
    p = os.path.join(ROOT, ".streamlit", "secrets.toml")
    if not os.path.exists(p):
        return "", ""
    with open(p, encoding="utf-8") as fh:
        toml = fh.read()
    m = re.search(r"\[supabase\](.*?)(?:\n\[|\Z)", toml, re.S)
    if not m:
        return "", ""
    blk = m.group(1)

    def one(k: str) -> str:
        mm = re.search(rf"^{k}\s*=\s*\"([^\"]*)\"", blk, re.M)
        return mm.group(1) if mm else ""

    return one("url").rstrip("/"), (one("service_key") or one("key"))


_su, _sk = _local_supabase()
if not (_su and _sk):
    UNVERIFIED.append("线上管理员口令是否已轮换（本机无 Supabase 凭据，未查）")
    print("SKIP 本机无 Supabase 凭据 → 无法带外复核线上口令，"
          "此项计入**未验证**（不算通过）")
else:
    import json as _json3
    import urllib.error
    import urllib.parse
    import urllib.request

    def _q(path: str):
        req = urllib.request.Request(
            f"{_su}/rest/v1/{path}",
            headers={"apikey": _sk, "Authorization": f"Bearer {_sk}"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            return _json3.loads(resp.read().decode("utf-8"))

    try:
        _rows = _q("users?select=email,password_hash&email=eq."
                   + urllib.parse.quote(_LEAKED_ADMIN))
    except Exception as e:                              # noqa: BLE001
        _rows = None
        UNVERIFIED.append(f"线上管理员口令是否已轮换（查询失败 {type(e).__name__}）")
        print(f"SKIP 查询 Supabase 失败（{type(e).__name__}），此项计入**未验证**")

    if _rows is not None:
        ck("A37", len(_rows) == 1,
           f"【元断言】确实查到了那个曾被公开的管理员账号（命中 {len(_rows)} 行），"
           "查不到时下一条会恒过")
        if len(_rows) == 1:
            _h = _rows[0].get("password_hash") or ""
            _parts = _h.split("$")
            _still = None
            if _h.startswith("pbkdf2_sha256$") and len(_parts) == 4:
                import hmac as _hm
                _dk = hashlib.pbkdf2_hmac(
                    "sha256", _LEAKED_PW.encode(),
                    bytes.fromhex(_parts[2]), int(_parts[1])).hex()
                _still = _hm.compare_digest(_dk, _parts[3])
            if _still is None:
                UNVERIFIED.append("线上管理员口令是否已轮换（哈希方案非 pbkdf2，未比对）")
                print("SKIP 线上哈希方案不是 pbkdf2，本探针不比对，计入**未验证**")
            else:
                ck("A38", _still is False,
                   "**曾公开在 git 历史里的那个管理员口令，线上必须已经改掉** —— "
                   "代码清理只是止血，口令本身进过 public 仓库就只能靠轮换处置")


# ============================================================ 汇总
print()
print("=" * 72)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项，未验证 {len(UNVERIFIED)} 项")
for _f in FAIL:
    print("  FAIL " + _f)
for _u in UNVERIFIED:
    print("  未验证 " + _u)
if UNVERIFIED:
    print("注意：存在**未验证**项 —— 这不是通过。"
          "（无凭据/连不上/哈希方案不认得，都只说明『没查到』，不说明『没问题』）")

# 未验证必须与失败同等待遇：**退出码非 0**。
# 原来写的是 `sys.exit(1 if FAIL else 0)` —— 上面刚声明完"未验证不是通过"，
# 转头就给它发了绿灯。真接进 CI 时（CI 里没有 secrets.toml），这条探针会
# 每次都绿着说"通过 0 项，失败 0 项，未验证 2 项"，而 CI 只看退出码。
# 「打印一行注意」拦不住机器，退出码才拦得住。
sys.exit(1 if (FAIL or UNVERIFIED) else 0)




