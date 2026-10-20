# -*- coding: utf-8 -*-
"""站点设置（店铺入口开关）与 JWT 密钥诊断的探针。

用法: <stcheck>/python.exe tools_probe_site_settings.py
退出码 0 = 全过；1 = 有失败。

为什么这份探针值得存在
----------------------
「入口已隐藏」这类需求最容易写出**永不失败的断言**：
直接断言「页面上没有这个按钮」，但整段代码被删掉、或数据库读不到、
或页面根本没渲染，三种坏情况全都会绿。所以这里每个「看不到」都配一条
「该看到时必须看得到」的反向用例，另有元断言证明页面真的渲染了。
断言本身能不能抓 bug，由 tools_probe_site_settings_reverse.py 造错验证。

三层覆盖
--------
1. 纯逻辑：`_truthy` / `is_safe_url` / `read_shop_entry` 五种路径 / `save_shop_entry`
2. 前台：AppTest 真渲染 app.py，用 patch 的配置驱动，看侧边栏到底渲不渲染
3. 后台：AppTest 真渲染 pages/admin.py（注入管理员 token），看建表提示与保存界面
"""
from __future__ import annotations

import contextlib
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import auth                                                        # noqa: E402
import database                                                    # noqa: E402
import site_settings as ss                                         # noqa: E402

FAILS = []
N = 0


def ck(ok, name, detail=""):
    global N
    N += 1
    print(("✅ " if ok else "❌ ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


@contextlib.contextmanager
def patched(obj, name, value):
    """临时替换属性，用完**必定还原**（并校验还原成功）。

    不还原的后果是实打实的：本探针曾在别处把模块常量改成 37 却忘记还原，
    污染了后续用例，让「相隔 24 天」被当成「相邻」，两条断言假失败。
    """
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)
        assert getattr(obj, name) is old, f"{name} 未还原"


def cfg(enabled=False, label="🛒 全天候小店", url="https://shop.chilam.club",
        source="db", err=None, warn=""):
    return {"enabled": enabled, "label": label, "url": url,
            "source": source, "err": err, "warn": warn}


# ==================== 1. 纯逻辑 ====================
def test_truthy():
    # 最关键的一条：字符串 "0" 在 Python 里是 truthy。
    # 写 bool(value) 会让「关」变成「开」——方向错在最危险的一侧。
    for v in ("1", "true", "TRUE", "yes", "on", " 1 "):
        ck(ss._truthy(v) is True, f"_truthy({v!r}) 为真")
    for v in ("0", "false", "", None, "2", "no", "off", "  "):
        ck(ss._truthy(v) is False, f"_truthy({v!r}) 为假")
    ck(ss._truthy(0) is False, "_truthy(0) 为假")
    ck(ss._truthy(True) is True, "_truthy(True) 为真")


def test_safe_url():
    for u in ("http://a.com", "https://shop.chilam.club", "HTTPS://X.COM", " https://x "):
        ck(ss.is_safe_url(u) is True, f"合法链接 {u!r} 通过")
    # javascript: 一旦被渲染成可点击按钮，就等于给全站访客挂 XSS
    for u in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<script>",
              "ftp://x.com", "", None, "//evil.com", "http:/x"):
        ck(ss.is_safe_url(u) is False, f"非法链接 {u!r} 被拒")


def test_read_paths():
    cases = [
        # (DB 返回值, err, 期望 enabled, 期望 source, 说明)
        ({"shop_entry_enabled": "1"}, None, True, "db", "读到开启"),
        ({"shop_entry_enabled": "0"}, None, False, "db", "读到关闭"),
        ({}, None, ss.SHOP_ENTRY_DEFAULT_ENABLED, "default", "表里没配 → 用代码默认"),
        ({}, {"status": 500, "code": "", "message": "boom", "detail": ""},
         ss.SHOP_ENTRY_DEFAULT_ENABLED, "db-read-failed", "读失败 → 回落默认"),
    ]
    for vals, err, exp_en, exp_src, desc in cases:
        with patched(database, "get_site_settings", lambda keys, _v=vals, _e=err: (_v, _e)):
            c = ss.read_shop_entry()
        ck(c["enabled"] is exp_en, f"read_shop_entry：{desc} → enabled={exp_en}", repr(c["enabled"]))
        ck(c["source"] == exp_src, f"read_shop_entry：{desc} → source={exp_src}", c["source"])

    # 默认值方向必须是「隐藏」——把已关的店又露出来，比少一个入口糟得多
    ck(ss.SHOP_ENTRY_DEFAULT_ENABLED is False, "代码默认值是「隐藏」（兜底方向正确）")

    # 开关开着但链接是伪协议 → 必须**消毒成不显示**，并给出告警
    with patched(database, "get_site_settings",
                 lambda keys: ({"shop_entry_enabled": "1", "shop_entry_url": "javascript:alert(1)",
                                "shop_entry_label": "x"}, None)):
        c = ss.read_shop_entry()
    ck(c["enabled"] is False, "伪协议链接：即使开关是开的也不显示")
    ck(bool(c["warn"]), "伪协议链接：给出告警而不是静默", repr(c["warn"]))

    # 文案为空 → 回落默认文案，不能渲染一个没有文字的按钮
    with patched(database, "get_site_settings",
                 lambda keys: ({"shop_entry_enabled": "1", "shop_entry_label": "   "}, None)):
        c = ss.read_shop_entry()
    ck(c["label"] == ss.SHOP_ENTRY_DEFAULT_LABEL, "空文案回落到默认文案", repr(c["label"]))


def test_save():
    calls = []

    def fake_set(key, value, updated_by=None):
        calls.append((key, value, updated_by))
        return True, None

    with patched(database, "set_site_setting", fake_set):
        ok, err, warn = ss.save_shop_entry(True, "🛒 新店", "https://new.example.com",
                                           updated_by="admin@x")
    keys = [c[0] for c in calls]
    ck(ok and err is None, "save_shop_entry 正常保存返回成功")
    ck(keys == [ss.KEY_ENABLED, ss.KEY_LABEL, ss.KEY_URL], "三项都被写入且顺序稳定", str(keys))
    ck(calls[0][1] == "1", "开启写成 '1'", calls[0][1])
    ck(warn == "", "合法链接无告警", warn)

    calls.clear()
    with patched(database, "set_site_setting", fake_set):
        ok, err, warn = ss.save_shop_entry(False, "x", "https://a.com")
    ck(calls[0][1] == "0", "关闭写成 '0'", calls[0][1])

    calls.clear()
    with patched(database, "set_site_setting", fake_set):
        ok, err, warn = ss.save_shop_entry(True, "x", "javascript:alert(1)")
    ck(bool(warn), "保存时对伪协议链接给出告警（不等前台才发现）", repr(warn))

    def failing_set(key, value, updated_by=None):
        return False, {"status": 400, "code": "X", "message": "nope", "detail": ""}

    with patched(database, "set_site_setting", failing_set):
        ok, err, warn = ss.save_shop_entry(True, "x", "https://a.com")
    ck(ok is False and err is not None, "写入失败如实返回错误，不假装成功")


def test_read_budget():
    """侧边栏路径上的读取必须收窄超时与重试次数。

    为什么值得一条断言：这条读跑在**每次页面渲染**的路径上，而读失败本来就有
    「按隐藏处理」的兜底。若有人图省事改回全局默认（重试 3 次 × 超时 10 秒），
    Supabase 抖一下就会让缓存过期后的第一个访客白卡半分钟，而且症状是
    「网站偶尔很慢」——这种问题极难归因，所以在这里钉死。
    """
    import urllib.request as _u
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise OSError("simulated network down")

    with patched(_u, "urlopen", fake_urlopen):
        vals, err = database.get_site_settings(["shop_entry_enabled"])
    ck(len(calls) == 1, "站点设置读失败只尝试 1 次（不按默认 3 次重试）", f"实际 {len(calls)} 次")
    ck(calls and calls[0] == database.SITE_SETTINGS_TIMEOUT,
       "站点设置读取用短超时（SITE_SETTINGS_TIMEOUT）而非默认 10 秒", str(calls))
    ck(err is not None, "读失败如实返回 err（不是静默的空值）")
    ck(vals == {}, "读失败时返回空映射而不是半截数据", repr(vals))


# ==================== 2. 前台真渲染 ====================
def render_app(timeout=300):
    from streamlit.testing.v1 import AppTest
    ss.clear_shop_entry_cache()          # 缓存不清，用例会互相串值
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=timeout)
    at.run()
    return at


def test_app_render():
    # 关闭（当前线上状态）
    with patched(ss, "read_shop_entry", lambda: cfg(enabled=False)):
        at = render_app()
        ck(not at.exception, "前台（关闭）：渲染无异常", str(at.exception)[:200])
        ck(len(at.get("link_button")) == 0, "前台（关闭）：没有店铺按钮",
           f"剩 {len(at.get('link_button'))} 个")

    # 打开 —— 反向用例。没有这一条，「关闭」那条把整段代码删掉也能绿。
    with patched(ss, "read_shop_entry",
                 lambda: cfg(enabled=True, label="🛒 测试小店", url="https://t.example.com")):
        at = render_app()
        lbs = at.get("link_button")
        ck(len(lbs) == 1, "前台（打开）：按钮出现（证明隐藏是配置所致，不是代码被删）",
           f"实际 {len(lbs)} 个")
        if len(lbs) == 1:
            ck(str(getattr(lbs[0], "label", "")) == "🛒 测试小店",
               "前台（打开）：文案取自配置", str(getattr(lbs[0], "label", "")))
            ck(str(getattr(lbs[0], "url", "")) == "https://t.example.com",
               "前台（打开）：链接取自配置", str(getattr(lbs[0], "url", "")))

    # 读失败 / 伪协议 → 不显示且不崩
    for c, desc in ((cfg(enabled=False, source="db-read-failed",
                         err={"status": 500, "code": "", "message": "x", "detail": ""}),
                     "读失败"),
                    (cfg(enabled=False, url="javascript:alert(1)", warn="坏链接"), "伪协议")):
        with patched(ss, "read_shop_entry", lambda _c=c: _c):
            at = render_app()
            ck(not at.exception, f"前台（{desc}）：渲染不崩", str(at.exception)[:160])
            ck(len(at.get("link_button")) == 0, f"前台（{desc}）：不显示店铺按钮")

    # 元断言：确认 app.py 真的渲染了（否则「没有按钮」是空洞通过）
    with patched(ss, "read_shop_entry", lambda: cfg(enabled=False)):
        at = render_app()
        ck(any("Chilam.Club" in str(t.value) for t in at.sidebar.title),
           "元断言：前台侧边栏确实渲染（排除空跑）")
        nav = at.sidebar.radio[0] if at.sidebar.radio else None
        ck(nav is not None and len(nav.options) == 15,
           "元断言：导航仍为 15 项（改动没误伤其他入口）")


# ==================== 3. 后台真渲染 ====================
def render_admin(timeout=300):
    """渲染 pages/admin.py，注入管理员 token，并隔离所有 Supabase 调用。"""
    from streamlit.testing.v1 import AppTest
    ss.clear_shop_entry_cache()
    at = AppTest.from_file(str(ROOT / "pages" / "admin.py"), default_timeout=timeout)
    at.session_state["token"] = auth.create_jwt_token(1, "admin@probe.local", is_admin=True)
    at.run()
    return at


def _admin_patches(shop_cfg):
    """后台页会查一堆表；全部替换成常量，验证才不依赖网络。"""
    return [
        (database, "get_all_users", lambda: []),
        (database, "get_pending_payments", lambda: []),
        (database, "get_expired_payments", lambda *a, **k: []),
        (database, "get_pending_password_reset_requests", lambda: []),
        # 不 patch 这个，tab_reset 里的 st.stop() 会中断整页，tab_site 根本渲染不到
        (database, "check_password_reset_tables", lambda: True),
        (ss, "read_shop_entry", lambda: shop_cfg),
    ]


@contextlib.contextmanager
def admin_env(shop_cfg):
    with contextlib.ExitStack() as stack:
        for obj, name, val in _admin_patches(shop_cfg):
            stack.enter_context(patched(obj, name, val))
        yield


def test_admin_render():
    # 表未建：要给出建表 SQL，且不能装作能保存
    with admin_env(cfg(enabled=False, source="db-read-failed",
                       err={"status": 404, "code": "PGRST205",
                            "message": "Could not find the table", "detail": ""})):
        at = render_admin()
        ck(not at.exception, "后台（表未建）：渲染无异常", str(at.exception)[:200])
        tx = "\n".join([str(x.value) for x in at.markdown]
                       + [str(x.value) for x in at.caption]
                       + [str(x.value) for x in at.warning]
                       + [str(x.value) for x in at.code])
        ck("site_settings" in tx and "尚未创建" in tx, "后台（表未建）：提示表未创建")
        ck("CREATE TABLE" in tx, "后台（表未建）：给出建表 SQL")
        ck(not any("保存" in str(b.label) for b in at.button), "后台（表未建）：不显示保存按钮")

    # 表就绪：出现开关 + 文案 + 链接 + 保存
    ok_cfg = cfg(enabled=False, source="db")
    with admin_env(ok_cfg):
        at = render_admin()
        ck(not at.exception, "后台（就绪）：渲染无异常", str(at.exception)[:200])
        ck(len(at.toggle) == 1, "后台（就绪）：有开关控件", f"{len(at.toggle)} 个")
        ck(len(at.text_input) >= 2, "后台（就绪）：有文案与链接输入框", f"{len(at.text_input)} 个")
        ck(any("保存" in str(b.label) for b in at.button), "后台（就绪）：有保存按钮")
        # 不加 hasattr 兜底：兜底会让 AppTest 将来删掉这个属性时断言恒真，
        # 变成永不失败的断言。已核实 at.tabs 存在（2026-10-20）。
        ck(any("站点设置" in str(t.label) for t in at.tabs),
           "后台（就绪）：站点设置 tab 存在")
        # 元断言：确实渲染在管理员态（否则整页被权限门禁 stop 了，上面几条是空洞通过）
        ck(any("后台管理" in str(t.value) for t in at.title), "元断言：后台页确以管理员身份渲染")

    # 密钥诊断：随机密钥时必须显式报「会掉登录」
    with admin_env(ok_cfg):
        at = render_admin()
        tx = "\n".join([str(x.value) for x in at.markdown]
                       + [str(x.value) for x in at.caption]
                       + [str(x.value) for x in at.success]
                       + [str(x.value) for x in at.error])
        ck("会话签名密钥" in tx, "后台：有密钥诊断区块")
        _persistent = not (auth.JWT_SECRET_SOURCE.startswith(auth.REJECTED_PREFIX)
                           or auth.JWT_SECRET_SOURCE == "ephemeral-random")
        if _persistent:
            ck("持久化密钥" in tx, "后台：密钥为持久化时显示「重启不会掉登录」")
            ck("进程级随机" not in tx, "后台：密钥为持久化时不再报掉登录")
        else:
            ck("进程级随机" in tx, "后台：密钥非持久化时明确警告会掉登录")

        # 另一条分支必须单独造出来验：本机已经是持久化密钥，上面那个 else
        # 在当前环境**跑不到**——「没跑到」不等于「通过」。这里把来源伪造成
        # 「拒用泄露旧值」，逼出告警分支。
        with patched(auth, "JWT_SECRET_SOURCE",
                     f"{auth.REJECTED_PREFIX} from st.secrets[auth].jwt_secret)"):
            at2 = render_admin()
            tx2 = "\n".join([str(x.value) for x in at2.markdown]
                            + [str(x.value) for x in at2.error]
                            + [str(x.value) for x in at2.caption])
            ck("进程级随机" in tx2, "后台（伪造旧密钥）：警告会掉登录")
            ck("jwt_secret" in tx2, "后台（伪造旧密钥）：给出具体修法（改哪个字段）")
            ck("持久化密钥" not in tx2, "后台（伪造旧密钥）：不再误报「不会掉登录」")
    # 密钥本身绝不能出现在页面上
    with admin_env(ok_cfg):
        at = render_admin()
        dump = "\n".join(str(getattr(x, "value", "")) for x in at.markdown)
        ck(auth.JWT_SECRET not in dump, "后台：页面上绝不出现密钥原文")


def main() -> int:
    """可选参数：只跑指定的测试函数名（造错反验用，避免每个用例都跑整套渲染）。"""
    only = set(sys.argv[1:])
    all_fns = [test_truthy, test_safe_url, test_read_paths, test_save,
               test_read_budget, test_app_render, test_admin_render]
    if only:
        unknown = only - {f.__name__ for f in all_fns}
        if unknown:
            print(f"!! 未知的测试函数：{sorted(unknown)}")
            return 2
    for fn in all_fns:
        if only and fn.__name__ not in only:
            continue
        print(f"\n--- {fn.__name__} ---")
        fn()
    print("\n" + "=" * 60)
    print(f"共 {N} 项，失败 {len(FAILS)} 项")
    for f in FAILS:
        print("  ❌", f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
