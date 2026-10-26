"""Application entry point for the pinned crawler with local deployment settings.

No remote debugging connection to the user's existing browser is made. Each
platform receives a new, dedicated persistent profile and manual QR login.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import logging
import os
from pathlib import Path
import sys
from uuid import uuid4
from urllib.parse import quote
class SourceRestricted(RuntimeError):
    """Stop rather than retry a platform access restriction."""


def failure_details(exc, previous_status=None):
    """Classify a proven native launch crash without mistaking it for login loss."""
    text = str(exc).lower()
    native_crash = any(value in text for value in (
        'exitcode=3221225477', 'exitcode=-1073741819', 'exitcode=0xc0000005'))
    if (type(exc).__name__ == 'TargetClosedError'
            and text.lstrip().startswith('browsertype.launch_persistent_context:')
            and native_crash):
        return {'status': 'browser_startup_crash', 'error_type': type(exc).__name__,
                'error_code': '0xC0000005', 'failure_stage': 'browser_startup',
                'message': '独立采集浏览器启动时崩溃，尚未验证平台登录；请查看本地运行日志'}
    status = ('source_unavailable' if isinstance(exc, SourceRestricted) else
              'login_required' if previous_status in ('awaiting_login', 'manual_verification', 'login_required') else 'error')
    return {'status': status, 'error_type': type(exc).__name__,
            'message': '采集未完成，请查看平台登录状态或本地运行日志'}


class SafeSourceLogs(logging.Filter):
    def filter(self,record):
        message=record.getMessage()
        if len(message)>900 or 'xsec_token' in message or 'Cookie:' in message:
            record.msg='Large or credential-bearing source response omitted from log; use bounded database evidence.';record.args=()
        return True
for _stream in (sys.stdout,sys.stderr):
    if hasattr(_stream,'reconfigure'):_stream.reconfigure(encoding='utf-8',errors='replace')

BASE=Path(__file__).resolve().parent
os.environ['MPLCONFIGDIR']=str(BASE/'.runtime/matplotlib')
WORKSPACE=BASE.parent.parent
CRAWLER=BASE/'runtime/MindSpider/DeepSentimentCrawling/MediaCrawler'
STATUS=WORKSPACE/'data/bettafish/collector_status.json'


def update(platform,status,**extra):
    from datetime import datetime,timezone
    data=json.loads(STATUS.read_text(encoding='utf-8')) if STATUS.exists() else {'platforms':{}}
    row=data.setdefault('platforms',{}).setdefault(platform,{})
    row.update(status=status,updated_at=datetime.now(timezone.utc).isoformat(),**extra)
    temp=STATUS.with_name(STATUS.name+'.'+uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(STATUS)
    finally:temp.unlink(missing_ok=True)


def configure(platform,headless=False,notes=None,smoke=False):
    os.chdir(CRAWLER);sys.path.insert(0,str(CRAWLER))
    config=importlib.import_module('config')
    local=json.loads((BASE/'collector_config.json').read_text(encoding='utf-8'))
    if platform not in local['platforms']:raise ValueError('platform not configured')
    values={'PLATFORM':platform,'KEYWORDS':','.join(local['keywords']),'LOGIN_TYPE':'qrcode','COOKIES':'',
        'CRAWLER_TYPE':'search','SAVE_DATA_OPTION':'postgres','ENABLE_CDP_MODE':False,'ENABLE_IP_PROXY':False,
        'HEADLESS':headless,'SAVE_LOGIN_STATE':True,'ENABLE_GET_MEIDAS':False,'ENABLE_GET_COMMENTS':True,
        'ENABLE_GET_SUB_COMMENTS':False,'ENABLE_GET_WORDCLOUD':False,'CRAWLER_MAX_NOTES_COUNT':notes or local['max_notes_per_keyword'],
        'CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES':local['max_comments_per_note'],'MAX_CONCURRENCY_NUM':local['max_concurrency'],
        'CRAWLER_MAX_SLEEP_SEC':local['sleep_seconds']}
    values['SORT_TYPE']=local.get('xhs_sort','time_descending')
    values['PUBLISH_TIME_TYPE']=local.get('douyin_publish_time_days',1)
    for key,value in values.items():setattr(config,key,value)
    if smoke:
        config.KEYWORDS=local['keywords'][0]
        config.CRAWLER_MAX_NOTES_COUNT=20 if platform=='xhs' else 10
    return config,local


def patch_browser(crawler_class,local,platform):
    async def launch(self,chromium,playwright_proxy,user_agent,headless=True):
        profile=(BASE/'browser_profiles'/platform).resolve();profile.mkdir(parents=True,exist_ok=True)
        browser_path=os.environ.get('QUANTLAB_BROWSER_PATH') or (local.get('browser_path') if os.name=='nt' else None)
        context=await chromium.launch_persistent_context(user_data_dir=str(profile),executable_path=browser_path,
            headless=headless,viewport={'width':1280,'height':900},
            accept_downloads=False,ignore_https_errors=False)
        context.set_default_timeout(30000)
        return context
    crawler_class.launch_browser=launch


def patch_login(platform):
    modules={'wb':('weibo','WeiboLogin'),'xhs':('xhs','XiaoHongShuLogin'),'dy':('douyin','DouYinLogin')}
    module_name,class_name=modules[platform]
    module=importlib.import_module('media_platform.'+module_name+'.login')
    klass=getattr(module,class_name)
    async def begin(self):
        if importlib.import_module('config').HEADLESS:
            update(platform,'login_required',message='登录会话失效，请运行首次登录入口重新扫码')
            raise RuntimeError('User platform login required')
        update(platform,'awaiting_login',message='请在独立采集浏览器中扫码；无需提供密码或Cookie')
        local=json.loads((BASE/'collector_config.json').read_text(encoding='utf-8'))
        # Upstream fixed CSS selectors can expire when login pages change.
        # The user performs login in the visible platform page; only inspect
        # this application's fresh session for the authenticated cookie names.
        targets={'wb':'https://weibo.com/',
                 'xhs':'https://www.xiaohongshu.com/explore','dy':'https://www.douyin.com/'}
        await self.context_page.goto(targets[platform],wait_until='domcontentloaded')
        authenticated_names={'wb':{'SSOLoginState'},'xhs':{'web_session'},'dy':{'sessionid','sessionid_ss'}}
        deadline=asyncio.get_running_loop().time()+local['login_timeout_seconds']
        while asyncio.get_running_loop().time()<deadline:
            session=await self.browser_context.cookies()
            if any(row.get('name') in authenticated_names[platform] and row.get('value') for row in session):
                update(platform,'collecting',message='已完成独立会话登录，正在采集')
                await asyncio.sleep(3)
                return
            await asyncio.sleep(2)
        raise TimeoutError('Initial platform login timed out')
    klass.begin=begin
    if platform=='dy':
        async def manual(self,*args,**kwargs):
            try:await self.context_page.wait_for_selector('#captcha-verify-image',timeout=1500)
            except Exception:return
            update(platform,'manual_verification',message='请在独立浏览器手动完成平台验证')
            await self.context_page.wait_for_selector('#captcha-verify-image',state='hidden',timeout=180000)
            update(platform,'collecting')
        async def no_automated_slider(self,*args,**kwargs):
            raise RuntimeError('Platform verification requires the user in the browser')
        klass.check_page_display_slider=manual;klass.move_slider=no_automated_slider
    # Persist only the QR image needed by the user; do not print its token/URL.
    utils=importlib.import_module('tools.utils')
    def show_qrcode(value):
        raw=value.split(',',1)[-1] if ',' in value else value
        path=BASE/'.runtime'/f'login-{platform}.png';path.parent.mkdir(exist_ok=True)
        path.write_bytes(base64.b64decode(raw))
        update(platform,'awaiting_login',login_image=str(path),message='请在独立采集浏览器中扫码登录')
    utils.show_qrcode=show_qrcode


def patch_weibo_errors():
    """Do not repeat requests for comments the author/platform does not expose."""
    client=importlib.import_module('media_platform.weibo.client').WeiboClient
    original=getattr(client.request,'__wrapped__',client.request)
    async def request(self,method,url,**kwargs):
        try:return await original(self,method,url,**kwargs)
        except Exception as exc:
            if '/comments/' in url:
                data=json.loads(STATUS.read_text(encoding='utf-8'))
                count=data.get('platforms',{}).get('wb',{}).get('comment_requests_failed',0)+1
                update('wb','collecting',comment_requests_failed=count,last_source_error=str(exc)[:160])
            raise
    client.request=request


def bounded_items(items,counts,key,limit):
    remaining=max(0,limit-counts.get(key,0));result=items[:remaining]
    counts[key]=counts.get(key,0)+len(result)
    return result


def patch_weibo_page_limit(limit):
    # Some actual API pages contain more posts than the nominal page size.
    module=importlib.import_module('media_platform.weibo.core')
    source=importlib.import_module('var').source_keyword_var
    original=module.filter_search_result_card;counts={}
    def filtered(cards):return bounded_items(original(cards),counts,source.get(),limit)
    module.filter_search_result_card=filtered


def numeric_source_id(value):
    if isinstance(value,bool) or not isinstance(value,(str,int)) or not str(value).isdigit():
        raise ValueError('Source identifier must be an exact decimal integer')
    result=int(value)
    if not 0<result<=9223372036854775807:raise ValueError('Source identifier outside bigint range')
    return result


def patch_douyin_storage():
    store=importlib.import_module('store.douyin._store_impl').DouyinDbStoreImplement
    models=importlib.import_module('database.models')
    from sqlalchemy import String,Integer
    def normalize_item(value,model):
        item=dict(value)
        for key,column in model.__table__.columns.items():
            current=item.get(key)
            if current is None:continue
            if isinstance(column.type,String) and not isinstance(current,str):
                item[key]=json.dumps(current,ensure_ascii=False) if isinstance(current,(dict,list,bool)) else str(current)
            elif isinstance(column.type,Integer) and not isinstance(current,int):
                if not isinstance(current,str) or not current.lstrip('-').isdigit():raise ValueError('Non-integral source field: '+key)
                item[key]=int(current)
        return item
    content,comment=store.store_content,store.store_comment
    async def store_content(self,content_item):
        item=normalize_item(content_item,models.DouyinAweme);item['aweme_id']=numeric_source_id(item['aweme_id'])
        return await content(self,item)
    async def store_comment(self,comment_item):
        item=normalize_item(comment_item,models.DouyinAwemeComment)
        for key in ('aweme_id','comment_id'):item[key]=numeric_source_id(item[key])
        return await comment(self,item)
    store.store_content=store_content;store.store_comment=store_comment


def patch_xhs_pipeline(klass):
    """Persist per note before comments; one unavailable note cannot abort all.

    Only ordinary upstream endpoints are used. Stop on explicit access limits
    or three consecutive comment failures, with no alternate bypass route.
    """
    core=importlib.import_module('media_platform.xhs.core')
    client_module=importlib.import_module('media_platform.xhs.client')
    client=client_module.XiaoHongShuClient
    wrapped_request=client.request
    request=getattr(wrapped_request,'__wrapped__',wrapped_request)
    from tenacity import stop_after_attempt
    single_attempt=wrapped_request.retry_with(stop=stop_after_attempt(1))
    async def checked_request(self,method,url,**kwargs):
        try:
            # Retain upstream's one-attempt RetryError contract for its ordinary
            # note HTML fallback; comment failures are handled per note below.
            operation=request if '/comment/' in url else single_attempt
            return await operation(self,method,url,**kwargs)
        except Exception as exc:
            cause=exc.last_attempt.exception() if hasattr(exc,'last_attempt') else exc
            text=str(cause)
            if isinstance(cause,client_module.IPBlockError) or any(x in text for x in ('CAPTCHA','403','429','471','461')):
                raise SourceRestricted('Platform requires manual verification or limits access') from None
            raise
    client.request=checked_request
    async def encoded_get(self,uri,params=None):
        params=params or {}
        headers=await self._pre_headers(uri,params)
        # Current upstream fix: HTTPX's default comma/space encoding differs
        # from the browser query string supplied to the signing SDK.
        url=self._host+uri+('?' + source_query(params) if params else '')
        return await self.request(method='GET',url=url,headers=headers)
    client.get=encoded_get
    original_client=klass.create_xhs_client
    async def make_client(self,proxy):
        result=await original_client(self,proxy)
        # Match the actual dedicated browser rather than upstream's hardcoded UA.
        result.headers['user-agent']=await self.context_page.evaluate('navigator.userAgent')
        return result
    klass.create_xhs_client=make_client
    async def search(self):
        config=importlib.import_module('config');failures=0;total_failures=0
        for keyword in config.KEYWORDS.split(','):
            core.source_keyword_var.set(keyword);used=0;page=1;search_id=core.get_search_id()
            while used<config.CRAWLER_MAX_NOTES_COUNT:
                sort=core.SearchSortType(config.SORT_TYPE) if config.SORT_TYPE else core.SearchSortType.GENERAL
                response=await self.xhs_client.get_note_by_keyword(keyword=keyword,search_id=search_id,page=page,sort=sort)
                items=[row for row in response.get('items',[]) if row.get('id') and row.get('model_type') not in ('rec_query','hot_query')]
                if not items:break
                for row in items[:config.CRAWLER_MAX_NOTES_COUNT-used]:
                    detail=await self.get_note_detail_async_task(note_id=row['id'],xsec_source=row.get('xsec_source',''),
                        xsec_token=row.get('xsec_token',''),semaphore=asyncio.Semaphore(1))
                    used+=1
                    if not detail:continue
                    await core.xhs_store.update_xhs_note(detail)
                    try:
                        await self.get_comments(note_id=detail['note_id'],xsec_token=row.get('xsec_token',''),semaphore=asyncio.Semaphore(1))
                        failures=0
                    except SourceRestricted:raise
                    except core.DataFetchError:
                        failures+=1;total_failures+=1
                        update('xhs','collecting',comment_requests_failed=total_failures,last_source_error='Comment endpoint returned an unavailable result; no fabricated comments')
                        if failures>=3:raise SourceRestricted('Comment endpoint rejected three distinct notes; stopped this source') from None
                        await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
                if not response.get('has_more'):break
                page+=1;await asyncio.sleep(config.CRAWLER_MAX_SLEEP_SEC)
    klass.search=search


def source_query(params):
    return '&'.join(quote(str(key),safe='')+'='+quote('' if value is None else str(value),safe=',') for key,value in params.items())


async def collect(platform,headless=False,notes=None,smoke=False):
    config,local=configure(platform,headless,notes,smoke)
    mapping={'wb':('weibo','WeiboCrawler'),'xhs':('xhs','XiaoHongShuCrawler'),'dy':('douyin','DouYinCrawler')}
    module,klass_name=mapping[platform]
    klass=getattr(importlib.import_module('media_platform.'+module+'.core'),klass_name)
    logging.getLogger('MediaCrawler').addFilter(SafeSourceLogs())
    if platform=='wb':patch_weibo_errors();patch_weibo_page_limit(config.CRAWLER_MAX_NOTES_COUNT)
    if platform=='dy':patch_douyin_storage()
    if platform=='xhs':patch_xhs_pipeline(klass)
    session=importlib.import_module('database.db_session');engine_factory=session.create_async_engine
    def safe_engine(*args,**kwargs):
        kwargs.setdefault('hide_parameters',True)
        return engine_factory(*args,**kwargs)
    session.create_async_engine=safe_engine
    patch_browser(klass,local,platform);patch_login(platform)
    crawler=klass()
    update(platform,'collecting')
    try:
        await crawler.start()
        update(platform,'completed')
    finally:
        context=getattr(crawler,'browser_context',None)
        if context:
            try:await context.close()
            except Exception:pass


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--platform',required=True,choices=['wb','xhs','dy'])
    parser.add_argument('--headless',action='store_true');parser.add_argument('--notes',type=int);parser.add_argument('--smoke',action='store_true');args=parser.parse_args()
    try:asyncio.run(collect(args.platform,args.headless,args.notes,args.smoke))
    except BaseException as exc:
        # Some upstream login paths use sys.exit(0); do not report them as success.
        if isinstance(exc,KeyboardInterrupt):raise
        previous=json.loads(STATUS.read_text(encoding='utf-8')) if STATUS.exists() else {}
        state=previous.get('platforms',{}).get(args.platform,{}).get('status')
        update(args.platform,**failure_details(exc,state))
        logging.exception('Collector did not complete: %s',type(exc).__name__)
        sys.exit(1)
