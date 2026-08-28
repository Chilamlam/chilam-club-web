# chilam-club-web 项目长期记录

## 项目定位
- 基于 Streamlit + GitHub Actions + 量化策略的个人投资驾驶舱与会员服务系统。

## 关键技术栈
- **UI/Web**: Streamlit, Plotly
- **数据源/量化**: Tushare Pro, AkShare, Gemini API
- **自动化工作流**: GitHub Actions (UTC 11:30 / CST 19:30 自动跑批更新 `data/`)
- **会员与后端**: SQLAlchemy, SQLite, PyJWT, bcrypt

## 核心功能模块
1. **全市场情绪看板**: 指数复盘、多空情绪、AI 市场分析。
2. **强势股 & ETF (RPS)**: 欧奈尔动量过滤（RPS 50/120/250）。
3. **投机与套利**: 可转债双低潜伏、高溢价 LOF/跨境 ETF 套利监控。
4. **核心龙头雷达**: 领涨板块与主升浪标的异动监控。
5. **投资作业本 (Guru Tracker)**: 华尔街大佬与国会山交易持仓追踪。
6. **黄金分割预测**: 价格波段推演。
7. **会员与权限**: 游客 / 免费用户 / VIP 订阅（按月/季/年）。
