import { LiveFeed, ETF_WATCHLIST, mergeQuotes, recalculateSectors, topicKey } from './live-data.js?v=20261026-relay1';
import { MARKETS, number, signed, sessionReference } from './markets.js';
const $=selector=>document.querySelector(selector);
const time=value=>new Date(value).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const color=value=>!Number.isFinite(value)?'missing-number':value>=0?'positive-number':'negative-number';
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function renderLiveEvidence(state,filtered){
  const social=state.context.social,analysis=new Map(social.analysis.map(row=>[row.record_id,row])),seen=new Set(),rows=[];
  for(const item of filtered){const row=analysis.get(item.id);if(row&&!seen.has(row.topic_group)){seen.add(row.topic_group);rows.push(row);}}
  const healthy=Object.values(social.status.sources||{}).filter(s=>s.status==='ok').length,total=Object.keys(social.status.sources||{}).length;
  $('#feed-subtitle').textContent=healthy?`已连接 ${healthy}/${total} · 快讯30秒 / 热榜120秒`:'舆情连接失败 · 暂无新的有效采集';
  $('#source-health').textContent=`本页公开源 ${healthy}/${total} · 热榜及快讯，非评论`;
  $('#bettafish-status').textContent='自动证据归类已开启 · 规则分析 · 新评论采集需私有后端';
  $('#news-list').querySelectorAll('.news-item').forEach((node,i)=>{
    const item=filtered[i],row=analysis.get(item?.id);if(!row)return;
    const detail=document.createElement('div');detail.className='evidence-detail';detail.textContent=`${row.verdict} · ${row.reason}`;node.append(detail);
    if(item.excerpt){const excerpt=document.createElement('div');excerpt.className='evidence-excerpt';excerpt.textContent=item.excerpt;node.append(excerpt);}
    node.title+=`；分析完成 ${row.analyzed_at}；来源更新 ${item.provider_updated_at||'未提供'}；方法 ${row.method}`;
  });
  const labels=document.querySelectorAll('.sentiment-legend>div>span');
  if(state.analysisMode==='comments'){['偏正面','中性','偏负面'].forEach((text,i)=>{labels[i].lastChild.textContent=text;});return;}
  ['偏积极线索','待核查/未定','风险线索'].forEach((text,i)=>{labels[i].lastChild.textContent=text;});
  $('#sentiment-total').textContent=rows.length||'—';$('#sentiment-unit').textContent='去重证据线索';
  const counts={positive:rows.filter(r=>r.verdict==='偏积极线索').length,negative:rows.filter(r=>r.verdict==='风险线索').length};counts.neutral=rows.length-counts.positive-counts.negative;
  for(const type of Object.keys(counts))$('#sentiment-'+type).textContent=rows.length?`${counts[type]} 条`:'—';
  $('#donut-arc').style.strokeDasharray=rows.length?'288 302':'0 302';$('#sentiment-wave').textContent='规范化标题去重 · 规则归类 · 非大模型研判';
  $('#sentiment-note').textContent='保留原链接与来源时间 · 待核原始公告 · 不修改交易目标';
}

export function mountLiveUI(state,renders){
  let selected='sh510300',feed,quotes={},quoteStatus={},lastSourceTime;
  const options=[...ETF_WATCHLIST,...MARKETS.shanghai.sectors.flatMap(s=>s.stocks.map(stock=>[(stock.code.startsWith('6')?'sh':'sz')+stock.code,stock.name]))];
  $('#book-symbol').innerHTML=options.map(([code,name])=>`<option value="${escape(code)}">${escape(name)} · ${code.slice(2)}</option>`).join('');
  function age(){const quote=quotes[selected];if(!quote)return;const seconds=Math.max(0,Math.floor((Date.now()-Date.parse(quote.quote_time))/1000)),session=sessionReference('shanghai'),stale=session==='常规交易时段*'&&seconds>30;
    $('#book-freshness').textContent=`源行情距今 ${seconds<60?seconds+'秒':Math.floor(seconds/60)+'分钟'} · ${stale?'行情可能延迟/陈旧':session} · 每手100股/份`;
    $('#book-freshness').classList.toggle('feed-warning',stale);
  }
  function book(){const quote=quotes[selected],depth=quote?.order_book;
    $('#book-name').textContent=$('#book-symbol').selectedOptions[0]?.textContent||selected;
    $('#book-price').textContent=quote?number(quote.price,3):'—';$('#book-price').className=color(quote?.change_pct);
    $('#book-change').textContent=signed(quote?.change_pct);$('#book-change').className=color(quote?.change_pct);
    const rows=depth?[...depth.asks].reverse().map(row=>({...row,side:'卖'})).concat(depth.bids.map(row=>({...row,side:'买'}))):[];
    $('#book-levels').innerHTML=rows.length?rows.map(row=>`<tr class="${row.side==='卖'?'ask':'bid'}"><td>${row.side}${row.level}</td><td>${number(row.price,3)}</td><td>${number(row.volume_lots,0)}</td></tr>`).join(''):'<tr><td colspan="3">五档盘口暂不可用，缺失不补造</td></tr>';
    $('#book-source').textContent=quote?`${quote.source} · 行情 ${new Date(quote.quote_time).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} · 请求 ${time(quote.observed_at)}`:'正在连接真实报价…';age();
  }
  function status(){const reference=lastSourceTime?' · 源行情 '+time(lastSourceTime):'';
    $('#quote-badge').textContent=!feed?.enabled?'自动更新已暂停':document.hidden?'后台暂停请求':quoteStatus.message||'正在连接实时公开报价';
    $('#data-timestamp').textContent=`行情 5秒 / 快讯 30秒 / 热榜 120秒${reference} · 公开源可能延迟`;
    $('#quote-badge').closest('.demo-badge').classList.toggle('feed-warning',quoteStatus.status==='error');
    const diagnostic=$('#live-health');if(diagnostic&&feed){diagnostic.replaceChildren();
      for(const row of feed.diagnostics.values()){const line=document.createElement('p');line.className=row.status==='error'?'feed-warning':'';
        const url=new URL(row.url),name=url.hostname==='uapis.cn'?'热榜 '+url.searchParams.get('type'):url.hostname;
        line.textContent=`${name} · ${row.transport==='server_relay'?'云端中继':'浏览器直连'} · ${row.status==='ok'?'HTTP读取成功':'失败'} · 最后成功 ${row.success_at?time(row.success_at):'从未成功'}${row.relay_observed_at?' · 服务器获取 '+time(row.relay_observed_at):''}${row.error?' · '+row.error:''}`;diagnostic.append(line);}
    }
  }
  state.selectBook=symbol=>{selected=symbol;$('#book-symbol').value=symbol;book();feed?.select(symbol);};
  $('#book-symbol').addEventListener('change',event=>state.selectBook(event.target.value));book();
  feed=new LiveFeed({universe:MARKETS,onQuotes:data=>{for(const [key,row] of Object.entries(data)){if(!quotes[key]||Date.parse(row.quote_time)>=Date.parse(quotes[key].quote_time))quotes[key]=row;}
      lastSourceTime=data.sh000001?.quote_time||lastSourceTime;mergeQuotes(state.context,data,feed.mappings);recalculateSectors(state.context,MARKETS);renders.quotes();book();},
    onSocial:social=>{state.context.social=social;renders.social();},onStatus:(kind,value)=>{if(kind==='quotes')quoteStatus=value;status();}});
  for(const item of state.context.social?.items||[]){const rows=feed.records.get(item.platform)||[];rows.push(item);feed.records.set(item.platform,rows);const first=item.first_seen_at||item.observed_at;feed.firstSeen.set(item.id,first);if(item.content_kind==='hot_topic')feed.firstSeen.set('live:'+item.platform+':'+topicKey(item.title),first);}
  for(const [key,row] of Object.entries(state.context.social?.status?.sources||{}))feed.statuses.set(key,{...row,status:'snapshot',at:state.context.social.status.at});
  feed.start();
  $('#live-toggle').addEventListener('click',()=>{if(feed.enabled)feed.stop();else feed.start();$('#live-toggle').textContent=feed.enabled?'暂停自动更新':'恢复自动更新';$('#live-toggle').setAttribute('aria-pressed',String(feed.enabled));status();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)feed.pause();else feed.resume();status();});
  const ageTimer=setInterval(age,1000);window.addEventListener('pagehide',()=>{feed.stop();clearInterval(ageTimer);});
  state.liveFeed=feed;return feed;
}
