# chilam-club-web 长期记录（细节见 .workbuddy/memory/YYYY-MM-DD.md）

## 项目
Streamlit Cloud 投资驾驶舱 + 会员付费，仓库 `Chilamlam/chilam-club-web`。Streamlit(`>=1.49,<2.0`)+Plotly / 直连HTTP / Supabase REST+PyJWT+PBKDF2(纯stdlib)。付费=payments 订单表+管理员确认收款+续期累加。跑批 CST 19:37 主 / 22:20 兜底。
分层：计算层 `tech_analysis`·`scorecard`·`sentiment`·`digest`·`sector_rotation`（**不 import streamlit、不联网**，可脱 runtime 单测）/ 展示 `page_*.py` / 跑批 `daily_*.py` / 自检 `tools_probe_*.py`（改哪层跑哪个探针）。
**多页路由只认 `app.py` + `pages/`**，根目录其它 `.py` 是模块；`pages/` 文件全员可见 → 页首 `is_logged_in`+`is_admin` 双校验才是真门禁。
自检解释器 `/c/Users/Lenovo/.workbuddy/binaries/python/envs/stcheck/Scripts/python.exe`；pip 加清华源；akshare 1.18.94 已装。

## Git / 部署
- **`data/` 归 Actions 云端所有**；push 前 fetch+rebase；**严禁强推 main**。`rejected (fetch first)`=远端有新提交，非传输故障。
- **【09-02 重大纠正】本机 `env` 被劫持成一个静默吞命令的壳**：PATH 里 `~/.local/bin/env` 排在 `/usr/bin/env` 前，内容只是「往 PATH 追加目录」的 sh 脚本，**完全忽略自己的参数**。于是 `env -u HTTP_PROXY ... git push` **什么都没执行却 exit 0** —— 我据此报过一次「PUSH_OK」而远端 HEAD 根本没动。判据：命令零输出且 exit 0（真 git 无论成败都有输出）。**要用绝对路径 `/usr/bin/env`**；此壳 Aug 20 就存在，早前所有「env -u ... git 成功」的记录都不可信，须以远端 sha 复核为准。
- **代理变量当前为空**（`HTTP_PROXY`/`HTTPS_PROXY`/`no_proxy` 全空），不再需要 `env -u` 绕代理；旧的 `CONNECT tunnel failed 502` 是当时本机代理挂了的指纹，与现在的故障无关。
- **`github.com:443` 间歇不可达（curl 三次一成，21s 超时）而 `api.github.com` 稳定 200** → git 传输通道（upload-pack/receive-pack）本身不可靠，**推送直接走 API 兜底 `tools_gh_put_via_gitdata.py --msg "$(git log -1 --format=%B <sha>)" <路径...>`** 比反复重试 push 划算。curl 通 ≠ git 通（两者主机不同）。
- API 兜底推出的 commit 与本地 commit **tree 相同但 sha 不同**（作者时间戳不同）→ 本地会显示 `ahead 1`；等 fetch 通了 `git pull --rebase` 会因 diff 已应用而自动丢弃空提交、快进到远端 sha。**别强推去「对齐」**。
- 传输不稳=HTTP/2 被打断 → `-c http.version=HTTP/1.1`(已进 config)+重试；**外呼一律 curl**（python urllib 会被 TLS 打断）。`git rev-parse origin/main` 是 fetch 缓存非实时 → 判落后用 API `/commits/main` + `git hash-object` 比 blob sha。
- **重试循环别写 `if cmd | tail -2`**：退出码取最后一个命令（`tail` 恒 0）→ 首次即 break。
- 兜底脚本 `--rm` 删文件；**token 在 remote url 用户名位**；**API 推送须 CRLF→LF**（已内置，本次归一化 1266 处）。

- PAT 只有 repo scope → **不再改 yml**（已薄壳化，逻辑全在 run_daily.py，secrets 走 ALL_SECRETS 透传）。dispatch body `{"ref":"main","inputs":{"backfill_days":"40"}}`。
- `run_daily.py`：逐步 try 后继续、**恒 exit 0**、每步独立 timeout；**新增跑批只改 `STEPS` 表**（sentiment 在 market_monitor 后，digest 最后）。
- cron **best-effort**（超时整条丢弃无痕迹）→ `tools_gh_watchdog.py` 按远端 `data/` 有无当日数据判缺失再 dispatch；只判周末不判节假日。
- `.git` 重损恢复：备份→浅 clone→**tar** 复制→`reset --mixed HEAD`→`fetch --unshallow`。**本机 `update-ref refs/remotes/*` 静默失效** → 手写 `.git/refs/remotes/origin/main`，fetch 后必须 `for-each-ref` 复核。
- 前端 10 分钟缓存。**提示紧跟 `st.rerun()` 会消失** → 走 `st.session_state` flash。

## 假数据与自检铁律（最高优先级）
- **严禁占位数据/前端硬编码字面量**；自检必含时间戳新鲜度断言。**一条永不失败的断言=没有断言**，必须造错反向验证。反向测试目录别用 `/tmp`（Windows 解析成 `\tmp`）；断言匹配语法结构而非裸子串（校验落在「写入」如 `st.session_state["x"] =`）。
- **反向验证四铁律**：①**造错本身先验证有效**（给 URL 加未知参数 smartbox 直接忽略照样返回 → 须换不存在的 host）；②**探针所有失败必须走 `bad()` 唯一出口**（自己 `print("FAIL")` 会让反向脚本的 `[FAIL]` 过滤抓不到退化）；③**断言别被无关数据喂饱**（判「按钮含中国稀土」而 `PRESETS` 本就有该按钮 → 搜索挂掉照样过）；④**别 monkey-patch 整个函数**（那是换实现）→ 改源码字符串再 exec，**锚点失配立即抛错**。
- **断言必须放在能验出它的环境里**：探针把 `st.cache_data` stub 成空装饰器后，「空结果不缓存」无论实现是 raise 还是 return[] 都恒真 → 缓存行为只能放跑真 runtime 的页面探针，**且配反向元断言**（有结果须命中缓存 1/3）。
- `AppTest` 可**无头渲染页面**做断言（`tools_probe_live_quote_page.py` 已用），能验「选择器真出现/按钮真生成」，值得推广。探针临时文件写系统 temp。
- **缺失显示 `—` 绝不补 0**；fixture 单位与生产一致（alpha 存小数，展示层乘 100）。
- **静默失败最致命**；归因错误的报错比未知错误更贵。`_supabase_request(return_error=True)` 透 status/code + `explain_uid_write_error()` 分流。
- **None(取数失败改配置) ≠ False/[](确实没有)**；PostgREST PATCH 零行命中返回 `[]` 而 `[] is not None` 为真 → 必须显式判空列表（已堵 4 处）。**改返回值语义时 None/空/有值三出口一次列全**。
- 等待态≠错误态；顶层成功≠单个成功（WxPusher 逐 UID code；Server酱 200 也可能额度耗尽须校验 body code）。失败文案须说清「已发生什么+下一步千万别做什么」。
- 一份规则两处实现必漂移（端点推导收进 `admin_notify.py` 单点）。
- **下游「保护逻辑生效」只能证明下游没错，证不到上游没出事**（09-01 我据此把真故障判成正常，被用户当场纠正）。定位静默失败必须回溯到最上游。
- **AST 断言必须认对人**（「第一个 `st.info`」抓到的是早退分支而非免责声明 → 按实参文本特征认）；**用例本身要有区分度**（锚定日恰等于今天 → 两种实现产物一样 → 用远期日历 + 元断言）。

## 跑批可靠性铁律（09-01 四班全灭换来的）
- **日期锚定以「数据是否已发布」为准，不以交易日历为准**。cron 实测延迟 1.2h~9.2h（历史 +12h），过零点后日历首位=当天而行情未发布 → 整步空转。范式 `daily_breakout.py::get_latest_trade_date`；`MAX_ANCHOR_BACK=3` 逐日探测，超限报错不静默用旧数据。**回退锚定日后各窗口对照日必须 `cal[anchor_i+n]`**。
- **写盘日期必须锚定交易日，绝不用 `datetime.now()`**（与锚定是两条独立的线；`9cf6aec` 把周六写进 `strong_etfs.csv`）。
- **装饰性字段绝不挡在主产物前**（判据：缺了它主产物是否失去意义）。两阶段落盘：主产物先 `to_csv`，装饰字段后补且带独立时间预算（曾算好 102 只榜单因题材接口挂住被超时杀掉，磁盘零字节）。线程池 `shutdown(wait=False, cancel_futures=True)` 且不能用 `with`。
- **退出码必须如实 + 监控判据逐产物覆盖 + 结论由机器给**。`main_job` 恒 return None = exit 0 = 汇总 `[OK]`；「打印原始日期让人肉眼比对」不算监控。`run_daily.check_date_consistency()` 取批内最大日期当基准（**刻意不引交易日历**），落后者点名 + `::warning::`。
- **修一个坑先横向 grep 同类实现**（同仓曾三套实现同一问题、两对两错）。

## 数据接口（勿再试错）
- **涨停池**东财 `push2ex.../getTopicZTPool`（ut=7eea3...），回溯约补 12 交易日。**东财 `push2`/`push2his` 已放弃**（高频探测触发 IP 限流>10min）。
- 指数 PE-TTM 走中证 `index-perf`（须 Referer csindex.com.cn，PE 字段=`peg`）；10Y 国债走东财 `RPTA_WEB_TREASURYYIELD`(`EMM00166466`) 需分页。
- 腾讯前缀：`bj`92xxx/43x/83x/87x、`sh`6/5/9、`sz`其余；沪深300=`sh000300`；判分钟线用 `m5/m15/m30/m60` 白名单（"month" 也 m 开头）；fqkline limit≈300。美股 `usfqkline` 须带 `.OQ/.N`；港股/国际商品分钟K 无免费源。
- **沪深撞号（行情页已修）**：`q=sh000831,sz000831,bj000831` 批量返回、**不存在的代码整行不出现**→探三前缀≈探一个开销，**一次拼 30 个混合市场代码正常**；**请求键必须原样小写**（`usAAPL` 返 `v_pv_none_match`）。活跃判据 `amount>0 or (high>0 and low>0)`；**但 31 个 `000xxx` 样本 21 个沪深双活跃** → 必须叠 `_MAJOR_SH_INDEX` 宽基白名单先验（`000002` 特意不列）。兜底写 `if not q` 是错的：**沪市指数未开盘也返回昨收价**，有返回≠是用户要的。类型判定用**代码段确定性判定**（110/111/113/118/123/127/128=转债），段位启发式会把「立讯转债」错分基金；非 `sh/sz/bj` 前缀不标类型。
- **北交所 BJ_SHARE**：日/周/月K **必须走 `newfqkline`**，老 `fqkline` **静默只返回 1 根**；`mkline` 返回 `m5` 键但列表为空→不暴露分钟周期。
- **名称搜索双源（09-02 定型，`_search_kernel`）**：
  - 主源 `smartbox.gtimg.cn/s3/?t=all&q=<UTF-8 urlencode>`（UA + `Referer: stockapp.finance.qq.com`）：**响应 GBK 但查询词须 UTF-8 编码** —— 「中文挂英文通」先查参数编码别怀疑限流；返回 `前缀~代码~名称~拼音~类型码`、`^` 分隔，名称 `\uXXXX` 需 `unicode_escape` 二次解码。支持中文名+拼音缩写（`ndsd`→sz300750）。**盲区：北交所与可转债一律 0 条**（中文名/拼音/纯代码三写法全搜不到）。
  - 兜底 `searchapi.eastmoney.com/api/suggest/get?input=<kw>&type=14&token=D43BF722C8E33BDC906FB84D85E326E8&count=N`，取 `QuotationCodeTable.Data`，`QuoteID` 形如 `0.920982`。**北交所 MktNum=0 与深A 相同，只看 MktNum 必错** → 北交所判 `SecurityTypeName=京A`+`Classify=NEEQ`（新三板同为 NEEQ 但 Type=三板，须叠代码段 92/899）；转债判 `Classify=Bond`+转债段位（企业债 751xxx 同为 Bond，靠段位挡）；转债沪深前缀**用 MktNum(1=沪/0=深)** 比按段位推稳。噪音须过滤 `OTCFUND`/`BK`/`LSE`/`OTCBB`/三板。**已退市标的（南银/浦发转债）suggest 返 0 而腾讯仍有挂牌价 → 探针用例只选在市标的**。
  - **合并后统一用腾讯批量报价校验**（`_drop_unquotable`）：搜索结果必须点得开，新三板 831071 / 企业债 751240 / 英股 BTRW 靠这步挡掉；**报价接口整体失败时降级放行**。
  - **空结果绝不能进 `st.cache_data`**：实测它**缓存空列表**（3 次只发 1 次）但**不缓存抛异常的调用**（3 次发 3 次）→ 无结果抛 `_SearchEmpty`，外层 `search_symbols` 收敛成 `[]`。否则接口一次瞬时空返回=用户 10 分钟白搜。
  - **两个输入框紧邻必混淆**：名字打进「标的代码」框时 `宁德时代` 走 resolve 失败、`ndsd` 更隐蔽（符合美股 ticker 语法 → `usNDSD` → 报「未取到行情」）。已加 `_fallback_to_search()` 原地给候选按钮 —— **报错文案里写「请用上面的搜索框」不算解法，用户看不到那行字**。
- **全球资产**：纳指100=`usNDX`/标普=`usINX`/道指=`usDJI`/恒生=`hkHSI`/A50=`hf_CHA50CFD`/现货金=`hf_XAU`/WTI=`hf_CL`/伦铜=`hf_CAD`/USDCNH=`fx_susdcnh`；美元指数与美债10Y 无免费源。`hf_*[7]`=昨收（幅自算），`fx_*`[8]价[10]幅。
- **同花顺题材**：q.10jqka 需 hexin-v cookie(401)；akshare THS 历史函数真名 `stock_board_concept_index_ths`（无 period 参数）；**东财 clist 概念板块(fs=m:90+t:3) 一次请求组全量 ~504 个，f109=5日/f160=10日/f110=20日/f24=60日（f110≠10日！已交叉验证）**，pz 上限 100 需翻页。

## 技术坑位
- pandas 3.x：禁 `s.replace(0,pd.NA)`（退化 object 抛错），「0当缺失」用 `s.where(s>0)`；外部数值列先 `pd.to_numeric(errors="coerce")`。
- 分时图：源返回非交易时段时间戳（A股15:06~15:30等）→ `_align_to_timeline()` 吸附；跨零点 +1440。
- 技术分析：线段与「同型」邻居比较；中枢 ZG/ZD 前三段定死；ABC 校验 C∈A~B。
- 前端 UI：禁 `st.image(use_column_width=)`→`ui_compat.image_stretch()`；禁 `components.v1.html`→`ui_compat.html_embed()`；排查 `tools_check_st_api.py`；同组件多页复用传 `key_prefix`。
- 本机跑 `python -c` 别在字符串里写裸 `\u`（转义失败报 unicode error）→ 落成临时 `.py` 文件。

## 付费闭环 & 推送
- 闭环=与我相关(绑自选)→主动触达(收盘推送)→事后对账(公示准确率)。**锁投递不锁内容**；文案只承诺已跑通通道；「权限生效≠权益交付」；下单即告警管理员（`admin_notify.py`，WxPusher 优先 Server酱兜底）。
- 统计只算 alpha（基准000300.SH）中位数为主；三态 complete/incomplete/failed（退出码0/2/3）；绝不发空推送；先归档再发送；合规不给价/仓位。
- **绩效统计铁律**（用户质疑「RPS 不该有反向超额」后补）：
  ① **必须分离 beta**。`alpha = ret - 1.0×bench` 隐含 beta=1，动量榜天然筛高波动小盘成长股（实测 **beta≈3.75**，R²=0.56），跌市放大的跌幅会整笔记成「选股为负」。`estimate_beta()` 以**入选日**为观测单位 OLS；校正后中位数 -3.61%→-1.79%。**换基准解决不了**（中证500/1000/国证2000/创业板指/科创50 全试过）。
  ② **必须报有效样本量**。T+N 逐日滚动+同日截面高相关 → n=2210 折算后**只有 8 个独立观测**，双侧二项检验 **p=0.727**。`effective_sample()` 不足 `MIN_INDEPENDENT=20` 时明写「负数不等于策略失效」。手写 `_binom_p_two_sided`（计算层不引 scipy）。
  ③ 排查「统计反常」顺序：数据完整性→独立复现计算→独立源交叉验证价格→成分体检→换基准→显著性检验。**`ret` vs `bench` 中位数分别看**，分清「基准算反」还是「标的真跌」。
  ④ 展示顺序刻意：先有效样本量→再 beta 拆解→最后区分度检验；跑批日志同理。
- **合规文案落点**：免责放榜单**上方**（页脚等于没放）+ 侧边栏全站声明 + 页内风险提示块。禁建议性措辞。页面探针扫禁词须**先剔除否定语境行**，过滤器要有正反自检；断言用 `ast.FunctionDef` 取**函数体切片**。
- WxPusher 唯一免费一对多微信通道（appToken 全员推送，code:1000 成功）；Server酱仅推自己→管理员告警（Turbo→sctapi.ftqq.com；³→{uid}.push.ft07.com，title 单行）。
- Supabase：`subscriptions.expires_at`（非 end_date）带 status=active；users.watchlist JSONB；wxpusher_uid 部分唯一索引；管理员微信=告警唯一收件人勿解绑。
