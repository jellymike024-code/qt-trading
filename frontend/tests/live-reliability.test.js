import test from 'node:test';
import assert from 'node:assert/strict';
import {LiveFeed,deriveSocial,dailyEvidenceFor,agentCoverage,parseTencent,recalculateSectors} from '../dist/js/live-data.js';
import {renderLiveEvidence} from '../dist/js/live-ui.js';
const universe={shanghai:{sectors:[]},newyork:{sectors:[]}};
function scheduler(){let id=0;const jobs=new Map();return {jobs,setTimeout:(fn,delay)=>{jobs.set(++id,{fn,delay});return id;},clearTimeout:id=>jobs.delete(id)};}
const observed='2026-10-26T09:00:00Z';
const item={id:'snapshot-id',platform:'eastmoney_news',title:'A股回购计划',source_url:'https://finance.eastmoney.com/a/202610263907832429.html',content_kind:'news_headline',published_at:'2026-10-26T05:02:04Z',observed_at:'2026-10-26T05:05:00Z'};
const daily={record_id:'snapshot-id',method:'daily_research_agent',source_title:item.title,source_url:item.source_url,reason:'已核对原公告；宽基方向未定。',analyzed_at:'2026-10-26T05:10:00Z',published_at:item.published_at,evidence_urls:['https://www.sse.com.cn/disclosure/example','javascript:alert(1)','https://user:secret@example.com']};
const news=(title='A股新快讯')=>({code:'1',data:{fastNewsList:[{code:'202610263907832429',title,summary:'真实摘要',showTime:'2026-10-26 13:02:04'}]}});
const response=payload=>({ok:true,json:async()=>payload});
async function until(check){for(let i=0;i<20&&!check();i++)await Promise.resolve();assert.ok(check(),'asynchronous stage was reached');}

test('daily Agent interpretations match a refreshed public source without becoming new model calls',()=>{
  const refreshed={...item,id:'live:eastmoney:202610263907832429'};
  const before=dailyEvidenceFor(refreshed,[daily],observed);
  assert.equal(before.status,'current');assert.equal(before.analysis.analyzed_at,'2026-10-26T05:10:00.000Z');
  assert.deepEqual(before.analysis.evidence_urls,['https://www.sse.com.cn/disclosure/example']);
  const mismatched={...refreshed,title:'A股另外一条标题'};
  assert.equal(dailyEvidenceFor(mismatched,[daily],observed).status,'pending');
  assert.equal(dailyEvidenceFor(refreshed,[{...daily,method:'transparent_keyword_rules_v1'}],observed).status,'pending');
  assert.deepEqual(agentCoverage([refreshed,mismatched],[daily],observed),{reviewed:1,pending:1,expired:0,last_analyzed_at:'2026-10-26T05:10:00.000Z'});
});
test('a new poll cannot extend Agent expiry, use a future analysis, or count expired work as current',()=>{
  const extended={...daily,expires_at:'2099-01-01T00:00:00Z'};
  const state=dailyEvidenceFor(item,[extended],'2026-10-27T05:02:04Z');
  assert.equal(state.status,'expired');assert.equal(state.analysis.expires_at,'2026-10-27T05:02:04.000Z');
  assert.equal(dailyEvidenceFor(item,[{...daily,analyzed_at:'2026-10-27T05:00:00Z'}],observed).status,'pending');
  const coverage=agentCoverage([item],[extended],'2026-10-27T05:02:04Z');assert.equal(coverage.reviewed,0);assert.equal(coverage.expired,1);assert.equal(coverage.pending,1);
});
test('failed source attempts keep their last success time and old sources become visibly stale',async()=>{
  const feed=new LiveFeed({universe,scheduler:scheduler(),fetchImpl:async()=>({ok:false,status:503})});feed.enabled=true;
  const old=new Date(Date.now()-7*3600000).toISOString();feed.statuses.set('eastmoney_news',{status:'ok',at:old,records:1});
  await assert.rejects(feed.source('eastmoney_news'));
  const status=feed.statuses.get('eastmoney_news');assert.equal(status.at,old);assert.equal(status.last_success_at,old);assert.ok(Date.parse(status.attempted_at)>Date.parse(old));
  assert.equal(deriveSocial(new Map(),feed.statuses).status.sources.eastmoney_news.status,'stale');feed.stop();
});
test('an old stopped request cannot overwrite a restarted poll or remove its pending token',async()=>{
  const pending=[],updates=[],clock=scheduler();
  const feed=new LiveFeed({universe,scheduler:clock,fetchImpl:()=>new Promise(resolve=>pending.push(resolve)),onSocial:value=>updates.push(value)});feed.enabled=true;
  const old=feed.run('news');assert.equal(pending.length,1);feed.pause();
  const current=feed.run('news');assert.equal(pending.length,2);
  pending[0](response(news('A股旧结果')));await old;
  assert.equal(updates.length,0);assert.equal(feed.records.size,0);assert.equal(feed.pending.has('news'),true);
  pending[1](response(news('A股新结果')));await current;
  assert.equal(updates.length,1);assert.equal(feed.records.get('eastmoney_news')[0].title,'A股新结果');feed.stop();
});
test('stop during the alternate quote request suppresses all late data and status callbacks',async()=>{
  let finish,calls=0,quotes=0;const statuses=[];
  const feed=new LiveFeed({universe,scheduler:scheduler(),fetchImpl:async()=>{if(++calls===1)throw Error('Tencent unavailable');return new Promise(resolve=>{finish=resolve;});},onQuotes:()=>quotes++,onStatus:(kind,value)=>statuses.push([kind,value])});feed.enabled=true;
  const operation=feed.run('quotes');await until(()=>finish);feed.stop();const count=statuses.length;
  finish(response({data:{f57:'510300',f43:4.167,f60:4.178,f170:-.26,f86:1792991396}}));await operation;
  assert.equal(quotes,0);assert.equal(statuses.length,count);assert.equal(feed.pending.size,0);
});
test('selecting a new book during a pending fallback requests it immediately and never labels the old response as new',async()=>{
  let finish,calls=0;const delivered=[],clock=scheduler();
  const feed=new LiveFeed({universe,scheduler:clock,fetchImpl:async()=>{if(++calls===1)throw Error('Tencent unavailable');return new Promise(resolve=>{finish=resolve;});},onQuotes:value=>delivered.push(value)});feed.enabled=true;
  const operation=feed.run('quotes');await until(()=>finish);feed.select('sh510500');
  finish(response({data:{f57:'510300',f43:4.167,f60:4.178,f170:-.26,f86:1792991396}}));await operation;
  assert.equal(delivered.length,0);assert.equal(clock.jobs.get(feed.timers.get('quotes')).delay,0);feed.stop();
});
test('Agent snapshot polling reads the same-origin snapshot directly and preserves source clocks and active retained titles',async()=>{
  let fetched,init;const clock=scheduler(),updates=[];
  const freshItem={...item,published_at:new Date(Date.now()-3600000).toISOString(),observed_at:new Date(Date.now()-3500000).toISOString()};
  const freshDaily={...daily,published_at:freshItem.published_at,analyzed_at:new Date(Date.now()-3000000).toISOString()};
  const feed=new LiveFeed({universe,relayBase:'https://relay.example',snapshotUrl:'https://dashboard.example/data/context.json',scheduler:clock,
    fetchImpl:async(url,options)=>{fetched=url;init=options;return response({social:{agent_analysis:[freshDaily],items:[freshItem]}});},onSocial:value=>updates.push(value)});feed.enabled=true;
  await feed.run('agent');assert.equal(fetched,feed.snapshotUrl);assert.equal(init.credentials,'omit');assert.equal(init.cache,'no-store');
  assert.equal(updates[0].agent_analysis[0].analyzed_at,freshDaily.analyzed_at);assert.equal(updates[0].items[0].id,freshItem.id);
  assert.equal(updates[0].agent_status.continuous_model_calls,false);assert.equal(updates[0].agent_status.reviewed,1);
  assert.equal(clock.jobs.get(feed.timers.get('agent')).delay,60000);feed.stop();
});
test('pausing during an Agent snapshot request cannot install the old response on resume',async()=>{
  let finish;const feed=new LiveFeed({universe,snapshotUrl:'https://dashboard.example/data/context.json',scheduler:scheduler(),fetchImpl:()=>new Promise(resolve=>{finish=resolve;})});feed.enabled=true;
  const operation=feed.run('agent');feed.pause();finish(response({social:{agent_analysis:[daily],items:[item]}}));await operation;
  assert.deepEqual(feed.agentAnalysis,[]);assert.deepEqual(feed.agentItems,[]);assert.equal(feed.pending.size,0);feed.stop();
});

class Element{
  constructor(){this.children=[];this.textContent='';this.title='';this.style={};this.lastChild={textContent:''};}
  append(...children){this.children.push(...children);}
  querySelectorAll(selector){return selector==='.news-item'?this.newsItems||[]:[];}
  insertAdjacentElement(position,element){this.adjacent=element;}
}
test('rendered evidence separates daily model interpretation from immediate rules and does not turn missing model scores into neutral votes',()=>{
  const nodes=new Map(),labels=Array.from({length:3},()=>new Element()),source=new Element(),list=new Element();list.newsItems=[source];nodes.set('#news-list',list);
  const previous=globalThis.document;
  globalThis.document={querySelector:selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);},querySelectorAll:()=>labels,createElement:()=>new Element()};
  const currentItem={...item,published_at:new Date(Date.now()-3600000).toISOString()},currentDaily={...daily,published_at:currentItem.published_at,analyzed_at:new Date(Date.now()-3000000).toISOString(),score:null};
  try{
    renderLiveEvidence({analysisMode:'topics',context:{social:{status:{sources:{}},items:[currentItem],analysis:[currentDaily],agent_analysis:[currentDaily]}}},[currentItem]);
    assert.match(source.children[0].textContent,/即时规则/);assert.match(source.children[1].textContent,/每日Agent摘要/);
    assert.match(nodes.get('#sentiment-wave').textContent,/每日Agent 1 条/);assert.equal(nodes.get('#sentiment-positive').textContent,'1 条');assert.equal(nodes.get('#sentiment-neutral').textContent,'0 条');
    assert.match(nodes.get('#sentiment-note').textContent,/不触发模型调用/);assert.equal(source.adjacent.children[0].href,'https://www.sse.com.cn/disclosure/example');
  }finally{globalThis.document=previous;}
});

test('mismatched provider security identities and missing sector returns never become valid observations',()=>{
  const fields=Array(40).fill('');fields[2]='510500';fields[3]='4.167';fields[4]='4.178';fields[30]='20261026130956';fields[32]='-0.26';
  assert.throws(()=>parseTencent(`v_sh510300="${fields.join('~')}";`,['sh510300'],observed));
  const context={markets:{regions:{shanghai:{stocks:{a:{change_pct:null,session_date:'2026-10-26'},b:{change_pct:2,session_date:'2026-10-26'}},sectors:{}}}}};
  recalculateSectors(context,{shanghai:{sectors:[{name:'test',stocks:[{code:'a'},{code:'b'}]}]}});
  assert.equal(context.markets.regions.shanghai.sectors.test.change_pct,null);assert.equal(context.markets.regions.shanghai.sectors.test.coverage,1);
  assert.equal(dailyEvidenceFor(item,[null,{...daily,analyzed_at:'2026-10-26T04:00:00Z'}],observed).status,'pending');
});
