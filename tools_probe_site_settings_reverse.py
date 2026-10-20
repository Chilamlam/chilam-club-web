# -*- coding: utf-8 -*-
"""造错反验：站点设置/店铺开关那批断言，是不是真断言。

用法: <stcheck>/python.exe tools_probe_site_settings_reverse.py [编号…]
退出码 0 = 全部造错都被抓住；3 = 存在假断言（改了错却没报红）。

一条永不失败的断言等于没有断言。「店铺入口已隐藏」这类需求的断言尤其容易假：
把整段渲染代码删掉、或配置读失败、或页面根本没渲染，三种坏情况都会让
「页面上没有这个按钮」变绿。所以这里逐条把代码改坏，检验探针能不能发现。

  site_settings.py（逻辑层）
    1) _truthy 改成 bool(value)              → 字符串 "0" 变真，关店变成开店
    2) is_safe_url 恒返回 True               → javascript: 伪协议被放行
    3) 代码默认值改成 True                    → 兜底方向反了（把已关的店露出来）
    4) `if err:` 短路                        → 读失败被当成「没配过」，错误被吞
    5) 去掉伪协议消毒                        → 开关开着就渲染，不管链接是否合法
    6) 开/关写成 '0'/'1'（写反）              → 保存后语义颠倒
    7) 写入失败也报成功                       → 管理员以为存上了，实际没存

  app.py（前台渲染）
    8) 去掉 enabled 判断，无条件渲染          → 关店后入口还在
    9) 文案/链接写死                         → 后台改配置不生效

  pages/admin.py（后台）
   10) 去掉「表未建」分支                     → 管理员看不到建表 SQL，不知为何改不了
   11) 密钥告警条件恒假                       → 掉登录的原因在后台看不见
   12) 告警文案不含「进程级随机」              → 说了问题但没说是这个
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent
PY = sys.executable
PROBE = str(ROOT / "tools_probe_site_settings.py")


def run_probe(funcs: list[str]) -> str:
    p = subprocess.run([PY, PROBE, *funcs], capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", cwd=str(ROOT), timeout=900)
    return (p.stdout or "") + (p.stderr or "")


def mutate(target: str, old: str, new: str, funcs: list[str],
           label: str, expect: str) -> bool:
    """临时改源码 → 跑探针相关函数 → 无条件还原（并校验还原逐字节一致）。

    锚点必须**唯一**：重复锚点会让 replace(…,1) 改到别的位置，
    于是「造错没生效」却可能被当成「断言抓住了」——两个方向都会骗人。
    """
    path = ROOT / target
    backup = path.read_text(encoding="utf-8")
    cnt = backup.count(old)
    if cnt == 0:
        print(f"--- {label}\n    !! 锚点未找到，造错未生效（视为失败）\n")
        return False
    if cnt > 1:
        print(f"--- {label}\n    !! 锚点不唯一（出现 {cnt} 次），造错未生效（视为失败）\n")
        return False
    path.write_text(backup.replace(old, new, 1), encoding="utf-8")
    try:
        out = run_probe(funcs)
        fails = [ln for ln in out.splitlines() if ln.startswith("❌")]
        hit = [ln for ln in fails if expect in ln]
        crashed = "Traceback" in out
        print(f"--- {label}")
        print(f"    FAIL 行数={len(fails)}  命中期望={len(hit)}"
              f"{'  [探针崩溃]' if crashed else ''}  → "
              f"{'反向验证通过' if hit else '!! 断言没抓住 —— 这是假断言 !!'}")
        for ln in hit[:2]:
            print(f"      {ln.strip()[:140]}")
        if not hit:
            for ln in fails[:3]:
                print(f"      （其它 FAIL）{ln.strip()[:140]}")
            if not fails:
                print("      （探针全绿，改动完全没被察觉）")
        print()
        return bool(hit)
    finally:
        path.write_text(backup, encoding="utf-8")
        if path.read_text(encoding="utf-8") != backup:
            print(f"!!!! 还原失败：{target} 与备份不一致，请立即人工恢复 !!!!")


# (文件, 原文, 改成, 只跑这些探针函数, 说明, 期望命中的 FAIL 片段)
CASES = [
    ("site_settings.py",
     '    return str(value).strip().lower() in ("1", "true", "yes", "on")',
     "    return bool(value)",
     ["test_truthy"], "1) _truthy 改成 bool(value) —— \"0\" 会变真", "_truthy('0')"),

    ("site_settings.py",
     '    return u.startswith("http://") or u.startswith("https://")',
     "    return True",
     ["test_safe_url"], "2) is_safe_url 恒 True —— 伪协议放行", "非法链接"),

    ("site_settings.py",
     "SHOP_ENTRY_DEFAULT_ENABLED = False",
     "SHOP_ENTRY_DEFAULT_ENABLED = True",
     ["test_read_paths"], "3) 代码默认值改成 True —— 兜底方向反了", "代码默认值是"),

    ("site_settings.py",
     "    if err:",
     "    if False and err:",
     ["test_read_paths"], "4) `if err:` 短路 —— 读失败被当成没配过", "source=db-read-failed"),

    ("site_settings.py",
     "    if enabled and not is_safe_url(url):",
     "    if False and not is_safe_url(url):",
     ["test_read_paths"], "5) 去掉伪协议消毒", "伪协议链接：即使开关是开的也不显示"),

    ("site_settings.py",
     '        (KEY_ENABLED, "1" if enabled else "0"),',
     '        (KEY_ENABLED, "0" if enabled else "1"),',
     ["test_save"], "6) 开/关写成 '0'/'1'（写反）", "开启写成 '1'"),

    ("site_settings.py",
     "            return False, err, warn",
     "            return True, None, warn",
     ["test_save"], "7) 写入失败也报成功", "写入失败如实返回错误"),

    ("app.py",
     '        if _shop.get("enabled"):',
     "        if True:",
     ["test_app_render"], "8) 前台去掉 enabled 判断，无条件渲染", "前台（关闭）：没有店铺按钮"),

    ("app.py",
     '            st.link_button(_shop["label"], _shop["url"],',
     '            st.link_button("硬编码文案", "https://hard.coded",',
     ["test_app_render"], "9) 前台文案/链接写死", "前台（打开）：文案取自配置"),

    ("pages/admin.py",
     "    if _table_missing:",
     "    if False:",
     ["test_admin_render"], "10) 去掉「表未建」分支", "后台（表未建）：提示表未创建"),

    ("pages/admin.py",
     '    if _jsrc.startswith(auth.REJECTED_PREFIX) or _jsrc == "ephemeral-random":',
     "    if False:",
     ["test_admin_render"], "11) 密钥告警条件恒假", "后台（伪造旧密钥）：警告会掉登录"),

    ("pages/admin.py",
     '        st.error("当前用的是**进程级随机密钥**：服务每次重启（含每次部署）"',
     '        st.error("密钥需要更换"',
     ["test_admin_render"], "12) 告警文案不含「进程级随机」", "后台（伪造旧密钥）：警告会掉登录"),

    ("database.py",
     """        # 这条直接跑在**前台侧边栏的渲染路径**上：网络抖动时若按默认 3 次 × 10 秒重试，
        # 缓存过期后的第一个访客会白卡半分钟。读失败本来就有「隐藏」兜底，不值得。
        retries=1, timeout=SITE_SETTINGS_TIMEOUT)""",
     """        retries=3, timeout=10)""",
     ["test_read_budget"], "13) 站点设置改回默认重试/超时（页面会被拖慢）", "只尝试 1 次"),
]


def main() -> int:
    only = sys.argv[1:]
    cases = [c for c in CASES if not only or c[4].split(")", 1)[0].strip() in only]
    if not cases:
        print(f"!! 没有匹配的用例，可选编号：{[c[4].split(')',1)[0] for c in CASES]}")
        return 2
    ok, bad = 0, []
    for case in cases:
        if mutate(*case):
            ok += 1
        else:
            bad.append(case[4])

    print("=" * 62)
    print(f"造错反验：{ok}/{len(cases)} 项被断言抓住")
    if bad:
        print("以下造错未被抓住（存在假断言，必须修断言或修代码）：")
        for b in bad:
            print(f"  · {b}")
        return 3
    print("全部通过：这些断言确实能抓 bug。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
