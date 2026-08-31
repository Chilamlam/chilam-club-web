# chilam-club-web 长期记录

## 项目
Streamlit Cloud 投资驾驶舱 + 会员付费，仓库 `Chilamlam/chilam-club-web`。Streamlit(`>=1.49,<2.0`)+Plotly / 直连HTTP取数 / Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加。跑批 CST 19:37 主 / 22:20 兜底。
分层：计算层 `tech_analysis`·`scorecard`·`sentiment`·`digest`（**不 import streamlit、不联网**，才能脱 runtime 单测）/ 展示 `page_*.py` / 跑批 `daily_*.py` / 自检 `tools_probe_*.py`（改哪层跑哪个探针）。
**多页路由**：Streamlit 只认「入口 `app.py` + `pages/` 下的文件」，根目录其它 `.py` 只是模块 → `pages/`=`admin/auth/dashboard.py`，**不许搬回根目录**。子页需 `sys.path` 引导 + `os.path.join(_ROOT,"*.sql")`；`pages/` 文件自动进侧边栏对所有人可见 → 页首 `is_logged_in`+`is_admin` 双校验是真门禁。
自检解释器 `/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`（主解释器无 pandas/plotly）。pip 加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`（官方源 10054）。

## Git / 部署
- **`data/` 归 Actions 云端所有**；push 前必须 fetch+rebase。**严禁强推或重写 main 历史**。
- **`rejected (fetch first)` ≠ 传输挂死**：前者是服务端答案（远端有跑批提交），重试无用，必须 fetch+rebase；后者才重试/走 API。
- 传输不稳根因是 HTTP/2 被中间设备打断 → `-c http.version=HTTP/1.1`（已进 config）+ 重试循环（实测第 4 次才通），推完用 API `/commits/main` 核验。**python urllib 会被 TLS 打断，外呼一律 curl**；`api.github.com` curl 200 ≠ git 通（两条通道不同端点，故障可只发生在一侧；8/31 曾 fetch 重试 8 次全失败而 curl 稳定 200）。**`git rev-parse origin/main` 读的是上次 fetch 的缓存不是实时远端** → 判断本地是否落后必须用 API `/commits/main`，并用 `git hash-object <file>` 比对远端 tree entry 的 blob sha 来判定「内容是否已相同」（相同就该 rebase 吸收，不是 reset）。
- 兜底 `tools_gh_put_via_gitdata.py --msg "..." <路径...>`（多文件一个 commit），`--rm <路径>` 删条目 → **重命名必须新增+删除同一 commit**。**token 在 remote url 用户名位**（`https://<TOKEN>:x-oauth-basic@...`），抠错拿到 `x-oauth-basic`(len=13) 致 API 恒 401。
- **PAT 只有 `repo` scope，改不了 `.github/workflows/*.yml`**（全 403|404），只能网页端编辑。**工作流已薄壳化（8/30），此后不应再改 yml**：逻辑全在 `run_daily.py`；secrets 走 `ALL_SECRETS: ${{ toJSON(secrets) }}` 整体透传 → 新增 secret 不必改 yml。手动输入走 `env:`（`${{ inputs.x }}` 直插 `run:` = 命令注入）。
- **`run_daily.py`**：逐步捕获异常后继续、**恒 exit 0**（否则提交 `data/` 的步骤不执行，全天不落盘）；每步独立 timeout（超时记 124 继续）；**新增跑批只改 `STEPS` 表**。顺序：sentiment 在 market_monitor 后、scorecard 在所有榜单后、digest 最后。
- **cron 不可信是平台语义不是配置问题**：官方 best-effort「高负载时最坏情况不会运行」——TTL 超时后整条派发被丢弃，**不发通知、Actions 列表无痕迹**。实测 8/22~26 +10~20 分钟、8/27 +9.5h、8/28 +12h、8/31 彻底没派发。避峰奇数分钟已是官方推荐，仍挡不住。
- **对策 `tools_gh_watchdog.py`**：判据从「cron 有没有触发」改为「远端 `data/` 有没有今天的数据」，缺失就 `workflow_dispatch` 补跑（走实时事件管线）。读**远端 raw** 不读本地（本地落后会误判重复补跑）；双探针；日期三种写法先归一化再比；只判周末不判节假日（过期假日表会明年悄悄漏掉整个假期）。
- 手动触发 `POST .../workflows/daily_update.yml/dispatches` body `{"ref":"main","inputs":{"backfill_days":"40"}}` 返 204，全量约 12 分钟。**Actions 用触发时刻那个 commit 的 yml**；cancel 会导致当日 `data/` 完全不落盘。
- 数据停更排查：`git log --all --grep="Auto-update"` 为空=历史被重写 → 查 API `/events` PushEvent 链 → `git checkout <sha> -- data/`。
- `.git` 重损（`objects/pack/` 只剩 `.idx`）：备份工作区 → 远端 `clone --depth 1 --no-checkout` → **`tar` 复制**（`cp -r` 会截断）→ **`git reset --mixed HEAD` 重建 index** → `fetch --unshallow`。
- 前端 10 分钟缓存（`ttl=600`）：立刻看走右上 Clear cache/Rerun。`data/*.json` 用 `cat` 看中文乱码是 Git Bash 按 GBK 解，非损坏。

## 假数据与自检铁律（最高优先级）
- **严禁占位数据与前端硬编码字面量**（8/29 宏观页写死纳指 19845，实际 29433）。**自检必须含时间戳新鲜度断言**——只校验「非空、格式正常」拦不住硬编码值。
- **一条永不失败的断言等于没有断言**：加完必须造错反向验证。反向测试目录**别用 `/tmp/...`**（Windows Python 解析成 `\tmp\...`，`rglob` 扫 0 文件同样显示"全部通过"）。**断言须匹配语法结构而非裸子串**（注释里的词会误伤）。
- **缺失即留空显示 `—`，绝不补 0**（补 0 显示成「平盘」误导）；禁止「能选但没数据」。
- **测试 fixture 单位必须与生产一致**：`alpha_median` 曾写 `0.83`（生产 `0.0083`），使展示层漏乘 100 的 bug 在 76 项断言下完全隐形。
- **静默失败最致命**：字段名写错的取数不抛异常，只返回语法正确、语义为空的答案。**变种：失败如实上报但归因是编的**——`_supabase_request` 曾把 HTTPError 全吞成 `None`（状态码丢失），前端只能猜一种原因写死，于是 409 唯一冲突被报成「缺列，请执行 init_wxpusher_column.sql」，管理员跑完幂等脚本问题分毫未动。**归因错误的错误信息比「未知错误」更贵**。已修（8/31）：`return_error=True` 透出 `{status,code,message,detail}` + `explain_uid_write_error()` 分流 409/23505→「已绑在别的账号」、PGRST204→才提迁移脚本、0→网络、401/403→凭据；`bind_wxpusher_uid()` 返回 `(ok, why)`。默认签名不变故其余调用方零改动。
- **`None` 与 `False`/`[]` 必须分开**：取数失败=None（修配置），确实没有=False/[]（引导）。混为一谈会让配置故障长期伪装成「暂时没人」。**写库同理且更隐蔽**：PostgREST 在 `Prefer: return=representation` 下 PATCH **零行命中返回 `[]`**，而 `[] is not None` 为真 → 只判 `res is not None` 会把「user_id 在库里不存在」当成保存成功（`bind_wxpusher_uid` 与 `update_user_watchlist` 都踩过，9/1 已堵）。**改返回值语义时三个出口（None / 空 / 有值）要一次列全**——修「失败没说清」时最容易顺手造出「失败说成成功」。
- **等待态 ≠ 错误态**（未扫码用 warning）；**顶层成功 ≠ 单个成功**（WxPusher `code:1000` 时每 UID 各有 code；Server酱额度耗尽仍返 HTTP 200 → 校验响应体 `code`）。
- **一份规则两处实现必然漂移**（端点推导收进 `admin_notify.py`，`daily_digest` 反向引用；漂移表现是"域名解析不到"，极易误判成网络问题）。

## 数据接口（只记「不要再试错」的）
- **涨停池**东财 `push2ex.../getTopicZTPool`（`ut=7eea3edcaed734bea9cbfc24409ed989`），字段 `c/n/hybk/lbc/fbt/fund/zbc` + `data.tc`；**回溯按自然日滑动，实测约补 12 个交易日**。**东财 `push2`/`push2his` 已放弃**（高频探测触发 IP 级限流逾 10 分钟）。
- 指数 PE-TTM 走中证 `index-perf`，**须带 `Referer: https://www.csindex.com.cn/`，PE 字段名是 `peg`**，000905/000688 空。中国 10Y 国债走东财 `RPTA_WEB_TREASURYYIELD` 字段 `EMM00166466`，需分页。
- 腾讯前缀：`bj` 92xxxx/43x/83x/87x、`sh` 6/5/9 开头、`sz` 其余；沪深300 写 `sh000300`。**腾讯 `"month"` 也以 m 开头** → 判分钟线须用 `m5/m15/m30/m60` 白名单；`fqkline limit` 上限约 300。港股与国际商品**分钟K 无公开源**；美股 `usfqkline` **须带 `.OQ`/`.N`**。**`HF_` 前缀区分撞名商品**（`HSI/C/S/W/CT/CAD`）。
- **全球资产代码勿再试错**：纳指100=`usNDX`（`usIXIC` 是综指差 3000 点）/ 标普=`usINX`（`usSPX` 空）/ 道指 `usDJI` / 恒生 `hkHSI` / A50=`hf_CHA50CFD` / 现货金=`hf_XAU`（`hf_GC` 是纽约期金差 ~50）/ WTI=`hf_CL` / 伦铜=`hf_CAD` / 离岸人民币=`fx_susdcnh`。**美元指数与美债10Y 无免费源**。`hf_*` 的 `[7]` 是昨收（涨跌幅自算），`fx_*` `[8]` 价 `[10]` 幅。

## 技术坑位
- **pandas 3.x**：**禁 `s.replace(0, pd.NA)`**（float64 退化 object，遇 `NAType` 抛错），「0 当缺失」统一 `s.where(s>0)`；外部数值列先 `pd.to_numeric(errors="coerce")`。
- **分时图**：行情源会返回非交易时段时间戳（A股 `15:06~15:30`、恒指 `18:31`），直接 merge 到 category 轴全 NaN → `_align_to_timeline()` 吸附到「不晚于它的最后一个刻度」；跨零点市场先把小于开盘分钟数的时间 +1440。
- **技术分析**：线段识别必须与「**同型**」邻居比较（顶比顶底比底），否则线段数恒等于笔数。**中枢 ZG/ZD 由前三段一次确定后固定不变**，延伸只判重叠。**ABC 目标位必须校验 C 落在 A~B 之间**，否则同向延伸算出荒谬 XOP。
- **前端 UI**：禁 `st.image(..., use_column_width=)` → `ui_compat.image_stretch()`；禁 `st.components.v1.html` → `ui_compat.html_embed()`。排查 `tools_check_st_api.py .`（AST 扫关键字参数 + `switch_page`/`page_link` 目标合法性），须在目标版本 venv 跑。**同组件多页复用必须传 `key_prefix`**。

## 付费闭环（8/29 定调，8/30 落地）
缺三段闭环：**与我相关**（绑自选）→**主动触达**（收盘推送）→**事后对账**（公示准确率换信任）。
- **锁投递不锁内容**：摘要正文全开放（最好的引流物），付费项是「自动送达+个性化段落」。信任建立前锁内容会把人赶走。
- **承诺 ≠ 实现**：文案只许承诺已跑通的通道（曾全站写「推到邮箱」而 `DIGEST_SMTP_*` 从未进 Secrets）。已清除 + 自检 `check_no_email_promise()`。
- **权限生效 ≠ 权益交付**：「付费未绑微信」在日志里长得跟正常一样（渠道全成功零失败）→ `recipients()` 统计 unbound 列为告警 problem；`push_binding.py` 挂三处，付费未绑用 `st.error`，非会员保持折叠 expander（还没付钱就弹红警告是骚扰）。
- **下单即告警管理员**（`admin_notify.py`，刻意不 import streamlit 供 Actions 复用）：WxPusher 优先、Server酱兜底，全失败如实返 False。否则订单永远 pending = 收钱不发货。
- **只统计 alpha**（基准 `000300.SH`）、**中位数为主**（均值被妖股拉飞）；**alpha 存小数不存百分数，展示层必须乘 100**。**区分度结论必须与战绩同时播报**：分档中位 alpha 须单调递减，实测 RPS 榜 `monotonic:false`（1-10 档最差）——自曝其短恰是信任来源。
- **T+N 口径诚实声明**：基准点是上榜当日收盘，用户当晚才看到、次日才能买 → 衡量「排序有无信息量」而非「照着买能赚多少」。
- **三态失败语义** `complete/incomplete/failed`（退出码 0/2/3），关键数据拿不到标 failed 不用旧值填空；`MIN_SAMPLE=20`/`MIN_SAMPLE_BUCKET=15`。
- **梯队断层只算被上下都夹住的空档**；**「明日验证条件」必须带今日基准值+可对账阈值**（不带基准永远无法证伪）。**缺失语义三分**：没数据记 missing / 没事发生 / 本来就没有。
- **绝不发空推送**（`has_content=False` 拒发）；**先归档再发送**；渠道互不阻断。
- **合规**：只给数据/框架/裁决点，不给建仓价、目标价、止损位、仓位；免责声明跟着周期定位走。
- **回溯只补客观事实**（涨停池+全市场涨幅收盘即固化；突破池/ETF 榜依赖盘中派生字段，强行重建=造假）；某日任一项缺失就整日跳过。

## 推送通道（8/30 定型）
- **WxPusher 是唯一免费且能一对多的微信通道**（用户投递主通道）：一个 `appToken` 推所有关注者，单次 2000 UID，支持 markdown。成功 `code:1000`，token 错 `1001`。
- **Server酱只能推给自己**（官方「群发不支持」，免费 5 条/天）→ 降级为管理员告警。Turbo(`SCT…`)→`sctapi.ftqq.com`；³(`sctp{uid}t…`)→`{uid}.push.ft07.com`。`title` 强制单行。
- 邮件=代码在凭据没配，恒不发。企业微信要拉进通讯录，对陌生付费用户是硬门槛，暂不选。
- Supabase：`subscriptions` 到期列是 **`expires_at`**（非 `end_date`）须带 `status=active`；`users.watchlist` JSONB/`digest_optin` BOOL；`wxpusher_uid` 含部分唯一索引（同一微信不许绑多账号）。**管理员微信是下单告警唯一收件人** → 为测绑定而解绑管理员会同时打瞎告警。
- 备查（非阻塞）：Secrets 里 `supabase.key` 与 `service_key` 同值且 `sb_secret_` 前缀，全站完整权限走 PostgREST、无 RLS 隔离。
