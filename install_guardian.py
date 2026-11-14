"""Install one independent audit LaunchAgent from a clean, reviewed release.

Call after the release installer has verified its exact commit/tree. Runtime
bundles and logs remain under Developer; only the plist lives in LaunchAgents.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tarfile
import durability as D


def install(root,*,load=True,expected_permission_sha256=None):
    root=Path(root).resolve()
    prior=D.read_json(root/'runtime/resilience/authority.json',{})
    if expected_permission_sha256 is None and prior.get('automation')=='AUT-0004':
        expected_permission_sha256=prior.get('permission_sha256')
        if not isinstance(expected_permission_sha256,str) or len(expected_permission_sha256)!=64:raise ValueError('Guardian publication requires a complete existing authority seal')
    if expected_permission_sha256 is not None:
        from maintenance_authority import permissions,digest
        if digest(permissions())!=expected_permission_sha256:raise ValueError('Guardian publication authority differs from the reviewed permission digest')
    if subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip():raise ValueError('Guardian requires clean installed source')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    resilience=root/'runtime'/'resilience';bundle=resilience/'bundles'/head
    bundle.mkdir(parents=True,exist_ok=True,mode=0o700)
    archive=subprocess.check_output(['git','archive',head],cwd=root)
    tracked=[]
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar:
            name=Path(member.name)
            if member.isdir():continue
            if not member.isfile() or name.is_absolute() or '..' in name.parts:raise ValueError('Recovery archive contains an unsupported member')
            path=bundle/name;D.atomic_write(path,tar.extractfile(member).read())
            tracked.append(str(name))
    required=['guardian.py','core_repair.py','recovery_policy.json','action_store.py','phase_runtime.py','phase_guard.py',
        'project_action.py','history_store.py','durability.py','cloud_policy.py','cloud_models.json','git_delivery.py','verification_sandbox.py',
        'process_observer.py','verification_runner.py','tests/test_models.py','tests/test_maintenance_sandbox.py','model_manager.py',
        'model_bench.py','model_cases.py','models.json','colibri_provider.py','maintenance_authority.py']
    manifest={'schema_version':1,'canonical':str(root),'bundle':str(bundle),'source':head,
        'files':{n:hashlib.sha256((bundle/n).read_bytes()).hexdigest() for n in tracked}}
    if any(not (bundle/n).is_file() for n in required):raise ValueError('Frozen recovery bundle is incomplete')
    D.atomic_write(resilience/'manifest.json',(json.dumps(manifest,indent=2)+'\n').encode())
    D.atomic_write(resilience/'guardian_bootstrap.py',(root/'guardian_bootstrap.py').read_bytes())
    from maintenance_authority import seal
    if expected_permission_sha256 is None:seal(root)
    else:seal(root,expected_permission_sha256=expected_permission_sha256)
    label='space.venin.midnight-guardian';plist=Path.home()/'Library'/'LaunchAgents'/(label+'.plist')
    value={'Label':label,'ProgramArguments':[sys.executable,'-B',str(resilience/'guardian_bootstrap.py')],
        'RunAtLoad':True,'StartInterval':60,'ProcessType':'Standard','WorkingDirectory':str(resilience),
        'EnvironmentVariables':{'PATH':'/opt/homebrew/bin:'+os.path.expanduser('~/.local/bin')+':/usr/bin:/bin:/usr/sbin:/sbin','TZ':'Australia/Adelaide','PYTHONDONTWRITEBYTECODE':'1'},
        'StandardOutPath':str(root/'runtime'/'guardian.out.log'),'StandardErrorPath':str(root/'runtime'/'guardian.err.log'),
        'ExitTimeOut':30,'ThrottleInterval':30}
    if load:subprocess.run(['/bin/launchctl','bootout',f'gui/{os.getuid()}/{label}'],capture_output=True,timeout=10)
    D.atomic_write(plist,plistlib.dumps(value))
    if load:subprocess.run(['/bin/launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],check=True,capture_output=True,timeout=10)
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default=str(Path(__file__).resolve().parent));args=parser.parse_args()
    print(json.dumps(install(Path(args.root))))
