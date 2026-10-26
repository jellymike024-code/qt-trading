// Public, credential-free, read-only feeds. No eval, JSONP or trading mutations.
export const LIVE_INTERVALS=Object.freeze({quotes:5000,news:30000,hotboards:120000});
export const ETF_WATCHLIST=[['sh510300','沪深300ETF'],['sh510500','中证500ETF'],['sz159915','创业板ETF'],['sh588000','科创50ETF']];
const HOT_HOSTS={weibo:['weibo.com','weibo.cn'],douyin:['douyin.com'],xiaohongshu:['xiaohongshu.com','xhslink.com'],zhihu:['zhihu.com'],bilibili:['bilibili.com','b23.tv'],toutiao:['toutiao.com']};
const FINANCE=/A股|股市|股票|ETF|沪深|中证|创业板|科创|上证|证监会|央行|降准|降息|加息|美联储|关税|出口管制|半导体|英伟达|证券|基金|人民币|国常会|净流入|净流出|融资|回购|债券/;
const nowIso=()=>new Date().toISOString();
export function finite(value){if(value===null||value===undefined||typeof value==='boolean'||!String(value).trim()||!/^[-+]?\d+(?:\.\d+)?$/.test(String(value).trim()))return null;const n=Number(value);return Number.isFinite(n)?n:null;}
export function topicKey(text){return String(text).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu,'');}
function safeSource(value,hosts){try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&hosts.some(host=>url.hostname===host||url.hostname.endsWith('.'+host))?url.href:null;}catch{return null;}}
function validTime(value,observed){const time=Date.parse(value);return Number.isFinite(time)&&time<=Date.parse(observed)+60000?new Date(time).toISOString():null;}
function tencentTime(value,key){
  const compact=/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})$/.exec(value);
  if(compact&&!key.startsWith('us'))return new Date(`${compact[1]}-${compact[2]}-${compact[3]}T${compact[4]}:${compact[5]}:${compact[6]}+08:00`).toISOString();
  const match=/^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(value);
  if(!match||!key.startsWith('us'))return null;
  const parts=match.slice(1).map(Number),utc=Date.UTC(parts[0],parts[1]-1,...parts.slice(2));
  const local=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(utc)).map(p=>[p.type,p.value]));
  const shifted=Date.UTC(+local.year,+local.month-1,+local.day,+local.hour,+local.minute,+local.second);
  return new Date(utc-(shifted-utc)).toISOString();
}
function levels(fields,start){return Array.from({length:5},(_,i)=>({level:i+1,price:finite(fields[start+i*2]),volume_lots:finite(fields[start+i*2+1])}));}
export function parseTencent(text,allowed,observed=nowIso()){
  const result={};const allow=new Set(allowed);
  for(const match of text.matchAll(/v_([^=\s]+)="([^"]*)";/g)){
    const key=match[1],fields=match[2].split('~');if(!allow.has(key)||fields.length<33)continue;
    const price=finite(fields[3]),previous=finite(fields[4]),change=finite(fields[32]);
    if(!(price>0&&previous>0)||change===null||Math.abs((price/previous-1)*100-change)>.08)continue;
    let time;try{time=tencentTime(fields[30],key);}catch{continue;}
    if(!time||!validTime(time,observed))continue;
    const quote={price,previous_close:previous,change_pct:change,source:'腾讯证券公开报价',source_url:'https://qt.gtimg.cn/q='+key,
      observed_at:observed,quote_time:time,session_date:new Intl.DateTimeFormat('sv-SE',{timeZone:key.startsWith('us')?'America/New_York':'Asia/Shanghai'}).format(new Date(time)),
      currency:key.startsWith('us')?'USD':'CNY',interval:'public_poll',status:Date.parse(observed)-Date.parse(time)>5*86400000?'stale':'ok',source_symbol:fields[2],history:[]};
    if(/^(sh|sz)\d{6}$/.test(key)&&key!=='sh000001'){
      const bids=levels(fields,9),asks=levels(fields,19),valid=rows=>rows.every(row=>row.price>0&&Number.isSafeInteger(row.volume_lots)&&row.volume_lots>=0);
      if(valid(bids)&&valid(asks)&&bids.every((row,i)=>!i||bids[i-1].price>=row.price)&&asks.every((row,i)=>!i||asks[i-1].price<=row.price)&&bids[0].price<=asks[0].price)
        quote.order_book={bids,asks,volume_unit:'lots_100_shares',levels:5};
    }
    result[key]=quote;
  }
  if(!Object.keys(result).length)throw Error('公开报价响应没有通过校验');return result;
}
export function parseEastmoney(payload,symbol,observed=nowIso()){
  const row=payload?.data,price=finite(row?.f43),previous=finite(row?.f60),change=finite(row?.f170),seconds=finite(row?.f86);
  if(!row||String(row.f57)!==symbol.slice(2)||!(price>0&&previous>0)||change===null||!seconds||Math.abs((price/previous-1)*100-change)>.08)throw Error('东方财富报价校验失败');
  const time=validTime(new Date(seconds*1000).toISOString(),observed);if(!time)throw Error('东方财富来源时间异常');
  return {price,previous_close:previous,change_pct:change,source:'东方财富公开报价（盘口缺失时不补造）',source_url:'https://quote.eastmoney.com/'+symbol+'.html',observed_at:observed,
    quote_time:time,session_date:new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date(time)),currency:'CNY',interval:'public_poll',status:'ok',history:[]};
}
export function parseNews(payload,observed=nowIso()){
  if(String(payload?.code)!=='1'||!Array.isArray(payload?.data?.fastNewsList))throw Error('财经快讯格式异常');
  return payload.data.fastNewsList.slice(0,50).flatMap(row=>{
    if(!/^\d{12,24}$/.test(String(row.code))||typeof row.title!=='string'||!row.title.trim())return [];
    const published=validTime(String(row.showTime).replace(' ','T')+'+08:00',observed);if(!published||Date.parse(observed)-Date.parse(published)>86400000)return [];
    const clean=value=>String(value||'').replace(/<[^>]*>/g,'').trim();
    return [{id:'live:eastmoney:'+row.code,platform:'eastmoney_news',content_kind:'news_headline',title:clean(row.title).slice(0,600),
      excerpt:clean(row.summary).slice(0,180),source_url:'https://finance.eastmoney.com/a/'+row.code+'.html',published_at:published,
      observed_at:observed,provider_updated_at:null,comment_text_collected:false}];
  });
}
export function parseHotboard(payload,platform,observed=nowIso()){
  const hosts=HOT_HOSTS[platform];if(!hosts||payload?.type!==platform||!Array.isArray(payload.list))throw Error('热榜格式或平台不匹配');
  const updated=validTime(payload.update_time,observed);if(!updated||Date.parse(observed)-Date.parse(updated)>6*3600000)throw Error('热榜来源已陈旧或时间异常');
  return payload.list.slice(0,200).flatMap(row=>{
    const url=safeSource(row.url,hosts);if(!url||typeof row.title!=='string'||!row.title.trim())return [];
    return [{id:'live:'+platform+':'+topicKey(row.title),platform,content_kind:'hot_topic',title:row.title.trim().slice(0,600),source_url:url,
      published_at:null,provider_updated_at:updated,observed_at:observed,comment_text_collected:false}];
  });
}
export function analyzeEvidence(item,analyzedAt=nowIso()){
  const text=item.title+' '+(item.excerpt||''),positive=['净流入','增持','降准','回购','盈利增长','同比增长','上调盈利','业绩预增'],negative=['净流出','减持','立案','退市','违约','亏损扩大','业绩预亏','同比下降','出口管制'];
  const positiveHits=positive.filter(word=>text.includes(word)),negativeHits=negative.filter(word=>text.includes(word));
  const caveat=/传闻|网传|爆料|辟谣|否认|未获|不代表|或将|可能|(未|不|无|没有|并未).{0,3}(减持|增持|回购|降准|降息|净流入|净流出|退市|立案)/.test(text);
  const verdict=caveat||positiveHits.length&&negativeHits.length?'待核查':negativeHits.length?'风险线索':positiveHits.length?'偏积极线索':'方向未定';
  const type=item.content_kind==='hot_topic'?'热榜标题，发布时间未知':'财经快讯转述，尚未核对原始公告';
  return {record_id:item.id,topic_group:topicKey(item.title),method:'transparent_keyword_rules_v1',verdict,score:null,confidence:null,
    positive_hits:positiveHits,negative_hits:negativeHits,analyzed_at:analyzedAt,evidence_urls:[item.source_url],
    reason:`${type}。${[...positiveHits,...negativeHits].length?'命中：'+[...positiveHits,...negativeHits].join('、')+'。':'未命中明确方向词。'}${caveat?'存在传闻、否认或不确定表述。':''}仅供核查，不代表ETF方向、投资者占比或交易信号。`};
}
export function deriveSocial(recordsBySource,statusBySource,firstSeen=new Map(),observed=nowIso()){
  const items=[],activeIds=new Set();
  for(const rows of recordsBySource.values())for(const row of rows){
    activeIds.add(row.id);
    if(!firstSeen.has(row.id))firstSeen.set(row.id,row.observed_at);
    const first=firstSeen.get(row.id),start=row.published_at||first;
    if(Date.parse(observed)-Date.parse(start)>86400000||!FINANCE.test(row.title+' '+(row.excerpt||'')))continue;
    const normalized=topicKey(row.title);items.push({...row,first_seen_at:first,topic_hash:normalized,
      cities:/美联储|美股|纳斯达克|英伟达/.test(row.title)?['newyork','shanghai']:/英国|伦敦|富时/.test(row.title)?['london']:/韩国|首尔|三星/.test(row.title)?['seoul']:['shanghai'],
      matched_keywords:[...new Set((row.title+' '+(row.excerpt||'')).match(/ETF|A股|半导体|降准|降息|回购|净流入|净流出|央行|美联储|股票|基金/g)||[])]});
  }
  // Bound tab-local memory; unknown publication times never become "published now".
  for(const [id,time] of firstSeen)if(!activeIds.has(id)&&Date.parse(observed)-Date.parse(time)>2*86400000)firstSeen.delete(id);
  items.sort((a,b)=>Date.parse(b.published_at||b.first_seen_at)-Date.parse(a.published_at||a.first_seen_at));
  const groups=new Map();for(const item of items)if(!groups.has(item.topic_hash))groups.set(item.topic_hash,item);
  const sources=Object.fromEntries([...statusBySource].map(([key,row])=>[key,{...row,status:row.at&&Date.parse(observed)-Date.parse(row.at)>6*3600000?'stale':row.status}]));
  const rows=items.slice(0,100);return {status:{at:observed,status:Object.values(sources).every(s=>s.status==='ok')?'ok':'partial',sources,financial_candidates:rows.length,actual_comment_texts:0,
    analysis_method:'transparent_keyword_rules_v1',analysis_is_llm:false},items:rows,analysis:[...groups.values()].slice(0,100).map(row=>analyzeEvidence(row,observed))};
}
export function mergeQuotes(context,quotes,mappings){
  context.markets??={regions:{}};context.markets.regions??={};
  for(const [key,quote] of Object.entries(quotes)){
    const mapping=mappings[key];if(!mapping)continue;const [city,code]=mapping;
    const region=context.markets.regions[city]??={index:null,stocks:{},sectors:{}};region.stocks??={};
    const old=code?region.stocks[code]:region.index;
    if(old?.quote_time&&Date.parse(quote.quote_time)<Date.parse(old.quote_time))continue;
    const merged={...quote,history:old?.history||[]};if(code)region.stocks[code]=merged;else region.index=merged;
  }
}
export function recalculateSectors(context,universe){
  for(const [city,market] of Object.entries(universe)){
    const region=context.markets?.regions?.[city];if(!region)continue;region.sectors??={};
    for(const sector of market.sectors){const rows=sector.stocks.map(s=>region.stocks?.[s.code]).filter(Boolean),dates=new Set(rows.map(r=>r.session_date));
      region.sectors[sector.name]={coverage:rows.length,session_date:dates.size===1?rows[0].session_date:null,change_pct:rows.length===sector.stocks.length&&dates.size===1?rows.reduce((n,r)=>n+r.change_pct,0)/rows.length:null};}
  }
}

export class LiveFeed {
  constructor({universe,onQuotes=()=>{},onSocial=()=>{},onStatus=()=>{},fetchImpl=globalThis.fetch,
    scheduler={setTimeout:(fn,delay)=>setTimeout(fn,delay),clearTimeout:id=>clearTimeout(id)},visible=()=>!globalThis.document?.hidden}){
    this.universe=universe;this.onQuotes=onQuotes;this.onSocial=onSocial;this.onStatus=onStatus;this.fetchImpl=fetchImpl;this.scheduler=scheduler;this.visible=visible;
    this.timers=new Map();this.pending=new Map();this.failures=new Map();this.records=new Map();this.statuses=new Map();this.firstSeen=new Map();this.controllers=new Set();this.enabled=false;
    this.mappings={'sh000001':['shanghai',null],'us.IXIC':['newyork',null]};
    for(const city of ['shanghai','newyork'])for(const sector of universe[city].sectors)for(const stock of sector.stocks){const key=city==='shanghai'?(stock.code.startsWith('6')?'sh':'sz')+stock.code:'us'+stock.code;this.mappings[key]=[city,stock.code];}
    this.symbol='sh510300';
  }
  async request(url,json=true,gbk=false){
    const controller=new AbortController(),timeout=this.scheduler.setTimeout(()=>controller.abort(),10000);
    this.controllers.add(controller);
    try{const response=await this.fetchImpl(url,{mode:'cors',credentials:'omit',cache:'no-store',referrerPolicy:'no-referrer',signal:controller.signal});
      if(!response.ok)throw Error('HTTP '+response.status);return json?await response.json():gbk?new TextDecoder('gbk').decode(await response.arrayBuffer()):await response.text();
    }finally{this.scheduler.clearTimeout(timeout);this.controllers.delete(controller);}
  }
  async quotes(){
    const keys=[...new Set([...Object.keys(this.mappings),...ETF_WATCHLIST.map(row=>row[0]),this.symbol])];
    try{
      const text=await this.request('https://qt.gtimg.cn/q='+keys.join(','),false,true),data=parseTencent(text,keys);
      if(!this.enabled)return;
      const missing=Object.keys(this.mappings).filter(key=>!data[key]);
      this.onQuotes(data,{provider:'腾讯证券公开报价',status:missing.length?'partial':'ok',missing:missing.length,observed_at:nowIso()});
      this.onStatus('quotes',{status:missing.length?'partial':'ok',message:missing.length?`A/美股缺失 ${missing.length} 个报价`:'A/美股 · 5秒轮询',at:nowIso()});
      if(!data[this.symbol])throw Error('所选盘口没有有效报价');
    }catch(error){
      if(!this.enabled||!this.visible())throw error;
      // Alternate ordinary public quote API; it currently does not return depth.
      const secid=(this.symbol.startsWith('sh')?'1.':'0.')+this.symbol.slice(2);
      try{const payload=await this.request('https://push2.eastmoney.com/api/qt/stock/get?secid='+secid+'&fltt=2&invt=2&fields=f43,f57,f58,f60,f170,f86');
        this.onQuotes({[this.symbol]:parseEastmoney(payload,this.symbol)},{provider:'东方财富',status:'partial',observed_at:nowIso()});
        this.onStatus('quotes',{status:'partial',message:'东方财富备选报价 · 五档暂缺',at:nowIso()});
      }catch{this.onStatus('quotes',{status:'error',message:'公开行情连接失败 · 已保留原始时间',at:nowIso()});throw error;}
    }
  }
  async source(platform){
    try{
      const news=platform==='eastmoney_news',url=news?'https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=50&req_trace=orbit-live':'https://uapis.cn/api/v1/misc/hotboard?type='+platform;
      const payload=await this.request(url),observed=nowIso(),rows=news?parseNews(payload,observed):parseHotboard(payload,platform,observed);
      this.records.set(platform,rows);this.statuses.set(platform,{status:'ok',records:rows.length,provider_updated_at:news?null:payload.update_time,at:observed});
    }catch(error){this.statuses.set(platform,{...this.statuses.get(platform),status:'error',at:nowIso(),error:'源不可用或跨域受限'});throw error;}
    finally{if(this.enabled)this.onSocial(deriveSocial(this.records,this.statuses,this.firstSeen));}
  }
  async hotboards(){
    const platforms=Object.keys(HOT_HOSTS);let failures=0;
    for(let i=0;i<platforms.length;i+=2){if(!this.enabled||!this.visible())return;const results=await Promise.allSettled(platforms.slice(i,i+2).map(p=>this.source(p)));failures+=results.filter(r=>r.status==='rejected').length;}
    this.onStatus('hotboards',{status:failures?'partial':'ok',message:`热榜 ${6-failures}/6 · 120秒轮询`,at:nowIso()});if(failures===6)throw Error('热榜均不可用');
  }
  async run(kind){
    if(!this.enabled||!this.visible()||this.pending.has(kind))return;
    const operation=(async()=>{try{if(kind==='quotes')await this.quotes();else if(kind==='news')await this.source('eastmoney_news');else await this.hotboards();this.failures.set(kind,0);}
      catch{this.failures.set(kind,(this.failures.get(kind)||0)+1);}
      finally{this.pending.delete(kind);if(this.enabled&&this.visible()){const delay=Math.min(LIVE_INTERVALS[kind]*2**(this.failures.get(kind)||0),kind==='quotes'?60000:600000);this.timers.set(kind,this.scheduler.setTimeout(()=>{this.timers.delete(kind);this.run(kind);},delay));}}})();
    this.pending.set(kind,operation);return operation;
  }
  start(){if(this.enabled)return;this.enabled=true;this.resume();}
  pause(){for(const id of this.timers.values())this.scheduler.clearTimeout(id);this.timers.clear();}
  resume(){if(!this.enabled||!this.visible())return;this.pause();for(const kind of Object.keys(LIVE_INTERVALS))this.run(kind);}
  stop(){this.enabled=false;this.pause();for(const controller of this.controllers)controller.abort();}
  select(symbol){if(!/^(sh|sz)\d{6}$/.test(symbol)||!([...ETF_WATCHLIST.map(r=>r[0]),...Object.keys(this.mappings)].includes(symbol)))throw Error('盘口代码不在观察列表');this.symbol=symbol;if(this.enabled){const timer=this.timers.get('quotes');if(timer)this.scheduler.clearTimeout(timer);this.timers.delete('quotes');this.run('quotes');}}
}
