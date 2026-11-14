"""Native evidence that the disposable candidate harness remains confined."""
import json,os,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path
from worker_probe_runtime import confined_worker
import worker_probe_runtime as R
import phase_runtime as P

class ProbeConfinementTests(unittest.TestCase):
 def test_detached_child_then_immediate_exit_is_denied_before_it_can_escape(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();source=root/'source';source.mkdir();case=root/'case';case.mkdir();marker=case/'detached.json'
   code='''import json,pathlib,subprocess,sys
try:
 child=subprocess.Popen([sys.executable,'-I','-B','-c','import time;time.sleep(20)'],start_new_session=True)
 pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid':child.pid}))
except PermissionError:print('fork-denied')
'''
   command,env=confined_worker(source,case,[sys.executable,'-I','-B','-c',code,str(marker)])
   launch=root/'launch.json';launch.write_text(json.dumps({'parent':P.lease(),'deadline':time.time()+15,'cwd':str(case),'env':env,'command':command}))
   proc=subprocess.Popen([sys.executable,'-I','-B',R.__file__,'--guard',str(launch)],start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   lease=dict(P.lease(proc.pid),group=True,members=[])
   try:
    until=time.monotonic()+5
    while not launch.with_suffix('.exit.json').exists() and time.monotonic()<until:time.sleep(.02)
    self.assertEqual(json.loads(launch.with_suffix('.exit.json').read_text())['code'],0);self.assertFalse(marker.exists())
    lease['members']=P.group_members(lease)+P.descendants(lease);self.assertTrue(P.stop_tree(lease,grace=5));out,error=proc.communicate(timeout=3)
    self.assertIn(b'fork-denied',out,error.decode());self.assertFalse(any(P.alive(p) for p in lease['members']));self.assertTrue(json.loads(launch.with_suffix('.drained.json').read_text())['drained'])
   finally:P.stop_tree(lease,grace=2);proc.communicate(timeout=3)
 def test_candidate_harness_cannot_read_private_files_write_outside_or_open_network(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();source=root/'source';source.mkdir();case=root/'case';case.mkdir()
   private=Path.home()/('midnight-probe-denial-'+uuid.uuid4().hex);private.write_text('private-fixture')
   self.addCleanup(private.unlink,missing_ok=True)
   code='''import json,pathlib,socket
p=pathlib.Path(__import__('sys').argv[1]);blocked=[]
for action in (lambda:p.read_text(),lambda:p.write_text('changed'),lambda:socket.socket().bind(('127.0.0.1',0))):
 try:action();blocked.append(False)
 except PermissionError:blocked.append(True)
print(json.dumps(blocked))
'''
   command,env=confined_worker(source,case,[sys.executable,'-I','-B','-c',code,str(private)])
   result=subprocess.run(command,cwd=case,env=env,capture_output=True,text=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),[True,True,True]);self.assertEqual(private.read_text(),'private-fixture')
 def test_candidate_cannot_install_a_native_git_fsmonitor_hook(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();source=root/'source';source.mkdir();case=root/'case';case.mkdir();work=case/'work';work.mkdir()
   subprocess.run(['git','init','--quiet',str(work)],check=True)
   sentinel=root/'escaped';hook=case/'hook.sh';hook.write_text('#!/bin/sh\nprintf escaped > '+str(sentinel)+'\n');hook.chmod(0o700)
   code="import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.write_text(p.read_text()+'\\n[core]\\n fsmonitor = '+sys.argv[2]+'\\n')"
   command,env=confined_worker(source,case,[sys.executable,'-I','-B','-c',code,str(work/'.git/config'),str(hook)])
   result=subprocess.run(command,cwd=case,env=env,capture_output=True,text=True,timeout=10)
   self.assertNotEqual(result.returncode,0);self.assertIn('Operation not permitted',result.stderr)
   subprocess.run(['git','diff','--name-only'],cwd=work,check=True,capture_output=True);self.assertFalse(sentinel.exists())
 def test_guard_retains_leader_until_descendants_are_verified_drained(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();launch=root/'launch.json'
   launch.write_text(json.dumps({'parent':P.lease(),'deadline':time.time()+15,'cwd':str(root),'env':{'PATH':'/usr/bin:/bin'},'command':[sys.executable,'-I','-B','-c','import subprocess;subprocess.Popen(["/bin/sleep","20"])']}))
   proc=subprocess.Popen([sys.executable,'-I','-B',R.__file__,'--guard',str(launch)],start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   lease=dict(P.lease(proc.pid),group=True,members=[])
   try:
    until=time.monotonic()+5
    while not launch.with_suffix('.exit.json').exists() and time.monotonic()<until:time.sleep(.05)
    self.assertTrue(launch.with_suffix('.guard.json').exists());self.assertTrue(launch.with_suffix('.exit.json').exists());self.assertTrue(P.alive(lease))
    lease['members']=P.group_members(lease)+P.descendants(lease);self.assertGreater(len(lease['members']),1)
    self.assertTrue(P.stop_tree(lease,grace=5));self.assertFalse(any(P.alive(p) for p in lease['members']));proc.wait(timeout=3)
    self.assertTrue(json.loads(launch.with_suffix('.drained.json').read_text())['drained'])
   finally:P.stop_tree(lease,grace=2);proc.wait(timeout=3);proc.stderr.close()
 def test_guard_exits_on_immediate_parent_death_without_leaving_child(self):
  with tempfile.TemporaryDirectory() as temporary:
   root=Path(temporary).resolve();parent_script=root/'parent.py';launch=root/'launch.json'
   parent_script.write_text('''import json,subprocess,sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1]);import phase_runtime as P\np=Path(sys.argv[2]);p.write_text(json.dumps({'parent':P.lease(),'deadline':time.time()+15,'cwd':str(p.parent),'env':{'PATH':'/usr/bin:/bin'},'command':['/bin/sleep','20']}))\nchild=subprocess.Popen([sys.executable,'-I','-B',sys.argv[1]+'/worker_probe_runtime.py','--guard',str(p)],start_new_session=True)\np.with_suffix('.spawn.json').write_text(json.dumps(P.lease(child.pid)))\nwhile True:time.sleep(1)\n''')
   parent=subprocess.Popen([sys.executable,'-I','-B',str(parent_script),str(Path(R.__file__).parent),str(launch)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    until=time.monotonic()+5
    while not launch.with_suffix('.spawn.json').exists() and time.monotonic()<until:time.sleep(.02)
    lease=json.loads(launch.with_suffix('.spawn.json').read_text());parent.kill();parent.wait(timeout=3)
    until=time.monotonic()+6
    while P.alive(lease) and time.monotonic()<until:time.sleep(.05)
    self.assertFalse(P.alive(lease))
    child=launch.with_suffix('.child.json')
    if child.exists():self.assertFalse(P.alive(json.loads(child.read_text())))
   finally:
    if parent.poll() is None:parent.kill();parent.wait(timeout=3)

if __name__=='__main__':unittest.main()
