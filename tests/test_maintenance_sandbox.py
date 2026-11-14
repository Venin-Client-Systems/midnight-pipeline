import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from verification_sandbox import confined


@unittest.skipUnless(Path('/usr/bin/sandbox-exec').exists(),'Native macOS confinement test')
class MaintenanceSandboxTests(unittest.TestCase):
    def test_generated_code_cannot_signal_an_unrelated_host_process(self):
        with tempfile.TemporaryDirectory() as name,subprocess.Popen(['/bin/sleep','20']) as sentinel:
            root=Path(name);work=root/'work';work.mkdir();evidence=root/'evidence';evidence.mkdir()
            script=work/'hostile_signal.py';script.write_text(f'import os,signal\ntry:\n os.kill({sentinel.pid},signal.SIGTERM);print("ALLOWED")\nexcept PermissionError:print("denied")\n')
            args,env=confined(work,evidence,[sys.executable,'-B',str(script)],evidence/'check.log')
            try:
                result=subprocess.run(args,cwd=work,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'denied')
                self.assertIsNone(sentinel.poll())
            finally:sentinel.terminate();sentinel.wait(timeout=3)

    def test_generated_test_cannot_read_private_source_write_runtime_or_connect_to_services(self):
        with tempfile.TemporaryDirectory(prefix='.midnight-confinement-',dir=Path.home()) as name:
            root=Path(name);work=root/'work';work.mkdir();evidence=root/'evidence';evidence.mkdir()
            secret=root/'account-canary';secret.write_text('private-canary')
            protected=root/'runtime-canary';protected.write_text('preserve')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
                program=('import json,socket;from pathlib import Path\nresults={}\n'
                    +f'for key,path,mode in [("read",{str(secret)!r},"read"),("write",{str(protected)!r},"write")]:\n'
                    +' try:\n  p=Path(path);p.read_text() if mode=="read" else p.write_text("changed");results[key]="ALLOWED"\n except PermissionError:results[key]="denied"\n'
                    +f'try:\n socket.create_connection(("127.0.0.1",{port}),timeout=.3);results["network"]="ALLOWED"\nexcept PermissionError:results["network"]="denied"\n'
                    +'print(json.dumps(results))\n')
                script=work/'hostile.py';script.write_text(program)
                log=evidence/'check.log';args,env=confined(work,evidence,[sys.executable,'-B',str(script)],log)
                result=subprocess.run(args,cwd=work,env=env,capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout),{'read':'denied','write':'denied','network':'denied'})
                self.assertEqual(protected.read_text(),'preserve');self.assertEqual(secret.read_text(),'private-canary')
                self.assertNotIn('CODEX_HOME',env);self.assertNotIn('GH_TOKEN',env)


if __name__=='__main__':unittest.main()
