"""Bounded complete accepted-source and actual discovery evidence for growth."""
import hashlib
import json
from pathlib import Path
import re
import evidence_context as E
import profile_manager as PM
from quality_cycle import profile_hash


def collect(root,runtime,update,profile,git,exists,*,budget=64000,verification_dir=None):
    source={};discovery=[];remaining=44000
    for path in update['task']['files']:
        if not path.endswith('.py') or not exists(update['commit'],path):continue
        text=git('show',update['commit']+':'+path);safe=E.sanitize(text)
        size=len(json.dumps(safe).encode())
        complete=size<=min(32000,remaining)
        source[path]={'displayed_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'bytes':len(text.encode()),'complete':complete,
            'text':safe if complete else '[Complete file exceeds the bounded handoff; a narrower coverage proof is required]'}
        if complete:remaining-=size
    run=update.get('evidence',{}).get('run','')
    folder=Path(runtime)/'runs'/run if re.fullmatch(r'[A-Za-z0-9_-]{1,160}',run) else Path(runtime)/'unavailable'
    if verification_dir is not None:
        binding=E.read_json(Path(verification_dir)/'coverage-base.json',runtime)
        if binding.get('head')==update['commit'] and binding.get('profile_hash')==profile_hash(profile):folder=Path(verification_dir)
    checks=E.read_json(folder/'checks.json',runtime)
    for path in update['task']['files']:
        if not path.startswith('tests/') or not path.endswith('.py'):continue
        for check in profile['checks']:
            if not check.get('required',True) or not PM._check_covers(check,path):continue
            packages=PM._discovery_packages(check,path)
            for index,actual in enumerate(checks if isinstance(checks,list) else []):
                if actual.get('command')!=check['command'] or actual.get('code')!=0 or actual.get('reason')!='exited':continue
                log=folder/f'check-{index}.log';lines=[]
                if E.safe_file(log,runtime) and log.stat().st_size<=2*1024*1024:
                    stem=Path(path).stem
                    lines=[E.sanitize(s)[:1000] for s in log.read_text(errors='replace').splitlines() if stem in s and ' ... ok' in s][:30]
                discovery.append({'test_path':path,'check_id':check['id'],'command':check['command'],
                    'selected_by_required_check':True,'packages_present':all(exists(update['commit'],p) for p in packages),
                    'observed_passed_tests':lines,'actual_check':{'code':actual['code'],'reason':actual['reason']},
                    'receipt':str(log.relative_to(runtime))})
    value={'version':1,'accepted_commit':update['commit'],'complete_accepted_source':source,'required_discovery':discovery}
    value['complete']=bool(source) and all(s['complete'] for s in source.values()) and bool(discovery) and all(
        d['packages_present'] and d['observed_passed_tests'] for d in discovery)
    if len(json.dumps(value).encode())>budget:raise ValueError('Profile evidence exceeds its serialized handoff bound')
    value['evidence_hash']=E.digest(value)
    return value
