# chilam-club-web 项目长期记录

## 项目定位
Streamlit + GitHub Actions + 量化策略的投资驾驶舱与会员服务（部署 Streamlit Cloud，仓库 `Chilamlam/chilam-club-web`）。
栈：Streamlit(`>=1.49,<2.0`)+Plotly / Tushare·AkShare·直连HTTP / Gemini / Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加（`init_payments_table.sql`，secrets 需 `[payment]`）。跑批 CST 19:37 主 / 22:20 兜底，push 失败自动 `pull --rebase` 重试 3 次。

## 分层与模块
侧边栏 12 项：全市场看板（情绪派生区块排在连板天梯**之前**）/ 收盘摘要 / 实时行情+技术分析 / 强势股(RPS+突破池) / 自选股雷达 / 战绩回看 / 宏观与股债性价比 / 投机与套利 / 核心龙头雷达 / 投资作业本 / 黄金分割预测 / 使用说明。
计算层 `tech_analysis.py`·`scorecard.py`·`sentiment.py`·`digest.py`（**不 import streamlit、不联网**，才能脱 runtime 单测）/ 展示层 `page_*.py` / 跑批层 `daily_*.py`（10 个）/ 自检 `tools_probe_*.py`。

## Git / 部署铁律
- **`data/` 归 Actions 云端所有**；本地 push 前必须 `fetch + pull --rebase`。**严禁强推或重写 main 历史**（8/28 强推抹掉三次数据提交）。
- **数据停更排查序**：① `git log --all --grep="Auto-update"` 为空 = 历史被重写；② 查 API `/events` PushEvent `before→head` 链是否断开；③ `git checkout <sha> -- data/` 恢复。
- **PAT 只有 `repo` scope，改不了 `.github/workflows/*.yml`，四条路全实测封死**：`git push` → `without 'workflow' scope`；Contents API PUT → 404；Git Data API `POST /git/trees` → 404（blob 成功、tree 被拦）；同两条 API 推**普通文件**均 200/201 成功。**改工作流只有：① 网页端编辑；② 换带 `workflow` scope 的 PAT。** 推不上去的提交用 `git reset --soft HEAD~1` 撤回（工作树保留）。
- **备用推送通道**（推普通文件）：`tools_gh_put_file.py`、`tools_gh_put_via_gitdata.py`，均从 remote url 取 token 且不回显。
- **git 传输不稳根因是 HTTP/2 被中间设备打断**（`close_notify` 后 `443 Timed out`）。加 `-c http.version=HTTP/1.1` 立刻可用，已写进本地 config（另 `http.postBuffer 524288000`）。**`api.github.com` curl 200 不代表 git 传输通。** fetch/push 一律写重试循环（实测第 4 次才通），推完用 API `/commits/main` 核验。
- **`.git` 损坏两级**。轻：`refs/` 消失 → `mkdir -p .git/refs/{heads,tags,remotes/origin}` + 从 `.git/FETCH_HEAD` 取 sha `printf >` 手写 loose ref（`update-ref`/`pack-refs` 此时静默失效）。重：`objects/pack/` 只剩 `.idx` 没 `.pack`（`.git` 掉到 480K）→ 直接重建：备份工作区（删其 `.git`）→ 远端 `clone --depth 1 --no-checkout` 取对象库 → `tar` 复制（`cp -r` 会截断）→ **`git reset --mixed HEAD` 重建 index 不动工作区**（漏这步 status 全报 deleted）→ `fetch --unshallow` → `git checkout -- data/`。
- **手动触发跑批**：`POST /repos/Chilamlam/chilam-club-web/actions/workflows/daily_update.yml/dispatches`，body `{"ref":"main","inputs":{"backfill_days":"40"}}`，token 从 `git config --get remote.origin.url` 提取（**不回显**），返 204，全量约 12 分钟。
- **cron 不可信**（8/28 两条均未派发）：先看有没有运行记录，再看是否失败。
- **新增 `daily_*.py` 必须同步加 workflow step**，每步 `continue-on-error: true`（否则任一异常中断 job，当日全量数据不落地）。顺序依赖：`sentiment` 在 `market_monitor` 后、`scorecard` 在所有榜单后、`digest` 在前两者后。
- **前端 10 分钟缓存**（`ttl=600`）：推完最多滞后 10 分钟；要立刻看走右上菜单 Clear cache / Rerun，必要时 `share.streamlit.io` → Reboot。
- `data/*.json` 用 `head`/`cat` 看中文是乱码（Git Bash 按 GBK 解 UTF-8），非损坏；校验用 `json.load(open(..., encoding='utf-8'))`。

## 假数据铁律（最高优先级）
- **严禁在 `data/` 写占位数据**，**同样适用于前端硬编码字面量**（8/29 宏观页写死纳指 19845，实际 29433）。凡展示数值必须当场接真接口 + 写自检；宁可显示「暂无数据」。
- **自检必须含时间戳新鲜度断言**（最近 5 天内）——只校验「非空、格式正常」拦不住硬编码值。
- **禁止「能选但没数据」**：页面选项必须与实测可用通道一致。
- **缺失即留空**：显示 `—`，绝不补 0（补 0 会显示成「平盘」误导）。
- **测试 fixture 单位必须与生产数据一致**：`tools_probe_digest.py` 曾把 `alpha_median` 写成 `0.83`（生产是 `0.0083`），使展示层漏乘 100 的 bug 在 76 项断言下完全隐形。单位错的测试数据是在验证一个不存在的世界。

## 关键数据接口（纯 stdlib）
- **涨停池/连板天梯**：`push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt%3Aasc&date=YYYYMMDD`。字段 `c`码 `n`名 `hybk`行业 `lbc`连板 `fbt`首封(92500→09:25) `fund`封单 `zbc`炸板 `data.tc`总数。**按 date 可回溯约 15 个交易日**（更早返回 `tc=0`）。
- **指数 PE-TTM**：`csindex.com.cn/csindex-home/perf/index-perf?indexCode=000300`，需 `Referer: https://www.csindex.com.cn/`，PE 字段名 `peg`；000905/000688 为空。
- **中国 10Y 国债**：东财 `datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD&sty=ALL&st=SOLAR_DATE&sr=-1&token=894050c76af8597a853f5b408b759f5d&ps=500&p=N`，字段 `EMM00166466`，需分页。
- **东财 `push2`/`push2his` 已放弃**：高频探测触发 IP 级限流后 `RemoteDisconnected` 逾 10 分钟。
- **腾讯号段前缀**：`bj` 92xxxx/43x/83x/87x、`sh` 6/5/9 开头、`sz` 其余；沪深300 写全 `sh000300`。

## 全市场行情通道（`page_live_quote.py`，穷举实测 0 失败）
- A股/ETF/指数：腾讯 `qt.gtimg.cn/q=` 报价、`minute/query` 分时、`kline/mkline` 分钟、`fqkline` 日周月
- 港股：腾讯 `hk00700` + 分时 + `hkfqkline`；**分钟K 无公开源**
- 美股个股：腾讯 `usNVDA` / 新浪 `US_MinlineService`+`US_MinKService` / 腾讯 `usfqkline` **须带 `.OQ`/`.N`**
- 美股指数：腾讯 `usIXIC` / 新浪 `symbol=.IXIC`（**带前导点**）
- 国际商品：新浪 `hf_GC` + `getGlobalFuturesMinLine`，日K 后本地 `resample`；**分钟K 无源**
- **腾讯 `"month"` 也以 m 开头**，判分钟线须用 `m5/m15/m30/m60` 白名单。`fqkline` 的 `limit` 上限约 300。
- `MARKET_PERIODS`/`MARKET_LIMIT_NOTE` 是周期选项唯一来源，改后跑 `tools_probe_quote_api.py`。
- **`HF_` 前缀区分撞名商品**：`HSI/C/S/W/CT/CAD` 与股票 ticker 重名。
- 自检解释器：`/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`（主解释器无 pandas/plotly）。

## 全球核心资产代码（`page_macro_erp.py`，勿再试错）
纳指100=`usNDX`（`usIXIC` 是综指差 3000 点）/ 标普=`usINX`（`usSPX` 空）/ 道指 `usDJI` / 恒生 `hkHSI` / A50=新浪 `hf_CHA50CFD` / 现货金=`hf_XAU`（`hf_GC` 是纽约期金差 ~50）/ WTI=`hf_CL` / 伦铜=`hf_CAD` / 离岸人民币=`fx_susdcnh`。**美元指数与美债10Y 无免费源**（`hf_DX`/`hf_DINIW`/`usTNX`/`hf_US10Y` 全空）。
- 新浪 `hf_*` 的 `[7]` 是昨收，涨跌幅须自算；`fx_*` 的 `[8]` 最新价、`[10]` 涨跌幅。
- **时间戳三家格式不统一**（腾讯美股 `-`、腾讯港股 `/`、新浪 `-`），正则须写 `[-/]`。
- 自检 `tools_probe_macro_assets.py`（price 正有限 / `|pct|≤30%` / 5 天内 / 无科学计数法 / 源码无硬编码价格残留）。

## pandas 3.x 兼容（Cloud 已是 3.x）
- **禁止 `s.replace(0, pd.NA)`**：float64 退化成 object，`.astype(float)` 遇 `NAType` 抛 TypeError。「把 0 当缺失」统一写 `s.where(s > 0)`。
- `astype` 一律 `astype("float64")`；外部数值列先 `pd.to_numeric(..., errors="coerce")`。

## 分时图时间轴对齐
行情源会返回非交易时段时间戳（A股 `15:06~15:30`、港股恒指 `18:31`、CAS `16:08`），直接 merge 到 category 轴全变 NaN。统一 `_align_to_timeline(df, timeline)` 吸附到「不晚于它的最后一个刻度」，同刻度 `drop_duplicates(keep="last")`；跨零点市场先把小于开盘分钟数的时间 +1440。港股 X 轴止于 `16:00`。校验：对齐后 time 必须 100% ∈ timeline。

## 技术分析实现铁律
- **线段识别必须与「同型」邻居比较**（顶比顶、底比底）。笔端点天然顶底交替，「比左右邻居更极端」恒成立 → 线段数恒等于笔数。
- **中枢 ZG/ZD 由前三段一次确定后固定不变**，延伸只判重叠。收窄得到的是公共交集不是中枢。保留 0.3% 宽度下限与 `_PIVOT_MAX_LEGS=9`。
- **ABC 目标位必须校验 C 落在 A~B 之间**，否则是同向延伸不是回撤，会算出荒谬 XOP。
- **价格格式化禁用 `:.4g`**，走 `_n()` 分档。
- 口径必须透明（线段是工程近似、中枢是区间重叠法）。改后跑 `tools_probe_tech_analysis.py`。

## 前端 UI 兼容
- **禁止直接写 `st.image(..., use_column_width=/use_container_width=)`**（三代改名，整页 TypeError），走 `ui_compat.image_stretch(path)`。
- **禁止直接写 `st.components.v1.html`**（2026-06-01 后移除），走 `ui_compat.html_embed(html, w, h)`。**`st.iframe(src=)` 只接 URL/路径不接 HTML 字符串**，须 base64 data URI；签名 `(src, *, width, height, tab_index)`。埋点类失败一律静默跳过。
- 兼容排查 `python tools_check_st_api.py .`（AST 扫 `st.*` 关键字参数），须在目标版本 venv 跑。
- **本机 pip 走官方源会 ConnectionResetError 10054**，一律加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

## 产品方向与付费闭环（2026-08-29 定调并落地）
用户反馈「功能挑不出毛病，但没有非用不可的感觉」。诊断：站点是**单向信息陈列**，缺三段闭环——**与我相关**（绑自选做个性化）→**主动触达**（收盘推送）→**事后对账**（公示准确率换信任）。
- **锁投递不锁内容**：摘要正文全开放（最好的引流物），付费项是「自动送到邮箱 + 用你自己自选股算的个性化段落」。信任建立前锁内容会把人赶走。
- **只统计超额收益 alpha**（基准 `000300.SH`），绝对收益在牛市人人都对。**中位数为主口径**（均值被妖股拉飞）。`direction_accuracy`：跟涨但没跑赢基准记为错。
- **alpha 在 `scorecard.py` 里存小数不存百分数**（`-0.0382` = `-3.82%`），**展示层必须乘 100**。战绩数字报小 100 倍比报大更危险——会让明显失效的榜单看起来「几乎打平」。
- **区分度结论必须与战绩数字同时播报**：只报中位数而藏起「排序没有信息量」等于让用户以为排名可用。单调性检验=分档（1-10/11-30/31-50/51+）中位 alpha 须单调递减；实测 RPS 榜 `monotonic: false`（1-10 档反而最差）。自曝其短恰是信任来源。
- **三态失败语义** `complete/incomplete/failed`，退出码 0/2/3；关键数据拿不到就标 failed，不用旧值填空。`MIN_SAMPLE=20`、`MIN_SAMPLE_BUCKET=15`，低于标 `insufficient`。
- **T+N 口径诚实声明**：基准点是上榜当日收盘价，但用户当晚才看到、次日才能买，故衡量的是「排序有无信息量」而非「照着买能赚多少」。
- **梯队断层只算被上下都有票夹住的空档**（`min_h < n < max_h`）。最低端没票是「今日无首板」。`ladder_gap` 返回含 `min_height`/`first_board`。
- **「明日验证条件」必须带今日基准值 + 可对账阈值**。不带基准的判断永远无法证伪。
- **缺失语义三分**：「没数据」记 missing / 「没事发生」/「本来就没有」（无自选股不记 missing）。
- **绝不发空推送**（`has_content=False` 拒发）；**先归档再发送**（渠道全失败也留产物）。渠道互不阻断，全部未配 = 退出码 0。**Server酱 失败仍返 HTTP 200，必须校验响应体 `code` 字段**；`title` 强制单行。免费额度每天 5 条。
- **密钥不回显自检要扫插值表达式** `print\([^)]*\{\s*hook\s*[}\[]`，不能匹配变量名（提示文案里合法写着环境变量名）。
- **合规边界**：只给数据/框架/裁决点，不给建仓价、目标价、止损位、仓位。**周期定位的免责声明必须跟着周期一起走**。禁词自检前先剔除 `DISCLAIMER` 本身。
- `performance.json` 顶层键：`generated_at/benchmark/horizons/min_sample/strategies/archive/status`（**没有** `as_of`/`archive_days`，归档区间读 `archive.date_from/date_to/trade_days`）。
- **回溯只补客观事实数据**：涨停池 + 全市场涨幅收盘即固化，重建不失真；RPS 榜可重建。突破池/ETF 榜依赖当日盘中派生字段，强行重建等于变相造假。**回溯不复用三通道兜底**（AkShare/Tushare 不支持任意历史日，硬凑=当日数据冒充历史）。宁可这一天没有，不要错的一天。
- `daily_scorecard.py` 与 `daily_sentiment.py` 均支持 `--backfill N`；工作流 `backfill_days` 入参对 sentiment 自动截顶到 15（接口能力所限）。某日任一项缺失就整日跳过（半天数据会让晋级率分母失真）。
