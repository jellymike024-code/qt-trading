"""Export only allowlisted public data. Never publish the local API response."""
import argparse
from datetime import datetime, timezone
import html
import importlib.util
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
QUOTE_FIELDS=('price','change_pct','source','source_url','observed_at','quote_time','session_date','currency','interval','status','history','session_note')
METRIC_FIELDS=('sessions','total_return','max_drawdown','sharpe','annualized_return','fees','fills','turnover','mean_exposure')


def pick(value,keys):
    return {key:value[key] for key in keys if key in value} if isinstance(value,dict) else {}


def public_context(source):
    """No original comments, identities, collector internals or paper account data."""
    markets=source.get('markets') or {}; social=source.get('social') or {}
    status=social.get('status') or {}; research=source.get('research') or {}
    public_markets=pick(markets,('mode','collected_at','status','coverage','notes'))
    public_markets['regions']={}
    for city,region in markets.get('regions',{}).items():
        public_markets['regions'][city]={
            'index':pick(region.get('index'),QUOTE_FIELDS) or None,
            'stocks':{code:pick(quote,QUOTE_FIELDS) for code,quote in region.get('stocks',{}).items()},
            'sectors':{name:pick(data,('change_pct','coverage','session_date','status')) for name,data in region.get('sectors',{}).items()}}
    public_status=pick(status,('at','status','active_records','financial_candidates','actual_comment_texts','trading_effect'))
    public_status['sources']={name:pick(data,('status','records','comment_text_records','provider_updated_at')) for name,data in status.get('sources',{}).items()}
    item_fields=('id','platform','content_kind','title','source_url','published_at','observed_at','first_seen_at','matched_keywords')
    items=[pick(row,item_fields) for row in social.get('items',[]) if row.get('content_kind') in ('hot_topic','news_headline')]
    return {'served_at':datetime.now(timezone.utc).isoformat(),'quote_mode':'real_sources_only',
        'publication':{'mode':'public_snapshot','original_comments_published':False,'paper_ledger_published':False,
            'note':'公开页仅展示真实公开行情、热榜/财经标题及历史研究汇总；不发布原始评论、登录会话或模拟账本。'},
        'markets':public_markets,'social':{'status':public_status,'items':items,'analysis':[]},
        'research':pick(research,('provider','latest_session','holdout_start'))|{
            'holdout':{name:pick(row,METRIC_FIELDS) for name,row in (research.get('holdout') or {}).items()}},
        'equity':[pick(row,('timestamp','equity')) for row in source.get('equity',[])],
        'paper':{},'configuration':{},'bettafish':{'status':{'status':'private_data_not_published','comments':0},'dashboard':{},'collector':{}}}


def local_context(root=ROOT):
    spec=importlib.util.spec_from_file_location('orbit_export_server',root/'frontend/serve.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.context()


def build(output,context):
    output=Path(output).resolve()
    if output==ROOT or output in ROOT.parents or output.is_relative_to(ROOT/'frontend/dist'):
        raise ValueError('Output must be a separate build directory')
    if output.exists() and any(output.iterdir()) and not (output/'.orbit-public-build').exists():
        raise ValueError('Refuse to copy into an unrelated nonempty directory')
    output.mkdir(parents=True,exist_ok=True);(output/'.orbit-public-build').touch()
    dist=ROOT/'frontend/dist'
    for name in ('index.html','styles.css'):
        shutil.copy2(dist/name,output/name)
    for name in ('js','vendor','assets','data'):
        shutil.copytree(dist/name,output/name,dirs_exist_ok=True)
    page=(output/'index.html').read_text(encoding='utf-8').replace('<html lang="zh-CN">','<html lang="zh-CN" data-context-url="./data/context.json">')
    (output/'index.html').write_text(page,encoding='utf-8')
    (output/'data/context.json').write_text(json.dumps(context,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    (output/'.nojekyll').touch()
    reports=output/'research';reports.mkdir(exist_ok=True)
    note=html.escape(context['publication']['note'])
    for name,title,payload in [('dashboard.html','历史研究汇总',context['research']),('social_dashboard.html','公开来源覆盖',context['social']['status'])]:
        body=html.escape(json.dumps(payload,ensure_ascii=False,indent=2))
        (reports/name).write_text(f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{title}</title><style>body{{background:#061221;color:#d8eaff;max-width:1000px;margin:40px auto;padding:20px;font:16px system-ui}}pre{{white-space:pre-wrap}}a{{color:#68d4ff}}</style><h1>{title}</h1><p>{note}</p><p>历史模拟不代表未来收益；行情可能延迟，日期以原始来源为准。</p><a href="../">返回大屏</a><pre>{body}</pre></html>',encoding='utf-8')
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'.site')
    parser.add_argument('--seed',type=Path);parser.add_argument('--write-seed',type=Path)
    parser.add_argument('--merge-public-seed',type=Path)
    args=parser.parse_args()
    seed=args.seed or args.merge_public_seed
    if seed:
        data=json.loads(seed.read_text(encoding='utf-8'))
        if data.get('publication',{}).get('mode')!='public_snapshot':raise ValueError('Not a sanitized public seed')
        # Apply the same allowlist again, so injected/private fields cannot pass through.
        data=public_context(data)
        if args.merge_public_seed:
            fresh=public_context(local_context())
            if fresh['markets'].get('collected_at'):data['markets']=fresh['markets']
            if fresh['social']['status'].get('at'):data['social']=fresh['social']
    else:data=public_context(local_context())
    if args.write_seed:
        args.write_seed.parent.mkdir(parents=True,exist_ok=True)
        args.write_seed.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(build(args.output,data))
