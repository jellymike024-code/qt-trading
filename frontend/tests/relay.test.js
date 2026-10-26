import test from 'node:test';
import assert from 'node:assert/strict';
import {createRelay,upstreamFor} from '../../services/relay/core.mjs';
const origin='https://jellymike024-code.github.io';
const req=feed=>new Request('https://relay.example/api/relay?'+feed,{headers:{Origin:origin}});
test('relay is read-only and cannot proxy arbitrary targets',async()=>{
  let calls=0;const relay=createRelay({fetchImpl:async()=>{calls++;throw Error('unexpected');}});
  assert.equal((await relay(req('feed=anything&url=http://localhost'))).status,400);
  assert.equal((await relay(req('feed=hotboard&platform=not-allowed'))).status,400);
  assert.equal((await relay(req('feed=eastmoney&symbol=sh999999'))).status,400);
  assert.equal((await relay(new Request('https://relay.example/api/relay?feed=news',{method:'POST'}))).status,405);
  assert.equal((await relay(new Request('https://relay.example/api/relay?feed=news',{headers:{Origin:'https://evil.example'}}))).status,403);
  assert.equal(calls,0);assert.ok(upstreamFor(new URL(req('feed=tencent').url)).url.startsWith('https://qt.gtimg.cn/q='));
});
test('cached source timestamps never become current merely because a browser polls again',async()=>{
  let now=1792992000000,calls=0;const relay=createRelay({clock:()=>now,fetchImpl:async()=>{calls++;return new Response('v_sh510300="real-provider-bytes";');}});
  const first=await (await relay(req('feed=tencent'))).json();now+=2000;const secondResponse=await relay(req('feed=tencent')),second=await secondResponse.json();
  assert.equal(calls,1);assert.equal(second.observed_at,first.observed_at);assert.equal(second.cache_age_ms,2000);
  assert.equal(secondResponse.headers.get('Access-Control-Allow-Origin'),origin);
  now+=2000;const third=await(await relay(req('feed=tencent'))).json();assert.equal(calls,2);assert.notEqual(third.observed_at,first.observed_at);
});
test('concurrent requests share one upstream fetch and error pages are not cached as data',async()=>{
  let calls=0,finish;const relay=createRelay({fetchImpl:()=>{calls++;return new Promise(r=>{finish=r;});}});
  const a=relay(req('feed=tencent')),b=relay(req('feed=tencent'));assert.equal(calls,1);finish(new Response('<html>Access denied</html>'));
  for(const r of await Promise.all([a,b])){assert.equal(r.status,502);assert.equal((await r.json()).ok,false);}
});
