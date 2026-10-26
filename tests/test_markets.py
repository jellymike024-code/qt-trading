"""Synthetic protocol fixtures stay in tests; production never uses fixtures."""
import copy

import pandas as pd
import pytest

from quantlab.markets import (observed_quote, parse_tencent, parse_sina_daily,
                             parse_naver, summarize_sector, merge_verified, finalize)

NOW = pd.Timestamp('2026-10-25T14:00:00Z')


def quote(**kwargs):
    return observed_quote(100, 0, 'test fixture', 'https://qt.gtimg.cn/q=sh000001', NOW,
                          session_date='2026-10-23', **kwargs)


def tencent(price='102', previous='100', change='2', timestamp='20261023150000'):
    fields = ['']*33
    fields[2],fields[3],fields[4],fields[30],fields[32] = '000001',price,previous,timestamp,change
    return 'v_sh000001="'+'~'.join(fields)+'";'


def test_tencent_checks_previous_close_and_source_timestamp():
    q = parse_tencent(tencent(), ['sh000001'], NOW)['sh000001']
    assert q['price']==102 and q['change_pct']==2
    assert pd.Timestamp(q['quote_time']).tz_convert('UTC')==pd.Timestamp('2026-10-23T07:00Z')
    assert not parse_tencent(tencent(change='-2'), ['sh000001'], NOW)
    assert not parse_tencent(tencent(timestamp='20261026150000'), ['sh000001'], NOW)
    assert not parse_tencent(tencent(price='nan'), ['sh000001'], NOW)


@pytest.mark.parametrize('kwargs', [dict(quote_time='2026-10-23T15:00:00'),
    dict(quote_time='2026-10-26T15:00:00Z'), dict(source_url='javascript:alert(1)'),
    dict(price=0),dict(price=float('nan'))])
def test_invalid_quotes_never_become_prices(kwargs):
    args = dict(price=100,change=0,source='fixture',source_url='https://example.com/quote',observed=NOW,session_date='2026-10-23')
    args.update(kwargs)
    with pytest.raises(ValueError): observed_quote(**args)


def test_global_daily_quotes_sort_bars_and_reject_duplicate_dates():
    payload={'result':{'data':[{'d':'2026-10-23','c':'102'},{'d':'2026-10-22','c':'100'}]}}
    q=parse_sina_daily(payload,'UKX',NOW)
    assert q['session_date']=='2026-10-23' and q['interval']=='1d'
    assert q['change_pct']==pytest.approx(2)
    payload['result']['data'].append({'d':'2026-10-23','c':'103'})
    with pytest.raises(ValueError,match='duplicate'):parse_sina_daily(payload,'UKX',NOW)


def test_naver_prices_preserve_korean_currency_and_session():
    payload={'datas':[{'itemCode':'005930','closePrice':'70,000','fluctuationsRatio':'-1.2',
        'localTradedAt':'2026-10-23T18:00:00+09:00','currencyType':{'code':'KRW'},'marketSessionType':'AFTER_MARKET'}]}
    q=parse_naver(payload,'005930',NOW)
    assert q['price']==70000 and q['currency']=='KRW'
    assert q['market_session']=='AFTER_MARKET'
    with pytest.raises(ValueError,match='wrong'):parse_naver(payload,'000100',NOW)


def test_sector_requires_complete_same_session_data_and_keeps_zero():
    stocks={str(i):quote() for i in range(3)}
    assert summarize_sector(stocks,list(stocks))['change_pct']==0
    assert summarize_sector(stocks,['0','1','missing'])['change_pct'] is None
    stocks['1']['session_date']='2026-10-22'
    assert summarize_sector(stocks,list(stocks))['change_pct'] is None
    assert summarize_sector({},[])['change_pct'] is None


def test_verified_page_import_never_refreshes_source_age_or_admits_unknown_symbols():
    universe={'london':{'sectors':[{'name':'sample','stocks':[{'code':'HSBA.L'}]}]}}
    result={'regions':{'london':{'index':quote(),'stocks':{},'sectors':{}}},'errors':{}}
    row=dict(quote(),city='london',code='HSBA.L',observed_at=str(NOW),price=100)
    merge_verified(result,universe,[row],NOW)
    later=finalize(result,universe,NOW+pd.Timedelta(days=7))
    assert later['regions']['london']['stocks']['HSBA.L']['status']=='stale'
    assert later['regions']['london']['sectors']['sample']['change_pct'] is None
    invalid=copy.deepcopy(row);invalid['code']='FAKE'
    merge_verified(result,universe,[invalid],NOW)
    assert 'FAKE' not in result['regions']['london']['stocks']
    invalid=copy.deepcopy(row);invalid['observed_at']=str(NOW+pd.Timedelta(days=1))
    merge_verified(result,universe,[invalid],NOW)
    assert 'future' in result['errors']['london:HSBA.L']
