"""Inspect the exact Git index without printing potential secret values."""
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def audit():
    names=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    names=[name for name in names if name]
    forbidden=('data/','state/','reports/','.venv/','.tmp/','.site/',
        'integrations/BettaFish/browser_profiles/','integrations/BettaFish/postgres/',
        'integrations/BettaFish/runtime/','integrations/BettaFish/inbox/')
    secret_file=ROOT/'integrations/BettaFish/.secrets.json'
    secrets=list(json.loads(secret_file.read_text(encoding='utf-8')).values()) if secret_file.exists() else []
    issues=[];total=0
    for name in names:
        if name.startswith(forbidden) or '/.runtime/' in name or name.endswith(('.pem','.key','.log')) or Path(name).name in ('.env','.secrets.json','collector_config.json'):
            issues.append((name,'private runtime path'))
        payload=subprocess.check_output(['git','show',':'+name],cwd=ROOT);total+=len(payload)
        if any(isinstance(value,str) and value and value.encode() in payload for value in secrets):
            issues.append((name,'matches local database credential'))
        if re.search(rb'(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)',payload):
            issues.append((name,'credential-shaped content'))
    if 'frontend/dist/data/market_universe.json' not in names:issues.append(('frontend/dist/data/market_universe.json','required file missing'))
    if issues:raise RuntimeError('Publication audit failed: '+repr(issues))
    print(json.dumps({'files':len(names),'bytes':total,'credential_matches':0,'private_runtime_files':0}))


if __name__=='__main__':audit()
