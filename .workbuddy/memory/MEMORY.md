# chilam-club-web 项目长期记录

## 项目定位
- 基于 Streamlit + GitHub Actions + 量化策略的个人投资驾驶舱与会员服务系统。

## 关键技术栈
- **UI/Web**: Streamlit, Plotly
- **数据源/量化**: Tushare Pro, AkShare, Gemini API
- **自动化工作流**: GitHub Actions 自动跑批更新 `data/`。2026-08-28 起：主跑批 UTC 11:37 / CST 19:37，兜底补跑 UTC 14:20 / CST 22:20；push 失败自动 `pull --rebase` 重试 3 次；跑完打印 data 末行日期做新鲜度自检。
- **会员与后端**: Supabase (PostgreSQL REST), PyJWT, PBKDF2-HMAC-SHA256 (纯 stdlib)
- **付费系统**: payments 订单表 + 管理员确认收款 + 续期累加逻辑 (RPC 存储过程 + REST fallback)

## 核心功能模块
1. **全市场情绪看板**: 指数复盘、多空情绪、AI 市场分析、连板情绪天梯。
2. **强势股 & ETF (RPS)**: 欧奈尔动量过滤（RPS 50/120/250）+ 阶段新高突破池。
3. **投机与套利**: 可转债双低潜伏、高溢价 LOF/跨境 ETF 套利监控。
4. **核心龙头雷达**: 领涨板块与主升浪标的异动监控。
5. **投资作业本 (Guru Tracker)**: 华尔街大佬与国会山交易持仓追踪。
6. **黄金分割预测**: 价格波段推演。
7. **会员与权限**: 游客 / 免费用户 / VIP 订阅（按月/季/年）。
8. **实时行情**: A股/港股/美股/商品 分时+多周期K线 (`page_live_quote.py`)。
9. **宏观与股债性价比**: ERP/FED模型 + 全球资产联动 + 行业市值分位 (`page_macro_erp.py`)。
10. **付费闭环**: 订单生成→收款码→管理员确认→VIP续期累加→剩余天数倒计时。建表SQL=`init_payments_table.sql`，需在Supabase SQL Editor执行。secrets需配 `[payment]` 收款码路径。
11. **自动技术分析**: K线图叠加缠论结构（分型/笔/线段/中枢）+ 帝纳波利（DMA、Fibnode、COP/OP/XOP、汇聚区、MACD 8/17/9 背离）+ 下方中文结论与关键价位表。计算层 `tech_analysis.py`、绘制层 `tech_overlay.py`、自检 `tools_probe_tech_analysis.py`。这是网站相对各家行情 app 的核心差异化价值。

## 项目铁律（必须遵守）
- **`data/` 目录归 GitHub Actions 云端所有**。本地任何 push 之前**必须先 `git fetch origin && git pull origin main --rebase`**。
- **严禁强推 / 重写 main 历史**（`git push -f`、重建仓库后覆盖推送）。2026-08-28 曾因本地重建成孤立历史后强推，静默抹掉 Actions 于 8/25、8/26、8/27 产出的三次数据提交，导致线上数据停滞在 8-24。
- **数据停更排查顺序**：① `git log --all --grep="Auto-update"` 为空即说明历史被重写（而非 Actions 失败）；② 查 GitHub API `/events` 的 PushEvent `before -> head` 链是否被人工推送断开；③ 用 `git checkout <被抹掉的sha> -- data/` 恢复（对象通常未 gc，仍可读）。
- **当前 PAT 缺少 `workflow` scope**，无法通过命令行推送 `.github/workflows/*.yml` 的改动。**约定做法：在 GitHub 网页端直接编辑该文件，然后本地 `git fetch origin && git merge --ff-only origin/main` 同步。** 2026-08-28 两次工作流加固（`d9e9a97` 调度加固、`156e2b5` v2 补突破池+容错）均通过此方式落地。注意网页端提交后本地 refs 常静默不更新，需 `printf <sha> > .git/refs/remotes/origin/main` 手写后再 ff-only。
- **`.git/refs/` 目录可能缺失导致 ref 静默不更新**：若 `git fetch` 提示更新成功但 `git rev-parse origin/main` 仍是旧值，先 `find .git/refs -type f`，为空或只剩 `heads/` 则 `mkdir -p .git/refs/{heads,tags,remotes/origin}`，再从 `.git/FETCH_HEAD` 第一列取 sha 手写 `printf <sha> > .git/refs/remotes/origin/main`。`git update-ref` / `git pack-refs` 在此情况下不报错也不生效。**该问题会反复复发（8/28、8/29 各一次），每次 push 前先检查。**
- **GitHub 连接常被重置**：`fetch`/`push` 需重试循环（实测第 4 次才通）。推完必须用 GitHub API `/commits/main` 核验远端 head，不能只信本地 `git log`。
- **严禁在 `data/` 写入占位/模拟假数据**。2026-08-28 `limit_ladder.json` 曾被手工写入 8 只股票的占位数据（total_count 虚标 28），云端从未跑过带天梯逻辑的版本 → 假数据长期展示给用户。新增数据文件时必须同步在当次跑批中产出真实内容，否则宁可让前端显示"暂无数据"。
- **假数据铁律同样适用于「前端页面里的硬编码字面量」**，不只是 `data/`。2026-08-29 发现 `page_macro_erp.py` 的「全球核心资产」6 张卡片（纳指 19845、标普 5620、USDCNH 7.1420、金 2512、油 75.8、A50 12180）全是写死字符串，从未接过行情，纳指实际已 29,433 —— 误差 9,500 点，用户一眼就看穿。**新页面凡是展示数值，必须当场接真接口 + 写自检；宁可显示「暂无数据」。**
- **自检脚本必须包含「时间戳新鲜度」断言**（如行情时间须在最近 5 天内）。纯粹校验「数值非空、格式正常」无法拦住假数据与过期数据——硬编码值永远「格式正常」。
- **新增 `daily_*.py` 跑批脚本后，必须同步在 `.github/workflows/daily_update.yml` 里加对应 step**。
- **跑批步骤必须带 `continue-on-error: true`**，否则任一脚本异常会中断整个 job，导致最后的 commit/push 不执行，当日全量数据都不落地（表面只看到某一步红叉）。
- **GitHub schedule (cron) 不可信，必须有手动触发兜底**。2026-08-28 当日 19:37 主跑批与 22:20 兜底补跑**两条 cron 均未派发**（Actions 里当天 0 条 schedule 记录）。数据没更新时先看有没有运行记录，而不是先看运行是否失败。
- **命令行手动触发跑批（无需网页点按钮，已验证）**：仓库 remote URL 内嵌 classic PAT（scope 仅 `repo`），不足以推工作流文件，但足以调 dispatch：
  `POST https://api.github.com/repos/Chilamlam/chilam-club-web/actions/workflows/daily_update.yml/dispatches`，body `{"ref":"main"}`，header `Authorization: Bearer <token>` + `Accept: application/vnd.github+json`，成功返回 **204**。取 token：`git config --get remote.origin.url` 正则 `https://([^@]+)@` 取组1、按 `:` 取末段；**不要回显明文**。全量跑批约 12 分钟。
- **`data/*.json` 用 `head`/`cat` 看中文会显示乱码**（Git Bash 按 GBK 解码 UTF-8），不代表文件损坏。校验一律用 `json.load(open(..., encoding='utf-8'))`。
- **前端有 10 分钟缓存**：`app.py` 数据读取是 `@st.cache_data(ttl=600)`。Actions 推完新数据后，Streamlit Cloud 页面最多滞后 10 分钟；要立刻看到需在页面右上菜单 `Clear cache` / `Rerun`。排查「数据没更新」时先排除这层缓存。

## 关键数据接口（已验证可用，纯 stdlib/requests，不依赖 akshare 版本）
- **涨停池/连板天梯**：`https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=600&sort=fbt:asc&date=YYYYMMDD`（`sort` 中的冒号需 URL 编码为 `%3A`）。字段：`c`代码 `n`名称 `hybk`行业 `lbc`连板数 `fbt`首封时间(整数 92500→09:25:00) `fund`封板资金 `zbc`炸板次数 `data.tc`总数。
- **沪深300/上证50/中证1000 PE-TTM 历史**：`https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=000300&startDate=20140101&endDate=YYYYMMDD`，需带 `Referer: https://www.csindex.com.cn/`，PE 字段名是 `peg`。中证500(000905)、科创50(000688) 该字段为空。
- **中国10年期国债收益率历史**：东财 `datacenter.eastmoney.com/api/data/get?type=RPTA_WEB_TREASURYYIELD&sty=ALL&st=SOLAR_DATE&sr=-1&token=894050c76af8597a853f5b408b759f5d&ps=500&p=N`，10Y 字段为 `EMM00166466`，需分页拉取。

## 全市场行情通道（`page_live_quote.py`，2026-08-29 穷举实测，0 失败）
| 市场 | 报价 | 分时 | 分钟K | 日/周/月K |
|---|---|---|---|---|
| A股/ETF/指数 | 腾讯 `qt.gtimg.cn/q=` | 腾讯 `minute/query` | 腾讯 `kline/mkline` | 腾讯 `fqkline` |
| 港股(含 hkHSI) | 腾讯 `hk00700` | 腾讯 ✅ | ❌ 无公开源 | 腾讯 `hkfqkline` |
| 美股个股 | 腾讯 `usNVDA` | 新浪 `US_MinlineService` | 新浪 `US_MinKService` | 腾讯 `usfqkline`，**须带 `.OQ`/`.N` 后缀** |
| 美股指数 | 腾讯 `usIXIC` | 新浪 `symbol=.IXIC`（**带前导点**） | 新浪 `symbol=.IXIC` | 腾讯 `usIXIC`（**不带后缀**） |
| 国际商品 | 新浪 `hf_GC` | 新浪 `getGlobalFuturesMinLine` | ❌ 无公开源 | 新浪日K + `resample` 本地聚合 |

- **东财 `push2`/`push2his` 已放弃**：`secid` 体系理论全市场通吃，但高频探测触发 IP 级限流后持续 `RemoteDisconnected` 逾 10 分钟，换 UA/Referer/数字前缀/`push2delay` 均无效。**当场不可验证的接口不能做线上依赖。**
- **腾讯 `_TX_PERIOD` 的 `"month"` 也以 m 开头**，判断分钟线必须用 `m5/m15/m30/m60` 白名单，否则月K 被误发到 `mkline` 而恒为空。`fqkline` 的 `limit` 上限约 300。
- **`MARKET_PERIODS` / `MARKET_LIMIT_NOTE` 是页面周期选项的唯一来源**。新增市场或周期必须同步改这两个字典并跑 `tools_probe_quote_api.py` 复验，**禁止让页面出现「能选但没数据」**。
- **行情自检**：`/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe tools_probe_quote_api.py`（主解释器没装 pandas/plotly，必须用这个 venv）。
- **`HF_` 前缀区分撞名商品**：`HSI/C/S/W/CT/CAD` 与股票 ticker 重名，裸输入按股票解析，要商品写 `HF_HSI`。

## 全球核心资产报价代码（`page_macro_erp.py`，2026-08-29 实测，勿再试错）
| 资产 | 正确代码 | 易错点 |
|---|---|---|
| 纳斯达克 100 | 腾讯 `usNDX` | `usIXIC` 是**综指**(26,402) 不是 100(29,433)，差 3000 点 |
| 标普 500 | 腾讯 `usINX` | `usSPX` 返回空 |
| 道琼斯 / 恒生 | 腾讯 `usDJI` / `hkHSI` | — |
| A50 期货 | 新浪 `hf_CHA50CFD` | `hf_CN` 空 |
| 现货黄金 | 新浪 `hf_XAU`（伦敦金） | `hf_GC` 是纽约期金，差 ~50 美元 |
| WTI 原油 | 新浪 `hf_CL` | — |
| 伦铜 | 新浪 `hf_CAD` | `hf_HG` 是美铜（663 vs 14278 单位不同），`hf_ZSD` 是伦锌 |
| 离岸人民币 | 新浪 `fx_susdcnh` | 裸 `USDCNH` 空 |
| 美元指数 / 美债10Y | ❌ 无免费源 | `hf_DX`/`hf_DINIW`/`usTNX`/`hf_US10Y` 全空，不要再试 |

- 新浪 `hf_*` 报价 **`[7]` 是昨收**（已用日K交叉核验：`hf_XAU[7]=4601.58` == 日K 08-27 收盘），涨跌幅须自算 `(cur-prev)/prev`。新浪 `fx_*` 的 `[8]` 是最新价、`[10]` 是涨跌幅。
- **行情时间戳三家格式不统一**：腾讯美股 `2026-08-28 17:15:59`、腾讯港股 `2026/08/28 18:31:31`、新浪 `2026-08-29 04:59:58`。解析正则必须写 `[-/]` 兼容两种分隔符。
- 自检：`stcheck venv + tools_probe_macro_assets.py`（5 条口径：price 正有限 / `|pct|≤30%` / 时间戳 5 天内 / 无科学计数法 / 源码无硬编码价格残留）。

## pandas 3.x 兼容铁律（Streamlit Cloud 已是 pandas 3.x）
- **禁止 `s.replace(0, pd.NA)`**：pandas 3.x 下会把 float64 列退化成 object dtype，参与运算后 `.astype(float)` 遇 `NAType` 抛 `TypeError: float() argument must be a string or a real number`。要「把 0 当缺失值」统一写 `s.where(s > 0)`。2026-08-29 港股分时均价线即因此崩页。
- `astype` 一律写 `astype("float64")`，不要写 `astype(float)`。外部接口来的数值列先过 `pd.to_numeric(..., errors="coerce")`。

## 分时图时间轴对齐铁律
- 行情源会返回**不在标准交易时段内**的时间戳，直接 merge 到 category 轴会全变 NaN，表现为「曲线只画一截」：A股有 `15:06~15:30`（收盘后集合竞价/延时快照，267 点里占 25 点）、港股恒指有 `18:31`（期指延伸报价）、港股收市竞价 CAS 在 16:00 之后（腾讯给 `16:08`）。
- 统一用 `_align_to_timeline(df, timeline)` 把越界点吸附到「不晚于它的最后一个轴刻度」，同刻度 `drop_duplicates(keep="last")`；跨零点市场（商品）先把小于开盘分钟数的时间 +1440 展平。港股 X 轴止于 `16:00`（不要拉到 16:10）。
- 校验口径：对齐后 `df["time"]` 必须 100% ∈ timeline。

## 技术分析实现铁律（`tech_analysis.py` / `tech_overlay.py`，2026-08-29 落地）
- **计算层禁止 import streamlit**。`tech_analysis.py` 保持纯 pandas/numpy，才能脱离 runtime 单测——线段与中枢两个 bug 就是靠这一点定位的。
- **线段识别必须与「同型」邻居比较**（顶比顶、底比底）。笔端点天然顶底交替，「比左右邻居更极端」的条件恒成立，会让线段数恒等于笔数（恒等映射）。
- **中枢 ZG/ZD 由前三段一次确定后固定不变**，延伸只判重叠。延伸时收窄 zg/zd 得到的是所有段的公共交集而非中枢（曾出现宽 0.03% 跨 16 段）。必须保留 0.3% 宽度下限与 `_PIVOT_MAX_LEGS = 9` 封顶。
- **ABC 目标位必须校验 C 落在 A~B 之间**，否则是同向延伸不是回撤，会算出荒谬的 XOP。无有效结构时如实输出「尚未形成有效的 ABC 回撤结构」。
- **价格格式化禁用 `:.4g`**，统一走 `_n()` 分档格式化，否则指数类标的显示成科学计数法。
- **口径必须透明**：线段是「对笔端点再做分型」的工程近似，中枢是区间重叠法，均非缠论原著严格定义。docstring 与 UI 免责声明都要写明「不同软件画法会有差异」「技术分析只描述已发生的价格结构」「不构成投资建议」。
- 改动后必须跑 `tools_probe_tech_analysis.py`（用 stcheck venv），校验 7 条口径且失败项为 0。

## 前端 UI 兼容铁律
- **禁止直接写 `st.image(..., use_column_width=...)` 或 `use_container_width=...`**。Streamlit 三代改名（`use_column_width` → `use_container_width` → `width="stretch"`），`requirements.txt` 未锁版本时 Streamlit Cloud 自动升级会让整页 TypeError 崩掉。统一用 `ui_compat.image_stretch(path)`（逐级降级 try/except）。2026-08-29 投资作业本即因此白屏。
- **升级/排查 Streamlit 兼容性先跑 `python tools_check_st_api.py .`**：AST 扫描全仓库 `st.*` 调用的关键字参数是否被当前版本移除。需在装了目标版本的 venv 里跑。
- **本机 pip 走官方源会 ConnectionResetError 10054**，安装依赖一律加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
- `requirements.txt` 已锁 `streamlit>=1.49,<2.0`；新增依赖同样要给上下界，别裸写包名。

