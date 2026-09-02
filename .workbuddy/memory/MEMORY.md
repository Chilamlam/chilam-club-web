# chilam-club-web 长期记录（细节见每日日志）

## 项目
Streamlit Cloud 投资驾驶舱 + 会员付费，仓库 `Chilamlam/chilam-club-web`。Streamlit(`>=1.49,<2.0`)+Plotly / 直连HTTP取数 / Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加。跑批 CST 19:37 主 / 22:20 兜底。
分层：计算层 `tech_analysis`·`scorecard`·`sentiment`·`digest`·`sector_rotation`（**不 import streamlit、不联网**，可脱 runtime 单测）/ 展示 `page_*.py` / 跑批 `daily_*.py` / 自检 `tools_probe_*.py`（改哪层跑哪个探针）。
**多页路由**：只认「`app.py` + `pages/` 下文件」，根目录其它 `.py` 是模块；`pages/` 文件全员可见 → 页首 `is_logged_in`+`is_admin` 双校验是真门禁。
自检解释器 `/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`。pip 加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。**akshare 1.18.94 已装入 stcheck venv**。

## Git / 部署
- **`data/` 归 Actions 云端所有**；push 前 fetch+rebase；**严禁强推/重写 main**。`rejected (fetch first)`=远端有新提交须 rebase，非传输故障。
- 传输不稳=HTTP/2 被打断 → `-c http.version=HTTP/1.1`(已进 config)+重试循环；**外呼一律 curl**（python urllib 会被 TLS 打断）；API curl 200 ≠ git 通。`git rev-parse origin/main` 是 fetch 缓存非实时 → 判是否落后用 API `/commits/main` + `git hash-object` 比对 blob sha。
- 兜底 `tools_gh_put_via_gitdata.py --msg "..." <路径...>`（多文件一 commit；`--rm` 删；重命名=同 commit 增+删）。**token 在 remote url 用户名位**。**API 推送须 CRLF→LF**（脚本 `_to_lf()` 已内置）。
- **PAT 只有 repo scope，yml 改不了且已薄壳化（逻辑全在 run_daily.py，secrets 走 ALL_SECRETS 整体透传）→ 不再改 yml**。手动触发 dispatch body `{"ref":"main","inputs":{"backfill_days":"40"}}`。
- **`run_daily.py`**：逐步 try 后继续、**恒 exit 0**；每步独立 timeout；**新增跑批只改 `STEPS` 表**（顺序：sentiment 在 market_monitor 后，digest 最后）。
- **cron 平台语义 best-effort**（超时整条丢弃无痕迹）→ `tools_gh_watchdog.py` 按远端 `data/` 有无当日数据判缺失再 dispatch 补跑；只判周末不判节假日。
- `.git` 重损恢复：备份→浅 clone→**tar** 复制→`reset --mixed HEAD`→`fetch --unshallow`。**本机 `update-ref refs/remotes/*` 静默失效**：手写 `.git/refs/remotes/origin/main` 文件；fetch 后必须复核 `for-each-ref`；对齐用 `git reset --mixed <已知远端sha>`。
- 前端 10 分钟缓存（Clear cache/Rerun）。**提示紧跟 `st.rerun()` 会消失** → 走 `st.session_state` flash。

## 假数据与自检铁律（最高优先级）
- **严禁占位数据/前端硬编码字面量**；自检必含时间戳新鲜度断言。**一条永不失败的断言=没有断言**：须造错反向验证；反向测试目录别用 `/tmp`（Windows 解析成 `\tmp`）；断言匹配语法结构而非裸子串（校验落在「写入」如 `st.session_state["x"] =`）。
- **反向验证三条铁律**（行情页撞号修复时各踩一次）：①**造错本身要先验证有效**（给 URL 加未知参数 smartbox 直接忽略照样返回 → 误判成假断言；须换不存在的 host）；②**探针里所有失败必须走 `bad()` 唯一出口** —— 曾有一段自己 `print("FAIL ...")`，反向脚本按 `[FAIL]` 过滤完全抓不到退化，造错生效但外部全绿；③**断言别被无关数据喂饱** —— 页面探针判「按钮标签含中国稀土」，而 `PRESETS` 里本就有该按钮，搜索挂掉照样通过。
- `streamlit.testing.v1.AppTest` 可**无头渲染页面**做断言（`tools_probe_live_quote_page.py` 已用），能验「选择器真出现/按钮真生成」，值得推广到其他页面。探针临时文件写系统 temp，别写仓库根目录。
- **缺失显示 `—` 绝不补 0**；fixture 单位与生产一致（alpha 存小数，展示层乘 100）。
- **静默失败最致命**；归因错误的报错比未知错误更贵。`_supabase_request(return_error=True)` 透出 status/code + `explain_uid_write_error()` 分流。`bind_wxpusher_uid` 返回 `(ok,why)`。
- **None(取数失败修配置) ≠ False/[](确实没有)**；PostgREST PATCH 零行命中返回 `[]` 而 `[] is not None` 为真 → 必须显式判空列表（写库「失败报成功」已堵 4 处）。**改返回值语义时 None/空/有值三出口一次列全**。
- 等待态≠错误态；顶层成功≠单个成功（WxPusher 逐 UID code；Server酱 200 也可能额度耗尽须校验 body code）。失败文案须说清「已发生什么+下一步千万别做什么」（部分成功时重试最糟：confirm_payment 已修）。
- 一份规则两处实现必漂移（端点推导收进 `admin_notify.py` 单点）。

## 数据接口（勿再试错）
- **涨停池**东财 `push2ex.../getTopicZTPool`（ut=7eea3...），回溯约补 12 交易日。**东财 `push2`/`push2his` 已放弃**（高频探测触发 IP 限流>10min）。
- 指数 PE-TTM 走中证 `index-perf`（须 Referer: csindex.com.cn，PE 字段=`peg`）；10Y 国债走东财 `RPTA_WEB_TREASURYYIELD`(`EMM00166466`) 需分页。
- 腾讯前缀：`bj`92xxx/43x/83x/87x、`sh`6/5/9、`sz`其余；沪深300=`sh000300`；判分钟线用 `m5/m15/m30/m60` 白名单（"month" 也 m 开头）；fqkline limit≈300。美股 `usfqkline` 须带 `.OQ/.N`；港股/国际商品分钟K 无免费源；`HF_` 前缀防撞名。
- **沪深撞号（行情页已修）**：`q=sh000831,sz000831,bj000831` 单请求批量返回，不存在的代码**整行不出现**→探三前缀≈探一个开销；**请求键必须原样小写**（`usAAPL` 返回 `v_pv_none_match`）。活跃判据 `amount>0 or (high>0 and low>0)`；**但 31 个 `000xxx` 样本里 21 个沪深双活跃**（000001 上证指数vs平安银行）→ 必须再叠 `_MAJOR_SH_INDEX` 宽基白名单先验（`000002` 特意不列入）。兜底判据写 `if not q` 是错的：**沪市指数未开盘也返回昨收价**，有返回≠是用户要的。类型判定用**代码段确定性判定**（110/111/113/118/123/127/128=转债），段位启发式会把「立讯转债」错分基金；非 `sh/sz/bj` 前缀直接不标类型（`hkHSI`/`usIXIC` 会误判）。
- **北交所 BJ_SHARE**：日/周/月K **必须走 `newfqkline`**，老 `fqkline` **静默只返回 1 根**（不报错不为空）；`mkline` 返回 `m5` 键但列表为空→不暴露分钟周期。
- **名称搜索** `smartbox.gtimg.cn/s3/?t=all&q=<UTF-8 urlencode>`（UA + `Referer: stockapp.finance.qq.com`）：**响应 GBK 但查询词须 UTF-8 编码** —— 「中文挂英文通」先查参数编码别先怀疑限流；返回 `前缀~代码~名称~拼音~类型码`，`^` 分隔，名称是 `\uXXXX` 需 `unicode_escape` 二次解码；有瞬时空返回须重试+降级；搜不到北交所与转债。
- **全球资产**：纳指100=`usNDX`/标普=`usINX`/道指=`usDJI`/恒生=`hkHSI`/A50=`hf_CHA50CFD`/现货金=`hf_XAU`/WTI=`hf_CL`/伦铜=`hf_CAD`/USDCNH=`fx_susdcnh`；美元指数与美债10Y 无免费源。`hf_*[7]`=昨收（幅自算），`fx_*`[8]价[10]幅。
- **同花顺题材**：q.10jqka 需 hexin-v cookie(401)；akshare THS 历史函数真名 `stock_board_concept_index_ths`（无 period 参数）；**东财 clist 概念板块(fs=m:90+t:3) 一次请求组全量 ~504 个，f109=5日/f160=10日/f110=20日/f24=60日（f110≠10日！已用板块日K交叉验证）**，pz 上限 100 需翻页；本机高频探测 push2 半小时即 IP 限流，每日一次没事。板块轮动功能即基于此（`sector_rotation.py` 计算层 + `daily_sector_rotation.py` 跑批）。

## 技术坑位
- pandas 3.x：禁 `s.replace(0,pd.NA)`（退化 object 抛错），「0当缺失」用 `s.where(s>0)`；外部数值列先 `pd.to_numeric(errors="coerce")`。
- 分时图：源返回非交易时段时间戳（A股15:06~15:30等）→ `_align_to_timeline()` 吸附；跨零点 +1440。
- 技术分析：线段与「同型」邻居比较；中枢 ZG/ZD 前三段定死；ABC 校验 C∈A~B。
- 前端 UI：禁 `st.image(use_column_width=)`→`ui_compat.image_stretch()`；禁 `components.v1.html`→`ui_compat.html_embed()`；排查 `tools_check_st_api.py`；同组件多页复用传 `key_prefix`。

## 付费闭环 & 推送
- 闭环=与我相关(绑自选)→主动触达(收盘推送)→事后对账(公示准确率)。**锁投递不锁内容**；文案只承诺已跑通通道；「权限生效≠权益交付」（recipients 统计 unbound 为告警）；下单即告警管理员（`admin_notify.py`，WxPusher 优先 Server酱兜底）。
- 统计只算 alpha（基准000300.SH）中位数为主；T+N 口径诚实声明；三态 complete/incomplete/failed（退出码0/2/3）；梯队断层=被上下夹住的空档；缺失语义三分；绝不发空推送；先归档再发送；合规：不给价/仓位。
- **绩效统计两条铁律（`8724570` 起，用户质疑「RPS 不该有反向超额」后补）**：
  ① **必须分离 beta**。`alpha = ret - 1.0×bench` 隐含 beta=1，而动量榜天然筛高波动小盘成长股（实测 RPS 榜 **beta≈3.75**，R²=0.56），跌市里放大的跌幅会被整笔记成「选股能力为负」。`estimate_beta()` 以**入选日**为观测单位 OLS（逐条回归会虚增样本）；校正后 alpha 中位数 -3.61%→-1.79%，独立窗口口径 -0.83%/胜率 50%。**换基准解决不了这个问题**（中证500/1000/国证2000/创业板指/科创50 全试过，alpha 仍 -1.9%~-3.6%）。
  ② **必须报有效样本量**。T+N 窗口逐日滚动（相邻日共享 N-1 天持有期）+ 同日截面高相关 → n=2210 折算后**只有 8 个独立观测**，双侧二项检验 **p=0.727**，谈不上「稳定跑赢/跑输」。`effective_sample()` 输出折算数+p 值，不足 `MIN_INDEPENDENT=20` 时明写「负数不等于策略失效」。手写 `_binom_p_two_sided`（计算层不引 scipy）。
  ③ 排查「统计结果反常」的固定顺序：数据完整性→独立复现计算→独立源交叉验证价格→成分体检→换基准→显著性检验。前四轮排除 bug，后两轮才定口径。**收益分解 `ret` vs `bench` 中位数分别看**，能一眼分清「基准算反」还是「标的真跌」。
  ④ 展示顺序刻意：先有效样本量→再 beta 拆解→最后区分度检验，否则用户先把噪音当结论。跑批日志同理，只打原始 alpha 会让日志成为误读源头。
- **合规文案落点**：免责放榜单**上方**（页脚等于没放）+ 侧边栏全站声明（不依赖用户滑到底）+ 页内风险提示块。禁建议性措辞（「只买 RPS>87」已删）。页面探针扫禁词须**先剔除否定语境行**（免责本身含「不提供目标价」），且过滤器要有正反自检。断言用 `ast.FunctionDef` 取**函数体切片**，全文匹配分不清「写在这个函数」与「写在别处」。
- WxPusher 唯一免费一对多微信通道（appToken 全员推送，code:1000 成功）；Server酱仅推自己→管理员告警（Turbo→sctapi.ftqq.com；³→{uid}.push.ft07.com，title 单行）。
- Supabase：`subscriptions.expires_at`（非 end_date）带 status=active；users.watchlist JSONB；wxpusher_uid 部分唯一索引；管理员微信=告警唯一收件人勿解绑。
