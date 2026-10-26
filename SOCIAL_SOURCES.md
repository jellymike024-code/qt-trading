# 免费舆情采集与影子分析

按2026-10-25用户要求，每天北京时间16:15运行，包含周末。本模块优先免费公开数据，不读取浏览器Cookie或密码，也不调用付费搜索服务。独立评论采集端只复用用户手动登录的本项目专用会话，具体规则见其适配说明。

## 当前接入

| 数据源 | 实际采集内容 | 不包含的内容 |
|---|---|---|
| UAPI聚合的微博榜单 | 热搜标题、排名、热度、原链接、榜单更新时间 | 微博全文和评论文本 |
| UAPI聚合的抖音榜单 | 热榜标题、排名、热度、原链接、榜单更新时间 | 视频转录和评论文本 |
| UAPI聚合的小红书榜单 | 热榜标题、排名、热度、原链接、榜单更新时间 | 笔记全文和评论文本 |
| UAPI聚合的知乎/B站/今日头条 | 各平台热榜/热门标题、排名、原链接、更新时间 | 完整原文和评论文本 |
| 东方财富公开财经快讯 | 标题、明确发布时间、原链接、评论数量 | 评论文本；评论数量不能代替情绪 |

上述接口均在本机实际读取验证。持续状态请看 `reports/social_status.json`，不能把文档中的“可用”当作每次运行成功。

参考BettaFish/MindSpider的话题发现流程，公开发现源已扩充至7个；每次成功数量、源时间及SHA256以最新状态文件为准。reports/social_status.json的actual_comment_texts=0只表示此热榜/快讯模块不采集评论正文，不代表整个项目没有真实评论。独立BettaFish适配器的评论总量、24小时有效数量、可追溯数量和评分覆盖，分别读取reports/bettafish_status.json与data/bettafish/dashboard.json；平台本次是否成功采集以data/bettafish/collector_status.json为准，历史存量不能替代本轮成功验证。见 [适配说明](integrations/BettaFish/README.md)。

股吧页面直接请求曾返回403，未接通。微博、抖音已通过独立采集端取得真实评论，自动运行仅限collector_config.json的automatic_platforms中已验证平台；登录有效性和本次覆盖仍须逐轮检查。小红书曾采到笔记，但评论未验证成功，不能称为评论源已接通。NewsNow公共实例本机返回403，今日热榜返回503，部分其他免费实例TLS/连接失败，因此未用于生产采集。不得绕过验证码、读取Cookie、关闭TLS验证或把搜索摘要冒充评论。后续如出现新的免费稳定来源，可以先验证，再在独立采集模块增加适配；不能修改已冻结的交易实验代码。

## 操作

```powershell
python -m quantlab.social collect
python -m quantlab.social status
```

输出：

- `data/social/snapshots/`：每个平台独立原始JSON、采集时间、请求参数；SHA256记录在状态文件。
- `data/social/observations.jsonl`：追加式观察记录，同一采集时点重复入库去重，历史观察不会覆盖。
- `data/social/candidates.json`：最近24小时内、来源不陈旧、经过财经关键词筛选的候选。关键词命中不是情感评分；semantic_review_status和needs_semantic_review区分已解释与待解释条目。
- `data/social/pending_analysis.json`：本轮待Agent解释的候选与原因；不匹配、无效或过期的旧解释不能替代当前分析。
- `data/social/annotations.json`：保留实际导入的解释记录，避免重新采集或短暂源故障丢失解释；这不是模型调用日志。
- `reports/social_dashboard.html`：可直接打开的覆盖率、财经候选与Agent分析页。
- `reports/social_status.json`：本次各源成功/缺失/陈旧/解析异常；完整评论采集条数单独列出。

热榜更新时间不是原帖发布时间，无法确认时 `published_at=null`。本系统从实际首次获取时起才能前向使用，不将首次获取伪装为原始发布时间；持续重复的旧话题不会每天当作新消息。不同平台的热度单位不可直接相加。相同文本的topic_hash用于发现转载重复，语义相近但文字不同的话题仍须Agent归并，不能伪称已全部自动去重。由于未读取评论作者，目前不能声称已经按作者完成水军识别。

## Agent语义分析

每天先collect，然后由定时Agent阅读candidates，按以下要求解释：

1. 区分新闻事实、机构观点、群众话题、广告与传闻。只看到标题时明确不确定性；不能据热榜推断散户多空占比。
2. 尽可能打开原始来源核验，识别ETF/指数调整、政策、行业与宏观关联。无明确方向的事件给中性，不能强行判利好。
3. 原链接、发布及采集/分析时间全部保留。未经证实的社交传闻不写入官方事件库；不将今天的Agent分析回填历史回测。
4. 文本近似转载应归为同一主题，不把多次转载当成独立证据。不同平台热度不可直接求和。
5. 无财经候选时记录缺失，不能推断市场安全；数据源失败不得伪造样本。

Agent将本次候选解释写入 `data/social/agent_inbox.json`（JSON数组），每条包含：

```json
{
  "record_id": "candidates.json中真实存在的id",
  "score": 0.0,
  "confidence": 0.4,
  "analyzed_at": "带时区的实际分析完成时间",
  "reason": "基于实际证据的解释与不确定性",
  "agent": "scheduled_topic_research",
  "topic_group": "同一事件跨平台使用同一个分组名",
  "evidence_urls": []
}
```

执行：

```powershell
python -m quantlab.social annotate --input data/social/agent_inbox.json
```

校验后保存reports/social_analysis.json和带时间戳的历史分析，更新舆情页。部分批次只补充本次实际提交的解释，不清除其他仍有效的解释；后续collect保留候选ID、来源链接和内容指纹匹配、且在有效时间内的解释，不把采集时间改写为分析时间。失效条目标为pending；已过期或本次来源失败的候选不进入当前已分析数量。历史解释仍保留，源恢复时重新核对有效性。

reports/social_status.json的analysis与当前分析报告保存已解释/待解释数量、当前有效解释的last_analyzed_at、历史记录中的latest_recorded_analysis_at、method_counts和无效记录原因。两种时间分开：刷新报告时间不是重新分析时间，历史最近分析时间也不表示当前候选已经分析。数量口径明确为annotation_records_not_model_calls，不推测或虚构LLM调用次数。空接口响应单独标为empty，不能让上一轮记录伪装成本轮成功采集。

这里的score是Agent对标题/事件的解释，不是评论情绪指数或涨跌概率。collect只采集并维护待办队列，annotate只校验外部研究Agent实际提供的解释，两者本身都不会调用大模型。当前语义分析由每日聊天Agent执行；公共网页的关键词规则归类与这些解释须分开标注。独立持续推理需另外配置私有模型服务或可用本地模型，不能因已安装采集器就声称完整多Agent报告引擎已运行。

## 与模拟盘的关系

新增社交采集和解释先作为独立影子分析。当前模拟实验的代码、参数和已冻结交易目标不变；社交标题不会被悄悄注入已核验政策事件库。日线模拟有冻结目标，休市日新话题可更新舆情页，但不能追改历史成交。未来如正式比较社交过滤仓位，需要建立明确新版本的独立实验账本。

## 已核验文档

- 免费热榜API、字段与限流：https://uapis.cn/docs/api-reference/get-misc-hotboard
- 东方财富财经页面：https://finance.eastmoney.com/a/cywjh.html
- 抖音评论接口授权范围：https://open.douyin.com/platform/resource/docs/openapi/enterprise-open-ability/comment-management-enterprise/comment-list/
- 小红书当前开放范围：https://openaccount.xiaohongshu.com/docs/scope
