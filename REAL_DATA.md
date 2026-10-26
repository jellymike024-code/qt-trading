# 大屏真实数据与更新口径

2026-10-25起，生产大屏不使用示例行情。最后一次实际覆盖请看data/markets/latest.json，不以本文的数量代替实时状态。

| 展示内容 | 实际来源 | 更新方式 |
|---|---|---|
| 上证指数、上海/深圳观察股、纳斯达克综合指数、美股观察股 | 腾讯公开证券报价qt.gtimg.cn | markets collect批量读取 |
| 富时100、KOSPI指数 | 新浪环球指数日线gi.finance.sina.com.cn/hq/daily | markets collect读取真实日期收盘价及日线 |
| 韩国观察股 | Naver公开证券报价polling.finance.naver.com | markets collect读取，保留会话/时区，可能含盘后 |
| 伦敦24只观察股 | Google Finance各股票报价页 | 本聊天研究Agent实际打开页面保存verified_quotes.json；当前不是独立Python自动接口 |
| 微博、抖音、小红书、知乎、B站、今日头条 | UAPI免费聚合热榜 | social collect，含原平台链接及榜单更新时间 |
| 东方财富 | 公开财经快讯接口 | social collect，标题/发布时间/评论数量；不含评论文本 |
| 原始评论 | 已部署MindSpider/MediaCrawler + 本地PostgreSQL | 采集端只读导出并sync；成功平台与实际数量看collector_status.json及大屏状态页 |
| 历史净值与策略指标 | 本地真实ETF日线与固定模拟引擎 | 研究模拟计算，非真实成交或券商业绩 |

```powershell
python -m quantlab.markets collect
python -m quantlab.markets reconcile  # 重新合并页面证据、评估陈旧状态，不发起网络请求
python -m quantlab.social collect
python -m quantlab.bettafish sync
.\Start-Dashboard.ps1
```

每条报价保存price、change_pct、source_url、session_date、observed_at；来源有精确时间时保留quote_time。公开指数日线只有交易日期，不能捏造秒级时间。指数曲线只画真实日线；没有历史数组时不画曲线。原始响应按时间写入data/markets/snapshots并记录SHA256。页面摘录包含原URL、实际读取时间和原始数值证据。

伦敦通用免费行情接口本机遇到限流/拒绝访问，现由每日研究Agent访问原报价页刷新。访问失败保留原证据及日期，超过5个日历日标陈旧，不把旧价格改成今天。重试不绕过429/403。机器或桌面Agent不运行时不会自动刷新；网页每60秒只是读取已有快照。后续独立常驻采集需要可用的免费接口或用户选择的行情服务。

板块采用人工观察列表，3个有效样本同交易日期才计算等权均值，缺一个或日期混合就留空。列表并非全行业成分，不代表官方行业指数。GBX是便士而非英镑；KRW为韩元；指数单位为点。不同市场不做跨币种价格相加。

首次采集、原始发布、模型解释是三个不同时间。热榜没有原帖发布时间时留空；当前收集的评论不能回填到过去策略。模型解释是判断，不是已核验事实，也不是未来涨跌概率。

生产大屏只读取明确路径，开发测试夹具及data/demo、reports/demo不会接入。地球纹理、星点、弧线属于视觉素材，不代表证券价格、资金流或通信实测。
