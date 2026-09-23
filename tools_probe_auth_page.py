# -*- coding: utf-8 -*-
"""pages/auth.py 无头冒烟（AppTest）：登录 / 注册 / 忘记密码三个面板。

为什么这个探针以前不存在（2026-09-23）：
    `pages/auth.py` 与根目录 `auth.py` 词干撞名，而 Streamlit 跑脚本前会把
    **脚本所在目录**插到 sys.path[0]，于是页面里的 `import auth` 解析回**自己**
    → 报 `partially initialized module 'auth'`；去「修」那个错位登记又会升级成
    RecursionError（实测 162 层）。结果这一页**长期进不了无头自检**，只能手点。
    修法是三页统一把项目根强制顶到 sys.path[0]（见 tech-pitfalls 分册）。
    本探针是那条修复的**行为级**守卫：`tools_probe_auth_reset.py` 只做静态 AST 断言，
    这里真的把页面跑起来。

验证三件事（顺序即依赖）：
  1. 三个面板都能无异常渲染（且 radio 的候选值齐全）；
  2. `?reset=<码>` 链接能把面板切到「忘记密码」并预填重置码输入框；
  3. 「忘记密码」的**只读**路径：用 RFC2606 保留域邮箱提交，必须给出「尚未注册」
     提示 —— 不写任何数据（注意：该邮箱不可能存在，所以永远不会真的发码）。

三态输出：✅ 通过 / ❌ 失败 / ⚠️ 未验证。「未验证」单独列出且**不计入通过**，
整体退出码非 0 —— 否则「这机器没有数据库凭据所以根本没测到」会被当成绿灯。

退出码：0 全通过 / 1 有失败或未验证
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

PASS, FAIL, UNVER = "pass", "fail", "unver"
results = []

BASE_EMAIL = "no-such-user-9f3a@example.invalid"   # RFC2606 保留域，不可能被注册
CODE_SAMPLE = "A7K2M9PQ3X"
MODE_LOGIN, MODE_REGISTER, MODE_RESET = "🔑 用户登录", "📝 新用户注册", "🆘 忘记密码"


def record(kind, label, detail=""):
    results.append((kind, label, detail))
    icon = {PASS: "✅", FAIL: "❌", UNVER: "⚠️"}[kind]
    print(f"{icon} {label}" + (f"：{detail}" if detail else ""))


def _first_frame(e):
    msg = (getattr(e, "message", None) or getattr(e, "value", None) or str(e))
    return str(msg).splitlines()[0][:300] if str(msg).strip() else type(e).__name__


def exc_text(at):
    return [_first_frame(e) for e in at.exception]


def is_env_missing(errs):
    """区分「被测对象坏了」与「这台机器没有凭据，根本测不到」。"""
    blob = " ".join(errs).lower()
    keys = ("supabase", "secret", "credential", "api key", "apikey",
            "connection", "timed out", "ssl", "getaddrinfo", "name or service")
    return any(k in blob for k in keys)


def find_button(at, prefix):
    for b in at.button:
        if (b.label or "").strip().startswith(prefix):
            return b
    return None


def set_mode(at, value):
    try:
        at.radio(key="auth_mode").set_value(value)
    except KeyError:
        at.radio[0].set_value(value)
    at.run()


def render(label, at):
    errs = exc_text(at)
    if errs:
        record(FAIL, label, " | ".join(errs))
        return False
    modes = [r.value for r in at.radio]
    record(PASS, label, f"radio={modes}，控件 {len(at.text_input)} 个")
    return True


def main() -> int:
    script = os.path.join(ROOT, "pages", "auth.py")

    # ---------- 1. 三个面板渲染 ----------
    at = AppTest.from_file(script, default_timeout=60)
    at.run()
    if not render("面板渲染：默认（登录）", at):
        print("\n首屏就抛异常，后续用例无法可靠执行，直接判失败。")
        return 1

    set_mode(at, MODE_REGISTER)
    render("面板渲染：切到注册", at)

    set_mode(at, MODE_RESET)
    reset_ok = render("面板渲染：切到忘记密码", at)

    # ---------- 2. ?reset= 链接预填 ----------
    try:
        at_q = AppTest.from_file(script, default_timeout=60)
        at_q.query_params["reset"] = CODE_SAMPLE
        at_q.run()
        errs = exc_text(at_q)
        if errs:
            record(FAIL, "?reset= 链接预填", " | ".join(errs))
        else:
            got = str({t.label: t.value for t in at_q.text_input}.get("重置码", ""))
            cur = [r.value for r in at_q.radio]
            if got != CODE_SAMPLE:
                record(FAIL, "?reset= 链接预填",
                       f"重置码输入框是 {got!r}，应为 {CODE_SAMPLE!r}")
            elif cur and cur[0] != MODE_RESET:
                record(FAIL, "?reset= 链接预填", f"面板停在 {cur[0]!r}，应切到忘记密码")
            else:
                record(PASS, "?reset= 链接预填",
                       f"面板={cur[0] if cur else '?'}，码已填入")
    except Exception as e:   # AppTest 版本差异不该被当成产品缺陷
        record(UNVER, "?reset= 链接预填", f"{type(e).__name__}: {str(e)[:120]}")

    # ---------- 3. 只读路径：不存在的邮箱 ----------
    if not reset_ok:
        record(UNVER, "只读找回：不存在的邮箱", "忘记密码面板渲染失败，未能执行")
    else:
        try:
            at.radio(key="auth_mode").set_value(MODE_RESET)
            at.run()
            at.text_input(key="reset_req_email").set_value(BASE_EMAIL)
            btn = find_button(at, "发送重置码")
            if btn is None:
                record(UNVER, "只读找回：不存在的邮箱",
                       f"没找到「发送重置码」按钮；现有按钮={[b.label for b in at.button]}")
            else:
                btn.click()
                at.run()
                errs = exc_text(at)
                if errs:
                    if is_env_missing(errs):
                        record(UNVER, "只读找回：不存在的邮箱",
                               f"本机没有数据库凭据，未真正验证：{errs[0][:120]}")
                    else:
                        record(FAIL, "只读找回：不存在的邮箱", " | ".join(errs))
                else:
                    texts = [e.value for e in at.error] + [w.value for w in at.warning]
                    hit = [t for t in texts if "尚未注册" in str(t)]
                    if hit:
                        record(PASS, "只读找回：不存在的邮箱", f"提示={hit[0][:60]}")
                    else:
                        record(FAIL, "只读找回：不存在的邮箱",
                               f"没看到「尚未注册」提示；message={texts}")
        except Exception as e:
            record(UNVER, "只读找回：不存在的邮箱", f"{type(e).__name__}: {str(e)[:120]}")

    n_pass = sum(1 for k, _, _ in results if k == PASS)
    n_fail = sum(1 for k, _, _ in results if k == FAIL)
    n_unver = sum(1 for k, _, _ in results if k == UNVER)
    print("-" * 60)
    print(f"通过 {n_pass} 项，失败 {n_fail} 项"
          + (f"，未验证 {n_unver} 项" if n_unver else ""))
    for kind, label, _ in results:
        if kind != PASS:
            print(f"   [{kind}] {label}")
    ok = (n_fail == 0 and n_unver == 0)
    print("✅ 全部通过" if ok else "❌ 未通过（未验证与失败同等待遇，不可当绿灯）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
