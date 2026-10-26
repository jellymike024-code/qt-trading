# 第三方组件与来源

- Three.js 0.180.0：MIT，许可证在 `frontend/dist/vendor/THREE-LICENSE.txt`。纹理和Natural Earth边界来源及SHA256在 `frontend/dist/assets/manifest.json`。
- BettaFish/MindSpider：固定版本及文件Git对象哈希在 `integrations/BettaFish/collector-source.json`，上游GPL v2许可证保留在 `integrations/BettaFish/reference/LICENSE`。
- MediaCrawler：固定版本及文件Git对象哈希在 `integrations/BettaFish/mediacrawler-source.json`。该固定版本源文件标注 **NON-COMMERCIAL LEARNING LICENSE 1.1**；`deploy/bootstrap_collector.py` 会同时恢复该版本的原始 LICENSE，位于重建的 `integrations/BettaFish/runtime/MindSpider/DeepSentimentCrawling/MediaCrawler/LICENSE`。
- 不将上游代码重新声明为本项目原创代码，也不修改上游许可证。下载的运行时、Python依赖和数据库二进制不重复存入本仓库。

公开行情和热榜的访问不等于获得其内容的所有权；公开页保留来源链接、时间和数据口径。
