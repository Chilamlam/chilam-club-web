"""跑批看门狗：检查「今天的数据是否已落盘」，没落就用 workflow_dispatch 补跑。

为什么需要它
------------
GitHub Actions 的 `schedule` 事件是 **best-effort**，官方文档原文：
「The schedule event can be delayed during periods of high loads ...
 In the worst case, the workflow may not run.」
即高负载时排队，队列 TTL 超时后**整条派发被丢弃**——不是延迟执行，是根本不创建 run。
被丢弃时 GitHub 不发邮件、不进状态页、Actions 列表里没有任何痕迹，
表现就是「网站数据停在前一天，而我看不到任何失败记录」。

本仓库实测（2026-08 全月派发延迟）：
    08-22 ~ 08-26   +10~20 分钟      （正常抖动）
    08-27           +9.5 小时
    08-28           +12.0 小时
    08-29 起        +1~4 小时不等
    08-31           19:37 那条彻底没派发（等到 22:00 仍无 run）
19:37 / 22:20 这种「奇数分钟」已经是官方推荐的避峰做法，仍然挡不住平台级拥塞。

所以判据不能是「cron 有没有触发」，而必须是**结果导向**：
今天的 data/ 里到底有没有今天的数据。没有就补跑，管它 cron 去哪了。

用法
----
    python tools_gh_watchdog.py            # 检查 + 需要时补跑（默认）
    python tools_gh_watchdog.py --check     # 只检查不补跑，退出码表达结论
    python tools_gh_watchdog.py --force     # 无条件补跑

退出码
------
    0  今日数据已就位（或补跑已成功派发）
    1  今日非交易日，无需跑批
    2  数据缺失且补跑派发失败（需人工介入）
    3  用法错误 / 取不到凭据

设计约束
--------
* 不 import streamlit（要能在纯命令行 / 计划任务里跑）。
* token 从 remote.origin.url 解析，**绝不回显明文**。
* 数据新鲜度一律读**远端 raw**，不读本地文件——本地可能落后于远端，
  用本地判断会误以为「没跑」而重复补跑。
* 判「今天」用北京时间。跑批产物的 date 字段是 CST 口径。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "Chilamlam/chilam-club-web"
API = "https://api.github.com"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
WORKFLOW = "daily_update.yml"

CST = dt.timezone(dt.timedelta(hours=8))

# 判据取「必然每个交易日都会变」的产物。
# 只看一两个会漏判：2026-09-01 连板天梯与收盘摘要都是当日新数据，
# 而 strong_stocks.csv 停在 08-31（RPS 跑批连续三班扑空），看门狗照样判「已就位」。
# 教训是判据必须逐产物覆盖，不能用「相邻产物是新的」推断整批都新。
#
# 第四个字段是取日期的方式：
#   "json:<key>"  —— JSON 文档取该键
#   "csv:<列名>"   —— CSV 取该列最后一行（RPS 是中文列名，突破池是 update_date，
#                     两张表列名不同，写死单一列名会静默漏判）
PROBES = [
    ("连板天梯", "data/limit_ladder.json", "json:date"),
    ("收盘摘要", "data/digest/latest.json", "json:date"),
    ("RPS 强势股", "data/strong_stocks.csv", "csv:更新日期"),
    ("突破池", "data/breakout_stocks.csv", "csv:update_date"),
]


def _token() -> str:
    url = subprocess.check_output(
        ["git", "config", "--get", "remote.origin.url"], text=True
    ).strip()
    m = re.search(r"https://([^@/:]+)[:@]", url)
    if not m:
        print("[ERR] remote.origin.url 里没有内嵌凭据，无法取 token")
        raise SystemExit(3)
    return m.group(1)


def _get(url: str, tok: str | None = None, raw: bool = False):
    r = urllib.request.Request(url)
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    r.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (body if raw else json.loads(body))
    except urllib.error.HTTPError as e:
        return e.code, {"raw": e.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return 0, {"raw": f"{type(exc).__name__}: {exc}"}


def _norm_date(v) -> str:
    """产物里的 date 字段有三种写法：20260831 / 2026-08-31 / 2026-08-31 22:05:08。

    统一压成 YYYYMMDD 再比。写死单一格式会让「格式不同」被误判成「数据过期」，
    进而每小时空跑一次补跑——这类误判比漏判更烦人。
    """
    s = str(v or "")
    digits = re.sub(r"\D", "", s)[:8]
    return digits


def is_trading_day(d: dt.date) -> bool:
    """周末必然不是交易日；法定节假日本脚本不判。

    宁可在节假日空跑一次（Actions 免费、脚本内部自己会判无新数据），
    也不要引一张需要年度维护的假日表——过期的假日表会在明年悄悄漏掉整个假期。
    """
    return d.weekday() < 5


def _extract_date(body: str, spec: str) -> str:
    """按 spec 从远端文件正文里取日期字符串。

    不引 pandas：本脚本要能在只有 stdlib 的环境（计划任务、纯净 runner）里跑。
    CSV 取「最后一行」而非第一行，与 run_daily.py 的 FRESHNESS 口径保持一致。
    """
    kind, _, key = spec.partition(":")
    if kind == "json":
        return _norm_date(json.loads(body).get(key))
    if kind == "csv":
        rows = list(csv.DictReader(io.StringIO(body)))
        if not rows:
            raise ValueError("CSV 无数据行")
        if key not in rows[-1]:
            raise KeyError(f"CSV 缺列 {key}（实际列：{','.join(rows[-1].keys())[:80]}）")
        return _norm_date(rows[-1][key])
    raise ValueError(f"未知取值方式 {spec!r}")


def check_freshness(today: str) -> tuple[bool, list[str]]:
    lines = []
    ok = True
    for label, path, spec in PROBES:
        st, body = _get(f"{RAW}/{path}", raw=True)
        if st != 200:
            lines.append(f"  {label}: 取不到远端文件 HTTP={st}")
            ok = False
            continue
        try:
            got = _extract_date(body, spec)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {label}: 解析失败 {type(exc).__name__}: {exc}")
            ok = False
            continue
        fresh = got == today
        lines.append(f"  {label}: {got} {'✅' if fresh else f'❌ (期望 {today})'}")
        ok = ok and fresh
    return ok, lines


def dispatch(tok: str) -> bool:
    url = f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches"
    data = json.dumps({"ref": "main", "inputs": {"backfill_days": "0"}}).encode()
    r = urllib.request.Request(url, data=data, method="POST")
    r.add_header("Authorization", "Bearer " + tok)
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=45) as resp:
            # dispatches 成功返回 204 No Content，没有响应体。
            return resp.status == 204
    except urllib.error.HTTPError as e:
        print(f"[ERR] dispatch 失败 {e.code}: {e.read().decode('utf-8','replace')[:200]}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[ERR] dispatch 异常 {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    argv = sys.argv[1:]
    check_only = "--check" in argv
    force = "--force" in argv

    now = dt.datetime.now(CST)
    today = now.strftime("%Y%m%d")
    print(f"[看门狗] 现在 {now:%Y-%m-%d %H:%M} CST，检查目标日 {today}")

    if not force and not is_trading_day(now.date()):
        print(f"[跳过] {now:%m-%d} 是{'周六' if now.weekday() == 5 else '周日'}，非交易日")
        return 1

    fresh, lines = check_freshness(today)
    print("[远端数据新鲜度]")
    for ln in lines:
        print(ln)

    if fresh and not force:
        print("[结论] 今日数据已就位，无需补跑")
        return 0

    if check_only:
        print("[结论] 今日数据缺失（--check 模式，不补跑）")
        return 2

    print("[动作] 数据缺失，触发 workflow_dispatch 补跑")
    if dispatch(_token()):
        print("[OK] 已派发（204）。全量约 12~18 分钟，完成后 Actions 会自动提交 data/")
        return 0
    print("[FAIL] 派发失败，需人工介入")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
