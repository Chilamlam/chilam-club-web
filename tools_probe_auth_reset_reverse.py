# -*- coding: utf-8 -*-
"""对 tools_probe_auth_reset.py 做造错反验（mutation testing）。

为什么必须做：一个**永不失败**的断言等于没有断言。密码这套功能里最危险的
失效形态是「拒绝的同时把事情做了一半」（码错了却仍改密码、被限流却仍发了码），
而这类 bug 在人工点测里几乎看不见——页面提示是对的。所以每条关键断言都要能
被一次**真实的**源码改动打红，否则它就只是装饰。

四条纪律（本项目反验铁律 + 本机踩过的坑）：
  1. **造错本身先验有效**：锚点必须唯一命中，且替换后内容确实变了；
     锚点失配一律报「造错未生效」并计入失败——不许静默当成"已造错"跑过去。
  2. **期望关键字必须取自失败分支才打印的文案**，并且只在 ❌ 行里找命中；
     在整份输出里找，会被「别的断言挂了 + 关键字恰好出现在 PASS 行」蒙过去。
  3. **造错用改源码字符串**，不 monkey-patch 被测函数（那等于换实现）。
  4. **行尾必须原样保留**：本项目 CRLF/LF 混用（auth.py / database.py 是 CRLF）。
     `open(encoding='utf-8')` 默认走 universal newlines，读的时候会把 CRLF 悄悄
     变成 LF，写回就把整个文件的行尾翻一遍 —— 反验结束后 `git status` 会显示
     满屏改动，真实的错在哪反而看不见。所以：按字节读 → 归一成 LF 做替换 →
     按原行尾风格写回 → 还原时直接写回原始字节。

每个变异跑完立刻按原字节还原，并校验 sha 与造错前一致——
反验脚本自己把生产代码留在半改状态，是比没有反验更严重的事故。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.join(ROOT, ".probe_tmp", "rev_auth_reset")
PROBE = os.path.join(ROOT, "tools_probe_auth_reset.py")

# (说明, 文件, 旧串, 新串, 期望出现在 ❌ 行里的关键字)
MUTATIONS = [
    ("把明文码塞进写库载荷（明文落库）",
     "database.py",
     '        "code_hash": code_hash,\n',
     '        "code_hash": code_hash,\n        "code": code,\n',
     "明文码字段"),

    ("码校验失败时仍顺手改密码",
     "auth.py",
     '    if not pr.verify_code(code, rec.get("code_salt"), rec.get("code_hash")):\n'
     '        used = attempts + 1\n',
     '    if not pr.verify_code(code, rec.get("code_salt"), rec.get("code_hash")):\n'
     '        database.update_user_password(row["id"], new_password)\n'
     '        used = attempts + 1\n',
     "没有改密码"),

    ("重置成功后不作废该账号其余未消费码",
     "auth.py",
     '    database.consume_reset_code(rec["id"])\n'
     '    database.invalidate_reset_codes(row["id"])\n'
     '    return True, "✅ 密码已重置成功，请用新密码登录。"\n',
     '    database.consume_reset_code(rec["id"])\n'
     '    return True, "✅ 密码已重置成功，请用新密码登录。"\n',
     "作废该账号未消费的重置码"),

    ("限流被拆掉（拦截分支永不触发，仍会发码）",
     "auth.py",
     '    elif per_min >= 1:\n'
     '        return "failed", f"请求过于频繁，请 {pr.RESEND_COOLDOWN_SECONDS} 秒后再试。"\n',
     '    elif per_min >= 999999:\n'
     '        return "failed", f"请求过于频繁，请 {pr.RESEND_COOLDOWN_SECONDS} 秒后再试。"\n',
     "冷却期内"),

    ("成功文案回显重置码（凭据进页面/日志）",
     "auth.py",
     '            return "sent", (\n'
     '                f"✅ 重置码已发送到 **{where}**。\\n\\n"\n',
     '            return "sent", (\n'
     '                f"✅ 重置码 {code} 已发送到 **{where}**。\\n\\n"\n',
     "返回值里不出现码变量"),

    ("重置成功后不写 flash（提示会被 rerun 吞掉）",
     "pages/auth.py",
     '                st.session_state["auth_mode"] = MODE_LOGIN\n'
     '                st.session_state["auth_flash"] = ("ok", msg)\n',
     '                st.session_state["auth_mode"] = MODE_LOGIN\n'
     '                st.success(msg)\n',
     "重置成功后的顺序是"),

    ("会员中心删掉改密调用（功能入口消失）",
     "pages/dashboard.py",
     '            ok, msg = auth.change_password(cur_pw, new_pw, new_pw2)\n',
     '            ok, msg = True, ""\n',
     "pages/dashboard.py 调用了 auth.change_password"),

    ("某个页面的导入引导改回条件式插入（pages/ 会稳坐 sys.path[0]）",
     "pages/admin.py",
     "while _ROOT in sys.path:\n"
     "    sys.path.remove(_ROOT)\n"
     "sys.path.insert(0, _ROOT)\n",
     "if _ROOT not in sys.path:\n"
     "    sys.path.insert(0, _ROOT)\n",
     "没有条件式 sys.path 插入"),

    ("子表映射键名写错（smtp.host 写成 smtp.hostname → 配了却不生效）",
     "auth.py",
     '    "DIGEST_SMTP_HOST": ("smtp", "host"),\n',
     '    "DIGEST_SMTP_HOST": ("smtp", "hostname"),\n',
     "子表写法"),

    ("重置链接指向别的页面（/auth → /dashboard）",
     "password_reset.py",
     '    return f"{base}/auth?reset={code}"\n',
     '    return f"{base}/dashboard?reset={code}"\n',
     "重置链接指向 `/auth` 这一页"),

    ("重置码表加一列明文码",
     "init_password_reset.sql",
     '    code_hash   TEXT NOT NULL,               -- sha256(salt:归一化后的码)\n',
     '    code_hash   TEXT NOT NULL,               -- sha256(salt:归一化后的码)\n'
     '    code        TEXT NOT NULL,               -- 造错：明文码\n',
     "没有明文码列"),
]


def read_raw(rel: str) -> bytes:
    with open(os.path.join(ROOT, rel), "rb") as f:
        return f.read()


def to_lf(raw: bytes) -> tuple[str, bool]:
    """返回 (LF 版文本, 原本是否 CRLF)。含 NUL 的二进制不做这项处理。"""
    crlf = b"\r\n" in raw
    return raw.decode("utf-8").replace("\r\n", "\n"), crlf


def write_text(rel: str, text_lf: str, crlf: bool) -> None:
    out = text_lf.replace("\n", "\r\n") if crlf else text_lf
    with open(os.path.join(ROOT, rel), "wb") as f:
        f.write(out.encode("utf-8"))


def write_raw(rel: str, raw: bytes) -> None:
    with open(os.path.join(ROOT, rel), "wb") as f:
        f.write(raw)


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def run_probe() -> tuple[int, list[str]]:
    r = subprocess.run([sys.executable, "-X", "utf8", PROBE],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    fails = [l for l in (r.stdout or "").splitlines() if "❌" in l]
    return r.returncode, fails


def main() -> int:
    os.makedirs(TMP, exist_ok=True)
    print("=" * 64)
    print("造错反验：tools_probe_auth_reset.py")
    print("=" * 64)

    rc, fails = run_probe()
    if rc != 0 or fails:
        print(f"❌ 基线不干净（exit={rc}，失败 {len(fails)} 条）——先修好再反验")
        for f in fails[:6]:
            print("   ", f[:150])
        return 1
    print("✅ 基线干净（exit=0，无失败）\n")

    touched = sorted({m[1] for m in MUTATIONS})
    backups: dict[str, bytes] = {}
    for rel in touched:
        raw = read_raw(rel)
        backups[rel] = raw
        with open(os.path.join(TMP, rel.replace("/", "__")), "wb") as f:
            f.write(raw)
        print(f"   备份 {rel}  sha256={sha_bytes(raw)[:12]}"
              f"{'  (CRLF)' if b'\\r\\n' in raw else ''}")
    print()

    caught = escaped = ineffective = 0
    for desc, rel, old, new, expect in MUTATIONS:
        raw = read_raw(rel)
        text_lf, crlf = to_lf(raw)
        n = text_lf.count(old)
        if n != 1:
            ineffective += 1
            print(f"❌ 造错未生效（锚点命中 {n} 次，应为 1）：{desc}  [{rel}]")
            continue
        mutated = text_lf.replace(old, new)
        if mutated == text_lf:
            ineffective += 1
            print(f"❌ 造错未生效（替换后内容未变，属等价改动）：{desc}")
            continue
        write_text(rel, mutated, crlf)

        rc, fails = run_probe()
        hit = next((l for l in fails if expect in l), None)
        if rc != 0 and hit:
            caught += 1
            print(f"✅ 被抓到：{desc}")
            print(f"      ↳ 失败行含「{expect}」：{hit.strip()[:112]}")
        elif rc != 0 and not hit:
            escaped += 1
            print(f"❌ 未被该断言抓到（探针红了，但红的不是这一条）：{desc}")
            print(f"      ↳ 期望关键字「{expect}」未出现在任何失败行；实际失败：")
            for l in fails[:3]:
                print("        ", l.strip()[:118])
        else:
            escaped += 1
            print(f"❌ **逃逸**：{desc} —— 探针仍然全绿，这条断言是假的")

        write_raw(rel, raw)          # 立刻按原始字节还原
        if sha_bytes(read_raw(rel)) != sha_bytes(raw):
            print(f"   ⚠️ 还原后字节不一致：{rel}")

    print()
    print("-" * 64)
    bad_restore = [rel for rel, raw in backups.items()
                   if sha_bytes(read_raw(rel)) != sha_bytes(raw)]
    for rel in bad_restore:
        with open(os.path.join(TMP, rel.replace("/", "__")), "rb") as f:
            write_raw(rel, f.read())
    if bad_restore:
        print(f"⚠️ 有文件未回到造错前状态，已从备份恢复：{bad_restore}")
    else:
        print("✅ 全部文件逐字节还原到造错前状态")

    rc, fails = run_probe()
    print(f"✅ 终态复跑：exit=0，无失败" if rc == 0
          else f"❌ 终态复跑仍失败 {len(fails)} 条")
    print()
    print(f"抓到 {caught} / 逃逸 {escaped} / 造错未生效 {ineffective}"
          f"（共 {len(MUTATIONS)} 个变异）")
    ok = (escaped == 0 and ineffective == 0 and rc == 0 and not bad_restore)
    print("✅ 反验通过：每条关键断言都能被真实的源码改动打红"
          if ok else "❌ 反验未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
