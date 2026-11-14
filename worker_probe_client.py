#!/usr/bin/env -S /opt/homebrew/bin/python3 -I -B
"""Fixed qualification-only boundary around the candidate typed client.

Ralphy remains byte-identical to the installed trusted entry. Candidate Python
cannot fork, spawn, detach, access accounts or write beyond the disposable case.
The host broker executes fixed tests in separate no-fork sandboxes.
"""
from pathlib import Path
import json,os,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import worker_probe_cases as C

def main():
 source=Path(os.environ['MIDNIGHT_PROBE_SOURCE']).resolve()
 manifest=Path(os.environ['MIDNIGHT_LOCAL_WORKER_MANIFEST']).resolve()
 directory=manifest.parent;value=json.loads(manifest.read_text())
 if (directory.name not in C.CASE_NAMES or value.get('task_id')!='typed-worker-qualification-'+directory.name
     or value.get('workspace')!=str(directory/'work') or value.get('allowed_files')!=['solution.py']
     or os.environ.get('MIDNIGHT_WORKER_PROBE_BROKER')!=str(directory/'probe-broker')):raise ValueError('Only the fixed disposable qualification may invoke this client')
 profile=directory/'validation.sb'
 if profile.is_symlink() or not profile.is_file():raise ValueError('Fixed qualification profile is unavailable')
 command=['/usr/bin/sandbox-exec','-f',str(profile),'/usr/bin/env','TMPDIR='+str(directory/'test-tmp'),str(source/'local_worker.py'),*sys.argv[1:]]
 os.execve(command[0],command,dict(os.environ))

if __name__=='__main__':main()
