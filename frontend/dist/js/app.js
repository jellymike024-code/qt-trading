import { MarketGlobe } from './globe.js';
import { mountLiveUI, renderLiveEvidence } from './live-ui.js?v=20261026-relay1';
import { MARKETS, CITY_ORDER, STRIP_ORDER, getMarket, getSector, signed, number, localTime, sessionReference } from './markets.js';

const $ = selector => document.querySelector(selector);
const state = { city: 'shanghai', sector: MARKETS.shanghai.sectors[0].name, context: null, newsFilter: 'all', analysisMode:'topics', rotating: true };
let globe, toastTimer;
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const safeUrl = value => { try { const url = new URL(value, location.origin); return ['http:', 'https:'].includes(url.protocol) ? url.href : '#'; } catch { return '#'; } };
const numberClass = value => !Number.isFinite(value) ? 'missing-number' : value >= 0 ? 'positive-number' : 'negative-number';

function applyQuotes(snapshot) {
  for(const [id,market] of Object.entries(MARKETS)) {
    const region=snapshot?.regions?.[id];
    market.quote=region?.index||null;market.value=market.quote?.price??null;market.change=market.quote?.change_pct??null;
    for(const sector of market.sectors){const data=region?.sectors?.[sector.name];sector.change=data?.change_pct??null;sector.coverage=data?.coverage??0;sector.session_date=data?.session_date??null;for(const stock of sector.stocks){stock.quote=region?.stocks?.[stock.code]||null;stock.price=stock.quote?.price??null;stock.change=stock.quote?.change_pct??null;}}
  }
}

function sparkline(values, { color = '#369fea', fill = false, id = 'spark', grid = false } = {}) {
  const width = 300, height = 76, min = Math.min(...values), max = Math.max(...values), range = Math.max(max-min, 1e-8);
  const points = values.map((value, i) => [i / Math.max(values.length-1, 1) * width, 7 + (1-(value-min)/range)*(height-16)]);
  const path = points.map(([x,y],i) => `${i?'L':'M'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const gridMarkup = grid ? [15,38,62].map(y => `<line x1="0" y1="${y}" x2="300" y2="${y}" stroke="#17364e" stroke-width=".6" stroke-dasharray="3 5"/>`).join('') : '';
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="${grid?'历史组合净值走势':'真实历史价格走势'}">${fill?`<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="${color}" stop-opacity=".2"/><stop offset="100%" stop-color="${color}" stop-opacity="0"/></linearGradient></defs><path d="${path} L300,76 L0,76 Z" fill="url(#${id})"/>`:''}${gridMarkup}<path d="${path}" fill="none" stroke="${color}" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}

function renderMarketStrip() {
  $('#market-strip').innerHTML = STRIP_ORDER.map(id => {
    const market=MARKETS[id], color=market.change>=0?'#fa596e':'#35cba6';
    const history=market.quote?.history||[], provenance=market.quote?`${market.quote.source} · ${market.quote.session_date||'时间未知'}${market.quote.status==='stale'?' · 已陈旧':''}`:'真实行情缺失';
    return `<button class="market-card ${id===state.city?'active':''}" data-market="${id}" aria-label="查看${market.city}市场，${escapeHtml(provenance)}" aria-pressed="${id===state.city}"><div class="market-card-top"><span>${market.index}</span><small>${market.code}</small></div><strong class="market-value ${numberClass(market.change)}">${number(market.value)}</strong><span class="market-change ${numberClass(market.change)}">${signed(market.change)} <span style="opacity:.6;font-size:8px;margin-left:5px">${market.city}</span></span><small class="market-provenance">${escapeHtml(provenance)}</small><div class="market-spark">${history.length>1?sparkline(history.map(p=>p.close),{color,fill:true,id:`m${id}`}):''}</div></button>`;
  }).join('');
  $('#market-strip').querySelectorAll('[data-market]').forEach(button=>button.addEventListener('click',()=>selectCity(button.dataset.market)));
}

function renderCityNav() {
  $('#city-navigation').innerHTML = CITY_ORDER.map(id => {
    const m=MARKETS[id];return `<button class="city-chip ${id===state.city?'active':''}" data-city="${id}" aria-pressed="${id===state.city}"><span class="chip-code">${m.code}</span><span class="chip-name"><strong>${m.city}</strong><small>${m.en}</small></span><i class="chip-dot"></i></button>`;
  }).join('');
  $('#city-navigation').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>selectCity(button.dataset.city)));
}

function renderRegion() {
  const market=getMarket(state.city);
  $('#region-monogram').textContent=market.code.slice(0,2);
  $('#region-country').textContent=market.country;
  $('#region-city').innerHTML=`${market.city} <span>${market.en}</span>`;
  $('#region-exchange').textContent=market.exchange;
  $('#region-description').textContent=market.description;
  $('#region-data-source').innerHTML=market.quote?`<a href="${escapeHtml(safeUrl(market.quote.source_url))}" target="_blank" rel="noopener noreferrer">${escapeHtml(market.quote.source)} ↗</a> · ${escapeHtml(market.quote.session_date||'时间未知')} · ${market.quote.interval==='1d'?'日线收盘':'公开报价/可能延迟'}${market.quote.status==='stale'?' · 陈旧数据':''}`:'该市场真实行情暂不可用，不显示估算价格';
  $('#region-index-name').textContent=market.index;
  $('#region-index-value').textContent=number(market.value);
  $('#region-index-value').className=numberClass(market.change);
  $('#region-index-change').textContent=signed(market.change);
  $('#region-index-change').className=numberClass(market.change);
  $('#focus-coordinates').textContent=`${Math.abs(market.lat).toFixed(2)}° ${market.lat>=0?'N':'S'} / ${Math.abs(market.lon).toFixed(2)}° ${market.lon>=0?'E':'W'}`;
  $('#focus-timezone').textContent=market.offset;
  renderSectors(); updateClocks();
}

function renderSectors() {
  const market=getMarket(state.city);
  $('#sector-grid').innerHTML=market.sectors.map((sector,i)=>`<button class="sector-cell ${!Number.isFinite(sector.change)?'missing':sector.change<0?'negative':''} ${sector.name===state.sector?'active':''}" data-sector="${i}" title="观察样本等权均值，非官方行业指数；有效样本${sector.coverage||0}/${sector.stocks.length}；${sector.session_date||'数据缺失'}" aria-label="${market.city}${sector.name}观察样本涨跌幅${signed(sector.change)}" aria-pressed="${sector.name===state.sector}"><span>${sector.name}</span><b>${signed(sector.change)}</b></button>`).join('');
  $('#sector-grid').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{state.sector=market.sectors[+button.dataset.sector].name;renderSectors();}));
  const sector=getSector(state.city,state.sector);
  $('.stock-table thead th:nth-child(2)').textContent=`报价/${market.currency}`;
  $('#sector-stocks-title').textContent=`${sector.name} · 观察样本`;
  $('#sector-stocks').innerHTML=sector.stocks.map(stock=>`<tr title="${escapeHtml(stock.quote?`${stock.quote.source} · ${stock.quote.quote_time||stock.quote.session_date} · ${stock.quote.currency||market.currency} · ${stock.quote.session_note||'可能延迟'}`:'真实行情缺失')}"><td>${stock.name}<small>${stock.code}${stock.quote?' · '+escapeHtml(stock.quote.session_date):' · 缺失'}</small></td><td>${number(stock.price,market.currency==='KRW'?0:2)}</td><td class="${numberClass(stock.change)}">${signed(stock.change)}</td></tr>`).join('');
  $('#sector-stocks').closest('table').setAttribute('aria-label',`${market.city}${sector.name}观察样本，仅展示有来源的真实报价`);
  if(state.city==='shanghai')$('#sector-stocks').querySelectorAll('tr').forEach((row,i)=>{const stock=sector.stocks[i];row.tabIndex=0;const choose=()=>state.selectBook?.((stock.code.startsWith('6')?'sh':'sz')+stock.code);row.addEventListener('click',choose);row.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});});
}

function selectCity(id,{notify=true}={}) {
  if(!MARKETS[id])return;
  state.city=id;state.sector=MARKETS[id].sectors[0].name;
  renderMarketStrip();renderCityNav();renderRegion();renderNews();globe?.focus(id);
  if(notify)toast(`已聚焦${MARKETS[id].city} · 点击板块查看对应个股`);
  history.replaceState(null,'',`#${id}`);
}

function updateClocks() {
  const date=new Date();
  $('#header-clock').textContent=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(date);
  $('#region-local-time').textContent=localTime(state.city,date)+' LOCAL';
  const status=$('#region-session-status');status.textContent=sessionReference(state.city,date);status.title='仅按当地常规交易时段判断；未接入各交易所节假日历，不代表实际开市状态。';
}

function rotationChanged(value) {
  state.rotating=value;
  $('#rotation-toggle').setAttribute('aria-pressed',String(value));
  $('#rotation-label').textContent=value?'暂停自转':'恢复自转';$('#rotation-icon').textContent=value?'Ⅱ':'▷';
  $('#globe-state').textContent=value?'地球自动旋转中':'视角已锁定';
}

function toast(message) {const node=$('#toast');node.textContent=message;node.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove('visible'),2600);}

function formatTime(value) {try{return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}catch{return '—';}}
function category(item){return /证监会|央行|政策|注册|国常会|降息|降准/.test(item.title)?'policy':'market';}

function renderNews() {
  const publicSnapshot=state.context?.publication?.mode==='public_snapshot';
  const comments=state.context?.bettafish?.dashboard?.regions?.[state.city];
  const items=[...(state.context?.social?.items||[]).filter(item=>!item.cities||item.cities.includes(state.city)),...(comments?.items||[]).filter(item=>item.traceable)];
  const filtered=(state.newsFilter==='all'?items:state.newsFilter==='comments'?items.filter(item=>item.content_kind==='comment'):items.filter(item=>category(item)===state.newsFilter)).sort((a,b)=>(Date.parse(b.published_at||b.first_seen_at||b.observed_at)||0)-(Date.parse(a.published_at||a.first_seen_at||a.observed_at)||0));
  $('#news-count').textContent=`${items.length} 条`;
  const platformNames={weibo:'微博',douyin:'抖音',xiaohongshu:'小红书',eastmoney_news:'东方财富',guba:'股吧',bilibili:'B站',tieba:'贴吧',zhihu:'知乎',kuaishou:'快手',toutiao:'今日头条'};
  $('#news-list').innerHTML=filtered.length?filtered.slice(0,24).map(item=>`<a class="news-item" href="${escapeHtml(safeUrl(item.source_url))}" target="_blank" rel="noopener noreferrer" title="${escapeHtml((item.published_at?'发布时间 '+item.published_at:'发布时间未知')+'；采集时间 '+item.observed_at)}"><div class="news-item-meta"><time>${formatTime(item.published_at||item.first_seen_at||item.observed_at)}</time><span class="news-source">${platformNames[item.platform]||'公开来源'}</span><span class="news-tag">${item.content_kind==='comment'?'真实评论':item.content_kind==='hot_topic'?'热榜线索':item.content_kind==='news_headline'?'财经报道':'原始内容'}</span></div><div class="news-item-title">${escapeHtml(item.title)}</div></a>`).join(''):`<p class="empty-state">${state.newsFilter==='comments'?'当前地区尚无已采集、可追溯的真实评论。':'当前筛选下没有本地舆情候选。'}<br>数据缺失不代表市场没有风险。</p>`;
  const status=state.context?.social?.status;
  $('#feed-subtitle').textContent=status?`采集快照 ${formatTime(status.at)} · 非实时`:'本地舆情暂不可用';
  if(status){const healthy=Object.values(status.sources||{}).filter(s=>s.status==='ok').length;const total=Object.keys(status.sources||{}).length;$('#source-health').textContent=`公开源 ${healthy}/${total} · 真实评论 ${state.context?.bettafish?.status?.comments||0} 条`;}
  else $('#source-health').textContent='未连接本地数据 · 不生成虚构舆情';
  if(publicSnapshot)$('#source-health').textContent+=' · 原始评论未公开';
  const words=new Map();for(const item of items)for(const word of item.matched_keywords||[])words.set(word,(words.get(word)||0)+1);
  const top=[...words.entries()].sort((a,b)=>b[1]-a[1]).slice(0,5),max=Math.max(1,...top.map(x=>x[1]));
  $('#keyword-list').innerHTML=top.length?top.map(([word,count],i)=>`<div class="keyword-row"><em>${i+1}</em><span>${escapeHtml(word)}</span><div class="keyword-track"><i style="width:${count/max*100}%"></i></div><b>${count}</b></div>`).join(''):'<p class="empty-state">等待财经主题数据</p>';
  const integration=state.context?.bettafish?.status;
  const collector=state.context?.bettafish?.collector,platform=collector?.active_platform,
    platformState=platform?collector?.platforms?.[platform]?.status:null,
    collectorLabels={awaiting_login:'等待扫码登录',manual_verification:'等待手动验证',collecting:'正在采集',starting:'正在启动'};
  $('#bettafish-status').textContent=platform?`BettaFish ${platform.toUpperCase()} · ${collectorLabels[platformState]||platformState||'运行中'}`:
    integration?.status==='connected'?`BettaFish 已导入 · 评论 ${integration.comments} 条`:
    collector?.status==='ready_for_login'?'BettaFish 采集端已部署 · 等待首次扫码':collector?.status==='idle'?'BettaFish 采集端已部署 · 请查看平台状态':'BettaFish 适配就绪 · 评论源未连接';
  if(publicSnapshot)$('#bettafish-status').textContent='公开快照 · 采集端与原始评论保留在私有运行环境';
  const analysis=state.context?.social?.analysis||[],groups=new Map();for(const row of analysis)if(!groups.has(row.topic_group||row.record_id))groups.set(row.topic_group||row.record_id,row);
  const unique=[...groups.values()],counts={positive:0,neutral:0,negative:0};
  for(const item of unique)counts[item.score>.15?'positive':item.score<-.15?'negative':'neutral']++;
  const isComments=state.analysisMode==='comments',total=isComments?(comments?.scored_sample||0):unique.length;
  $('#sentiment-total').textContent=total||'—';$('#sentiment-unit').textContent=isComments?'已评分真实评论':'模型解释主题';
  for(const type of Object.keys(counts))$(`#sentiment-${type}`).textContent=total?`${Math.round(isComments?comments.sentiment_pct[type]:counts[type]/unique.length*100)}%`:'—';
  $('#donut-arc').style.strokeDasharray=total?'288 302':'0 302';
  $('#sentiment-wave').textContent=isComments?`原始 ${comments?.comments||0} · 去重 ${comments?.deduplicated_sample||0} · 已评分 ${comments?.scored_sample||0}`:'主题判断为模型解释，不是投资者支持率';
  $('#sentiment-note').textContent=isComments?'未评分不计为中性 · 缺失比例留空':'新闻事实与模型判断分开 · 非涨跌概率';
  if(publicSnapshot)$('#sentiment-note').textContent='原始评论及模拟账户未公开 · 未展示不表示没有采集';
  if(status?.analysis_method==='transparent_keyword_rules_v1')renderLiveEvidence(state,filtered);
}

function renderResearch() {
  const research=state.context?.research, rows=research?.holdout||{}, names={ma_trend:'均线趋势',donchian:'唐奇安突破',ts_momentum:'时序动量',volume_momentum:'量价动量'};
  $('#strategy-table').innerHTML=Object.entries(names).map(([key,name])=>{const row=rows[key];return `<tr><td>${name}</td><td class="${row?numberClass(row.total_return):''}">${row?signed(row.total_return*100):'—'}</td><td>${row?(row.max_drawdown*100).toFixed(2)+'%':'—'}</td><td class="strategy-state">${row?'模拟验证':'等待数据'}</td></tr>`;}).join('');
  $('#strategy-date').textContent=research?.latest_session?new Date(research.latest_session).toLocaleDateString('sv-SE')+' · 样本外':'未连接研究快照';
  const targets=state.context?.paper?.signals?.ensemble?.targets;
  $('#portfolio-exposure').textContent=targets?`${Math.round(Object.values(targets).reduce((sum,t)=>sum+(+t.weight||0),0)*100)}%`:'—';
  const curve=state.context?.equity||[];
  $('#equity-chart').innerHTML=curve.length>1?sparkline(curve.map(row=>row.equity),{color:'#40beff',fill:true,id:'equity-gradient',grid:true}):'<div class="empty-state" style="padding:15px 4px">暂无可展示的历史净值</div>';
  const value=rows.ensemble?.total_return;
  $('#equity-caption').textContent=value!==undefined?`历史样本外组合收益 ${signed(value*100)}`:'历史样本外组合净值 · 等待数据';
  $('#equity-mode').textContent=curve.length?'历史样本外':'未连接数据';
  const capital=state.context?.configuration?.initial_cash,budget=state.context?.configuration?.risk?.max_drawdown_budget;
  $('#configured-capital').innerHTML=Number.isFinite(capital)?`${number(capital/10000,0)}<span>万</span>`:'—';
  $('#configured-drawdown').innerHTML=Number.isFinite(budget)?`${number(budget*100,0)}<span>%</span>`:'—';
}

async function loadContext() {
  try {
    const response=await fetch(document.documentElement.dataset.contextUrl||'./api/context',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    state.context=await response.json();
    const timestamp=state.context?.social?.status?.at;
    $('#data-timestamp').textContent=`舆情快照 ${timestamp?new Date(timestamp).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'未采集'}　·　行情均为有来源的公开数据`;
  } catch(error) {console.warn('Context snapshot is unavailable',error);state.context??={};$('#data-timestamp').textContent='研究快照不可用 · 正在连接公开行情';}
  applyQuotes(state.context?.markets);renderMarketStrip();renderRegion();renderNews();renderResearch();
  const available=Object.values(MARKETS).filter(m=>Number.isFinite(m.value)).length;
  $('#quote-badge').textContent=available===4?'公开行情 · 可能延迟':`行情覆盖 ${available}/4`;
}

renderMarketStrip();renderCityNav();renderRegion();
$('#rotation-toggle').addEventListener('click',()=>globe?.setRotation(!state.rotating));
$('#reset-view').addEventListener('click',()=>{selectCity('shanghai',{notify:false});globe?.reset();toast('已恢复上海视角与自动旋转');});
$('#zoom-in').addEventListener('click',()=>globe?.zoom(1));$('#zoom-out').addEventListener('click',()=>globe?.zoom(-1));
document.querySelectorAll('[data-news-filter]').forEach(button=>button.addEventListener('click',()=>{state.newsFilter=button.dataset.newsFilter;document.querySelectorAll('[data-news-filter]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});renderNews();}));
document.querySelectorAll('[data-analysis-mode]').forEach(button=>button.addEventListener('click',()=>{state.analysisMode=button.dataset.analysisMode;document.querySelectorAll('[data-analysis-mode]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});renderNews();}));

globe=new MarketGlobe({canvas:$('#globe-canvas'),container:$('#globe-container'),labels:$('#city-labels'),onSelect:selectCity,onRotation:rotationChanged,
  onReady:({renderer,textured})=>{$('#globe-loading').classList.add('hidden');$('#renderer-badge').innerHTML=`<i></i>${renderer.toUpperCase()} / ${textured?'3D EARTH':'VECTOR EARTH'}`;},
  onError:error=>{$('#globe-loading').innerHTML=`<div class="webgl-error"><strong>WebGL 地球暂不可用</strong>请使用支持WebGL 2的浏览器并开启图形加速。<br>四个地区的市场快捷按钮仍可正常使用。<br><small>${escapeHtml(error.message)}</small></div>`;$('#renderer-badge').textContent='WebGL 不可用';for(const id of ['rotation-toggle','zoom-in','zoom-out'])$("#"+id).disabled=true;},
});
const hash=location.hash.slice(1);if(MARKETS[hash])selectCity(hash,{notify:false});
const clockTimer=setInterval(updateClocks,1000);
loadContext().then(()=>mountLiveUI(state,{quotes:()=>{applyQuotes(state.context.markets);renderMarketStrip();renderRegion();},social:renderNews}));
window.addEventListener('pagehide',()=>{clearInterval(clockTimer);globe?.dispose();});

// Deliberately small inspection surface: no trading or account mutations.
window.orbitDashboard=Object.freeze({selectCity:id=>selectCity(id),getState:()=>({city:state.city,sector:state.sector,rotating:state.rotating,hasLocalSnapshot:Boolean(state.context),quoteMode:'real_sources_only'}),renderer:()=>globe?.renderer?.getContext()?.getParameter(globe.renderer.getContext().VERSION)||null});
