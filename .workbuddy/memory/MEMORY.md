# chilam-club-web 项目长期记录

## 项目定位
Streamlit + GitHub Actions + 量化策略的投资驾驶舱与会员服务（部署 Streamlit Cloud，仓库 `Chilamlam/chilam-club-web`）。
技术栈：Streamlit(`>=1.49,<2.0`)+Plotly / Tushare·AkShare·直连HTTP / Gemini / Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加（建表 `init_payments_table.sql`，secrets 需 `[payment]`）。跑批 Actions 主 CST 19:37 / 兜底 22:20，push 失败自动 `pull --rebase` 重试 3 次。

## 功能模块（侧边栏 12 项）
全市场看板（含情绪派生指标区块，排在连板天梯**之前**）/ 收盘摘要 / 实时行情+技术分析 / 强势股(RPS+突破池) / 自选股雷达(我的池子每日复盘) / 战绩回看 / 宏观与股债性价比 / 投机与套利 / 核心龙头雷达 / 投资作业本 / 黄金分割预测 / 使用说明。
分层：计算层 `tech_analysis.py`·`scorecard.py`·`sentiment.py`·`digest.py`（一律不 import streamlit、不联网，才能脱离 runtime 单测）/ 展示层 `page_*.py` / 跑批层 `daily_*.py`（10 个）/ 自检 `tools_probe_*.py`。

## Git / 部署铁律
- **`data/` 归 Actions 云端所有**，本地 push 前必须 `git fetch origin && git pull origin main --rebase`。**严禁强推或重写 main 历史**（8/28 曾强推抹掉 8/25~8/27 三次数据提交）。
- **数据停更排查序**：① `git log --all --grep="Auto-update"` 为空 = 历史被重写（非 Actions 失败）；② 查 API `/events` 的 PushEvent `before→head` 链是否被人工推送断开；③ `git checkout <被抹掉sha> -- data/` 恢复。
- **PAT 只有 `repo` scope（`X-OAuth-Scopes: repo`），改不了 `.github/workflows/*.yml`，四条路全实测封死**：`git push` → `remote rejected ... without 'workflow' scope`（唯一给明确原因的）；Contents API PUT → **404**；Git Data API `POST /git/trees` → **404**（blob 能建成功，tree 阶段被拦）；同两条 API 提交**普通文件**均 200/201 成功。**改工作流只有：① GitHub 网页端编辑；② 换带 `workflow` scope 的 PAT。** 无法推送的提交用 `git reset --soft HEAD~1` 撤回，工作树改动保留。
- **备用推送通道**（git 传输不可达时推普通文件）：`tools_gh_put_file.py`（Contents API）、`tools_gh_put_via_gitdata.py`（blob→tree→commit→ref），均从 remote url 取 token 且不回显。
- **git 传输不稳的根因是 HTTP/2 被中间设备打断**：verbose 显示 `close_notify` 后重连 `443 Timed out`。加 `-c http.version=HTTP/1.1` 立刻可用，已写进本地 config（另配 `http.postBuffer 524288000`）。**`api.github.com` curl 200 不代表 git 传输通，两者分开判断。**
- **`.git` 损坏分两级**。轻：`refs/` 消失（8/28、8/29 各一次）→ `mkdir -p .git/refs/{heads,tags,remotes/origin}` + 从 `.git/FETCH_HEAD` 取 sha `printf >` 手写 loose ref（`update-ref`/`pack-refs` 此时不报错也不生效）。重：**`objects/pack/` 只剩 `.idx` 没有 `.pack`**（8/29 晚，`.git` 掉到 480K，所有历史对象 not found）→ 别修 refs，直接重建：① `cp -r repo/. _backup/` 后删 `_backup/.git`；② 远端 `clone --depth 1 --no-checkout` 到临时目录只取对象库；③ `mv repo/.git _broken_git_<ts>` + `cp -r _tmp/.git repo/.git`；④ **`git reset --mixed HEAD` 重建 index 但不动工作区**（漏这步 status 会把所有文件报成 deleted）；⑤ `fetch --unshallow` 补全历史；⑥ `git checkout -- data/` 丢弃本地 data 改动。
- **连接常被重置**，fetch/push 写重试循环（实测第 4 次才通）；推完必须用 API `/commits/main` 核验远端 head。
- **手动触发跑批**：`POST /repos/Chilamlam/chilam-club-web/actions/workflows/daily_update.yml/dispatches`，body `{"ref":"main","inputs":{"backfill_days":"40"}}`，Bearer token 从 `git config --get remote.origin.url` 提取（**不回显明文**），返 204，全量约 12 分钟。
- **cron 不可信**：8/28 两条 cron 均未派发。数据没更新先看有没有运行记录，再看是否失败。
- **新增 `daily_*.py` 必须同步加 workflow step**，每步带 `continue-on-error: true`（否则任一异常中断 job，当日全量数据都不落地）。步骤顺序有依赖：`sentiment` 在 `market_monitor` 后、`scorecard` 在所有榜单后、`digest` 在前两者后。
- **前端 10 分钟缓存**（`@st.cache_data(ttl=600)`）：推完数据页面最多滞后 10 分钟，要立刻看需右上菜单 Clear cache / Rerun；部署 webhook 也不可靠，必要时 `share.streamlit.io` → Reboot app。
- `data/*.json` 用 `head`/`cat` 看中文是乱码（Git Bash 按 GBK 解 UTF-8），非损坏；校验用 `json.load(open(..., encoding='utf-8'))`。

## 假数据铁律（最高优先级）
- **严禁在 `data/` 写占位数据**（8/28 `limit_ladder.json` 曾手工写 8 只占位、total_count 虚标 28）。**同样适用于前端硬编码字面量**（8/29 宏观页 6 张卡片写死纳指 19845，实际 29433，差 9500 点）。凡展示数值必须当场接真接口 + 写自检；宁可显示「暂无数据」。
- **自检必须含时间戳新鲜度断言**（行情时间在最近 5 天内）——只校验「非空、格式正常」拦不住硬编码值。
- **禁止「能选但没数据」**：页面选项必须与实测可用通道一致。
- **缺失即留空**：数值显示 `—`，绝不补 0（补 0 会显示成「平盘」误导）。

## 关键数据接口（纯 stdlib）
- **涨停池/连板天梯**：`push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date=YYYYMMDD`。字段 `c`码 `n`名 `hybk`行业 `lbc`连板 `fbt`首封(92500→09:25) `fund`封单 `zbc`炸板 `data.tc`总数。
- **指数 PE-TTM**：`csindex.com.cn/csindex-home/perf/index-perf?indexCode=000300&...`，需 `Referer: https://www.csindex.com.cn/`，PE 字段名 `peg`；000905/000688 为空。
- **中国 10Y 国债**：东财 `datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD&sty=ALL&st=SOLAR_DATE&sr=-1&token=894050c76af8597a853f5b408b759f5d&ps=500&p=N`，字段 `EMM00166466`，需分页。
- **东财 `push2`/`push2his` 已放弃**：高频探测触发 IP 级限流后持续 `RemoteDisconnected` 逾 10 分钟。当场不可验证的接口不能做线上依赖。
- **腾讯行情号段前缀**：`bj` 北交所 92xxxx/43x/83x/87x、`sh` 6/5/9 开头、`sz` 其余；沪深300 必须写全 `sh000300`。

## 全市场行情通道（`page_live_quote.py`，穷举实测 0 失败）
- A股/ETF/指数：腾讯 `qt.gtimg.cn/q=` 报价、`minute/query` 分时、`kline/mkline` 分钟、`fqkline` 日周月
- 港股(含 hkHSI)：腾讯 `hk00700` + 分时 + `hkfqkline`；**分钟K 无公开源**
- 美股个股：腾讯 `usNVDA` 报价 / 新浪 `US_MinlineService`+`US_MinKService` / 腾讯 `usfqkline` **须带 `.OQ`/`.N`**
- 美股指数：腾讯 `usIXIC` / 新浪 `symbol=.IXIC`（**带前导点**）/ 腾讯 `usIXIC`（**不带后缀**）
- 国际商品：新浪 `hf_GC` + `getGlobalFuturesMinLine`，日K 后本地 `resample`；**分钟K 无源**
- **腾讯 `"month"` 也以 m 开头**，判断分钟线须用 `m5/m15/m30/m60` 白名单，否则月K 发到 `mkline` 恒为空。`fqkline` 的 `limit` 上限约 300。
- `MARKET_PERIODS`/`MARKET_LIMIT_NOTE` 是周期选项唯一来源，改后跑 `tools_probe_quote_api.py`。
- **`HF_` 前缀区分撞名商品**：`HSI/C/S/W/CT/CAD` 与股票 ticker 重名，商品写 `HF_HSI`。
- 自检解释器：`/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`（主解释器无 pandas/plotly）。

## 全球核心资产代码（`page_macro_erp.py`，勿再试错）
纳指100=腾讯 `usNDX`（`usIXIC` 是综指，差 3000 点）/ 标普=`usINX`（`usSPX` 空）/ 道指 `usDJI` / 恒生 `hkHSI` / A50=新浪 `hf_CHA50CFD`（`hf_CN` 空）/ 现货金=`hf_XAU`（`hf_GC` 是纽约期金差 ~50）/ WTI=`hf_CL` / 伦铜=`hf_CAD`（`hf_HG` 是美铜单位不同，`hf_ZSD` 是伦锌）/ 离岸人民币=`fx_susdcnh`。**美元指数与美债10Y 无免费源**，`hf_DX`/`hf_DINIW`/`usTNX`/`hf_US10Y` 全空，不要再试。
- 新浪 `hf_*` 的 `[7]` 是昨收（日K交叉核验过），涨跌幅须自算；`fx_*` 的 `[8]` 最新价、`[10]` 涨跌幅。
- **时间戳三家格式不统一**（腾讯美股 `-`、腾讯港股 `/`、新浪 `-`），正则须写 `[-/]`。
- 自检 `tools_probe_macro_assets.py`（5 条：price 正有限 / `|pct|≤30%` / 5 天内 / 无科学计数法 / 源码无硬编码价格残留）。

## pandas 3.x 兼容（Cloud 已是 3.x）
- **禁止 `s.replace(0, pd.NA)`**：会把 float64 退化成 object，后续 `.astype(float)` 遇 `NAType` 抛 TypeError。「把 0 当缺失」统一写 `s.where(s > 0)`。
- `astype` 一律 `astype("float64")`；外部数值列先过 `pd.to_numeric(..., errors="coerce")`。

## 分时图时间轴对齐
行情源会返回非交易时段时间戳（A股 `15:06~15:30`、港股恒指 `18:31`、港股 CAS `16:08`），直接 merge 到 category 轴全变 NaN → 曲线只画一截。统一 `_align_to_timeline(df, timeline)` 吸附到「不晚于它的最后一个刻度」，同刻度 `drop_duplicates(keep="last")`；跨零点市场先把小于开盘分钟数的时间 +1440。港股 X 轴止于 `16:00`。校验：对齐后 time 必须 100% ∈ timeline。

## 技术分析实现铁律
- **线段识别必须与「同型」邻居比较**（顶比顶、底比底）。笔端点天然顶底交替，「比左右邻居更极端」恒成立 → 线段数恒等于笔数。
- **中枢 ZG/ZD 由前三段一次确定后固定不变**，延伸只判重叠。收窄得到的是公共交集不是中枢（曾出现宽 0.03% 跨 16 段）。保留 0.3% 宽度下限与 `_PIVOT_MAX_LEGS=9` 封顶。
- **ABC 目标位必须校验 C 落在 A~B 之间**，否则是同向延伸不是回撤，会算出荒谬 XOP。
- **价格格式化禁用 `:.4g`**，走 `_n()` 分档，否则指数显示成科学计数法。
- 口径必须透明：线段是「对笔端点再做分型」的工程近似，中枢是区间重叠法，均非原著严格定义。改后跑 `tools_probe_tech_analysis.py`。

## 前端 UI 兼容
- **禁止直接写 `st.image(..., use_column_width=/use_container_width=)`**（三代改名，自动升级整页 TypeError），统一走 `ui_compat.image_stretch(path)`。
- **禁止直接写 `st.components.v1.html`**：官方标注 2026-06-01 后移除（今天已过期），统一走 `ui_compat.html_embed(html, w, h)`。**`st.iframe(src=)` 只接 URL/路径，不接 HTML 字符串**，须 base64 data URI 承载；`st.iframe` 在 1.62.0 存在，签名 `(src, *, width, height, tab_index)`。埋点类失败一律静默跳过，绝不影响页面。
- 兼容性排查跑 `python tools_check_st_api.py .`（AST 扫 `st.*` 关键字参数），须在目标版本 venv 里跑。
- **本机 pip 走官方源会 ConnectionResetError 10054**，一律加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

## 产品方向与付费闭环（2026-08-29 定调并落地）
用户反馈「功能挑不出毛病，但没有非用不可的感觉」。诊断：站点是**单向信息陈列**（跑批→看板→看完关掉），缺三段闭环——**与我相关**（绑自选做个性化）→**主动触达**（收盘推送，不依赖用户想起来打开）→**事后对账**（公示历史准确率换信任）。对标 simonlin1212 6 个仓库，共同做法是「派生指标纯计算直出 + AI 只做串联叙述 + 口径与失败语义全部显式声明」。
- **锁投递不锁内容**：摘要正文全开放（最好的引流物），付费项是「自动送到邮箱 + 用你自己自选股算的个性化段落」。在用户建立信任前锁内容会把人赶走。
- **只统计超额收益 alpha**（基准 `000300.SH`），绝对收益在牛市人人都对没有信息量。**中位数为主口径**（均值被妖股拉飞）。`direction_accuracy`：跟涨但没跑赢基准记为错。
- **评级区分度单调性检验**：分档（1-10/11-30/31-50/51+）平均 alpha 须单调递减，不单调说明排序本身没信息量——自曝其短恰是信任来源。
- **三态失败语义** `complete/incomplete/failed`，退出码 0/2/3；关键数据拿不到就标 failed，不用旧值或猜测填空。样本 `MIN_SAMPLE=20`，低于则标 `insufficient` 并写「基本是噪音」。
- **T+N 口径诚实声明**：基准点是上榜当日收盘价，但用户当晚才看到、次日才能买，故衡量的是「排序有无信息量」而非「照着买能赚多少」。
- **梯队断层只算被上下都有票的高度夹住的空档**（`min_h < n < max_h`）。最低端没票是「今日无首板」，混进 `gaps` 会让「链条断裂」信号失真。`ladder_gap` 返回含 `min_height`/`first_board`。
- **「明日验证条件」必须带今日基准值 + 可对账阈值**。不带基准的判断永远无法证伪，是信任头号杀手。
- **缺失语义三分**：「没数据」记 missing / 「没事发生」/「本来就没有」（无自选股不记 missing，否则每个免费用户都被误报）。
- **绝不发空推送**：`has_content=False` 时拒发，宁可今天不推也不推「今天没有数据」的骚扰信。**先归档再发送**（渠道全失败也留下产物，站内仍可读）。渠道互不阻断，全部未配 = 退出码 0。
- **密钥不回显自检要扫插值表达式** `print\([^)]*\{\s*hook\s*[}\[]`，不能匹配变量名——提示文案里合法地写着「配置 DIGEST_WECOM_WEBHOOK 后自动启用」，那是文档不是泄露。
- **合规边界**：只给数据/框架/裁决点，不给建仓价、目标价、止损位、仓位。**周期定位的免责声明必须跟着周期一起走**——脱开声明单独播报「发酵期」极易被读成「可以进场」。禁词自检前要先剔除 `DISCLAIMER` 本身，否则免责声明自己会命中。
- `performance.json` 顶层键实测：`generated_at/benchmark/horizons/min_sample/strategies/archive/status`（**没有** `as_of`/`archive_days`，归档区间读 `archive.date_from/date_to/trade_days`）。
- 回溯只能重建 RPS 榜——突破池与 ETF 榜依赖当日盘中派生字段，强行重建会得到「另一个策略」的战绩，属于变相造假。
