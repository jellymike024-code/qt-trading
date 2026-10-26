# 公开数据中继

业务源码保存在本目录，供现有大屏读取固定的公开行情和新闻源。`core.mjs`不接受任意URL、账号、Cookie、交易指令；`symbols.mjs`固定54个公开行情代码。Node测试覆盖只读限制、来源白名单、跨域、缓存时间和并发合并。

部署环境使用Sites/Vinext包装：`app/api/relay/route.ts`将GET/OPTIONS转发给`handleRelay`，部署源在本机独立的`realtime-service/`工作目录及Sites专用Git源库。业务代码在GitHub持续管理，不上传服务凭据、node_modules或部署缓存。

项目ID：`appgprj_6adee789249c8191bb8de14de747ef4b`。服务地址：`https://quant-orbit-live-data.rightthyme5.chatgpt.site`。2026-10-26已按用户最新明确授权开放只读公开访问，无需账号、Cookie或前端密钥。

路由：`/api/relay?feed=tencent`、`feed=news`、`feed=hotboard&platform=weibo`、`feed=eastmoney&symbol=sh510300`、`feed=health`。

成功响应保留服务器实际获取时间、上游地址、原始字节编码和缓存年龄。行情缓存3秒、快讯20秒、热榜90秒；重复读取不会改写源时间。部署环境实际验证腾讯54条报价（包括ETF五档）、东方财富50条快讯和微博50条热榜成功。

如果部署到其他兼容Worker环境，`worker.mjs`提供同一业务入口；需启用`nodejs_compat`支持`node:buffer`。不需要行情密钥。中继负责读数据，原始评论采集与大模型Agent仍是独立后台功能。
