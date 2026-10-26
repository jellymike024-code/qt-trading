# GitHub 云端与版本管理

仓库：https://github.com/jellymike024-code/qt-trading

## 公开大屏：GitHub Pages

### 打开页面自动更新

浏览器直连接口在用户实际访问中失败，不能仅凭脚本请求成功认定实时链路可用。修正版本通过`services/relay/`的只读云端中继获取数据，并提供每个来源的传输方式、最后成功时间和错误诊断。2026-10-26已按用户最新授权公开中继，匿名读取实测成功；前端只请求该服务，不携带凭据。开盘期间与休市期间均保留原始行情时间，重新请求不会让收盘价伪装成新成交。

页面通过已开放的只读中继读取数据：腾讯证券 A股/美股批量报价与 A股五档每5秒轮询，东方财富作为报价备选；东方财富财经快讯每30秒、微博/小红书/抖音/知乎/B站/今日头条公开热榜每120秒更新。打开页面默认开启，有暂停/恢复按钮；隐藏标签页暂停新请求，失败后退避重试。关闭页面后不再持续采集。

五档为公开Level 1快照，数量按手（100股/份）展示；不是逐笔或Level 2。交易所源时间、浏览器实际接收时间分开保留，源延迟/休市时不伪造价格跳动。伦敦、首尔尚未验证可在浏览器直接跨域读取的实时源，继续显示原快照及原日期，不称为全部市场实时。

舆情实时证据区会对新取得的公开标题/快讯摘要做财经相关性、标题规范化去重、风险/积极词与不确定表述归类，展示原链接、发布时间、首次观察时间和真实分析时间。界面明确标为**关键词规则分析**，不冒充大模型Agent、不把热榜当作原始评论、不给未判方向的内容强制打分。微博/小红书/抖音的已登录评论采集和大模型Agent仍需私有后台与模型服务，GitHub Pages本身不能运行这些进程；本次没有把凭据或模型密钥放进网页。

5/30/120秒是请求间隔，不是上游更新或低延迟保证。公开接口可能延迟、限流、改变跨域策略或中断，界面保留缺口和来源时间；实时观察独立于冻结的日线模拟，不修改其目标或成交。

`.github/workflows/pages.yml` 在 main 更新、手动执行和每日北京时间16:25构建公开大屏。页面保留WebGL、地球自转、四地选择和板块交互。GitHub Actions定时执行可能延迟，数据日期以原始来源为准。

首次在仓库 Settings → Pages 将 Source 设为 **GitHub Actions**（若自动启用权限不足），然后重跑 Publish public dashboard。成功地址为 https://jellymike024-code.github.io/qt-trading/ 。只有部署工作流成功并且网址实际可访问才算已上线。

公开构建从 `deploy/seed/context.json` 中经过白名单筛选的真实快照开始，随后更新免费公开行情和热榜。免费接口失败时保留缺口；伦敦个股的人工网页核验不能由云端伪造。研究曲线保留其真实历史日期。公开页没有本地模拟仓位、资金、订单、原始评论、作者、登录二维码、数据库或运行日志。网页每分钟读取静态快照不代表每分钟采集。

在本机显式更新公开种子（会发布行情及历史研究汇总，不包含账本/评论）：

```powershell
python -m deploy.public_site --write-seed deploy/seed/context.json
git add deploy/seed/context.json
git commit -m "Refresh sanitized public dashboard snapshot"
git push
```

## 完整 Python 环境：Codespaces

访问 https://codespaces.new/jellymike024-code/qt-trading 。`.devcontainer` 安装Python、Node、PostgreSQL、固定版本MindSpider/MediaCrawler依赖与Chromium，校验上游文件哈希并初始化独立数据库，自动启动8765端口。端口保持私有，使用GitHub登录访问。Codespaces是会休眠的开发运行环境，不能当作24小时常驻服务器；额度和费用以账号设置为准。

本机数据库、模拟账本及社交平台登录会话**不会搬到云端**。新环境的 `automatic_platforms` 为空；只有新环境确实采到评论才启用。已有本机手动登录只对Windows专用配置有效。云端默认无图形登录入口，因此完整代码可运行不表示三家平台评论能立即自动采集；公开热榜不依赖登录。不要提交Cookie或把本机浏览器配置上传。

云端终端：

```bash
.venv/bin/python -m quantlab.social collect
.venv/bin/python -m quantlab.markets collect
integrations/BettaFish/.venv/bin/python -E integrations/BettaFish/collector.py status
integrations/BettaFish/.venv/bin/python -E integrations/BettaFish/collector.py crawl --automatic
```

如果希望建立另一个模拟实验，可在新环境单独执行 `.venv/bin/python -m quantlab cycle`；它不续接Windows冻结实验。原实验仍由本机每天16:15运行。当前日线日期校验故障不会通过部署更换策略或数据填充来掩盖。

## 版本管理

main是发布分支，日常修改建议创建功能分支并提交PR。Project checks在push/PR执行Python正确性测试、JavaScript检查及公开构建。所有源码、固定上游版本记录、依赖和部署脚本纳入Git；下载的运行时通过哈希清单重建，避免重复上传上游完整仓库。保留上游GPL许可证、Three.js MIT许可证和素材来源。此仓库未另行授权本项目原创代码的开源许可证。

```powershell
git switch -c feature/your-change
git add <已检查的文件>
git commit -m "Describe the change"
git push -u origin feature/your-change
```

`.gitignore` 排除全部 data/、state/、reports/、登录会话、密码、环境文件、数据库、下载包和虚拟环境。不要用 `git add -f` 上传这些内容。公开仓库的代码版本管理不等于私有运行数据备份。
