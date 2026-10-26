# 观澜 QUANT ORBIT

打开网页即启动公开行情与舆情自动更新：A股/美股5秒、财经快讯30秒、热榜120秒；A股支持四只研究ETF及24只观察股票的五档盘口。证据自动分析当前为明确标注的关键词规则，不是已部署的大模型Agent；伦敦/首尔仍保留真实快照日期。标签页隐藏时暂停，恢复可见后继续；可使用底部按钮暂停。详情见 [部署与数据说明](../DEPLOYMENT.md)。

本地WebGL全球市场监控前端，基于用户的科技大屏参考图制作。依赖、地球纹理、国家边界均保存在dist内，加载界面不需要外部CDN。

## 启动

在项目根目录运行：

```powershell
.\Start-Dashboard.ps1
```

地址默认为 http://127.0.0.1:8765 。服务仅监听本机回环地址。`-NoBrowser`只启动服务；`-Port 8766`可更换端口。可运行根目录Stop-Dashboard.ps1停止脚本所记录的服务。

也可以前台启动：

```powershell
python frontend/serve.py --port 8765
```

需要通过HTTP打开，不能直接双击index.html；浏览器ES模块和纹理加载需要本地HTTP环境。需要支持WebGL 2的现代浏览器，并启用图形加速。无法建立WebGL时会显示错误说明，地区快捷按钮仍可切换市场信息。

## 交互

- 地球根据动画时间持续自转，切换后台标签页会暂停绘制以减少资源消耗。
- 鼠标拖动旋转、滚轮或加减按钮缩放；触摸设备支持拖动与双指缩放。
- 地球上的城市标记、底部城市按钮及顶部指数卡片均可选择伦敦、纽约、首尔、上海。
- 选择城市后平滑聚焦、暂停自转，更新当地时间、交易所、代表指数、8个板块及板块内3只代表个股。
- 点击板块可继续切换代表个股；四地共32个板块条目。地球背面的城市标签隐藏，底部快捷按钮始终可用。
- “恢复自转”重新启动动画，“重置视角”返回上海和默认缩放。
- 支持地址锚点 #london、#newyork、#seoul、#shanghai；支持键盘按钮与减少动画偏好。

## 数据口径

所有报价来自实际读取的公开来源，缺失显示`—`，没有随机行情、填充价格或合成曲线。数据来源、交易日期和陈旧状态会显示在界面；公开来源可能延迟，各地报价不一定属于同一交易时点。四地指数为上证指数、纳斯达克综合指数、富时100和KOSPI。来源及更新限制见根目录 [REAL_DATA.md](../REAL_DATA.md)。

板块是人工维护的观察列表，每组3只样本；仅在三者都有效且交易日期一致时计算等权涨跌均值，**不是官方行业指数**。城市时钟使用真实时区；交易状态只按常规时段参考，未连接交易所节假日历。地球弧线和粒子用于地理导航，不能解释为真实资金流。

`/api/context`是只读本地接口，读取data/markets、data/social、data/bettafish、reports及state。页面每60秒读取快照；这个频率不等于上游行情更新频率。页面不下单或触发采集。回测净值来自真实历史行情上的模拟计算，标明“历史样本外”，不是券商实绩。

舆情区分热榜线索、财经报道、原始帖子及真实评论。BettaFish兼容导入接口按所选城市联动；默认评论视图，未评分样本不计为中性，0个已评分样本时比例留空。主题视图是Agent解释，不能当作投资者支持率。MindSpider完整采集端及数据库已部署，实际登录与采集结果见[本机状态页](http://127.0.0.1:8765/collector.html)。接入说明见 [BettaFish适配](../integrations/BettaFish/README.md)。

## 文件

- `dist/index.html`、`dist/styles.css`：响应式大屏布局。
- `dist/js/globe.js`：Three.js WebGL球体、地理边界、大气光晕、流动弧线、标记拾取与交互。
- `dist/js/markets.js`、`dist/data/market_universe.json`：四地观察列表、经纬度与时区逻辑；初始报价全部为空。
- `dist/js/app.js`：市场/板块联动、图表、快照读取和界面状态。
- `serve.py`：本机HTTP服务与只读快照接口，不提供文件目录浏览。

Three.js固定为0.180.0，保留MIT许可证。地球纹理来自Three.js公开示例（NASA地球素材），边界来自Natural Earth公开数据；下载源及SHA256在dist/assets/manifest.json。依赖缺失时可重新运行`python frontend/download_assets.py`。

## 检查

```powershell
node --check frontend/dist/js/app.js
node --check frontend/dist/js/globe.js
node --test frontend/tests/markets.test.js
```
