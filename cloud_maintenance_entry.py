"""Owned controller child; never called by a status path."""
import datetime as dt
import json
from pathlib import Path
import sys
import os
import signal
from cloud_policy import maintenance
from github_setup import reconcile
from coordinator_release import reconcile as reconcile_release
if __name__ == '__main__':
    def stop(_sig,_frame):
        signal.signal(signal.SIGTERM,signal.SIG_DFL)
        os.killpg(os.getpgrp(),signal.SIGTERM)
    signal.signal(signal.SIGTERM,stop)
    runtime=Path(sys.argv[1]);cutoff=dt.datetime.fromisoformat(sys.argv[2])
    if dt.datetime.now(dt.timezone.utc)<cutoff and not (runtime/'PAUSED').exists():
        registry=json.loads((Path(__file__).parent/'projects.json').read_text())
        setup=reconcile(runtime,registry['projects'])
        if setup.get('status')=='configured':reconcile_release(runtime,cutoff=cutoff)
    if dt.datetime.now(dt.timezone.utc)<cutoff and not (runtime/'PAUSED').exists():
        maintenance(runtime,cutoff,task_boundary=True,allow_qualification=False)
