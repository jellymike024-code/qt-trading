"""Local MindSpider infrastructure, bounded runs and read-only dashboard export.

Run with this integration's isolated Python. Credentials stay in ignored local
files and are never included in status responses. No existing browser profile
or account credentials are imported.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from uuid import uuid4

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE.parent.parent
MIND = BASE/'runtime/MindSpider'
CRAWLER = MIND/'DeepSentimentCrawling/MediaCrawler'
RUNTIME = BASE/'.runtime'
PG = Path(os.environ.get('QUANTLAB_PG_BIN', str(BASE/'postgres/pgsql/bin') if os.name=='nt' else '/usr/lib/postgresql/15/bin'))
SECRET = BASE/'.secrets.json'
STATUS = WORKSPACE/'data/bettafish/collector_status.json'


def read(path, default=None):
    return json.loads(Path(path).read_text(encoding='utf-8-sig')) if Path(path).exists() else default


def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    finally:temp.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def environment():
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME') and not k.startswith('PIP_')}
    env['PYTHONIOENCODING']='utf-8';env['PIP_CONFIG_FILE']=os.devnull
    env['MPLCONFIGDIR']=str(BASE/'.runtime/matplotlib')
    return env


def settings():
    return read(BASE/'collector_config.json',read(BASE/'collector_config.example.json'))


def pg_tool(name):
    path=PG/(name+'.exe' if os.name=='nt' else name)
    return path if path.exists() else shutil.which(name) or path


def credentials():
    if not SECRET.exists():
        write(SECRET,{'superuser_password':secrets.token_urlsafe(32),'app_password':secrets.token_urlsafe(32)})
    return read(SECRET)


def database_env():
    values=credentials();port=settings()['database_port']
    return {'DB_DIALECT':'postgresql','DB_HOST':'127.0.0.1','DB_PORT':str(port),'DB_USER':'mindspider',
        'DB_PASSWORD':values['app_password'],'DB_NAME':'mindspider','DB_CHARSET':'utf8mb4',
        'POSTGRES_DB_HOST':'127.0.0.1','POSTGRES_DB_PORT':str(port),'POSTGRES_DB_USER':'mindspider',
        'POSTGRES_DB_PWD':values['app_password'],'POSTGRES_DB_NAME':'mindspider'}


def command(args, cwd=None, env=None, check=True):
    return subprocess.run([str(a) for a in args],cwd=cwd,env=env or environment(),check=check,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)


def database_start():
    RUNTIME.mkdir(exist_ok=True);data=BASE/'postgres/data';conf=settings();secret=credentials()
    if not (data/'PG_VERSION').exists():
        pwfile=RUNTIME/'init-password.tmp';pwfile.write_text(secret['superuser_password'],encoding='utf-8')
        try:command([pg_tool('initdb'),'-D',data,'-U','postgres','--pwfile',pwfile,'--auth=scram-sha-256','--encoding=UTF8','--locale=C'])
        finally:pwfile.unlink(missing_ok=True)
        with (data/'postgresql.conf').open('a',encoding='utf-8') as stream:
            stream.write(f"\nlisten_addresses = '127.0.0.1'\nport = {conf['database_port']}\nshared_buffers = '64MB'\nmax_connections = 30\ntimezone = 'Asia/Shanghai'\n")
            if os.name!='nt':
                socket_directory=str(RUNTIME.resolve()).replace("'","''")
                stream.write(f"unix_socket_directories = '{socket_directory}'\n")
    status=subprocess.run([str(pg_tool('pg_ctl')),'-D',str(data),'status'],env=environment(),capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if status.returncode:
        command([pg_tool('pg_ctl'),'-D',data,'-l',RUNTIME/'postgres.log','-w','start'])
    return {'host':'127.0.0.1','port':conf['database_port'],'status':'running'}


def connect(superuser=False, autocommit=False):
    import psycopg
    secret=credentials()
    return psycopg.connect(host='127.0.0.1',port=settings()['database_port'],
        user='postgres' if superuser else 'mindspider',password=secret['superuser_password'] if superuser else secret['app_password'],
        dbname='postgres' if superuser else 'mindspider',connect_timeout=5,autocommit=autocommit)


def initialize():
    database_start()
    from psycopg import sql
    secret=credentials()
    with connect(True,True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='mindspider'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE mindspider LOGIN PASSWORD {}').format(sql.Literal(secret['app_password'])))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='mindspider'").fetchone():
            conn.execute('CREATE DATABASE mindspider OWNER mindspider')
    values=database_env()
    envfile=BASE/'runtime/.env'
    envfile.write_text('\n'.join(f'{k}={v}' for k,v in values.items())+'\nHOST=127.0.0.1\n',encoding='utf-8')
    env=environment()|values
    command([sys.executable,'-E',MIND/'schema/init_database.py'],cwd=MIND,env=env)
    # Initialize platform ORM tables directly: no login or browser is needed.
    script=f'import sys,asyncio;sys.path.insert(0,{str(CRAWLER)!r});from database.db_session import create_tables;asyncio.run(create_tables("postgres"))'
    command([sys.executable,'-E','-c',script],cwd=CRAWLER,env=env)
    with connect() as conn:
        tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")]
    migrate_douyin_ids()
    result={'at':now(),'status':'ready_for_login','database':{'host':'127.0.0.1','port':settings()['database_port'],'tables':len(tables)},
        'runtime':'MindSpider + pinned MediaCrawler','platforms':{},'analysis_mode':settings()['analysis_mode']}
    previous=read(STATUS,{})
    result['platforms']=previous.get('platforms',{})
    write(STATUS,result)
    return result


def migrate_douyin_ids():
    """Reconcile MindSpider's legacy varchar IDs with pinned crawler bigint IDs.

    Validate all existing values before casting, never drop/recreate tables.
    The numeric identifiers remain strings in the dashboard export.
    """
    from psycopg import sql
    if (RUNTIME/'crawl.lock').exists():raise RuntimeError('Do not migrate while a collector run is active')
    changed=[];before=[]
    targets=[('douyin_aweme','aweme_id'),('douyin_aweme_comment','aweme_id'),('douyin_aweme_comment','comment_id')]
    with connect() as conn:
        for table,column in targets:
            dtype=conn.execute('SELECT data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s',('public',table,column)).fetchone()
            if not dtype or dtype[0]=='bigint':continue
            if dtype[0] not in ('text','character varying'):raise ValueError('Unexpected platform ID schema')
            t,c=sql.Identifier(table),sql.Identifier(column)
            if conn.execute(sql.SQL("SELECT 1 FROM {} WHERE {} IS NOT NULL AND {} !~ '^[0-9]+$' LIMIT 1").format(t,c,c)).fetchone():
                raise ValueError('Non-numeric existing Douyin ID; migration left unchanged')
            if conn.execute(sql.SQL('SELECT 1 FROM {} WHERE {}::numeric>9223372036854775807 LIMIT 1').format(t,c)).fetchone():
                raise ValueError('Existing ID exceeds bigint; migration left unchanged')
            count=conn.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(t)).fetchone()[0]
            before.append({'table':table,'column':column,'old_type':dtype[0],'rows':count})
        if before:write(RUNTIME/'douyin-schema-before.json',{'at':now(),'columns':before})
        for row in before:
            t,c=sql.Identifier(row['table']),sql.Identifier(row['column'])
            conn.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} TYPE BIGINT USING {}::BIGINT').format(t,c,c))
            if conn.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(t)).fetchone()[0]!=row['rows']:
                raise RuntimeError('Row-count invariant failed; transaction rolled back')
            changed.append(row['table']+'.'+row['column'])
    return {'changed_columns':changed,'preserved_rows':True}


TABLES = {
    'wb':('weibo','weibo_note_comment','weibo_note','note_id','note_url','content','create_time'),
    'xhs':('xiaohongshu','xhs_note_comment','xhs_note','note_id','note_url','title','time'),
    'dy':('douyin','douyin_aweme_comment','douyin_aweme','aweme_id','aweme_url','title','create_time'),
    'bili':('bilibili','bilibili_video_comment','bilibili_video','video_id','video_url','title','create_time'),
    'ks':('kuaishou','kuaishou_video_comment','kuaishou_video','video_id','video_url','title','create_time'),
}


def export_database():
    """Fixed table allowlist; join the actually collected parent evidence URL."""
    from psycopg import sql
    from psycopg.rows import dict_row
    totals={}
    with connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        for short,(platform,comments,posts,key,url,title,pub) in TABLES.items():
            query=sql.SQL('SELECT c.*,p.{url} AS source_url,p.{title} AS parent_title,p.source_keyword FROM {comments} c '
                'LEFT JOIN {posts} p ON c.{key}=p.{key} WHERE c.add_ts IS NOT NULL ORDER BY c.id DESC LIMIT 5000').format(
                **{k:sql.Identifier(v) for k,v in dict(url=url,title=title,comments=comments,posts=posts,key=key).items()})
            with conn.cursor(row_factory=dict_row) as cursor:
                rows=cursor.execute(query).fetchall()
            records=[]
            for row in rows:
                # The source's add_ts is the first collection timestamp, never reset here.
                records.append({'platform':platform,'content_type':'comment','comment_id':str(row['comment_id']),
                    'content':row['content'],'publish_time':row.get('create_date_time') or row.get('create_time'),
                    'observed_at':row['add_ts'],'user_id':row.get('user_id'),'source_url':row.get('source_url'),
                    'source_keyword':row.get('source_keyword'),'parent_title':row.get('parent_title'),
                    'source_table':comments,'evidence_url_kind':'collected_parent_post'})
            # No empty exports that could falsely imply an active comment source.
            if records:write(BASE/'inbox'/f'{platform}_comments_database.json',records)
            totals[platform]=len(records)
    return totals


def sync_dashboard():
    # The integration environment also has pandas/requests required by this adapter.
    sys.path.insert(0,str(WORKSPACE))
    from quantlab.bettafish import sync
    return sync(WORKSPACE)


def seed_topics():
    """Keep actual discovery evidence and explicit manual scope separate."""
    from zoneinfo import ZoneInfo
    timestamp=int(datetime.now(timezone.utc).timestamp());day=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    candidates=read(WORKSPACE/'data/social/candidates.json',{}).get('items',[])
    with connect() as conn:
        for row in candidates:
            conn.execute('INSERT INTO daily_news (news_id,source_platform,title,url,extra_info,crawl_date,rank_position,add_ts,last_modify_ts) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (news_id) DO UPDATE SET last_modify_ts=EXCLUDED.last_modify_ts',
                (row['id'],row['platform'],row['title'][:500],row['source_url'],json.dumps({k:row.get(k) for k in ['published_at','observed_at','provider_updated_at','content_kind']},ensure_ascii=False),
                 day,row.get('rank'),timestamp,timestamp))
        conn.execute('INSERT INTO daily_topics (topic_id,topic_name,topic_description,keywords,extract_date,processing_status,add_ts,last_modify_ts) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (topic_id) DO UPDATE SET keywords=EXCLUDED.keywords,last_modify_ts=EXCLUDED.last_modify_ts',
            ('configured-etf-'+str(day),'ETF研究观察列表（人工设定）','采集范围配置；不是新闻事实或模型发现的热点',json.dumps(settings()['keywords'],ensure_ascii=False),day,'configured',timestamp,timestamp))
    return {'real_discovery_candidates':len(candidates),'keyword_origin':'configured_watchlist','date':str(day)}


def verify_sessions(platforms=None):
    """Enable automation only after actual new comment data proves access."""
    from psycopg import sql
    status=read(STATUS,{});conf=settings();verified={}
    with connect() as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        for platform in platforms or conf['platforms']:
            if platform not in conf['platforms']:raise ValueError('unknown configured platform')
            row=status.get('platforms',{}).get(platform,{})
            if not row.get('started_at') or row.get('status') in ('starting','awaiting_login','login_required','manual_verification'):
                verified[platform]=False;continue
            start=datetime.fromisoformat(row['started_at']).timestamp()
            table=TABLES[platform][1]
            query=sql.SQL('SELECT COUNT(*) FROM {} WHERE (CASE WHEN add_ts>=1000000000000 THEN add_ts/1000.0 ELSE add_ts END)>=%s').format(sql.Identifier(table))
            count=conn.execute(query,(start,)).fetchone()[0]
            verified[platform]=bool(count)
            if count and platform not in conf['automatic_platforms']:conf['automatic_platforms'].append(platform)
            if count:row.update(last_data_verified_at=now(),comments_observed_in_run=count)
    write(BASE/'collector_config.json',conf);write(STATUS,status)
    return verified


def _crawl(platforms=None, headless=False, notes=None, smoke=False):
    conf=settings();platforms=platforms or conf['platforms']
    if not all(p in conf['platforms'] for p in platforms):raise ValueError('platform is not enabled in collector_config.json')
    if notes is not None and not 1<=notes<=conf['max_notes_per_keyword']:raise ValueError('notes must be within configured collection limit')
    if notes is not None and notes<(20 if 'xhs' in platforms else 10):raise ValueError('notes is below the upstream minimum page size')
    database_start()
    seed_topics()
    env=environment()|database_env();status=read(STATUS,{'platforms':{}})
    for platform in platforms:
        log=RUNTIME/f'{platform}-latest.log';RUNTIME.mkdir(exist_ok=True)
        args=[sys.executable,'-E',BASE/'runner.py','--platform',platform]
        if headless:args.append('--headless')
        if notes is not None:args+=['--notes',str(notes)]
        if smoke:args.append('--smoke')
        try:
            with log.open('w',encoding='utf-8') as stream:
                proc=subprocess.Popen([str(a) for a in args],cwd=CRAWLER,env=env,stdout=stream,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            status=read(STATUS,status);status.update(at=now(),status='running',active_platform=platform)
            status.setdefault('platforms',{})[platform]={'status':'starting','pid':proc.pid,'started_at':now(),'log':str(log)}
            write(STATUS,status)
            deadline=time.monotonic()+conf['platform_timeout_seconds']
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(args,conf['platform_timeout_seconds'])
                try:
                    code=proc.wait(timeout=min(20,remaining));break
                except subprocess.TimeoutExpired:
                    if time.monotonic()>=deadline:raise
                    totals=export_database();sync_dashboard();verify_sessions([platform])
                    status=read(STATUS,status);status['database_export']=totals;write(STATUS,status)
            status=read(STATUS,status);row=status['platforms'].setdefault(platform,{})
            row.update(return_code=code,finished_at=now())
            if code:row['status']=row['status'] if row.get('status') in ('source_unavailable','browser_startup_crash') else 'login_required' if row.get('status') in ('awaiting_login','manual_verification','login_required') else 'error'
            else:
                row['status']='completed'
        except subprocess.TimeoutExpired:
            proc.terminate();proc.wait(timeout=15)
            status=read(STATUS,status);status['platforms'].setdefault(platform,{}).update(status='timeout',finished_at=now())
        finally:
            status.update(at=now(),active_platform=None,status='idle');write(STATUS,status)
            status['database_export']=export_database();sync_dashboard();write(STATUS,status);verify_sessions([platform])
    return read(STATUS)


def crawl(platforms=None,headless=False,notes=None,automatic=False,smoke=False):
    if automatic:
        platforms=settings()['automatic_platforms']
        if not platforms:return {'status':'skipped','reason':'initial_platform_login_not_completed'}
        headless=True
    RUNTIME.mkdir(exist_ok=True);lock=RUNTIME/'crawl.lock'
    descriptor=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    try:
        os.write(descriptor,str(os.getpid()).encode());os.close(descriptor)
        return _crawl(platforms,headless,notes,smoke)
    finally:lock.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['db-start','db-stop','init','migrate','seed','verify','crawl','export','status'])
    parser.add_argument('--platforms',nargs='+');parser.add_argument('--headless',action='store_true');parser.add_argument('--notes',type=int);parser.add_argument('--automatic',action='store_true');parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    if args.command=='db-start':result=database_start()
    elif args.command=='db-stop':command([pg_tool('pg_ctl'),'-D',BASE/'postgres/data','-m','fast','-w','stop']);result={'status':'stopped'}
    elif args.command=='init':result=initialize()
    elif args.command=='migrate':result=migrate_douyin_ids()
    elif args.command=='seed':result=seed_topics()
    elif args.command=='verify':result=verify_sessions(args.platforms)
    elif args.command=='crawl':result=crawl(args.platforms,args.headless,args.notes,args.automatic,args.smoke)
    elif args.command=='export':result={'database_export':export_database(),'dashboard':sync_dashboard()}
    else:result=read(STATUS,{'status':'not_initialized'})
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
