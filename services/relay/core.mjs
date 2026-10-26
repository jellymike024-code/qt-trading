import { Buffer } from 'node:buffer';
import { QUOTE_SYMBOLS } from './symbols.mjs';
const HOTBOARDS=new Set(['weibo','douyin','xiaohongshu','zhihu','bilibili','toutiao']);
const ORIGINS=new Set(['https://jellymike024-code.github.io','http://127.0.0.1:8765','http://localhost:8765']);
const A_SHARES=new Set(QUOTE_SYMBOLS.filter(symbol=>/^(sh|sz)\d{6}$/.test(symbol)));
export function upstreamFor(url){
  const feed=url.searchParams.get('feed');
  if(feed==='tencent')return {url:'https://qt.gtimg.cn/q='+QUOTE_SYMBOLS.join(','),ttl:3000,encoding:'gbk'};
  if(feed==='news')return {url:'https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724&fastColumn=102&sortEnd=&pageSize=50&req_trace=orbit-relay',ttl:20000,encoding:'utf-8'};
  if(feed==='hotboard'){
    const platform=url.searchParams.get('platform');if(!HOTBOARDS.has(platform))throw Error('Unsupported hotboard');
    return {url:'https://uapis.cn/api/v1/misc/hotboard?type='+platform,ttl:90000,encoding:'utf-8',platform};
  }
  if(feed==='eastmoney'){
    const symbol=url.searchParams.get('symbol');if(!A_SHARES.has(symbol))throw Error('Security is outside the watchlist');
    return {url:'https://push2.eastmoney.com/api/qt/stock/get?secid='+(symbol.startsWith('sh')?'1.':'0.')+symbol.slice(2)+'&fltt=2&invt=2&fields=f43,f57,f58,f60,f170,f86',ttl:3000,encoding:'utf-8'};
  }
  throw Error('Unsupported feed');
}
export function createRelay({fetchImpl=globalThis.fetch,clock=Date.now}={}){
  const snapshots=new Map(),pending=new Map();
  const json=(data,status,origin)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',
    'Access-Control-Allow-Origin':origin||'https://jellymike024-code.github.io','Access-Control-Allow-Methods':'GET, OPTIONS','Access-Control-Allow-Headers':'Content-Type',
    'Access-Control-Expose-Headers':'X-Orbit-Transport','X-Orbit-Transport':'server-relay-v1','Vary':'Origin','X-Content-Type-Options':'nosniff'}});
  async function retrieve(source){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),9000);
    try{
      const response=await fetchImpl(source.url,{headers:{Accept:'application/json,text/plain,*/*'},redirect:'manual',signal:controller.signal});
      if(!response.ok)throw Error('Upstream HTTP '+response.status);
      const reader=response.body.getReader(),chunks=[];let length=0;
      while(true){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>400000){await reader.cancel();throw Error('Upstream response too large');}chunks.push(value);}
      const bytes=new Uint8Array(length);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
      const text=new TextDecoder().decode(bytes);
      if(source.encoding==='gbk'){if(!/^\s*v_[A-Za-z0-9_.]+=/.test(text))throw Error('Upstream is not a quote response');}
      else{
        const value=JSON.parse(text);
        if(source.platform&&(!Array.isArray(value.list)||value.type!==source.platform))throw Error('Unexpected hotboard response');
        if(source.url.includes('getFastNewsList')&&!Array.isArray(value.data?.fastNewsList))throw Error('Unexpected fast-news response');
        if(source.url.includes('qt/stock/get')&&!value.data?.f57)throw Error('No quote data from provider');
      }
      const now=clock();return {ok:true,transport:'server_relay',observed_at:new Date(now).toISOString(),upstream_url:source.url,
        encoding:source.encoding,body_base64:Buffer.from(bytes).toString('base64'),expires:now+source.ttl};
    }finally{clearTimeout(timer);}
  }
  return async function relay(request){
    const origin=request.headers.get('origin');
    if(origin&&!ORIGINS.has(origin))return json({ok:false,error:'Origin not allowed'},403,null);
    if(request.method==='OPTIONS')return json({ok:true},200,origin);
    if(request.method!=='GET')return json({ok:false,error:'Read-only endpoint'},405,origin);
    const url=new URL(request.url);if(url.searchParams.get('feed')==='health')return json({ok:true,transport:'server_relay',time:new Date(clock()).toISOString(),cached_sources:snapshots.size},200,origin);
    let source;try{source=upstreamFor(url);}catch(error){return json({ok:false,error:error.message},400,origin);}
    try{
      let data=snapshots.get(source.url),cacheHit=Boolean(data&&data.expires>clock());
      if(!cacheHit){
        let task=pending.get(source.url);if(!task){task=retrieve(source).then(value=>{snapshots.set(source.url,value);return value;}).finally(()=>pending.delete(source.url));pending.set(source.url,task);}
        data=await task;
      }
      const {expires,...payload}=data;
      return json({...payload,cache_hit:cacheHit,cache_age_ms:Math.max(0,clock()-Date.parse(data.observed_at))},200,origin);
    }catch(error){return json({ok:false,transport:'server_relay',error:String(error.message||error).slice(0,180),upstream_host:new URL(source.url).hostname,attempted_at:new Date(clock()).toISOString()},502,origin);}
  };
}
export const handleRelay=createRelay();
