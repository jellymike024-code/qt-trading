# 本地ETF量化实验室

GitHub版本管理、公开大屏和Codespaces云端启动说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。公开构建只发布筛选后的真实行情及历史研究汇总；本机账本、原始评论与登录会话不上传。

按用户目标设置：A股股票类ETF，模拟资金100万元，最大回撤预算12%，Windows本地运行。当前是**日线交易信号、研究回测及指示性前向模拟**，没有券商账户连接。开盘撮合是日线估算，不能当作券商模拟盘真实回报。

## 已实现

- 固定研究池：510300、510500、159915、588000，仅用于覆盖不同宽基指数，并非选基建议。长仓/现金，无融资及做空。
- 20/60均线趋势、55/20唐奇安突破、20/60/120日时序动量、量价动量；另有固定等权策略组合、舆情影子组合及同风控持续做多基准。
- 各策略独立100万元公平比较；组合账户本身资金总额仍为100万元。总目标仓位最高60%，单ETF最高25%，波动率目标10%用于限制仓位。
- 6%回撤目标仓位减半，10%触发永久停止新增仓及下一交易日退出计划，保留12%预算缓冲。跳空、跌停、T+1可能穿透预算，不能保证最大损失。
- 收盘信号次日开盘加滑点模拟；100份整手、T+1、先卖后买、现金和手续费记账、涨跌停拒单、按前日成交量限制订单规模。
- 公开腾讯/东方财富日线（config明确选择，默认腾讯）：未复权价成交，前复权价仅生成信号；交易量由手转换为份。原始CSV、时间和SHA256快照可审计。部分下载失败不会用合成数据补齐或混用数据源。
- 最近252个交易日独立起始资金的样本外对比、多年扩展区间、双倍成本压力测试、费用/回撤/仓位/换手和策略相关性报告。
- 一个月前向账本：初始化只热身，不追补历史交易；重复运行不重复成交；漏跑多个交易日只估值，不事后补单；代码和配置指纹锁定，到期停止新目标并等待复核。

## 本地使用

已使用当前电脑的Python 3.12及现有pandas/numpy/requests验证。需要独立环境时：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e '.[test]'
```

从本目录运行：

```powershell
python -m quantlab fetch     # 下载真实历史行情
python -m quantlab research  # 多策略历史对比，生成reports/dashboard.html
python -m quantlab paper     # 初始化或推进一个月前向账本
python -m quantlab cycle     # 下载、推进账本、重做研究报告，一次完成
python -m quantlab status    # 查看状态
python -m pytest -q          # 正确性测试
```

`Run-Daily.ps1`依次更新大屏公开行情、免费热榜、BettaFish真实导出（若有），再执行cycle；伦敦原网页核验及语义分析由每日聊天Agent执行。报告可直接双击 `reports/dashboard.html`，无需后台服务器。`reports/signals.json`保存下一个交易日的目标仓位和来源状态，`state/paper.json`保存账本；CSV在reports/holdout和reports/paper中。

`python -m quantlab demo`只用于离线演示，合成数据及报告分别写入data/demo与reports/demo，不会初始化真实前向账本。

## 舆情Agent

本地Python模块不自带LLM账号。由本聊天的定时研究Agent联网检索、核查来源、写入证据，随后Python执行可解释的风险过滤。没有有效新闻时明确显示missing，保留纯量价基线。正面新闻不加杠杆；高置信负面事件降仓。舆情是否有用由前向对照检验，不预设结论。

每条JSONL证据包含：id、symbol（六位代码或宏观通用`*`）、published_at、observed_at、analyzed_at（均带时区）、score[-1,1]、confidence[0,1]、source_url、evidence、可选agent/ expires_at。三个时间的最晚值才是可用时间；默认从原发布时间起24小时过期。不能把今天分析的旧新闻放进历史交易时点。

研究Agent将候选证据写入data/news/inbox.jsonl后执行：

```powershell
python -m quantlab.news data/news/inbox.jsonl
```

自动化在北京时间每天16:15执行，包含周末和休市日；机器需保持开机，Codex桌面应用运行，且能联网。休市日也更新舆情，交易账本按实际行情日推进。变化不重要时不打扰，仅在显著目标变化、风控、故障及一个月完成时通知。

新增免费数据采集见 [SOCIAL_SOURCES.md](SOCIAL_SOURCES.md)：`python -m quantlab.social collect` 采集微博/抖音/小红书聚合热榜和东方财富财经快讯，输出 `reports/social_dashboard.html`。当前是热榜/新闻标题，完整评论文本数量单独报告；不将热榜伪称为评论情绪。新增来源作为独立影子分析，不重写已冻结的交易实验。

三维交互大屏见 [frontend/README.md](frontend/README.md)，运行`.\Start-Dashboard.ps1`打开本机页面。四地真实公开行情、来源时间与观察样本计算口径见 [REAL_DATA.md](REAL_DATA.md)。免费舆情已增加知乎/B站/今日头条；MindSpider完整采集端、独立数据库与评论导出已部署，见 [运行与登录说明](integrations/BettaFish/README.md)。实际评论数量与平台状态以本地状态页为准，不把安装成功称为采集成功。

## 研究限制和验收

1. **分红/拆并份尚未逐项核验**，现金账不计入分红。当前是扣交易成本的价格收益，并非完整总回报。`corporate_actions_verified=false`仅表达状态，不能通过手工改为true补齐事件。股票类ETF日线撮合缺少开盘订单簿/停牌实时状态，不能代表真实成交质量。
2. 固定ETF池存在选择/幸存者偏差；当前快照的前复权价不是完整时点数据库。每个日期的复权OHLC使用相同因子近似，信号历史仍可能随数据修订而变化。数据快照用于追溯，不能消除这些偏差。
3. 默认佣金万分之一、最低5元，单边滑点5bp，无股票印花税（本版本只研究ETF）。这些是成本假设，必须按实际券商费率校准。
4. 当前持续做多基准使用同样的仓位、波动率及回撤控制，不是满仓被动指数；与市场指数比较需另补含分红总回报基准。
5. 最近252日是预留的历史测试窗；没有训练或自动挑选参数。历史对比不等于前向验证。一个月主要验收数据更新、信号准时、账本守恒、重启、风控和舆情A/B；不足以证明长期盈利。模拟期不自动更改参数、不自动转实盘。
6. `state/.cycle.lock`用于避免并发。若进程异常终止，先确认锁文件中的PID已退出，再手动删除该锁文件；不要删除已有paper.json账本。代码/配置变化应另建实验并保留原账本。

## 核验来源

- 上交所ETF交易单位、股票ETF T+1：https://www.sse.com.cn/assortment/fund/etf/question/
- 深交所相关创业板ETF涨跌幅：https://www.szse.cn/www/investor/institute/rules/t20200821_580899.html
- AKShare官方ETF行情实现与复权参数：https://github.com/akfamily/akshare/blob/main/akshare/fund/fund_etf_em.py
- 时序动量原始研究（非当前盈利保证）：https://www.aqr.com/Insights/Research/Journal-Article/Time-Series-Momentum
- 本地定时任务条件：https://learn.chatgpt.com/docs/automations
