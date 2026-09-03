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
    python tools_gh_watchdog.py --force     # 无条件补跑（同时跳过交易日与时间窗守卫）

退出码
------
    0  今日数据已就位，什么都没做
    1  今日无需跑批（非交易日，或还没到跑批该产出的时点）
    2  数据确实缺失（--check 模式），或补跑派发失败（需人工介入）
    3  用法错误 / 取不到凭据
    4  数据缺失 → 补跑已派发成功，**结果尚未验证**（12~18 分钟后需再查一次）
    9  取数通道失效，本次什么都没验到 —— **绝不据此补跑**

为什么 4 要和 0 分开：「一切正常」与「刚才是坏的、我派了一次补跑、还不知道有没有救回来」
是两个完全不同的状态。旧版都返回 0，于是接监控的一方无法区分
「今天很太平」和「今天出过事、且修复结果无人复核」——
2026-09-01 的 RPS 连续三班扑空正是这种形状：补跑派发成功、退出码 0，
数据却依旧停在前一天。派发成功不等于数据落盘。

两条铁律（2026-09-03 实测踩出来的，改本文件前先读）
--------------------------------------------------
1. **取不到 ≠ 数据旧。** 09:55 实测：urllib 读四个产物全 HTTP=0、整趟耗时 4m13s，
   而同一时刻 curl 落盘四个全 200。**40 分钟后同一段 urllib 又 3/3 全通** ——
   所以这是**间歇性**故障，不是「urllib 对 raw 域名不可用」这种固有属性，
   也正因为它间歇，才最容易骗人：偶尔一次全灭就足以让判据翻转。
   旧版把 HTTP=0 直接算进 `ok = False`，于是通道一抖就报「今日数据缺失」，
   默认模式下会据此派发一次 12~18 分钟的全量补跑 —— 拿通道故障当数据故障。
   现在：curl 为主通道、urllib 为备用，两条都失败才算这个文件取不到；
   一个产物都没读出日期则判 verdict="blind" 返回 9，
   宁可说「什么都没验到」，也不给一个归因错误的结论。
2. **「今天还没有今天的数据」在跑批前是正常态。** 主 cron 19:37，
   早上 10:00 或过零点后 00:30 去查，必然查不到当天数据。
   旧版此时报「缺失」，若把看门狗挂成每小时一次，整个白天都在空派补跑，
   而 10:00 派发的跑批因为「行情未发布」只会把昨天的数据再写一遍（见
   daily_breakout.get_latest_trade_date 的锚定规则）—— 纯烧 Actions 额度。
   现在：早于 JUDGE_AFTER（主 cron + 运行时长 + 余量）一律返回 1 不派发。

设计约束
--------
* 不 import streamlit（要能在纯命令行 / 计划任务里跑）。
* token 从 remote.origin.url 解析，**绝不回显明文**。
* 数据新鲜度一律读**远端 raw**，不读本地文件——本地可能落后于远端，
  用本地判断会误以为「没跑」而重复补跑。
* 判「今天」用北京时间。跑批产物的 date 字段是 CST 口径。
* `main()` 的 now / argv 都可注入，否则探针没法验时间窗与决策表。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPO = "Chilamlam/chilam-club-web"
API = "https://api.github.com"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
WORKFLOW = "daily_update.yml"

CST = dt.timezone(dt.timedelta(hours=8))

# 主 cron 时点与全量跑批耗时。判定「今天的数据本该已经有了」的时刻由三者相加得出，
# 写成可读常量而不是硬编码 20:30，是为了改 cron 时不会忘记同步改判定线。
MAIN_CRON_CST = (19, 37)   # .github/workflows/daily_update.yml 主 cron（37 11 * * * UTC）
RUNTIME_MIN = 18           # 全量跑批实测 12~18 分钟，取上限
JUDGE_MARGIN_MIN = 35      # 平台正常排队抖动 10~20 分钟，留一倍余量

_CHANNEL = {"urllib": 0, "curl": 0, "fail": 0}  # 供元断言与日志用，不参与判据

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


def _get_via_curl(url: str) -> tuple[int, str]:
    """备用取数通道：curl 落盘后再读。

    为什么需要第二条通道：本机 urllib 对 raw.githubusercontent **间歇性**整体抽风
    （2026-09-03 09:55 四个产物全 HTTP=0 且整趟 4m13s，10:35 同一段代码又 3/3 全通，
    同期 api.github.com 一直 4/4 正常）。间歇故障最会骗人 —— 偶尔一次全灭
    就足以让「有没有数据」的判据整体翻转。只有一条通道时，
    「取不到」根本无法与「远端真的没有」区分开。

    为什么必须落盘：本机 `curl -o /dev/null` 会假报 exit=23 / size=0
    （连 api.github.com 也中招），拿它判成败会得出「服务挂了」的错误结论。
    写进临时文件再读文件长度，是唯一可信的判法。
    """
    fd, path = tempfile.mkstemp(suffix=".wd")
    os.close(fd)
    try:
        p = subprocess.run(
            ["curl", "-sS", "--http1.1", "--max-time", "30", "--retry", "2",
             "-o", path, "-w", "%{http_code}", url],
            capture_output=True, text=True,
        )
        code = (p.stdout or "").strip()
        if p.returncode != 0 or code != "200":
            return 0, f"curl rc={p.returncode} http={code or '?'}"
        with open(path, "rb") as f:
            return 200, f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return 0, "curl 不可用"
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def fetch_remote(path: str) -> tuple[bool, str, str]:
    """取一个远端产物正文。返回 (成功, 正文, 通道说明)。

    双通道串联，**curl 在前**：本项目既有结论是「外呼一律 curl，urllib 易被
    TLS 打断」，2026-09-03 又实测 urllib 四连 HTTP=0 而 curl 四连 200，
    所以主通道给 curl，urllib 退化为备用。顺序反了会白等 4×45s 才拿到结果。
    """
    ok, body = _get_via_curl(f"{RAW}/{path}")
    if ok == 200:
        _CHANNEL["curl"] += 1
        return True, body, "curl"
    st2, body2 = _get(f"{RAW}/{path}", raw=True)
    if st2 == 200 and isinstance(body2, str):
        _CHANNEL["urllib"] += 1
        return True, body2, "urllib(curl 失败)"
    _CHANNEL["fail"] += 1
    return False, "", f"两条通道均失败 curl={body[:40]} urllib={st2}"


def judge_after(now: dt.datetime) -> dt.datetime:
    """今天几点之后才有资格说「数据本该已经有了」。"""
    h, m = MAIN_CRON_CST
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return base + dt.timedelta(minutes=RUNTIME_MIN + JUDGE_MARGIN_MIN)


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


def check_freshness(today: str) -> tuple[str, list[str]]:
    """返回 (verdict, 明细行)。verdict 三取一，**不是布尔**：

        "fresh"  逐产物覆盖检查全部为今日 → 无需补跑
        "stale"  至少一个产物成功取到、且日期落后 → 数据确实缺失，可以补跑
        "blind"  一个都没取到 → 取数通道失效，本次什么都没验到

    为什么必须区分 stale 与 blind：旧版把两者都塞进 `ok=False`，
    通道抖一下就派发全量补跑，是「拿测量工具的故障当被测对象的故障」。
    元断言：verdict=="stale" 必须至少有一个产物真的读出了日期，
    否则说明是通道全灭走错了分支。
    """
    lines: list[str] = []
    got_any = False   # 至少一个产物成功解析出日期（stale 结论的前提）
    all_fresh = True
    for label, path, spec in PROBES:
        ok, body, via = fetch_remote(path)
        if not ok:
            lines.append(f"  {label}: 取不到远端文件（{via}）")
            all_fresh = False
            continue
        try:
            got = _extract_date(body, spec)
        except Exception as exc:  # noqa: BLE001
            # 解析失败是「文件确实取到了但内容不对」，属于真问题，算 stale 而非 blind。
            lines.append(f"  {label}: 解析失败 {type(exc).__name__}: {exc}")
            got_any = True
            all_fresh = False
            continue
        got_any = True
        fresh = got == today
        tag = "✅" if fresh else f"❌ (期望 {today})"
        suffix = "" if via == "curl" else f"  [{via}]"
        lines.append(f"  {label}: {got} {tag}{suffix}")
        all_fresh = all_fresh and fresh

    if not got_any:
        return "blind", lines
    if all_fresh:
        return "fresh", lines
    return "stale", lines


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


def main(argv: list[str] | None = None, now: dt.datetime | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check_only = "--check" in argv
    force = "--force" in argv

    now = now or dt.datetime.now(CST)
    today = now.strftime("%Y%m%d")
    print(f"[看门狗] 现在 {now:%Y-%m-%d %H:%M} CST，检查目标日 {today}")

    if not force and not is_trading_day(now.date()):
        print(f"[跳过] {now:%m-%d} 是{'周六' if now.weekday() == 5 else '周日'}，非交易日")
        return 1

    # 时间窗守卫：跑批还没到该出数的时点，「今天没有今天的数据」是正常态。
    # 少了这一层，把看门狗挂成每小时一次就会整个白天空派补跑，
    # 而收盘前派发的跑批只会把昨天的数据再写一遍（行情未发布 → 锚定回退）。
    line = judge_after(now)
    if not force and now < line:
        print(f"[跳过] 主跑批 {MAIN_CRON_CST[0]:02d}:{MAIN_CRON_CST[1]:02d} CST，"
              f"判定线 {line:%H:%M}，现在还早 —— 当日数据尚未产出属正常")
        return 1

    verdict, lines = check_freshness(today)
    print("[远端数据新鲜度]")
    for ln in lines:
        print(ln)
    print(f"[通道] urllib={_CHANNEL['urllib']} curl={_CHANNEL['curl']} 失败={_CHANNEL['fail']}")

    if verdict == "blind":
        print("[结论] 取数通道全部失效，本次什么都没验到 —— 不补跑（避免拿通道故障当数据故障）")
        return 9

    if verdict == "fresh" and not force:
        print("[结论] 今日数据已就位，无需补跑")
        return 0

    if check_only:
        print("[结论] 今日数据确实缺失（--check 模式，不补跑）")
        return 2

    print("[动作] 数据缺失，触发 workflow_dispatch 补跑")
    if dispatch(_token()):
        print("[OK] 已派发（204）。全量约 12~18 分钟，完成后 Actions 会自动提交 data/")
        print("[注意] 派发成功 ≠ 数据落盘（2026-09-01 的 RPS 就是派发成功但数据仍停在前一天）。"
              "退出码 4 表示「已派发、结果未验证」，请在 20 分钟后再查一次。")
        return 4
    print("[FAIL] 派发失败，需人工介入")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
