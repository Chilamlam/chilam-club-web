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

## 项目铁律（必须遵守）
- **`data/` 目录归 GitHub Actions 云端所有**。本地任何 push 之前**必须先 `git fetch origin && git pull origin main --rebase`**。
- **严禁强推 / 重写 main 历史**（`git push -f`、重建仓库后覆盖推送）。2026-08-28 曾因本地重建成孤立历史后强推，静默抹掉 Actions 于 8/25、8/26、8/27 产出的三次数据提交，导致线上数据停滞在 8-24。
- **数据停更排查顺序**：① `git log --all --grep="Auto-update"` 为空即说明历史被重写（而非 Actions 失败）；② 查 GitHub API `/events` 的 PushEvent `before -> head` 链是否被人工推送断开；③ 用 `git checkout <被抹掉的sha> -- data/` 恢复（对象通常未 gc，仍可读）。
- **当前 PAT 缺少 `workflow` scope**，无法通过命令行推送 `.github/workflows/*.yml` 的改动。**约定做法：在 GitHub 网页端直接编辑该文件，然后本地 `git fetch origin && git merge --ff-only origin/main` 同步。** 2026-08-28 两次工作流加固（`d9e9a97` 调度加固、`156e2b5` v2 补突破池+容错）均通过此方式落地。注意网页端提交后本地 refs 常静默不更新，需 `printf <sha> > .git/refs/remotes/origin/main` 手写后再 ff-only。
- **`.git/refs/` 目录可能缺失导致 ref 静默不更新**：若 `git fetch` 提示更新成功但 `git rev-parse origin/main` 仍是旧值，先 `find .git/refs -type f`，为空则 `mkdir -p .git/refs/{heads,tags,remotes/origin}` 后重新 fetch。`git update-ref` / `git pack-refs` 在此情况下不报错也不生效。
- **严禁在 `data/` 写入占位/模拟假数据**。2026-08-28 `limit_ladder.json` 曾被手工写入 8 只股票的占位数据（total_count 虚标 28），云端从未跑过带天梯逻辑的版本 → 假数据长期展示给用户。新增数据文件时必须同步在当次跑批中产出真实内容，否则宁可让前端显示"暂无数据"。
- **新增 `daily_*.py` 跑批脚本后，必须同步在 `.github/workflows/daily_update.yml` 里加对应 step**。2026-08-28 发现 `daily_breakout.py`（8/28 09:28 随突破池功能引入）从未被工作流调用，`breakout_stocks.csv` 因此长期停在 8-24。检查命令：`ls -1 daily_*.py` 与 `grep -oE "python [a-z_]+\.py" .github/workflows/daily_update.yml` 两边数量必须一致。
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

## 前端 UI 兼容铁律
- **禁止直接写 `st.image(..., use_column_width=...)` 或 `use_container_width=...`**。Streamlit 三代改名（`use_column_width` → `use_container_width` → `width="stretch"`），`requirements.txt` 未锁版本时 Streamlit Cloud 自动升级会让整页 TypeError 崩掉。统一用 `ui_compat.image_stretch(path)`（逐级降级 try/except）。2026-08-29 投资作业本即因此白屏。
- **升级/排查 Streamlit 兼容性先跑 `python tools_check_st_api.py .`**：AST 扫描全仓库 `st.*` 调用的关键字参数是否被当前版本移除。需在装了目标版本的 venv 里跑。
- **本机 pip 走官方源会 ConnectionResetError 10054**，安装依赖一律加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。
- `requirements.txt` 已锁 `streamlit>=1.49,<2.0`；新增依赖同样要给上下界，别裸写包名。

