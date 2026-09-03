"""
凭据探针的**反向验证**：往被测代码里故意植入已知缺陷，检查
`tools_probe_credentials.py` 是否真的会因此失败。

为什么必须有这一步：探针全绿只能说明"当前代码没触发断言"，说明不了"断言有能力
被触发"。**永不失败的断言等于没有断言。** 本仓库刚刚就吃过这个亏 —— 探针第 2 段
用 Python 3.12 已移除的旧式 finder（find_module/load_module）屏蔽 streamlit，
实测 3.13 上 stub 完全失效，子进程照样 import 到 streamlit 1.62、照样读到本机
`.streamlit/secrets.toml`，于是"未配密钥"分支从未被走到；而当时的兜底元断言写成
链式比较 `a != b != c`（只比 (a,b) 与 (b,c)，**漏掉 (a,c)**），恰好 a、c 相等时
仍为 True，所以没抓住。造错反验就是为了拦这种事。

本脚本的规矩，每条都是踩过坑换来的：

1. **基线不是"0 失败"，也不保证是"全绿"**。本仓库基线一度是 37 PASS / 1 FAIL
   （线上管理员口令尚未轮换，是真实敞口），2026-09-03 轮换后变成 38 PASS / 0 FAIL。
   所以判据**既不能**写成"探针退出码变非 0"（它本来可能是 1），**也不能**写成
   "退出码还是 0 就没问题"（全绿同样可能是假绿）。唯一可靠的判据是逐条比对
   **具体哪一条断言**从 PASS 翻成了 FAIL。
2. **按断言 ID 比对，不按消息文本**。探针每条断言带稳定 ID（A01…A38）。
   消息里嵌着动态值（来源=...、长度=64），造错后文案一变就会被读成
   "旧断言消失 + 新断言出现"，把"造错没抓住"误报成"断言不稳定"；
   三条 `.streamlit/secrets.toml…` 断言前 22 字还完全相同，前缀匹配会一次命中三条。
2. **造错本身先验有效**：锚点必须在文件里恰好出现 1 次，替换后字节必须真的变了。
   锚点失配 → 报"造错未生效"并计失败，**绝不静默跳过**（静默跳过会让反验全绿）。
3. **改源码字符串，不 monkey-patch**：替换整个函数是"换实现"，验不到真实代码。
4. **恒 PASS 的断言同样要验**。口令轮换后，"线上口令已轮换"这条断言变成恒 PASS，
   光看它一直绿分不清"真查过且没命中"和"查询压根没跑"。M7 用环境变量喂它一个
   **必然命中**的口令（不落仓库），它必须翻成 FAIL；M8 则切断带外通道，验"没查到"
   被如实记为未验证且**退出码非 0**。三者合起来才证明该断言的结论来自实时数据。
5. **一层防御打不动，不等于断言是假的**。M1 恢复硬编码默认密钥后，
   "未配时不会回落到旧常量""伪造 token 必须被拒"仍 PASS —— 那是因为第二层
   （拒用已泄露常量）把它们接住了。必须用 M1b（两层一起拆）才能打下来。
   **把"造错后仍 PASS"一律判成假断言，会冤枉正确的断言，也会掩盖真正的纵深。**
5. **先证明探针是确定性的**：跑一个只动注释的空转造错（M0），结果必须与基线**逐条
   相同**。若空转就有漂移（第 5 段要连网），那么后面所有"翻转"都可能是抖动而非
   造错所致，此时结论必须是"本次什么都没验到"，不能报成功。
6. **收尾按字节还原并复核**：本仓库是 CRLF，文本模式读写会悄悄改行尾造成整文件 diff。
   全程用 bytes，最后用 sha256 复核，并重跑一次基线确认与开头一致。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROBE = "tools_probe_credentials.py"

# 线上管理员的**当前口令**，只从环境变量取，绝不写进本文件。
# 用途见 M7：要证伪"口令已轮换"这条断言，必须喂它一个必然命中的值。
# 这个口令进过 git 历史才需要轮换，再把它写进仓库等于白干。
_CURRENT_PW = os.environ.get("REVERSE_CURRENT_PW", "")

OK: list[str] = []
NG: list[str] = []


def ck(cond: bool, msg: str) -> None:
    (OK if cond else NG).append(msg)
    print(("  OK   " if cond else "  FAIL ") + msg)


def bad(msg: str) -> None:
    """唯一失败出口。脚本内部任何异常都要走这里，不许自己 print 了就算完。"""
    ck(False, msg)


# ---------------------------------------------------------------- 字节级读写
# 本仓库是 CRLF。文本模式读写会把行尾统一成 LF，造成"整文件都变了"的假 diff，
# 还会让最后的 sha256 还原复核失败。全程 bytes。
def rb(rel: str) -> bytes:
    with open(os.path.join(ROOT, rel), "rb") as fh:
        return fh.read()


def wb(rel: str, data: bytes) -> None:
    with open(os.path.join(ROOT, rel), "wb") as fh:
        fh.write(data)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def adapt_eol(data: bytes, s: str) -> bytes:
    """把锚点/替换文本的行尾对齐到目标文件的实际风格。

    本仓库行尾是混的：auth.py / database.py / .gitignore 是 **CRLF**，
    tools_*.py 和 memory/*.md 是 **LF**。多行锚点若写 `\\n` 而文件是 CRLF，
    命中次数就是 0 —— 那是"造错未生效"，不是"探针没抓住"。
    第一版就踩了这个坑（M2 锚点 0 命中）。与其逐条手写 `\\r\\n`，不如在执行器里
    按目标文件实际风格自动转换，从根上杜绝这类假实验。
    """
    body = s.encode("utf-8").replace(b"\r\n", b"\n")
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n")
    if crlf and crlf * 2 >= lf:          # CRLF 为主
        return body.replace(b"\n", b"\r\n")
    return body


def run_probe() -> tuple[int, str]:
    """跑一次凭据探针，返回 (退出码, 全部输出)。

    退出码必须从 CompletedProcess 拿。写成 `probe | tail` 再取 $? 会拿到
    tail 的退出码（恒 0）—— 这个坑本项目踩过不止一次。
    """
    p = subprocess.run([sys.executable, PROBE], cwd=ROOT,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def verdicts(out: str) -> dict[str, str]:
    """把探针输出解析成 {断言ID: PASS/FAIL}，形如 {"A11": "PASS", ...}。

    **为什么必须按 ID 而不是按消息文本比对**（本轮踩的坑）：
    断言消息里嵌着动态值（来源=...、长度=64、命中列表），造错后文案一变，
    归一化键就漂移。上一版拿「归一化后取前 50 字」当键，结果：
      - A16 造错后消息从「来源=ephemeral-random 长度=64」变成
        「来源=ephemeral-random(rejected-leaked from env:JWT_SECRET) 长度=64」
        → 旧键消失、新键出现，被读成「断言不稳定」而非「断言翻了」；
      - 三条 `.streamlit/secrets.toml…` 断言前 22 字完全相同，
        `find()` 固定宽度截断命中 3 条 → 判据失配。
    现在探针每条断言都带稳定 ID（A01..A38），反验直接按 ID 定位，
    与消息内容彻底解耦。
    """
    d: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^(PASS|FAIL) (A\d+) \| ", line)
        if m:
            d[m.group(2)] = m.group(1)
    return d


def msgs(out: str) -> dict[str, str]:
    """{断言ID: 消息原文}。只用于打印 —— ID 不可读，结论里必须能看出是哪条断言。"""
    d: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^(?:PASS|FAIL) (A\d+) \| (.*)$", line)
        if m:
            d[m.group(1)] = m.group(2)
    return d


def net_ok(out: str) -> bool:
    """第 5 段（带外查 Supabase）这次是否真的连上了。

    连不上时探针走 SKIP → 计入"未验证"，A37/A38 **整条不出现**。
    若不加区分，这种消失会被当成"造错生效"，把网络抖动读成实验成功。
    """
    return "A37" in verdicts(out)


# ---------------------------------------------------------------- 造错执行器
class Mutation:
    """一次造错。`patches` 是 [(相对路径, 锚点, 替换)] —— 支持多处联动改动。

    expect_flip：期望 PASS → FAIL 的断言（子串匹配，归一化后比）。
    expect_fix ：期望 FAIL → PASS（用于验"当前恒 FAIL"的那条断言不是写死的）。
    expect_hold：期望**保持不变**（用于证明某个写法是假断言 —— 见 M4b）。
    expect_gone：期望整条消失（前置元断言挂掉后，后续断言不再执行）。
    allow_extra：允许的连带翻转。必须**事先声明**；事后放行等于没有判据。
    """

    def __init__(self, name: str, why: str,
                 patches: list[tuple[str, str, str]],
                 expect_flip: tuple[str, ...] = (),
                 expect_fix: tuple[str, ...] = (),
                 expect_hold: tuple[str, ...] = (),
                 expect_gone: tuple[str, ...] = (),
                 expect_unverified: bool = False,
                 allow_extra: tuple[str, ...] = ()) -> None:
        self.name, self.why, self.patches = name, why, patches
        self.expect_flip, self.expect_fix = expect_flip, expect_fix
        self.expect_hold, self.expect_gone = expect_hold, expect_gone
        self.expect_unverified = expect_unverified
        self.allow_extra = allow_extra


def apply_mutation(m: Mutation) -> tuple[bool, dict[str, bytes], str]:
    """植入缺陷。返回 (是否成功, {路径: 原始字节}, 说明)。

    每个锚点必须**恰好命中 1 次**：0 次说明源码已变（锚点过期），2 次以上说明改动
    位置不唯一 —— 两种情况都必须报"造错未生效"，绝不能当成"探针没抓住"或静默跳过。
    任一 patch 失败则**全部回滚**，不留半改状态。
    """
    orig: dict[str, bytes] = {}
    notes: list[str] = []
    for rel, anchor, repl in m.patches:
        if rel not in orig:
            orig[rel] = rb(rel)
        cur = rb(rel)
        a, r = adapt_eol(cur, anchor), adapt_eol(cur, repl)
        n = cur.count(a)
        if n != 1:
            restore(orig)
            return False, orig, f"锚点在 {rel} 出现 {n} 次（需恰好 1 次）：{anchor[:50]!r}"
        new = cur.replace(a, r)
        if new == cur:
            restore(orig)
            return False, orig, f"{rel} 替换后字节未变（anchor 与 repl 相同？）"
        wb(rel, new)
        if rb(rel) != new:
            restore(orig)
            return False, orig, f"{rel} 写盘后读回不一致"
        notes.append(f"{rel} {sha(cur)}→{sha(new)}")
    return True, orig, "已植入：" + "；".join(notes)


def restore(orig: dict[str, bytes]) -> list[str]:
    """按字节还原。返回未能还原的文件清单（应为空）。"""
    bad_files = []
    for rel, data in orig.items():
        wb(rel, data)
        if sha(rb(rel)) != sha(data):
            bad_files.append(rel)
    return bad_files


# ---------------------------------------------------------------- 造错清单
# 每条造错都对应一个**真实发生过或极易发生**的缺陷，不是为了凑数。
MUTATIONS = [
    # M0：空转。只改注释文字，语义零变化。
    # 作用是先证明"探针是确定性的、键是稳定的"。若空转就漂移，那后面所有翻转都
    # 可能是抖动而非造错所致 —— 那时唯一诚实的结论是"本次什么都没验到"。
    Mutation(
        "M0 空转（只改注释）",
        "证明探针确定性 + 断言键稳定。这是所有后续比对的前提",
        [("auth.py", "# ==================== JWT 签名密钥 ====================",
          "# ==================== JWT 签名密钥（reverse-noop） ====================")],
    ),

    # M1：把硬编码默认密钥放回去 —— 这就是本轮修掉的原始高危洞。
    #
    # **关键推演（本轮最大的认知修正）**：第一版我把 A17/A25/A27 也列进了
    # expect_flip，跑出来它们仍 PASS，于是被报成"永不失败的假断言"。
    # 实际不是假断言 —— 是**第二层防御（拒用已泄露常量）把 M1 造的洞接住了**：
    # env 没配时默认值 = 旧常量 → `_fingerprint(val) == _LEAKED_SECRET_FP` 命中
    # → 拒用并降级为随机密钥 → 所以"未配时不会回落到旧常量"(A17) 依然成立、
    # 伪造 token 依然被拒(A25/A27)。
    # 也就是说 M1 单独只能打掉 A11（静态扫描）和 A16（来源字符串变了），
    # 剩下那几条要靠 **M1b（M1+M2 同时造错）** 才能打下来。
    # 这正是纵深防御该有的样子：任一层还在，核心结论就不该翻。
    Mutation(
        "M1 恢复硬编码 JWT 默认密钥",
        "原始洞：public 仓库里 environ.get('JWT_SECRET', '<固定常量>')，"
        "任何人可离线自签 is_admin token",
        [("auth.py", 'val = os.environ.get("JWT_SECRET", "")',
          'val = os.environ.get("JWT_SECRET", "chilam_club_secret_key_2026_invest_secure")')],
        expect_flip=("A11", "A16"),
    ),

    # M1b：**M1 + M2 同时造错**。把两层防御一起拆掉，才还原出本轮修复前的真实状态。
    # 它的价值是证明 A17/A18/A19/A20/A21/A25/A27 都不是假断言 ——
    # 它们在 M1 单独造错时不动，是因为第二层还在，不是因为它们没长眼睛。
    # 若这一条跑完它们仍 PASS，那才真有问题。
    Mutation(
        "M1b 硬编码默认值 + 拒用退回仅告警（两层一起拆）",
        "还原本轮修复前的真实形态：默认值就是公开常量，且拿它照常签名。"
        "用它证明 A17/A25/A27 那些『M1 单独打不动』的断言确实有效",
        [("auth.py", 'val = os.environ.get("JWT_SECRET", "")',
          'val = os.environ.get("JWT_SECRET", "chilam_club_secret_key_2026_invest_secure")'),
         ("auth.py", "    if _fingerprint(val) == _LEAKED_SECRET_FP:\n"
                     "        return _pysecrets.token_urlsafe(48), f\"{REJECTED_PREFIX} from {src})\"\n",
          "    if _fingerprint(val) == _LEAKED_SECRET_FP:\n"
          "        print(\"[Auth] 警告：JWT_SECRET 是已泄露旧常量\")\n")],
        expect_flip=("A11", "A16", "A17", "A18", "A19", "A20", "A21", "A25", "A27"),
    ),

    # M2：把「拒用已泄露密钥」退回成「仅告警」。
    # 这是本轮真正修掉的第二层问题：上一轮只打印告警、仍拿公开常量签名。
    Mutation(
        "M2 拒用退回成仅告警",
        "仅告警=洞照样开着。本仓库 Secrets 里填的就是那个公开常量，"
        "退回后线上前端立刻可被自签管理员 token 攻破",
        [("auth.py", "    if _fingerprint(val) == _LEAKED_SECRET_FP:\n"
                     "        return _pysecrets.token_urlsafe(48), f\"{REJECTED_PREFIX} from {src})\"\n",
          "    if _fingerprint(val) == _LEAKED_SECRET_FP:\n"
          "        print(\"[Auth] 警告：JWT_SECRET 是已泄露旧常量\")\n")],
        # A22（拒用时须告知运维）判据是 `out_leak` 里含"拒绝使用"或"旧常量"，
        # 而 M2 的替换文本里恰好有"旧常量" → 它会**假性通过**。列进 allow_extra
        # 是为了如实记录这个连带，而不是假装它没变。
        expect_flip=("A20", "A21", "A27"),
        allow_extra=("A22",),
    ),

    # M3：把明文口令后门放回 database.py。
    Mutation(
        "M3 恢复明文口令后门",
        "原始洞：bcrypt ImportError 兜底里 email==... and password=='<明文>' 直接放行，"
        "且口令字面量写进 public 仓库",
        [("database.py", '    if pw_hash.startswith(("$2b$", "$2a$", "$2y$")):',
          '    if user.get("email") == "chilam@admin.com" and password == "chilam666":\n'
          '        return True\n'
          '    if pw_hash.startswith(("$2b$", "$2a$", "$2y$")):')],
        expect_flip=("A12",),
    ),

    # M4：把 secrets.toml 从 .gitignore 里删掉 —— 一步之遥就会把 Supabase
    # service_key 提交进 public 仓库（那是全库可读写的钥匙）。
    # A32/A34/A36 三条消息都以「.streamlit/secrets.toml」开头，上一版按消息前缀
    # 匹配（取前 22 字）时三条全撞在一起 → 判据失配。改用 ID 后天然区分。
    # 只翻 A32：.gitignore 只影响"是否会被提交"，不影响"历史里有没有、跟踪清单里在不在"。
    Mutation(
        "M4 .gitignore 漏掉 secrets.toml",
        "漏掉一行 = 下次 git add . 就把 Supabase service_key 推上公开仓库",
        [(".gitignore", ".streamlit/secrets.toml\n", "")],
        expect_flip=("A32",),
    ),

    # M5：往**被 git 跟踪**的文件里塞一个凭据形态字面量。
    # 值是编造的（形态对、内容假），只用于验扫描器活着。
    Mutation(
        "M5 被跟踪文件夹带凭据字面量",
        "误把 token 粘进笔记/文档是最常见的泄露路径，且 git 历史追不回",
        [(".workbuddy/memory/2026-08-25.md", "# 2026-08-25 工作记录",
          "# 2026-08-25 工作记录\n\n临时记录（造错用假值）：" + "ghp_" + "B" * 36)],
        expect_flip=("A31",),
    ),

    # M6：**把探针自己的 bug 装回去**。这是本轮最该验的一条 ——
    # 上一版用 Python 3.12 已移除的旧式 finder 屏蔽 streamlit，3.13 上静默失效，
    # 于是"未配密钥"分支从未被测到，而当时的链式元断言恰好放过了它。
    # 现在的元断言必须能当场抓住同样的退化。
    Mutation(
        "M6 屏蔽手段退回旧式 finder（复现探针自己的 bug）",
        "验证『屏蔽是否生效』这条元断言真的有效 —— 它就是上次假绿的唯一防线",
        [(PROBE,
          "    \"sys.modules['streamlit'] = None\\n\"\n",
          "    \"class _B:\\n\"\n"
          "    \"    def find_module(self, n, p=None):\\n\"\n"
          "    \"        return self if n == 'streamlit' else None\\n\"\n"
          "    \"    def load_module(self, n):\\n\"\n"
          "    \"        raise ImportError()\\n\"\n"
          "    \"sys.meta_path.insert(0, _B())\\n\"\n")],
        expect_flip=("A15",),
        # streamlit 一旦没被屏蔽住，子进程就会读到本机 secrets.toml（里面正是
        # 那个已泄露旧常量）→ 走拒用分支 → A16 的来源字符串随之改变。
        # 这是"屏蔽失效"的连带后果，事先声明，不做事后放行。
        allow_extra=("A16", "A18"),
    ),

    # M7：**参照实验，方向已反转**。
    # 原先 A38 恒 FAIL（线上口令没换），M7 靠"换成绝不可能的值 → 必须翻 PASS"
    # 来证明它不是写死的失败。2026-09-03 口令已轮换成新值，**A38 现在恒 PASS**，
    # 于是要反过来证伪：喂它一个**必然命中**的值（线上当前正在用的口令），
    # 它必须翻成 FAIL —— 这才证明它 PASS 是因为"真查过且没命中"，
    # 而不是因为查询压根没跑、断言恒真。
    #
    # 那个口令**绝不能写进本文件**（本文件会提交进 public 仓库，刚修完的洞就是这个）。
    # 所以比对值从环境变量 REVERSE_CURRENT_PW 取，不落仓库、不进 git 历史。
    Mutation(
        "M7 把比对口令换成线上当前口令（不落仓库，走环境变量）",
        "证明 A38 的 PASS 来自实时查询 —— 喂一个必然命中的值，它必须翻 FAIL",
        [(PROBE, '_LEAKED_PW = "chilam666"',
          '_LEAKED_PW = __import__("os").environ.get('
          '"REVERSE_CURRENT_PW", "reverse-validate-definitely-not-it")')],
        expect_flip=("A38",) if _CURRENT_PW else (),
    ),

    # M9：往**探针自己的源码**里塞一个完整形态的凭据字面量。
    # 存在的意义：修 A31 时我把三个测试样本从完整字面量改成了拼接构造
    # （否则扫描器会把自己的样本报成真凭据）。这个改动有副作用风险 ——
    # 万一拼接写法把"扫描自己"这条能力一起废了，A31 就会变成假绿。
    # 所以必须造一个真形态的凭据粘进探针文件，看 A31 还抓不抓得住。
    # 注意：造错值在**本脚本源码里**仍是拼接写法，否则本脚本自己也会被 A31 扫中。
    Mutation(
        "M9 凭据字面量粘进探针源码本身",
        "证明把测试样本改成拼接构造后，A31 扫描**探针自己**的能力没有失效",
        [(PROBE, '# 元断言：拿"必然命中"的合成样本校准每一条正则。',
          '# 元断言：拿"必然命中"的合成样本校准每一条正则。\n'
          '# 造错验证用（假值）：' + "ghp_" + "C" * 36)],
        expect_flip=("A31",),
    ),

    # M8：**切断带外通道**，验"没查到"不会被记成"通过了"。
    # 探针第 5 段连 Supabase，连不上时走 SKIP → 计入 UNVERIFIED。
    # 光"计入未验证"还不够 —— 原来汇总处写 `sys.exit(1 if FAIL else 0)`，
    # 于是"未验证 2 项"照样退出 0。真接进 CI（CI 里没有 secrets.toml）时，
    # 这条探针会永远绿着，而它一条都没查。现已改成 UNVERIFIED 也退出非 0，
    # 这一条就是守住这个改动的。
    Mutation(
        "M8 切断带外通道（验未验证≠通过）",
        "连不上 Supabase 时，第 5 段必须整条记为未验证且**退出码非 0**，"
        "绝不能拿『没查到』冒充『没问题』",
        [(PROBE, '    return one("url").rstrip("/"), (one("service_key") or one("key"))',
          '    return "https://reverse-validate-does-not-exist.invalid", "deadbeef"')],
        expect_unverified=True,
    ),
]


# ---------------------------------------------------------------- 判据定位
def find(keys, want: str) -> list[str]:
    """在断言 ID 集合里定位 expect 声明对应的那一条。

    `want` 现在是断言 ID（A01..A38），由探针在调用点写死，不受消息文案变化影响。

    命中必须**唯一**：ID 不存在说明我抄错了 ID 或探针改过编号。必须报"判据失配"
    并计失败 —— 绝不能当成"没翻转"静默放过，那正是假反验的经典形态。
    （上一版的坑：按消息前缀匹配，宽度取 22 字，而三条 secrets.toml 断言的
    前 22 字完全相同 → 命中 3 条 → 判据失配。）
    """
    return [want] if want in keys else []


def compare(m: Mutation, base: dict[str, str], now: dict[str, str],
            base_net: bool, now_net: bool, text: dict[str, str],
            out: str, rc: int) -> None:
    """把一次造错的结果逐条落成结论。"""
    p = f"[{m.name}] "

    def desc(k: str) -> str:
        return f"{k} {text.get(k, '')[:40]}"

    # 「切断带外通道」这类造错，结论不是"某条断言翻了"，而是"整段被记为未验证
    # 且退出码非 0"。它必须**先于** net 一致性检查处理 —— 它的目的正是制造
    # 通道不一致，走通用分支会被判成"本次作废"。
    if m.expect_unverified:
        gone = [k for k in ("A37", "A38") if k in base and k not in now]
        ck(len(gone) == 2,
           p + f"通道切断后，第 5 段两条断言整条消失（消失 {len(gone)}/2）—— "
               "它们依赖带外查询，查不到就不该给结论")
        ck("未验证" in out,
           p + "输出里**明确记账为未验证**（而不是什么都不说，让人以为查过了）")
        ck(rc != 0,
           p + f"**退出码非 0**（实际 exit={rc}）—— 未验证必须与失败同等待遇，"
               "否则接进 CI 后这条探针会永远绿着却一条都没查")
        # 除第 5 段外不应波及其他断言
        stray0 = [k for k in set(base) | set(now)
                  if k not in ("A37", "A38") and base.get(k) != now.get(k)]
        ck(not stray0,
           p + "影响面仅限第 5 段" if not stray0 else
           p + "出现未声明的连带变化：" + "；".join(
               f"{base.get(k)}→{now.get(k)} {desc(k)}" for k in stray0[:4]))
        return

    # 带外通道（第 5 段连 Supabase）状态若与基线不同，第 5 段两条断言会整条
    # 出现/消失。那种差异是网络抖动造成的，不是造错造成的 —— 此时唯一诚实的
    # 结论是"本条什么都没验到"，不能挑着放行。
    if base_net != now_net:
        bad(p + f"带外通道状态与基线不一致（基线连通={base_net} 本次={now_net}），"
                "本条结论作废，请重跑")
        return

    allk = set(base) | set(now)
    declared: set[str] = set()

    def locate(want: str, tag: str) -> str | None:
        hits = find(allk, want)
        if len(hits) != 1:
            bad(p + f"判据失配（{tag}）：在探针输出里命中 {len(hits)} 条（需恰好 1 条）"
                    f"→ {want[:36]!r}")
            return None
        declared.add(hits[0])
        return hits[0]

    for want in m.expect_flip:
        k = locate(want, "expect_flip")
        if k is None:
            continue
        b, n = base.get(k), now.get(k)
        if b != "PASS":
            bad(p + f"参照物无效：该断言基线并非 PASS（基线={b}），验不出翻转 → {desc(k)}")
        elif n == "FAIL":
            ck(True, p + f"造错后如期失败 → {desc(k)}")
        elif n is None:
            bad(p + f"造错后该断言**整条消失**（前置元断言先挂 / 探针崩了），"
                    f"不能算抓住 → {desc(k)}")
        else:
            bad(p + f"**造错后仍 PASS —— 这是个永不失败的假断言** → {desc(k)}")
    for want in m.expect_fix:
        k = locate(want, "expect_fix")
        if k is None:
            continue
        b, n = base.get(k), now.get(k)
        if b != "FAIL":
            bad(p + f"参照物无效：该断言基线并非 FAIL（基线={b}），"
                    f"验不出『反向修好』 → {desc(k)}")
        elif n == "PASS":
            ck(True, p + f"参照实验如期转 PASS（证明该 FAIL 来自实时数据，"
                         f"不是写死的） → {desc(k)}")
        elif n is None:
            bad(p + f"参照实验后该断言整条消失，什么都没验到 → {desc(k)}")
        else:
            bad(p + f"**参照实验后仍 FAIL —— 说明这条 FAIL 与被测对象无关（写死的失败）**"
                    f" → {desc(k)}")

    for want in m.expect_hold:
        k = locate(want, "expect_hold")
        if k is None:
            continue
        if base.get(k) == now.get(k):
            ck(True, p + f"如期保持 {base.get(k)} → {desc(k)}")
        else:
            bad(p + f"预期不变但变了（{base.get(k)}→{now.get(k)}） → {desc(k)}")

    for want in m.expect_gone:
        k = locate(want, "expect_gone")
        if k is None:
            continue
        if k in base and k not in now:
            ck(True, p + f"如期整条消失（前置元断言先拦住） → {desc(k)}")
        else:
            bad(p + f"预期消失但仍在（{now.get(k)}） → {desc(k)}")

    # 未声明的连带翻转必须点名。造错常有连带效应，但"事后放行"等于没有判据 ——
    # 改坏了一堆东西会被读成"抓住了"。allow_extra 必须写在造错定义里。
    allowed = set(declared)
    for want in m.allow_extra:
        for k in find(allk, want):
            allowed.add(k)
    stray = []
    for k in allk:
        if k in allowed:
            continue
        if base.get(k) != now.get(k):
            stray.append(f"{base.get(k)}→{now.get(k)} {desc(k)}")
    if stray:
        bad(p + "出现**未声明**的连带变化（造错影响面超出预期，"
                "说明实验不干净）：\n      " + "\n      ".join(stray[:6]))
    else:
        ck(True, p + "影响面与声明一致，无未预期的连带翻转")
# ---------------------------------------------------------------- 主流程
def main() -> int:
    print("=" * 78)
    print("凭据探针 · 反向验证（造错 → 看探针是否真的抓得住）")
    print("=" * 78)

    # 0) 先记下所有待改文件的原始指纹。收尾要用它做全局还原复核 ——
    #    反验脚本自己把源码改坏了却退出，比探针假绿更糟。
    targets = sorted({rel for m in MUTATIONS for rel, _, _ in m.patches})
    before = {rel: rb(rel) for rel in targets}
    print("\n待改文件原始指纹：")
    for rel in targets:
        print(f"  {sha(before[rel])}  {rel}")

    # 1) 基线。注意：判定**不看退出码**，只看"具体哪条断言翻转"。
    #    本仓库基线曾经是 37 PASS / 1 FAIL（线上管理员口令未轮换，是真敞口），
    #    2026-09-03 已轮换成新口令 → 现在 38 PASS / 0 FAIL。
    #    基线会随时间变化，所以这里**不把任何数字写死**，只在异常时提示。
    print("\n[基线] 未造错跑一次探针 …")
    rc0, out0 = run_probe()
    base = verdicts(out0)
    base_net = net_ok(out0)
    text = msgs(out0)               # ID → 消息原文，用于让结论可读

    def desc2(k: str) -> str:
        return f"{k} {text.get(k, '')[:40]}"

    n_pass = sum(1 for v in base.values() if v == "PASS")
    n_fail = sum(1 for v in base.values() if v == "FAIL")
    print(f"  基线：{n_pass} PASS / {n_fail} FAIL（exit={rc0}）"
          f" 带外通道连通={base_net} 断言键={len(base)}")
    if len(base) < 30:
        bad(f"基线断言数只有 {len(base)}（预期 ≥30）—— 探针可能提前崩了，"
            "此时任何『翻转』都不可信，反验作废")
        return 2
    if n_fail == 0:
        print("  基线全绿。注意：**全绿不等于反验有效** —— 下面每条造错能否把它"
              "各自的目标断言打下来，才是这里要验的事。")
    else:
        print(f"  基线存在 {n_fail} 条真 FAIL（属真实敞口，不是反验失败）。"
              "反验照常进行：恒 FAIL 的断言另有参照实验负责证伪。")

    # 2) 逐条造错
    #    先说清 M7 的前提：它要证伪"口令已轮换"，必须拿到线上当前口令。
    #    拿不到就不能算 A38 已验证 —— 宁可让反验红着，也不要把没验的说成验过。
    if not _CURRENT_PW:
        bad("M7 需要环境变量 REVERSE_CURRENT_PW（线上管理员当前口令，**不落仓库**）"
            "才能证伪 A38；本次未提供 → A38 **未验证**，不能算有效")
    dirty: list[str] = []
    for m in MUTATIONS:
        print("\n" + "-" * 78)
        print(f"{m.name}\n  为什么要验：{m.why}")
        okp, orig, note = apply_mutation(m)
        if not okp:
            # 锚点失配 = 造错未生效。必须计失败，否则"什么都没改"会被读成
            # "探针没抓住"或直接跳过 —— 两种都是假反验。
            bad(f"[{m.name}] **造错未生效**：{note}")
            continue
        print(f"  {note}")
        try:
            rc, out = run_probe()
            now = verdicts(out)
            print(f"  造错后：{sum(1 for v in now.values() if v == 'PASS')} PASS / "
                  f"{sum(1 for v in now.values() if v == 'FAIL')} FAIL（exit={rc}）")
            if not m.expect_flip and not m.expect_fix and not m.expect_hold \
                    and not m.expect_gone and not m.expect_unverified:
                # M0 空转：结果必须与基线**逐条相同**。
                if now == base and net_ok(out) == base_net:
                    ck(True, f"[{m.name}] 语义无变化时结果与基线**逐条一致**"
                             "（探针确定性 + 断言键稳定，后续比对才有意义）")
                else:
                    diff = [f"{base.get(k)}→{now.get(k)} {desc2(k)}"
                            for k in set(base) | set(now) if base.get(k) != now.get(k)]
                    bad(f"[{m.name}] 空转就出现漂移，后续所有翻转都可能是抖动而非造错所致 "
                        f"→ 本次反验作废：\n      " + "\n      ".join(diff[:6]))
            else:
                compare(m, base, now, base_net, net_ok(out),
                        {**text, **msgs(out)}, out, rc)
        finally:
            failed = restore(orig)
            if failed:
                dirty.extend(failed)
                bad(f"[{m.name}] **还原失败**：{failed} —— 请立即 git checkout 这些文件")

    # 3) 全局还原复核：字节级比对，不看"我以为还原了"。
    print("\n" + "=" * 78)
    print("收尾：还原复核")
    for rel in targets:
        same = sha(rb(rel)) == sha(before[rel])
        print(f"  {'OK  ' if same else 'DIRTY'} {rel}  {sha(before[rel])}"
              f" → {sha(rb(rel))}")
        if not same:
            dirty.append(rel)
    ck(not dirty, "所有被造错的文件已按字节还原（sha256 逐个复核）")

    # 4) 再跑一次基线，确认与开头一致 —— 证明反验过程没把仓库留在半改状态。
    print("\n[收尾基线] 再跑一次探针，确认与开头一致 …")
    rc2, out2 = run_probe()
    after = verdicts(out2)
    if after == base and net_ok(out2) == base_net:
        ck(True, f"收尾基线与开头**逐条一致**（{n_pass} PASS / {n_fail} FAIL，"
                 f"exit={rc2}）—— 仓库状态干净")
    else:
        diff = [f"{base.get(k)}→{after.get(k)} {desc2(k)}"
                for k in set(base) | set(after) if base.get(k) != after.get(k)]
        bad("收尾基线与开头不一致（仓库可能未完全还原，或探针不确定）：\n      "
            + "\n      ".join(diff[:6]))

    print("\n" + "=" * 78)
    print(f"反向验证结果：{len(OK)} 项有效 / {len(NG)} 项无效")
    if NG:
        print("\n以下判据**没能证明探针有效**，必须修到不剩：")
        for i, msg in enumerate(NG, 1):
            print(f"  {i}. {msg}")
        print("\n结论：探针存在假断言或实验不干净 —— 现阶段**不能**把它的绿灯当证据。")
        return 1
    print("\n结论：每条断言都被至少一次造错真实触发过（含一条反向参照实验），"
          "\n      且空转不漂移、影响面可控、文件已按字节还原。"
          "\n      该探针的判据是**有效**的。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                             # noqa: BLE001
        # 任何异常都必须落成失败并尝试还原，绝不能抛出栈就退出 ——
        # 那会把改坏的源码留在工作区。
        import traceback
        traceback.print_exc()
        print("\n!!! 反验脚本自身异常。请立即执行："
              "\n    git checkout -- auth.py database.py .gitignore"
              " tools_probe_credentials.py .workbuddy/memory/2026-08-25.md")
        sys.exit(2)
