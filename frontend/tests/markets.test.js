import test from 'node:test';
import assert from 'node:assert/strict';
import { MARKETS, CITY_ORDER, getMarket, getSector, latLonVector, sessionReference, localTime, signed, number } from '../dist/js/markets.js';

test('four markets each expose sectors, exchanges and representative stocks',()=>{
  assert.equal(CITY_ORDER.length,4);
  for(const id of CITY_ORDER){const market=getMarket(id);assert.equal(market.id,id);assert.ok(market.exchange);assert.equal(market.value,null);assert.equal(market.sectors.length,8);for(const sector of market.sectors){assert.equal(sector.change,null);assert.equal(sector.stocks.length,3);assert.ok(sector.stocks.every(s=>s.code&&s.name&&s.price===null&&s.change===null));}}
});
test('sector changes remain local to each selected market',()=>{
  assert.equal(getSector('newyork','信息科技').stocks[0].code,'NVDA');
  assert.equal(getSector('seoul','半导体').stocks[0].code,'005930');
  assert.equal(getSector('london','金融服务').stocks[0].code,'HSBA.L');
  assert.equal(getSector('shanghai','半导体').stocks[0].code,'688981');
});
test('longitude and latitude map to actual sphere coordinates',()=>{
  assert.deepEqual(latLonVector(0,0),[0,0,1]);
  assert.ok(Math.abs(latLonVector(0,90)[0]-1)<1e-9);
  for(const market of Object.values(MARKETS)){const coords=latLonVector(market.lat,market.lon,1.9);assert.ok(Math.abs(Math.hypot(...coords)-1.9)<1e-9);}
});
test('city clocks respect time zones and weekend sessions',()=>{
  const sunday=new Date('2026-10-25T12:00:00Z');
  assert.equal(localTime('shanghai',sunday),'20:00:00');
  assert.equal(localTime('seoul',sunday),'21:00:00');
  for(const city of CITY_ORDER)assert.equal(sessionReference(city,sunday),'周末休市');
  assert.equal(sessionReference('shanghai',new Date('2026-10-26T04:00:00Z')),'午间休市');
});
test('missing or nonfinite market observations never render as zero',()=>{for(const value of [null,undefined,NaN,Infinity]){assert.equal(signed(value),'—');assert.equal(number(value),'—');}assert.equal(signed(0),'+0.00%');assert.equal(number(0),'0.00');});
