import test from 'node:test';
import assert from 'node:assert/strict';
import {parseTencent,parseEastmoney,parseNews,parseHotboard,deriveSocial,analyzeEvidence,mergeQuotes,LiveFeed,finite} from '../dist/js/live-data.js';
const observed='2026-10-26T05:10:00Z';
function quoteLine({key='sh510300',time='20261026130956',bids=[4.166,4.165,4.164,4.163,4.162],asks=[4.167,4.168,4.169,4.170,4.171]}={}){
  const row=Array(40).fill('');row[2]='510300';row[3]='4.167';row[4]='4.178';row[30]=time;row[32]='-0.26';
  bids.forEach((p,i)=>{row[9+2*i]=String(p);row[10+2*i]=String(10+i);});asks.forEach((p,i)=>{row[19+2*i]=String(p);row[20+2*i]=String(20+i);});return `v_${key}="${row.join('~')}";`;
}
test('actual quote time is distinct from receipt time and five levels preserve lots',()=>{
  const q=parseTencent(quoteLine(),['sh510300'],observed).sh510300;
  assert.equal(q.quote_time,'2026-10-26T05:09:56.000Z');assert.equal(q.observed_at,observed);
  assert.equal(q.order_book.bids[0].price,4.166);assert.equal(q.order_book.asks[4].price,4.171);assert.equal(q.order_book.volume_unit,'lots_100_shares');
});
test('crossed, missing and out-of-order depth cannot be presented as valid',()=>{
  for(const options of [{bids:[4.2,4.165,4.164,4.163,4.162]},{asks:[0,4.168,4.169,4.170,4.171]},{bids:[4.165,4.166,4.164,4.163,4.162]}]){
    assert.equal(parseTencent(quoteLine(options),['sh510300'],observed).sh510300.order_book,undefined);
  }
  assert.throws(()=>parseTencent(quoteLine({time:'20261027130956'}),['sh510300'],observed));
  assert.throws(()=>parseTencent(quoteLine(),['unrequested'],observed));
  for(const v of ['',null,'-','NaN',true,Infinity])assert.equal(finite(v),null);
});
test('US source timestamps respect New York daylight saving rather than browser timezone',()=>{
  const q=parseTencent(quoteLine({key:'usTEST',time:'2026-10-23 16:00:01'}),['usTEST'],observed).usTEST;
  assert.equal(q.quote_time,'2026-10-23T20:00:01.000Z');assert.equal(q.order_book,undefined);
});
test('fallback price cannot invent depth or use the wrong security',()=>{
  const p={data:{f57:'510300',f43:4.167,f60:4.178,f170:-.26,f86:1792991396}};
  assert.equal(parseEastmoney(p,'sh510300',observed).order_book,undefined);
  assert.throws(()=>parseEastmoney(p,'sh510500',observed));
});
test('configured relay is the actual fetch destination and preserves exact source bytes',async()=>{
  let destination,options;
  const feed=new LiveFeed({relayBase:'https://relay.example',universe:{shanghai:{sectors:[]},newyork:{sectors:[]}},fetchImpl:async(url,init)=>{
    destination=url;options=init;return {ok:true,json:async()=>({ok:true,encoding:'utf-8',observed_at:observed,body_base64:btoa('actual source text')})};
  }});
  assert.equal(await feed.request('https://qt.gtimg.cn/q=sh510300',false,true),'actual source text');
  assert.equal(destination,'https://relay.example/api/relay?feed=tencent');assert.equal(options.credentials,'omit');
  const diagnostic=[...feed.diagnostics.values()][0];assert.equal(diagnostic.status,'ok');assert.equal(diagnostic.relay_observed_at,observed);
});
test('relay errors remain visible instead of looking like successful live updates',async()=>{
  const feed=new LiveFeed({relayBase:'https://relay.example',universe:{shanghai:{sectors:[]},newyork:{sectors:[]}},fetchImpl:async()=>({ok:false,json:async()=>({ok:false,error:'Upstream HTTP 403'})})});
  await assert.rejects(feed.request('https://uapis.cn/api/v1/misc/hotboard?type=weibo'),/403/);
  const diagnostic=[...feed.diagnostics.values()][0];assert.equal(diagnostic.status,'error');assert.equal(diagnostic.success_at,undefined);
});
test('news keeps publication time while hotboard time never becomes publication',()=>{
  const news=parseNews({code:'1',data:{fastNewsList:[{code:'202610263907832429',title:'A股回购',summary:'<b>回购</b>计划',showTime:'2026-10-26 13:02:04'}]}},observed)[0];
  assert.equal(news.published_at,'2026-10-26T05:02:04.000Z');assert.equal(news.excerpt,'回购计划');
  const hot=parseHotboard({type:'weibo',update_time:observed,list:[{title:'A股回购',url:'https://s.weibo.com/weibo?q=test'}]},'weibo',observed)[0];
  assert.equal(hot.published_at,null);assert.equal(hot.comment_text_collected,false);
  assert.throws(()=>parseHotboard({type:'douyin',update_time:observed,list:[]},'weibo',observed));
  assert.equal(parseHotboard({type:'weibo',update_time:observed,list:[{title:'A股',url:'https://weibo.com.evil.example/a'}]},'weibo',observed).length,0);
});
test('rule analysis is explicit and does not convert rumors or negation to buy/sell scores',()=>{
  const item={id:'actual',title:'公司未减持，盈利同比增长但收入同比下降',source_url:'https://example.com',content_kind:'news_headline'};
  const row=analyzeEvidence(item,observed);assert.equal(row.verdict,'待核查');assert.equal(row.score,null);assert.equal(row.confidence,null);assert.match(row.method,/rules/);
});
test('repeated collection does not refresh first-seen or count cross-source titles twice',()=>{
  const a={id:'a',title:'A股回购计划',platform:'weibo',content_kind:'hot_topic',source_url:'https://weibo.com/a',published_at:null,observed_at:observed};
  const first=new Map(),records=new Map([['weibo',[a]],['douyin',[{...a,id:'b',platform:'douyin'}]]]),status=new Map([['weibo',{status:'ok',at:observed}]]);
  const one=deriveSocial(records,status,first,observed);const later=deriveSocial(records,status,first,'2026-10-26T06:00:00Z');
  assert.equal(one.analysis.length,1);assert.equal(later.items[0].first_seen_at,observed);assert.equal(later.status.actual_comment_texts,0);
  assert.equal(deriveSocial(records,status,first,'2026-10-27T06:00:00Z').items.length,0);
  deriveSocial(records,status,first,'2026-10-29T06:00:00Z');
  assert.equal(first.get('a'),observed);
});
test('older prices never replace a newer source quote and historical curve stays historical',()=>{
  const context={markets:{regions:{shanghai:{index:{price:2,quote_time:observed,history:[{date:'2026-10-23',close:2}]},stocks:{}}}}};
  mergeQuotes(context,{sh000001:{price:1,quote_time:'2026-10-26T04:00:00Z'}},{sh000001:['shanghai',null]});assert.equal(context.markets.regions.shanghai.index.price,2);
  mergeQuotes(context,{sh000001:{price:3,quote_time:'2026-10-26T05:11:00Z'}},{sh000001:['shanghai',null]});assert.deepEqual(context.markets.regions.shanghai.index.history,[{date:'2026-10-23',close:2}]);
});
test('background tabs start no requests; resume does not overlap; stop cancels next poll',async()=>{
  let visible=false,calls=0,resolve;const jobs=new Map();let id=0;
  const feed=new LiveFeed({universe:{shanghai:{sectors:[]},newyork:{sectors:[]}},visible:()=>visible,
    fetchImpl:()=>{calls++;return new Promise(r=>{resolve=r;});},scheduler:{setTimeout:(fn,delay)=>{jobs.set(++id,{fn,delay});return id;},clearTimeout:id=>jobs.delete(id)}});
  feed.start();assert.equal(calls,0);visible=true;
  const first=feed.run('quotes');feed.run('quotes');assert.equal(calls,1);
  feed.stop();resolve({ok:true,arrayBuffer:async()=>new TextEncoder().encode(quoteLine()).buffer});await first;
  assert.equal(calls,1);assert.equal(jobs.size,0);assert.throws(()=>feed.select('sh999999'));
});
