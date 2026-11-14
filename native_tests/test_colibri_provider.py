"""Native process/socket conformance; never loads a model or contacts cloud."""
import http.client
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import colibri_provider as G
import phase_runtime as P


@unittest.skipUnless(sys.platform=='darwin','Native macOS resource accounting')
class NativeProvider(unittest.TestCase):
    def test_sigterm_after_child_creation_stays_globally_fenced_without_gateway_pid(self):
        import memory_coordination as MC
        import memory_runtime as MR
        import model_bench as MB
        real_popen=subprocess.Popen;children=[]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();runtime=root/'runtime';engine=root/'engine';engine.mkdir()
            config={'version':2,'engine_cli':str(engine/'coli'),'model':str(root/'model'),'context':32768,'environment':{}}
            model={'model':G.MODEL,'parameters_sha256':G.hashlib.sha256(G.canonical(config)).hexdigest()}
            proof={'phase':'fixture-phase','job':'fixture-job','input_key':'fixture-key','boot_id':G.D.boot_id(),
                   'model_sha256':P.fingerprint(model)}
            def stopped(_signal,_frame):raise InterruptedError('Stopped by SIGTERM')
            def interrupted_creation(*args,**kwargs):
                child=real_popen([sys.executable,'-B','-c','import time;time.sleep(30)'],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
                children.append((child,P.lease(child.pid)))
                os.kill(os.getpid(),signal.SIGTERM)
                raise AssertionError('Signal handler must interrupt creation')
            previous=signal.signal(signal.SIGTERM,stopped)
            try:
                with patch.object(G,'ROOT',root),patch.object(G,'DIRECTORY',runtime/'models/colibri'), \
                     patch.object(G,'config',return_value=config),patch.object(G,'validate_config',return_value=config), \
                     patch.object(MR,'validate_provider_start',return_value=proof),patch.object(MB,'api',return_value={'models':[]}), \
                     patch.dict(os.environ,{'MIDNIGHT_ACTION_ID':'fixture-job','MIDNIGHT_RESOURCE_INPUT_KEY':'fixture-key'}):
                    owned=G.NativeSession(model,time.time()+30);owned.begin_request('a'*32,{'fixture':'input'})
                    with patch.object(G.subprocess,'Popen',side_effect=interrupted_creation):
                        with self.assertRaises(InterruptedError):owned.start(time.monotonic()+20)
                    with patch('sys.stdout',new_callable=io.StringIO):owned.close()
                    self.assertEqual(owned.native_launch,'unknown');self.assertIsNone(owned.server)
                    self.assertIsNone(children[0][0].poll());self.assertTrue(P.alive(children[0][1]))
                    self.assertFalse((owned.directory/'unloaded.json').exists())
                    self.assertEqual(P.Actions(runtime).executions(),[])
                    self.assertTrue(MC.unresolved_native(runtime))
                    self.assertFalse(json.loads((owned.directory/'failure.json').read_text())['drain']['owned_processes_drained'])
            finally:
                signal.signal(signal.SIGTERM,previous)
                for child,lease in children:
                    P.stop_tree(lease,grace=.1);child.wait(timeout=3)

    def test_real_sdk_footprint_and_birth(self):
        usage=G.memory(os.getpid())
        self.assertEqual(usage['pid'],os.getpid())
        self.assertGreater(usage['birth'],0)
        self.assertGreater(usage['footprint'],0)

    def test_memory_failure_still_drains_gateway_detached_child_and_watcher(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(G,'DIRECTORY',Path(temporary)):
            directory=Path(temporary);child=directory/'child.pid'
            script='import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],start_new_session=True); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)'
            process=subprocess.Popen([sys.executable,'-B','-c',script,str(child)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            original=P.lease(process.pid)
            self.addCleanup(lambda:P.stop_tree(original,grace=.1))
            deadline=time.monotonic()+5
            while not child.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(child.exists());child_lease=P.lease(int(child.read_text()))
            self.addCleanup(lambda:P.stop_tree(child_lease,grace=.1))
            owned=G.NativeSession({'model':G.MODEL});owned.directory.mkdir(parents=True)
            owned.server=process;owned.gateway_lease=original;owned.track()
            joined=threading.Event()
            def watcher():owned.stop.wait();joined.set()
            owned.watcher=threading.Thread(target=watcher);owned.watcher.start()
            with patch.object(G,'memory',side_effect=OSError('synthetic telemetry failure')):
                with self.assertRaises(OSError):owned.capture()
                owned.close()
            self.assertTrue(joined.is_set());self.assertFalse(owned.watcher.is_alive())
            self.assertFalse(P.alive(original));self.assertFalse(P.alive(child_lease))
            self.assertTrue(json.loads((owned.directory/'unloaded.json').read_text())['owned_processes_drained'])

    def test_memory_watcher_emits_bound_failure_only_after_native_fixture_drain(self):
        class FastStop:
            def __init__(self):self.event=threading.Event()
            def wait(self,seconds):return self.event.wait(min(seconds,.01))
            def set(self):self.event.set()
            def is_set(self):return self.event.is_set()
        with tempfile.TemporaryDirectory() as temporary,patch.object(G,'DIRECTORY',Path(temporary)):
            process=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            lease=P.lease(process.pid);self.addCleanup(lambda:P.stop_tree(lease,grace=.1))
            owned=G.NativeSession({'model':G.MODEL,'parameters_sha256':'f'*64});owned.begin_request('a'*32,{'synthetic':'input'})
            owned.server=process;owned.gateway_lease=lease;owned.profile={'charged_limit_bytes':1};owned.stop=FastStop()
            (owned.directory/'native.log').write_text('')
            with patch.object(G.subprocess,'check_output',return_value='1'),patch('sys.stdout',new_callable=io.StringIO) as output:
                owned.watcher=threading.Thread(target=owned.watch);owned.watcher.start();owned.watcher.join(5)
                self.assertFalse(owned.watcher.is_alive());self.assertIsNotNone(owned.failure)
                self.assertFalse((owned.directory/'failure.json').exists());owned.close()
            self.assertFalse(P.alive(lease));self.assertFalse(owned.watcher.is_alive())
            receipt=json.loads((owned.directory/'failure.json').read_text())
            self.assertEqual(receipt['kind'],'charged_limit');self.assertEqual(receipt['gateway'],lease)
            self.assertGreater(receipt['memory']['charged_bytes'],1)
            self.assertTrue(receipt['drain']['owned_processes_drained'])
            event=json.loads(output.getvalue());self.assertEqual(event['receipt_sha256'],G.digest(owned.directory/'failure.json'))

    def test_cleanup_never_reacquires_a_reused_gateway_pid(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary,patch.object(G,'DIRECTORY',Path(temporary)):
            owned=G.NativeSession({'model':G.MODEL});owned.directory.mkdir(parents=True)
            original={'pid':42,'stamp':'original','boot_id':'fixed','birth_us':10}
            owned.gateway_lease=original;owned.members={('original',):original}
            owned.server=SimpleNamespace(pid=42,wait=lambda **kwargs:0)
            with patch.object(P,'lease',side_effect=AssertionError('PID identity reacquired')),patch.object(P,'alive',return_value=False),patch.object(P,'stop_tree',return_value=True) as stop,patch.object(G.os,'kill') as kill:
                owned.close()
            self.assertEqual(stop.call_args.args[0]['birth_us'],10)
            kill.assert_not_called()

    def test_absolute_socket_deadline_interrupts_stalled_headers(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();self.addCleanup(listener.close)
        done=threading.Event()
        def stalled():
            peer,_=listener.accept()
            with peer:peer.recv(8192);done.wait(2)
        thread=threading.Thread(target=stalled,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',listener.getsockname()[1],timeout=2)
        started=time.monotonic();timer,expired,transport=G.deadline_watch(connection,started+.1)
        try:
            connection.request('GET','/');transport[0]=connection.sock
            with self.assertRaises((OSError,http.client.HTTPException)):connection.getresponse()
            self.assertTrue(expired.is_set());self.assertLess(time.monotonic()-started,1)
        finally:timer.cancel();connection.close();done.set();thread.join(2)


if __name__=='__main__':unittest.main()
