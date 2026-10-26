"""Restore the pinned upstream source from verified Git blob manifests."""
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import requests

BASE=Path(__file__).resolve().parents[1]/'integrations/BettaFish'


def restore():
    jobs=[]
    for filename,repository,destination in [
        ('collector-source.json','666ghj/BettaFish',BASE/'runtime'),
        ('mediacrawler-source.json','NanmiCoder/MediaCrawler',BASE/'runtime/MindSpider/DeepSentimentCrawling/MediaCrawler')]:
        manifest=json.loads((BASE/filename).read_text(encoding='utf-8'))
        for row in manifest['files']:
            target=(destination/row['path']).resolve()
            if not target.is_relative_to(destination.resolve()):raise ValueError('Unsafe source path')
            jobs.append((f'https://raw.githubusercontent.com/{repository}/{manifest["revision"]}/{row["path"]}',target,row.get('git_blob_sha1') or row['sha']))
    def fetch(job):
        url,path,expected=job
        def digest(data):return hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if path.exists() and digest(path.read_bytes())==expected:return
        response=requests.get(url,timeout=40);response.raise_for_status();data=response.content
        if digest(data)!=expected:raise ValueError('Upstream content hash mismatch: '+path.name)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(fetch,jobs))
    config=BASE/'collector_config.json'
    if not config.exists():config.write_bytes((BASE/'collector_config.example.json').read_bytes())
    print(f'Verified {len(jobs)} pinned source files. No credentials or sessions were copied.')


if __name__=='__main__':restore()
