# BettaFish / MindSpider 大屏适配

参考上游：https://github.com/666ghj/BettaFish ，固定审阅版本b67928ff1ce515dd7672408221fb1e9500d0972d。reference/保存审阅源文件及上游LICENSE；upstream.json记录版本。本地quantlab/bettafish.py是独立数据互通适配器。

**已部署完整MindSpider采集端、固定版本MediaCrawler、独立Python环境、PostgreSQL数据库及26张表。** 实际平台登录与采集结果见data/bettafish/collector_status.json。平台评论能否取得须以真实试跑为准，不能用“安装完成”代替“采集成功”。未配置付费模型；话题研判沿用每日聊天Agent，固定采集关键词明确标为人工观察范围。

数据库仅监听127.0.0.1:55432，不安装系统服务；数据保存在postgres/data。独立环境、密码及新登录会话均在本目录，不修改原量化环境、不导入日常浏览器Cookie。密码随机生成且不输出；.secrets.json、runtime/.env、browser_profiles及运行日志均不纳入版本管理。

## 启动与手动登录

从项目根目录运行，首次优先使用正常网页手动登录：

```powershell
.\Open-Sentiment-Login.ps1 -Platform wb   # 微博
# 在打开的专用窗口登录，完成后关闭该窗口，再启动采集
.\Start-Sentiment-Collector.ps1 -Platforms wb

.\Open-Sentiment-Login.ps1 -Platform xhs  # 小红书
.\Start-Sentiment-Collector.ps1 -Platforms xhs
.\Open-Sentiment-Login.ps1 -Platform dy   # 抖音
.\Start-Sentiment-Collector.ps1 -Platforms dy
```

每次手动登录完成都需先关闭专用窗口，避免同一浏览器配置被两个进程占用。三个平台会话分别保存；不能在日常浏览器登录后假设采集端已登录。登录流程支持正常账号操作，无需向聊天发送密码、Cookie或验证码。采集进程遇到需要登录时也可等待用户手动完成，10分钟超时；自动模式会立即报告登录失效。抖音平台验证保留人工处理，不自动拖滑块。

默认每平台2个ETF关键词、每关键词配置20条内容（上游整页返回可能多于页面标称值）、每条最多10条一级评论、单并发、请求间隔5秒，不下载媒体或二级评论。首次加`-Smoke`只用第一个关键词和一页。只有本轮确实取得新评论、验证访问可用的平台加入collector_config.json的automatic_platforms；每日运行时只执行这些平台。超时或部分帖子评论不可见不会删除已取得的原始数据，必须保留覆盖缺口。

```powershell
.\Start-Sentiment-Collector.ps1 -Automatic -Foreground
.\Stop-Sentiment-Collector.ps1
.\integrations\BettaFish\.venv\Scripts\python.exe -E integrations/BettaFish/collector.py status
.\integrations\BettaFish\.venv\Scripts\python.exe -E integrations/BettaFish/collector.py export
```

本地[采集状态页](http://127.0.0.1:8765/collector.html)每5秒读取真实状态。正文和评论写入PostgreSQL；只读导出会将评论关联到实际采得的父帖URL、首次采集时间和关键词，再自动sync入大屏。成功退出但0条评论仍显示0，不生成替代样本。数据库可用collector.py db-start/db-stop独立控制；Stop脚本只停止其记录的采集进程，不删除数据库或登录会话。

部署记录：collector-source.json、mediacrawler-source.json逐文件Git对象哈希；postgres-source.json官方ZIP来源、范围下载哈希及CRC验证；requirements.lock.txt记录实际隔离环境版本。上游较旧的依赖固定值不完全支持当前Python3.12，使用隔离环境兼容版本，核心平台导入与数据库初始化已实际验证。

## 已适配

- 接受MindSpider/MediaCrawler原始JSON/JSONL、InsightEngine的DBResponse.results。标题、评论数量及生成报告不能代替评论正文；查询报错和空结果分开报告。
- 支持微博、小红书、抖音、B站、贴吧、知乎、快手及股吧数据规范。兼容接口不表示这些评论已经接通。
- 保留平台ID、原始正文、原链接、发布时间、真实首次获取时间、导入文件SHA256。缺少原链接的内容可保存，但不进入可追溯评论情绪样本。作者ID散列保存。
- 按城市关键词匹配上海/纽约/伦敦/首尔；这是可解释规则匹配，并非已经部署的语义实体模型。
- 最近24小时样本、规范化相同文本去重、同一已知作者最多3条；未知作者不伪造身份或人数。这里只限制重复/集中样本，不能宣称识别出水军。
- 另存模型评分和理由、模型名称、分析时间、置信度。默认置信度至少0.6才进入比例，分母仅为已评分去重样本；未评分不算中性。比例不代表全体投资者。
- 界面所选城市联动评论及评分；无评论时留空，主题分析另行展示。社交解释不修改冻结交易实验。

## 接收真实导出

将真实原始导出放入integrations/BettaFish/inbox/，扩展名.json或.jsonl，再运行：

```powershell
python -m quantlab.bettafish sync
python -m quantlab.bettafish status
```

输入字段：platform、content_type、comment_id/id、content/title_or_content、publish_time/create_time、source_url/url、可选user_id及observed_at。MediaCrawler文件名如weibo_comments_*.json可提供平台和评论类型提示。毫秒/秒时间戳自动识别；没有时区的源时间按bettafish_config.json的默认时区解释。平台可能不同，跨境数据必须提供显式时区。

评论表导出若没有URL，应从已采集的父帖子连接原链接后导出；不能猜造链接或把搜索页当评论证据。原始采集时间没有提供时，首次可用时间取本次导入，不能用于过去回测。同ID正文变化会报告冲突，不覆盖旧证据。24小时内没有数据与未连接分别报告。

输出：data/bettafish/records.jsonl、status.json、dashboard.json；reports/bettafish_status.json；大屏通过只读/api/context读取。annotations.jsonl单独保存解释。

## 模型解释格式

未来获得真实评论后，由本聊天研究Agent或用户配置的本地模型对真实record_id提供JSON数组，每项字段：record_id、score[-1,1]、confidence[0,1]、analyzed_at（真实完成时间带时区）、model、reason。不要预先填充样例评分至生产目录。

```powershell
python -m quantlab.bettafish annotate --input data/bettafish/agent_inbox.json
```

校验要求是真实可追溯评论，且分析时间晚于可用时间；不存在的样本、缺模型来源、非有限数值、未来时间均拒绝。

## 免费模式与上游完整部署的边界

参考上游BroadTopicExtraction的“发现话题→采集原文→分析→报告”流程，当前用已验证可访问的UAPI热榜替换本机返回403的NewsNow实例，并增加知乎、B站、今日头条免费源。只有实际获取成功才更新状态，不将拒绝访问伪装为成功。

MindSpider采集数据库和依赖已部署，相应平台可用访问方式仍需首次手动登录。BettaFish的Query/Media/Insight/Report完整多Agent报告服务是另一个运行层，尚未配置模型/搜索接口；本部署运行采集端，不假装这些报告引擎已经运行。股吧不属于该版本MediaCrawler支持的平台，东方财富快讯保留独立采集。
