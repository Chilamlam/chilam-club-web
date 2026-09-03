# chilam-club-web 长期记录（叙述细节见 .workbuddy/memory/YYYY-MM-DD.md）

## 项目
Streamlit Cloud 投资驾驶舱+会员付费，`Chilamlam/chilam-club-web`。Streamlit(`>=1.49,<2.0`)+Plotly/直连HTTP/Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加。跑批 CST 19:37 主/22:20 兜底。
分层：计算层 `tech_analysis`·`scorecard`·`sentiment`·`digest`·`sector_rotation`（**不 import streamlit、不联网**，可脱 runtime 单测）/ 展示 `page_*.py` / 跑批 `daily_*.py` / 自检 `tools_probe_*.py`（改哪层跑哪个探针）。
**多页路由只认 `app.py`+`pages/`**，其余 `.py` 是模块；`pages/` 全员可见 → 页首 `is_logged_in`+`is_admin` 双校验才是真门禁。
自检解释器 `~/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`；pip 加清华源。

## Git / 部署
- **线上是两个仓库**：业务 `Chilamlam/chilam-club-web`（Streamlit Cloud）；域名壳页 `Chilamlam/chilam.club`（GitHub Pages，只有 CNAME+index.html）。`chilam.club`→301→`www.chilam.club`→iframe→`chilam-club-app.streamlit.app`。**排线上故障先确认链路属于哪个仓库**，只翻业务仓永远查不到白屏。
- **`?embed=true` 不是 app 本体**，是 Streamlit Cloud 的嵌入包装页，它把内层真 app 的 iframe 钳到 **150px**（父 `DIV._stateContainer_*` height:150px）→ 整页只剩一条灰边=用户眼里的「空白页」。官方 `30days.streamlit.app` 同样中招 → 平台侧行为，状态页仍显示 All Systems Operational（等修没时间表）。**壳页 iframe 必须写 `…/~/+/?embed=true`**（app 本体路径，官方 app 上同样有效）；`/~/+/` 属内部路径非公开 API，平台再改会二次失效，彻底免疫要靠 302 跳转（代价：地址栏丢自有域名）。
- **`data/` 归 Actions 云端所有**；push 前 fetch+rebase；**严禁强推 main**。`rejected (fetch first)`=远端有新提交，非传输故障。
- **本机 `env` 静默吞命令**（判据：零输出且 exit 0）→ 用 `/usr/bin/env`。**推送成功只认远端 sha**。
- **`github.com:443` 间歇不可达而 `api.github.com` 稳定**（curl 通≠git 通）→ 推送走 `tools_gh_put_via_gitdata.py --msg "$(git log -1 --format=%B <sha>)" <路径...>`（多文件一 commit，`--rm` 删，token 在 remote url 用户名位，CRLF→LF 内置）。API commit 与本地 **tree 同 sha 不同** → 本地 `ahead 1`，fetch 通后 `git pull --rebase` 快进，**别强推「对齐」**。
- 外呼一律 curl（urllib 易被 TLS 打断）；`http.version=HTTP/1.1` 已进 config。判落后用 API `/commits/main`+`git hash-object` 比 blob sha（`rev-parse origin/main` 只是缓存）。
- **重试循环别写 `if cmd | tail -2`**（退出码取 `tail` 恒 0 → 首次即 break）。
- PAT 只 repo scope → **不改 yml**（逻辑在 run_daily.py，secrets 走 ALL_SECRETS）。dispatch body `{"ref":"main","inputs":{"backfill_days":"40"}}`。
- `.git` 重损恢复：备份→浅 clone→**tar** 复制→`reset --mixed HEAD`→`fetch --unshallow`；**`update-ref refs/remotes/*` 本机静默失效** → 手写 `.git/refs/remotes/origin/main` 再复核。
- 前端 10 分钟缓存。**提示紧跟 `st.rerun()` 会消失** → 走 session_state flash。

## 自检铁律（最高优先级）
- **严禁占位数据/前端硬编码字面量**；必含新鲜度断言。**永不失败的断言=没有断言**，必须造错反验。反验目录别用 `/tmp`（Windows→`\tmp`）。
- **反向验证七铁律**：①造错本身先验有效（smartbox 忽略未知 URL 参数 → 须换不存在的 host）；②探针失败只走 `bad()` 唯一出口（自己 print FAIL 会漏抓）；③断言别被无关数据喂饱（PRESETS 本就有「中国稀土」按钮 → 搜索挂了照样过）；④别 monkey-patch 整函数（那是换实现）→ 改源码字符串，锚点先验唯一、失配即报「造错未生效」；⑤断言要放在**能验出它**的环境（stub 掉 `cache_data` 后「空结果不缓存」恒真；`end` 用固定过去日期验不出「end=当天才丢末根」）；⑥按 tag 跑子集时 tag 写错=零用例=fail 0 → 须判「没抓住」；⑦**参照物不能与被测对象共享故障模式**（拿「同一函数的短区间」当参照，造错后两边一起错、对比恒等=假断言）→ 参照须独立通道，通道自己挂了报「本次什么都没验到」而非 PASS。
- **元断言**：断言旁要有「确实进入被测路径」的守卫（长区间须 >2800 根才算触发分段+补尾；「有结果须命中缓存 1/3」；两页壳须出现 2 个 radio）。**跨站 iframe 场景须真的出现 OOPIF；模拟隐私模式须真的观测到 `blockedCookies>0`**，否则报「本次什么都没验到」。
- **修 UI 类线上问题要做 A/B 双向反验**：同一套浏览器+同一套判据同时测「线上现状」与「修复版」，**旧的必须 FAIL、新的必须 PASS**；旧的也 PASS 就说明判据抓不住该故障（假断言），结论作废。判据不能只判「有没有渲染」，还要判**量**（app 高度 ≥ 视口 80%），否则 150px 灰条能蒙过 hasApp。放宽判据后必须重跑造错组。
- `AppTest` 可**无头渲染页面**（`tools_probe_live_quote_page.py`/`tools_probe_fibonacci_page.py`）。断言落在**页面对用户的承诺**（caption「共 N 根」「实际区间」）而非渲染细节；临时文件写系统 temp。**AppTest 证不到线上白屏**——它只验 Python 侧，白屏可能全在部署链路/平台侧。
- **查线上页面须用真实浏览器取证**：`chrome --headless=new --remote-debugging-port=9222` + 自写 CDP 驱动（`Target.setAutoAttach flatten=True` 拿子 session、`Runtime.evaluate` 逐 frame 读）。agent-browser 的 open 会挂起（实测 12 分钟无输出）、`--dump-dom` 对跨站 iframe 无效。**截图不可靠**：`captureBeyondViewport=True` 时跨进程 iframe 不入图，会假报纯白 → 结论落在 DOM 数值（innerText 长度、clientHeight）上。
- **测量工具自己会骗人**：Git Bash `curl -o /dev/null` 报 `exit=23`+`size=0`（连 api.github.com 也中招），据此误判「腾讯 fqkline 下线」——实际 15/15 次 200。**判「服务挂了」前换一种测法**。
- **缺失显示 `—` 绝不补 0**；fixture 单位与生产一致。**静默失败最致命**，归因错误的报错比未知错误更贵。依赖外部接口的断言失败要**重试+带外复核**，分开给「通道抖动」与「逻辑失效」结论。
- **None（取数失败）≠ `[]`（确实没有）**；PostgREST PATCH 零行返回 `[]` 而 `[] is not None` 为真 → 必须显式判空列表。**改返回值语义时 None/空/有值三出口一次列全**。
- 等待态≠错误态；顶层成功≠单个成功（WxPusher 逐 UID code；Server酱 200 也可能额度耗尽）。失败文案须说清「已发生什么+下一步千万别做什么」。
- 一份规则两处实现必漂移 → **修一个坑先横向 grep 同类实现**，且**必须按规则本身 grep**（`startswith(("6","5","9"))`、`fqkline`）**而不是按调用入口 grep**：只搜 `text_input`/`resolve_symbol` 会漏掉「入口正常但下游自己抄了一份取数」的页面 —— 我据此误判 `page_watchlist.py` 无坑，实测它 `000905` 静默取成「厦门港务」、`bj920982` 老 fqkline 只 1 根却报「K 线不足 30 根」（归因错误的报错）。**沪深撞号+北交所端点这一份规则目前三处实现**：行情页与黄金分割页已收口，`page_watchlist.py:46 _tx_code()` / `:279 _fetch_daily_kline()` **仍未修**。
- **下游「保护逻辑生效」证不到上游没出事** → 查静默失败须回溯最上游。**AST 断言必须认对人**（「第一个 `st.info`」抓到的是早退分支）。

## 跑批可靠性（09-01 四班全灭换来的）
- **日期锚定以「数据是否已发布」为准，不以交易日历为准**。cron 延迟 1.2h~9.2h，过零点后日历首位=当天而行情未发布 → 整步空转。范式 `daily_breakout.py::get_latest_trade_date`；`MAX_ANCHOR_BACK=3` 逐日探测，超限报错不静默用旧数据。**回退锚定日后各窗口对照日必须 `cal[anchor_i+n]`**，否则窗口悄悄少一天。
- **写盘日期必须锚定交易日，绝不用 `datetime.now()`**（曾把周六写进 `strong_etfs.csv`）。
- **装饰性字段绝不挡在主产物前**（判据：缺了它主产物是否失去意义）。两阶段落盘，装饰字段带独立时间预算；线程池 `shutdown(wait=False, cancel_futures=True)` 且不能用 `with`。
- **退出码必须如实+监控判据逐产物覆盖+结论由机器给**。`main_job` 恒 return None=exit 0=汇总 `[OK]`；「打印日期让人肉眼比对」不算监控。`check_date_consistency()` 取批内最大日期当基准（**刻意不引交易日历**），落后者点名+`::warning::`。
- `run_daily.py` 逐步 try 后继续、恒 exit 0、每步独立 timeout；**新增跑批只改 `STEPS` 表**。cron best-effort → `tools_gh_watchdog.py` 按远端 `data/` 有无当日数据判缺失再 dispatch。
- **cron 派发是「被丢弃」而非「延后」**：09-02 实测 07-29~09-02 计划 72 条只产生 40 条（**丢 44%**，不依赖归因的下界；GitHub API 不暴露 run 是哪条 cron 触发的，按「最近计划时刻」硬归因会张冠李戴，别据此说「某班失灵」）。延迟 08-26 前 +0.2~2.3h，08-27 骤增到 +9.5h、08-28 +12.0h，之后长期 +3.6~6.5h。被丢弃时**无邮件、不进状态页、Actions 列表无痕迹** → 现象=数据停更且查不到失败记录。`tools_gh_watchdog.py` 逻辑齐备但**没有任何东西定时调用它**，这才是停更事故的真缺口。
- **判数据新鲜度只认文件内的交易日字段**，别看 commit 时间（只能证明写过，不能证明写的是今天）。且**日期正则必须限定列**：扫全文取最大日期会把 `20271027` 这类 ID 数字认成 2027-10-27，直接产出假 PASS。

## 技术坑位
- pandas 3.x：禁 `s.replace(0,pd.NA)`（退化 object 抛错），「0当缺失」用 `s.where(s>0)`；外部数值列先 `pd.to_numeric(errors="coerce")`。
- 分时图：源返回非交易时段时间戳（A股15:06~15:30等）→ `_align_to_timeline()` 吸附；跨零点 +1440。
- 技术分析：线段与「同型」邻居比较；中枢 ZG/ZD 前三段定死；ABC 校验 C∈A~B。
- **Streamlit widget key 四铁律**：①**带 key 的 widget rerun 时用自己存的旧值、忽略 `value=`** → 搜索按钮改了 session_state 也刷不进输入框（点了没反应）；要让 `value=` 生效就**别给 key**。②**key 写死会跨标的串值** → `number_input` 的 key 必须绑「标的+区间」，否则 601869(≈400) 换 600519(≈1300) 后基准价还是上一只的、图面看不出异常。③**同组件多页复用必须传 `key_prefix`/`key_tag`/`state_key` 隔离**，否则两页同进程渲染抛 `StreamlitDuplicateElementKey`，单跑任一页都不暴露。④`date_input` 别同时给 `value=` 和 session_state（触发警告且探针无法注入）→ 只用 session_state 存默认值。
- 前端 UI：禁 `st.image(use_column_width=)`→`ui_compat.image_stretch()`；禁 `components.v1.html`→`ui_compat.html_embed()`；排查 `tools_check_st_api.py`。
- 本机跑 `python -c` 别在字符串里写裸 `\u`（转义失败报 unicode error）→ 落成临时 `.py` 文件。

## 分册索引（主文件有注入上限，明细已拆出，别再往主文件塞长内容）
- **外部数据接口全部细节** → `.workbuddy/memory/topics/data-apis.md`
  （腾讯日K区间取数的 800 悬崖 / end=当天末根丢失 / 分段回补、沪深撞号、北交所 newfqkline、
  名称搜索双源 smartbox+东财、全球资产代码、东财概念板块字段）——**动这些之前必须先读**。
- **付费闭环 / 推送通道 / 绩效统计（beta+有效样本量）/ 合规文案** → `.workbuddy/memory/topics/monetization.md`
