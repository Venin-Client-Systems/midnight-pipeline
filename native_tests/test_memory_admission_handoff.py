"""Real guarded admission handoff; no model, native gateway or live controls."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import colibri_provider as GP
import durability as D
import memory_runtime as MR
import phase_runtime as P
from action_store import Actions


WORKER = r'''
import json, os, sys, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
import colibri_provider as GP
import memory_runtime as MR
import model_bench as MB
import phase_runtime as P
root=Path(sys.argv[2]); MB.GPU_LOCK=root/'gpu.lock'
model=json.loads((root/'model.json').read_text()); key=sys.argv[3]
limit=time.monotonic()+10
while not (root/'parent-descriptors-closed').exists():
    if time.monotonic()>=limit:raise TimeoutError('Parent did not release descriptor copies')
    time.sleep(.02)
fd=int(os.environ['MIDNIGHT_GPU_LEASE_FD'])
with P.gpu_lock(MB.GPU_LOCK):
    try:os.fstat(fd);closed=False
    except OSError:closed=True
    with patch.object(GP,'phase_profile',return_value={}) as profile, patch.object(GP.NativeSession,'start',side_effect=AssertionError('Native gateway forbidden in fixture')) as native:
        proof=MR.validate_provider_start(root,model,key)
        profile.assert_called_once_with(model);native.assert_not_called()
    identity=os.environ['MIDNIGHT_MEMORY_ADMISSION_PHASE']
    execution=next(e for e in P.Actions(root/'runtime').executions() if e['phase']==identity)
    result={'phase':proof['phase'],'job':proof['job'],'input_key':proof['input_key'],
            'worker_pid':os.getpid(),'worker_group':os.getpgrp(),'guard_pid':execution['lease']['pid'],
            'inherited_gpu_fd_closed':closed,'token_removed':'MIDNIGHT_GPU_LEASE_FD' not in os.environ,
            'execution_has_members':bool(execution['lease']['members']),
            'native_started':native.called}
    (root/'ready.json').write_text(json.dumps(result))
    limit=time.monotonic()+10
    while not (root/'done').exists():
        if time.monotonic()>=limit:raise TimeoutError('Fixture parent did not finish')
        time.sleep(.02)
'''


@unittest.skipUnless(sys.platform=='darwin','Native macOS guarded process admission')
class NativeMemoryAdmissionHandoff(unittest.TestCase):
    def test_real_guard_shell_worker_validates_proof_after_parent_and_worker_fd_release(self):
        source=Path(MR.__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary,contextlib.ExitStack() as stack:
            root=Path(temporary).resolve();runtime=root/'runtime';state=root/'project';state.mkdir()
            resources=runtime/'resources';resources.mkdir(parents=True)
            D.save_json(root/'projects.json',{'timezone':'Australia/Adelaide','schedule':{'start':'00:00','end':'09:00'},
                'projects':[{'id':'fixture','on_schedule':True,'state_dir':str(state)}]})
            D.save_json(runtime/'override.json',{'mode':'continuous','expires_at':None})
            D.save_json(runtime/'memory-coordination.json',{'version':1,'enabled':True})
            model={'model':GP.MODEL,'digest':'a'*64,'context':32768,'bytes':1,'parameters_sha256':'b'*64}
            (root/'model.json').write_text(json.dumps(model));key='c'*64;deadline=time.time()+30
            store=Actions(runtime);job=store.submit('fixture',{'worker_model':model},deadline)
            self.assertTrue(store.claim(job['id'],P.lease()))
            phase=store.phase(job['id'],'fixture','local',key,deadline-1,{})
            store.phase_update(phase['id'],'running',lease=P.lease())
            folder=runtime/'phases'/phase['id'];folder.mkdir(parents=True)
            paths={'local':resources/'local-0.lock','heavy':resources/'heavy-memory.lock','gpu':root/'gpu.lock'}
            handles={name:stack.enter_context(path.open('a')) for name,path in paths.items()}
            for handle in handles.values():fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            quiet={'ready':True,'quiet_seconds':120,'sample_count':25,'minimum_free_inactive_bytes':24_000_000_000,
                   'swap_out_bytes':0,'latest':{'at':time.time(),'boot_id':D.boot_id(),'pressure':'normal','free_inactive_bytes':24_000_000_000}}
            # Use the real proof producer and immutable launch hash. Only the
            # profile and two-minute host observation are synthetic.
            with patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':job['id']}),patch.object(GP,'config',return_value={'version':2}),patch.object(GP,'phase_profile',return_value={}), \
                 patch.object(P,'SLOT_FD',handles['local'].fileno()),patch.object(P,'MEMORY_FD',handles['heavy'].fileno()),patch.object(P,'GPU_FD',handles['gpu'].fileno()), \
                 patch.dict(P.SLOT_NAMES,{handles['local'].fileno():'local-0.lock'}):
                admitted=MR.Admission(runtime,phase['id'],model,key,deadline,lambda:False)
                admitted.gate=SimpleNamespace(observe=lambda:quiet);admitted.before_launch()
            command=['/bin/sh','-c','exec "$@"','fixture-shell',sys.executable,'-I','-B','-c',WORKER,str(source),str(root),key]
            launch={'command':command,'cwd':str(root),'slot_name':'local-0.lock','memory_slot':'heavy-memory.lock',
                    'gpu_lease':True,'fresh_controls':True,**MR.launch_fields(runtime,phase['id'])}
            D.atomic_write(folder/'launch.json',json.dumps(launch).encode())
            self.assertEqual(launch['memory_admission_sha256'],hashlib.sha256((folder/'memory-admission-proof.json').read_bytes()).hexdigest())
            wrapper="import sys;sys.path.insert(0,sys.argv.pop(1));from pathlib import Path;import model_bench as B;B.GPU_LOCK=Path(sys.argv.pop(1));import phase_guard as G;sys.exit(G.main())"
            env=dict(os.environ,MIDNIGHT_ACTION_ID=job['id'],MIDNIGHT_SHARED_ROOT=str(root),
                     MIDNIGHT_MEMORY_ADMISSION_PHASE=phase['id'],MIDNIGHT_RESOURCE_INPUT_KEY=key)
            proc=subprocess.Popen([sys.executable,'-I','-B','-c',wrapper,str(source),str(paths['gpu']),
                '--runtime',str(runtime),'--phase',phase['id'],'--launch',str(folder/'launch.json'),
                '--slot',str(handles['local'].fileno()),'--memory-slot',str(handles['heavy'].fileno()),'--gpu-slot',str(handles['gpu'].fileno())],
                pass_fds=tuple(handle.fileno() for handle in handles.values()),env=env,start_new_session=True,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                for handle in handles.values():handle.close()
                (root/'parent-descriptors-closed').touch()
                limit=time.monotonic()+10
                while not (root/'ready.json').exists() and proc.poll() is None and time.monotonic()<limit:time.sleep(.03)
                self.assertTrue((root/'ready.json').exists(),proc.communicate(timeout=1) if proc.poll() is not None else 'No worker proof')
                result=json.loads((root/'ready.json').read_text())
                self.assertEqual((result['phase'],result['job'],result['input_key']),(phase['id'],job['id'],key))
                self.assertEqual(result['worker_group'],proc.pid);self.assertEqual(result['guard_pid'],proc.pid)
                self.assertTrue(result['execution_has_members']);self.assertTrue(result['inherited_gpu_fd_closed']);self.assertTrue(result['token_removed'])
                self.assertFalse(result['native_started']);self.assertFalse((runtime/'models/colibri/sessions').exists())
                self.assertEqual({item['pid'] for item in P.descendants(P.lease(proc.pid))},{result['worker_pid']})
                for path in paths.values():
                    with path.open('a') as competing:
                        with self.assertRaises(BlockingIOError):fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
                (root/'done').touch();out,error=proc.communicate(timeout=10)
                self.assertEqual(proc.returncode,0,error)
                receipt=json.loads((folder/'guard-result.json').read_text())
                self.assertEqual((receipt['reason'],receipt['code'],receipt['launch_sha256']),('exited',0,P.fingerprint(launch)))
                for path in paths.values():
                    with path.open('a') as competing:fcntl.flock(competing,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                (root/'done').touch()
                if proc.poll() is None:
                    proc.terminate()
                    try:proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:proc.kill();proc.communicate(timeout=5)


if __name__=='__main__':unittest.main()
